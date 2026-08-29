# -*- coding: utf-8 -*-
"""
ProactiveMixin — 从 main.py 重新拆分出的主动消息调度
"""
from __future__ import annotations

import asyncio
import base64
import gc
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
from copy import deepcopy
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse
from xml.etree import ElementTree as ET

from astrbot.api import AstrBotConfig, logger
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
from astrbot.core.star.star_handler import EventType, star_handlers_registry
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
from .helpers import _date_key, _now_ts, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks, _today_key


_ANONYMOUS_AREA_STABLE_GAP_SECONDS = 90 * 60
_ANONYMOUS_AREA_PENDING_TTL_SECONDS = 24 * 60 * 60
_ANONYMOUS_AREA_VISIT_GAP_SECONDS = 2 * 60 * 60
_ANONYMOUS_AREA_DWELL_THRESHOLDS_SECONDS = {2: 6 * 3600, 3: 3 * 3600, 4: 90 * 60, 5: 45 * 60}
_MOBILE_LOCATION_HUMANIZATION_BUDGET_SECONDS = 60 * 60
from .relationship_policy import relationship_stage_for_score
from .companion_interaction_expression import current_interaction_projection
from .user_rest_gate import UserRestGateMixin
from .proactive_routes import PROACTIVE_ROUTE_REGISTRY
from .unified_profile_service import capability_summary as req036_capability_summary
from .unified_profile_service import update_capabilities as req036_update_capabilities
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

DEFAULT_AI_DAILY_NEWS_SOURCE = "B站 AI早报|bilibili:285286947"

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


def _proactive_setting_value(obj: Any, key: str, default: Any = None) -> Any:
    """Resolve an active-persona setting, with a plain-attribute fallback."""
    getter = getattr(obj, "persona_setting", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            try:
                return getter(key, default=default)
            except Exception:
                pass
        except Exception:
            pass
    return getattr(obj, key, default)

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

class ProactiveMixin(UserRestGateMixin):
    """主动消息调度"""

    def _proactive_setting(self, key: str, default: Any = None) -> Any:
        """Read an active-persona setting without mutating shared attrs."""
        return _proactive_setting_value(self, key, default)

    _PROACTIVE_DAILY_LIMIT_UNLIMITED = 999_999
    _PROACTIVE_DAILY_QUOTA_MAX = 25
    _PROACTIVE_USER_DAILY_QUOTA_MAX = 30

    _PROACTIVE_QUOTA_TIER_POLICIES: dict[int, dict[str, Any]] = {
        0: {
            "label": "已关闭",
            "min_quota": 0,
            "max_quota": 0,
            "target_ratio": 0.0,
            "interval_cap_minutes": 0,
            "idle_cap_minutes": 0,
            "delay_range_hours": (0.0, 0.0),
            "unanswered_interval_weight": 1.0,
            "moment_probability_multiplier": 0.0,
            "candidate_score_bias": 0.0,
        },
        1: {
            "label": "克制",
            "min_quota": 1,
            "max_quota": 3,
            "target_ratio": 0.78,
            "interval_cap_minutes": 240,
            "idle_cap_minutes": 120,
            "delay_range_hours": (2.5, 8.0),
            "unanswered_interval_weight": 1.0,
            "moment_probability_multiplier": 0.82,
            "candidate_score_bias": -0.04,
        },
        2: {
            "label": "轻陪伴",
            "min_quota": 4,
            "max_quota": 7,
            "target_ratio": 0.86,
            "interval_cap_minutes": 150,
            "idle_cap_minutes": 75,
            "delay_range_hours": (1.25, 4.0),
            "unanswered_interval_weight": 0.72,
            "moment_probability_multiplier": 1.0,
            "candidate_score_bias": 0.0,
        },
        3: {
            "label": "稳定陪伴",
            "min_quota": 8,
            "max_quota": 12,
            "target_ratio": 0.92,
            "interval_cap_minutes": 90,
            "idle_cap_minutes": 45,
            "delay_range_hours": (0.65, 2.25),
            "unanswered_interval_weight": 0.42,
            "moment_probability_multiplier": 1.16,
            "candidate_score_bias": 0.04,
        },
        4: {
            "label": "亲密陪伴",
            "min_quota": 13,
            "max_quota": 18,
            "target_ratio": 0.97,
            "interval_cap_minutes": 55,
            "idle_cap_minutes": 25,
            "delay_range_hours": (0.38, 1.55),
            "unanswered_interval_weight": 0.18,
            "moment_probability_multiplier": 1.34,
            "candidate_score_bias": 0.08,
        },
        5: {
            "label": "持续在线",
            "min_quota": 19,
            "max_quota": None,
            "target_ratio": 1.0,
            "interval_cap_minutes": 35,
            "idle_cap_minutes": 10,
            "delay_range_hours": (0.22, 1.05),
            "unanswered_interval_weight": 0.0,
            "moment_probability_multiplier": 1.52,
            "candidate_score_bias": 0.12,
        },
    }

    _PROACTIVE_KIND_POLICIES: dict[str, dict[str, Any]] = {
        "transactional": {
            "label": "明确事务",
            "interval_multiplier": 0.2,
            "unanswered_score_penalty": 0.0,
            "score_bias": 0.16,
            "response_expectation": "none",
        },
        "continuation": {
            "label": "对话延续",
            "interval_multiplier": 0.65,
            "unanswered_score_penalty": 0.04,
            "score_bias": 0.08,
            "response_expectation": "optional",
        },
        "ritual": {
            "label": "日常仪式",
            "interval_multiplier": 0.82,
            "unanswered_score_penalty": 0.03,
            "score_bias": 0.04,
            "response_expectation": "optional",
        },
        "relational": {
            "label": "关系关怀",
            "interval_multiplier": 1.0,
            "unanswered_score_penalty": 0.08,
            "score_bias": 0.0,
            "response_expectation": "optional",
        },
        "self_life": {
            "label": "生活自述",
            "interval_multiplier": 0.86,
            "unanswered_score_penalty": 0.015,
            "score_bias": 0.02,
            "response_expectation": "none",
        },
        "content_share": {
            "label": "内容分享",
            "interval_multiplier": 0.9,
            "unanswered_score_penalty": 0.015,
            "score_bias": 0.03,
            "response_expectation": "none",
        },
        "safety_event": {
            "label": "安全与环境事件",
            "interval_multiplier": 0.12,
            "unanswered_score_penalty": 0.0,
            "score_bias": 0.2,
            "response_expectation": "none",
        },
    }

    _PROACTIVE_INTENSITY_PRESETS: dict[str, dict[str, Any]] = {
        "off": {
            "label": "关闭预设",
            "description": "沿用手动配置，不覆盖任何主动频率参数。",
            "effects": {},
        },
        "balanced": {
            "label": "标准偏主动",
            "description": "轻度提高主动触达，适合想比手动默认更有存在感但仍保持低打扰的场景。",
            "effects": {
                "max_daily_messages": 9,
                "idle_minutes": 40,
                "min_interval_minutes": 75,
                "unanswered_slowdown_start": 2,
                "unanswered_max_interval_multiplier": 1.65,
                "friend_unanswered_max_cooldown_hours": 30,
                "friend_idle_floor_minutes": 60,
                "friend_min_interval_floor_minutes": 120,
                "delay_factor": 0.72,
                "proactive_persona_judge_send_threshold": 54,
                "proactive_review_strength": "lenient",
                "group_wakeup_cooldown_seconds": 50,
                "group_high_intensity_cooldown_seconds": 105,
                "group_wakeup_interest_probability": 0.24,
                "group_wakeup_question_threshold": 60,
                "group_wakeup_cold_group_threshold": 62,
                "group_wakeup_topic_interest_max_boost": 0.55,
                "group_interject_min_interval_minutes": 90,
                "group_interject_max_daily": 4,
            },
        },
        "high_private": {
            "label": "私聊高频",
            "description": "显著提高主要用户私聊主动频率，适合希望 Bot 更常来找的用户。",
            "effects": {
                "max_daily_messages": 15,
                "idle_minutes": 14,
                "min_interval_minutes": 24,
                "unanswered_slowdown_start": 4,
                "unanswered_max_interval_multiplier": 1.25,
                "friend_unanswered_max_cooldown_hours": 14,
                "friend_idle_floor_minutes": 30,
                "friend_min_interval_floor_minutes": 60,
                "delay_factor": 0.42,
                "proactive_persona_judge_send_threshold": 45,
                "proactive_review_strength": "lenient",
                "group_wakeup_cooldown_seconds": 45,
                "group_high_intensity_cooldown_seconds": 90,
                "group_wakeup_interest_probability": 0.22,
                "group_wakeup_question_threshold": 60,
                "group_wakeup_cold_group_threshold": 62,
                "group_wakeup_topic_interest_max_boost": 0.5,
                "group_interject_min_interval_minutes": 90,
                "group_interject_max_daily": 4,
            },
        },
        "high_group": {
            "label": "群聊活跃",
            "description": "明显提高群聊唤醒、兴趣词接话和群主动插话，私聊只轻度增强。",
            "effects": {
                "max_daily_messages": 8,
                "idle_minutes": 50,
                "min_interval_minutes": 95,
                "unanswered_slowdown_start": 2,
                "unanswered_max_interval_multiplier": 1.8,
                "friend_unanswered_max_cooldown_hours": 36,
                "friend_idle_floor_minutes": 75,
                "friend_min_interval_floor_minutes": 150,
                "delay_factor": 0.75,
                "proactive_persona_judge_send_threshold": 56,
                "proactive_review_strength": "lenient",
                "group_wakeup_cooldown_seconds": 20,
                "group_high_intensity_cooldown_seconds": 45,
                "group_wakeup_interest_probability": 0.45,
                "group_wakeup_question_threshold": 52,
                "group_wakeup_cold_group_threshold": 54,
                "group_wakeup_topic_interest_max_boost": 0.95,
                "group_interject_min_interval_minutes": 24,
                "group_interject_max_daily": 12,
            },
        },
        "live": {
            "label": "在线陪伴",
            "description": "最高在线陪伴档，每日主动上限 25 条，也不再替用户节省主动成本；仍会尊重免打扰、休息、拒绝、隐私和硬限额。",
            "effects": {
                "max_daily_messages": _PROACTIVE_DAILY_QUOTA_MAX,
                "idle_minutes": 0,
                "min_interval_minutes": 5,
                "unanswered_slowdown_start": 8,
                "unanswered_max_interval_multiplier": 1.0,
                "friend_unanswered_max_cooldown_hours": 8,
                "friend_idle_floor_minutes": 5,
                "friend_min_interval_floor_minutes": 15,
                "delay_factor": 0.08,
                "ignore_token_soft_limit": True,
                "ignore_soft_daily_target": True,
                "proactive_persona_judge_send_threshold": 32,
                "proactive_review_strength": "lenient",
                "group_wakeup_cooldown_seconds": 3,
                "group_high_intensity_cooldown_seconds": 30,
                "group_wakeup_interest_probability": 0.78,
                "group_wakeup_question_threshold": 40,
                "group_wakeup_cold_group_threshold": 42,
                "group_wakeup_topic_interest_max_boost": 1.5,
                "group_interject_min_interval_minutes": 6,
                "group_interject_max_daily": _PROACTIVE_DAILY_LIMIT_UNLIMITED,
                "ignore_group_interject_daily_limit": True,
            },
        },
    }

    def _normalize_proactive_intensity_preset(self, value: Any) -> str:
        preset = str(value or "off").strip().lower()
        aliases = {
            "": "off",
            "manual": "off",
            "none": "off",
            "default": "off",
            "standard": "balanced",
            "active": "high_private",
            "private": "high_private",
            "group": "high_group",
            "online": "live",
            "直播": "live",
            "高频": "live",
        }
        preset = aliases.get(preset, preset)
        return preset if preset in self._PROACTIVE_INTENSITY_PRESETS else "off"

    def _proactive_intensity_runtime(self) -> dict[str, Any]:
        preset = self._normalize_proactive_intensity_preset(
            _proactive_setting_value(self, "proactive_intensity_preset", "off")
        )
        spec = self._PROACTIVE_INTENSITY_PRESETS.get(preset) or self._PROACTIVE_INTENSITY_PRESETS["off"]
        effects = dict(spec.get("effects") or {})
        return {
            "preset": preset,
            "enabled": preset != "off",
            "label": str(spec.get("label") or preset),
            "description": str(spec.get("description") or ""),
            "effects": effects,
        }

    def _proactive_intensity_effect(self, key: str, default: Any = None) -> Any:
        runtime = self._proactive_intensity_runtime()
        if not runtime.get("enabled"):
            return default
        return runtime.get("effects", {}).get(key, default)

    @classmethod
    def _proactive_daily_limit_is_unlimited(cls, value: Any) -> bool:
        return _safe_int(value, 0, 0) >= cls._PROACTIVE_DAILY_LIMIT_UNLIMITED

    @classmethod
    def _format_proactive_daily_limit(cls, value: Any) -> str:
        return "不限" if cls._proactive_daily_limit_is_unlimited(value) else str(_safe_int(value, 0, 0))

    def _proactive_intensity_ignores_daily_limit(self) -> bool:
        return bool(self._proactive_intensity_effect("ignore_daily_limit", False))

    @classmethod
    def _proactive_quota_tier_for_limit(cls, value: Any) -> int:
        quota = max(0, _safe_int(value, 0, 0))
        if quota <= 0:
            return 0
        if quota <= 3:
            return 1
        if quota <= 7:
            return 2
        if quota <= 12:
            return 3
        if quota <= 18:
            return 4
        return 5

    def _proactive_quota_policy(self, user: dict[str, Any] | None = None) -> dict[str, Any]:
        limit = self._effective_user_daily_limit(user or {}) if isinstance(user, dict) else self._runtime_max_daily_messages()
        if self._proactive_daily_limit_is_unlimited(limit):
            limit = self._PROACTIVE_DAILY_QUOTA_MAX
        quota = max(0, min(self._PROACTIVE_USER_DAILY_QUOTA_MAX, _safe_int(limit, 0, 0)))
        tier = self._proactive_quota_tier_for_limit(quota)
        policy = dict(self._PROACTIVE_QUOTA_TIER_POLICIES[tier])
        policy.update({"tier": tier, "quota": quota})
        return policy

    def _proactive_message_kind(
        self,
        *,
        reason: Any = "",
        source: Any = "",
        semantic_kind: Any = "",
    ) -> str:
        return PROACTIVE_ROUTE_REGISTRY.route_for(
            source=source,
            reason=reason,
            semantic_kind=semantic_kind,
        ).key

    def _proactive_route_for(
        self,
        *,
        reason: Any = "",
        source: Any = "",
        semantic_kind: Any = "",
        kind: Any = "",
    ):
        return PROACTIVE_ROUTE_REGISTRY.route_for(
            source=source,
            reason=reason,
            semantic_kind=semantic_kind,
            kind=kind,
        )

    def _proactive_kind_policy(self, kind: Any) -> dict[str, Any]:
        route = self._proactive_route_for(kind=kind)
        return {
            "label": route.label,
            "interval_multiplier": route.interval_multiplier,
            "unanswered_score_penalty": route.unanswered_score_penalty,
            "score_bias": route.score_bias,
            "response_expectation": route.response_expectation,
        }

    def _planned_proactive_kind(self, user: dict[str, Any]) -> str:
        persisted = _single_line(user.get("planned_proactive_kind"), 40).lower()
        if persisted in {route.key for route in PROACTIVE_ROUTE_REGISTRY.all_routes()}:
            return persisted
        return self._proactive_message_kind(
            reason=user.get("planned_proactive_reason"),
            source=user.get("planned_proactive_source"),
            semantic_kind=user.get("planned_proactive_semantic_kind"),
        )

    def _proactive_route_prompt(self, user: dict[str, Any], *, reason: Any = "", source: Any = "") -> str:
        kind = self._proactive_message_kind(
            reason=reason or user.get("planned_proactive_reason"),
            source=source or user.get("planned_proactive_source"),
            semantic_kind=user.get("planned_proactive_semantic_kind"),
        )
        tier_policy = self._proactive_quota_policy(user)
        route = self._proactive_route_for(kind=kind)
        tier_rule = (
            "当前属于高主动配额用户，可以自然、具体地开口，不要因为此前没有逐条回应就写得疏远；仍然避免催促和凑数。"
            if _safe_int(tier_policy.get("tier"), 0) >= 4
            else "按当前关系自然表达，不解释主动频率、配额、候选或调度机制。"
        )
        return (
            "【本轮主动路线】\n"
            f"- 类型：{route.label}。\n"
            f"- 路线要求：{route.render_directive(quota_tier=_safe_int(tier_policy.get('tier'), 0))}\n"
            f"- 终审重点：{route.review_directive()}\n"
            f"- 配额策略：L{tier_policy.get('tier', 0)} {tier_policy.get('label', '')}。{tier_rule}"
        )

    def _prepare_proactive_route_candidate(
        self,
        user: dict[str, Any],
        candidate: dict[str, Any],
        *,
        source: str,
        now: float,
    ) -> dict[str, Any]:
        route = self._proactive_route_for(
            reason=candidate.get("reason"),
            source=source or candidate.get("source"),
            semantic_kind=candidate.get("semantic_kind"),
            kind=candidate.get("kind"),
        )
        prepared = route.prepare_candidate(
            candidate,
            source=source,
            now=now,
            date_key=_today_key(),
        )
        prepared["quota_tier"] = _safe_int(self._proactive_quota_policy(user).get("tier"), 0, 0, 5)
        return prepared

    def _planned_proactive_route(self, user: dict[str, Any]):
        return self._proactive_route_for(
            reason=user.get("planned_proactive_reason"),
            source=user.get("planned_proactive_source"),
            semantic_kind=user.get("planned_proactive_semantic_kind"),
            kind=user.get("planned_proactive_kind"),
        )

    def _planned_proactive_route_payload(self, user: dict[str, Any]) -> dict[str, Any]:
        return {
            "reason": user.get("planned_proactive_reason"),
            "source": user.get("planned_proactive_source"),
            "kind": self._planned_proactive_kind(user),
            "topic": user.get("planned_proactive_topic"),
            "motive": user.get("planned_proactive_motive"),
            "trigger_message_id": user.get("planned_proactive_trigger_message_id"),
            "trigger_ts": user.get("planned_proactive_trigger_ts"),
            "trigger_inbound_count": user.get("planned_proactive_trigger_inbound_count"),
            "private_inbound_count": user.get("private_inbound_count"),
            "origin_event_id": user.get("planned_proactive_origin_event_id"),
            "semantic_anchor_type": user.get("planned_proactive_anchor_type"),
            "followup_kind": user.get("planned_followup_kind"),
            "chain": user.get("planned_event_chain") if isinstance(user.get("planned_event_chain"), list) else [],
            "window_start_at": user.get("planned_proactive_window_start_at"),
            "best_until_at": user.get("planned_proactive_best_until_at"),
            "expire_at": user.get("planned_proactive_expire_at"),
            "window_timezone": user.get("planned_proactive_window_timezone"),
        }

    def _store_planned_proactive_route_fields(self, user: dict[str, Any], item: dict[str, Any]) -> None:
        route = self._proactive_route_for(
            reason=item.get("reason") or user.get("planned_proactive_reason"),
            source=item.get("source") or user.get("planned_proactive_source"),
            semantic_kind=item.get("semantic_kind") or user.get("planned_proactive_semantic_kind"),
            kind=item.get("kind") or user.get("planned_proactive_kind"),
        )
        route_item = route.prepare_candidate(
            {
                **self._planned_proactive_route_payload(user),
                **item,
            },
            source=_single_line(item.get("source") or user.get("planned_proactive_source"), 40),
            now=_now_ts(),
            date_key=_today_key(),
        )
        options = route.delivery_options(route_item)
        user["planned_proactive_kind"] = route.key
        user["planned_proactive_route_version"] = _safe_int(route_item.get("route_version"), 2, 0)
        user["planned_proactive_route_dedupe_key"] = _single_line(route_item.get("route_dedupe_key"), 180)
        user["planned_proactive_route_review_profile"] = _single_line(
            route_item.get("route_review_profile") or route.review_profile,
            40,
        )
        user["planned_proactive_route_retry_profile"] = _single_line(
            route_item.get("route_retry_profile") or route.retry_profile,
            40,
        )
        user["planned_proactive_route_cancel_if_new_inbound"] = bool(
            route_item.get("route_cancel_if_new_inbound", options.get("cancel_if_new_inbound", True))
        )
        user["planned_proactive_route_recent_chat_policy"] = _single_line(
            route_item.get("route_recent_chat_policy") or options.get("recent_chat_policy"),
            40,
        )
        user["planned_proactive_route_allow_automatic_followup"] = bool(
            route_item.get("route_allow_automatic_followup", route.allow_automatic_followup)
        )
        user["planned_proactive_route_disable_segmenting"] = bool(
            route_item.get("route_disable_segmenting", options.get("disable_segmenting", False))
        )
        user["planned_proactive_response_expectation"] = _single_line(
            route_item.get("response_expectation") or route.response_expectation,
            24,
        )
        user["planned_proactive_origin_event_id"] = _single_line(route_item.get("origin_event_id"), 80)

    def _planned_proactive_route_preflight(self, user: dict[str, Any], *, now: float):
        route = self._planned_proactive_route(user)
        return route.preflight(user, self._planned_proactive_route_payload(user), now=now)

    def _planned_proactive_route_delivery_options(self, user: dict[str, Any]) -> dict[str, Any]:
        route = self._planned_proactive_route(user)
        return route.delivery_options(self._planned_proactive_route_payload(user))

    def _planned_proactive_route_settlement(self, user: dict[str, Any]) -> dict[str, Any]:
        route = self._planned_proactive_route(user)
        return route.settlement(self._planned_proactive_route_payload(user))

    def _effective_proactive_int(self, key: str, configured: int, *, minimum: int = 0, maximum: int | None = None) -> int:
        value = configured
        effect = self._proactive_intensity_effect(key, None)
        if effect is not None:
            value = _safe_int(effect, configured, minimum, maximum if maximum is not None else 10**9)
        value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

    def _effective_proactive_float(self, key: str, configured: float, *, minimum: float = 0.0, maximum: float | None = None) -> float:
        value = configured
        effect = self._proactive_intensity_effect(key, None)
        if effect is not None:
            value = _safe_float(effect, configured, minimum)
        value = max(minimum, float(value))
        if maximum is not None:
            value = min(maximum, value)
        return value

    def _effective_group_wakeup_cooldown_seconds(self) -> int:
        return self._effective_proactive_int(
            "group_wakeup_cooldown_seconds",
            _safe_int(_proactive_setting_value(self, "group_wakeup_cooldown_seconds", 90), 90, 0, 3600),
            minimum=0,
            maximum=3600,
        )

    def _effective_group_high_intensity_cooldown_seconds(self) -> int:
        return self._effective_proactive_int(
            "group_high_intensity_cooldown_seconds",
            _safe_int(_proactive_setting_value(self, "group_high_intensity_cooldown_seconds", 150), 150, 30, 1800),
            minimum=30,
            maximum=1800,
        )

    def _effective_group_interject_min_interval_minutes(self) -> int:
        base_interval = self._effective_proactive_int(
            "group_interject_min_interval_minutes",
            _safe_int(_proactive_setting_value(self, "group_interject_min_interval_minutes", 180), 180, 10, 1440),
            minimum=1,
            maximum=1440,
        )
        profile = self._cycle_proactive_frequency_profile()
        return max(1, min(1440, int(round(base_interval * profile["group_interval_multiplier"]))))

    def _cycle_proactive_frequency_profile(self) -> dict[str, Any]:
        neutral = {
            "phase": "neutral",
            "private_interval_multiplier": 1.0,
            "group_interval_multiplier": 1.0,
            "group_probability_multiplier": 1.0,
        }
        if not bool(_proactive_setting_value(self, "enable_cycle_state", True)):
            return neutral
        data = getattr(self, "data", {})
        state = data.get("daily_state") if isinstance(data, dict) else {}
        if not isinstance(state, dict) or str(state.get("date") or "") not in {"", _today_key()}:
            return neutral
        cycle_text = _single_line(state.get("body_cycle"), 100)
        if not cycle_text or cycle_text in {"无明显周期影响", "不处于生理期", "生理期模拟未开启"}:
            return neutral
        phase = ""
        conditions = state.get("conditions")
        if isinstance(conditions, list):
            phase = next(
                (
                    str(item.get("phase") or "")
                    for item in conditions
                    if isinstance(item, dict) and str(item.get("kind") or "") == "body_cycle"
                ),
                "",
            )
        upper_cycle_text = cycle_text.upper()
        if not phase:
            if "PMS" in upper_cycle_text or "经前综合征" in cycle_text:
                phase = "pms"
            elif "排卵前期" in cycle_text:
                phase = "pre_ovulation"
            elif "月经期" in cycle_text:
                phase = "menstrual"
            elif "卵泡期" in cycle_text:
                phase = "follicular"
            elif "排卵期" in cycle_text:
                phase = "ovulation"
            elif "黄体期" in cycle_text:
                phase = "luteal"
            elif "后" in cycle_text or "恢复" in cycle_text:
                phase = "recovery"
            elif "前" in cycle_text:
                phase = "pre"
            elif "生理期" in cycle_text:
                phase = "period"
        if phase == "recovery":
            return {
                "phase": "recovery",
                "private_interval_multiplier": 1.05,
                "group_interval_multiplier": 1.08,
                "group_probability_multiplier": 0.92,
            }
        if phase in {"pre", "pms"}:
            return {
                "phase": phase,
                "private_interval_multiplier": 1.08,
                "group_interval_multiplier": 1.12,
                "group_probability_multiplier": 0.88,
            }
        if phase in {"period", "menstrual"}:
            return {
                "phase": phase,
                "private_interval_multiplier": 1.18,
                "group_interval_multiplier": 1.25,
                "group_probability_multiplier": 0.76,
            }
        if phase in {"follicular", "pre_ovulation", "ovulation", "luteal"}:
            return {**neutral, "phase": phase}
        return neutral

    def _cycle_group_interject_probability(self, probability: float) -> float:
        profile = self._cycle_proactive_frequency_profile()
        return max(0.0, min(1.0, float(probability) * profile["group_probability_multiplier"]))

    def _effective_group_interject_max_daily(self) -> int:
        if bool(self._proactive_intensity_effect("ignore_group_interject_daily_limit", False)):
            return self._PROACTIVE_DAILY_LIMIT_UNLIMITED
        return self._effective_proactive_int(
            "group_interject_max_daily",
            _safe_int(_proactive_setting_value(self, "group_interject_max_daily", 2), 2, 0, 12),
            minimum=0,
            maximum=48,
        )

    def _effective_group_wakeup_interest_probability(self) -> float:
        return self._effective_proactive_float(
            "group_wakeup_interest_probability",
            max(0.0, min(1.0, _safe_float(_proactive_setting_value(self, "group_wakeup_interest_probability", 0.18), 0.18, 0.0))),
            minimum=0.0,
            maximum=1.0,
        )

    def _effective_group_wakeup_question_threshold(self) -> int:
        return self._effective_proactive_int(
            "group_wakeup_question_threshold",
            _safe_int(_proactive_setting_value(self, "group_wakeup_question_threshold", 65), 65, 0, 100),
            minimum=0,
            maximum=100,
        )

    def _effective_group_wakeup_cold_group_threshold(self) -> int:
        return self._effective_proactive_int(
            "group_wakeup_cold_group_threshold",
            _safe_int(_proactive_setting_value(self, "group_wakeup_cold_group_threshold", 65), 65, 0, 100),
            minimum=0,
            maximum=100,
        )

    def _effective_group_wakeup_topic_interest_max_boost(self) -> float:
        return self._effective_proactive_float(
            "group_wakeup_topic_interest_max_boost",
            max(0.0, min(1.5, _safe_float(_proactive_setting_value(self, "group_wakeup_topic_interest_max_boost", 0.45), 0.45, 0.0))),
            minimum=0.0,
            maximum=1.5,
        )

    def _effective_proactive_persona_judge_send_threshold(self) -> int:
        return self._effective_proactive_int(
            "proactive_persona_judge_send_threshold",
            _safe_int(_proactive_setting_value(self, "proactive_persona_judge_send_threshold", 62), 62, 0, 100),
            minimum=0,
            maximum=100,
        )

    def _effective_proactive_review_strength(self) -> str:
        value = str(self._proactive_intensity_effect("proactive_review_strength", "") or "").strip().lower()
        if value in {"lenient", "balanced", "strict"}:
            return value
        configured = str(_proactive_setting_value(self, "proactive_review_strength", "lenient") or "lenient").strip().lower()
        return configured if configured in {"lenient", "balanced", "strict"} else "lenient"

    def _proactive_intensity_ignores_token_soft_limit(self, task: str | None = None) -> bool:
        return bool(self._proactive_intensity_effect("ignore_token_soft_limit", False))

    def _configured_target_ids(self) -> list[str]:
        raw = self.target_user_ids
        if isinstance(raw, str):
            parts = re.split(r"[,\s,、;；]+", raw)
        elif isinstance(raw, list):
            parts = raw
        else:
            parts = []
        ids = []
        normalizer = getattr(self, "_normalize_private_identity_id", None)
        for part in parts:
            user_id = normalizer(part) if callable(normalizer) else _single_line(part, 128)
            if user_id and not self._is_bot_self_user_id(user_id) and user_id not in ids:
                ids.append(user_id)
        return ids

    def _user_enabled_for_proactive(self, user_id: str, user: dict[str, Any] | None = None) -> bool:
        if not isinstance(user, dict):
            return False
        req036_gate = getattr(self, "_req036_proactive_private_allowed", None)
        if callable(req036_gate):
            try:
                if not bool(req036_gate(user)):
                    return False
            except Exception:
                return False
        elif not req036_capability_summary(user).get("proactive_private_enabled"):
            return False
        return bool(user_id and not self._is_bot_self_user_id(user_id))

    def _default_private_umo_for_user_id(self, user_id: str) -> str:
        normalizer = getattr(self, "_normalize_private_identity_id", None)
        user_id = normalizer(user_id) if callable(normalizer) else _single_line(user_id, 128)
        if not user_id or self._is_bot_self_user_id(user_id):
            return ""
        platform = ""
        instance_resolver = getattr(self, "_preferred_platform_instance_id", None)
        if callable(instance_resolver):
            platform = _single_line(instance_resolver(), 80)
        if not platform:
            platform = _single_line(getattr(self, "target_platform", ""), 80) or "aiocqhttp"
        return f"{platform}:FriendMessage:{user_id}"

    def _private_delivery_user_id_for(self, user_id: str) -> str:
        canonical = self._canonical_private_user_id(str(user_id or "").strip())
        aliases = getattr(self, "private_user_delivery_aliases", {}) or {}
        normalizer = getattr(self, "_normalize_private_identity_id", None)
        target = normalizer(aliases.get(canonical)) if callable(normalizer) else _single_line(aliases.get(canonical), 128)
        if target and not self._is_bot_self_user_id(target):
            return target
        return canonical

    def _private_delivery_alias_target(self, user_id: str) -> str:
        canonical = self._canonical_private_user_id(str(user_id or "").strip())
        aliases = getattr(self, "private_user_delivery_aliases", {}) or {}
        return _single_line(aliases.get(canonical), 240)

    def _private_umo_session_id(self, umo: str) -> str:
        clean_umo = _single_line(umo, 240)
        if not clean_umo or ":FriendMessage:" not in clean_umo:
            return ""
        parser = getattr(self, "_parse_message_session", None)
        if callable(parser):
            try:
                session = parser(clean_umo)
            except Exception:
                session = None
            if session is not None:
                return _single_line(getattr(session, "session_id", ""), 128)
        return _single_line(clean_umo.rsplit(":FriendMessage:", 1)[-1], 128)

    @staticmethod
    def _private_delivery_route_store(user: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw = user.setdefault("private_delivery_routes", {})
        if not isinstance(raw, dict):
            raw = {}
            user["private_delivery_routes"] = raw
        return raw

    def _remember_private_delivery_route(
        self,
        user: dict[str, Any] | None,
        umo: str,
        *,
        outcome: str,
        error: str = "",
    ) -> None:
        if not isinstance(user, dict):
            return
        clean_umo = _single_line(umo, 240)
        session_id = self._private_umo_session_id(clean_umo)
        if not clean_umo or not session_id:
            return
        routes = self._private_delivery_route_store(user)
        item = routes.get(clean_umo)
        if not isinstance(item, dict):
            item = {}
            routes[clean_umo] = item
        now = _now_ts()
        item["umo"] = clean_umo
        item["session_id"] = session_id
        kind_getter = getattr(self, "_platform_kind_for_umo", None)
        item["platform_kind"] = _single_line(kind_getter(clean_umo) if callable(kind_getter) else "", 40)
        if outcome == "success":
            item["last_success_at"] = now
            item["failure_count"] = 0
            item["last_error"] = ""
            user["preferred_delivery_umo"] = clean_umo
        elif outcome == "failure":
            item["last_failure_at"] = now
            item["failure_count"] = _safe_int(item.get("failure_count"), 0, 0) + 1
            item["last_error"] = _single_line(error, 240)
        else:
            item["last_seen_at"] = now
            if _safe_float(item.get("last_failure_at"), 0) <= _safe_float(item.get("last_seen_at"), 0):
                item["failure_count"] = 0
        if len(routes) > 12:
            ordered = sorted(
                routes.items(),
                key=lambda pair: max(
                    _safe_float(pair[1].get("last_success_at"), 0) if isinstance(pair[1], dict) else 0,
                    _safe_float(pair[1].get("last_seen_at"), 0) if isinstance(pair[1], dict) else 0,
                    _safe_float(pair[1].get("last_failure_at"), 0) if isinstance(pair[1], dict) else 0,
                ),
                reverse=True,
            )
            routes.clear()
            routes.update(ordered[:12])

    def _private_delivery_umo_is_verified(self, user_id: str, user: dict[str, Any], umo: str) -> bool:
        clean_umo = _single_line(umo, 240)
        if not clean_umo:
            return False
        explicit = self._private_delivery_alias_target(user_id)
        if explicit and ":FriendMessage:" in explicit and explicit == clean_umo:
            return True
        if clean_umo in {
            _single_line(user.get("bound_delivery_umo"), 240),
            _single_line(user.get("last_inbound_umo"), 240),
            _single_line(user.get("preferred_delivery_umo"), 240),
            _single_line(user.get("last_proactive_delivery_umo"), 240),
        }:
            return True
        routes = user.get("private_delivery_routes")
        item = routes.get(clean_umo) if isinstance(routes, dict) else None
        return bool(
            isinstance(item, dict)
            and (
                _safe_float(item.get("last_success_at"), 0) > 0
                or _safe_float(item.get("last_seen_at"), 0) > 0
            )
        )

    def _private_delivery_umo_candidates(self, user_id: str) -> list[str]:
        canonical = self._canonical_private_user_id(str(user_id or "").strip())
        delivery_id = self._private_delivery_user_id_for(canonical)
        if not delivery_id:
            return []
        users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
        user = users.get(canonical) if isinstance(users, dict) and isinstance(users.get(canonical), dict) else {}
        candidates: list[str] = []

        def add(value: Any, *, trusted: bool = False) -> None:
            umo = _single_line(value, 240)
            if not umo or umo in candidates:
                return
            if not self._private_umo_matches_user_id(umo, delivery_id) and not (
                trusted and self._private_umo_session_id(umo)
            ):
                return
            platform_available = self._private_delivery_umo_platform_available(umo)
            if platform_available is False:
                return
            if umo and umo not in candidates:
                candidates.append(umo)

        if isinstance(user, dict):
            add(user.get("bound_delivery_umo"), trusted=True)

        explicit = self._private_delivery_alias_target(canonical)
        if ":FriendMessage:" in explicit:
            add(explicit)

        routes = user.get("private_delivery_routes") if isinstance(user, dict) else {}
        ranked_routes: list[tuple[tuple[int, int, float], str]] = []
        if isinstance(routes, dict):
            for route_umo, raw in routes.items():
                if not isinstance(raw, dict) or not self._private_umo_matches_user_id(route_umo, delivery_id):
                    continue
                success_at = _safe_float(raw.get("last_success_at"), 0)
                seen_at = _safe_float(raw.get("last_seen_at"), 0)
                failure_at = _safe_float(raw.get("last_failure_at"), 0)
                failed_latest = int(
                    _safe_int(raw.get("failure_count"), 0, 0) > 0
                    and failure_at >= max(success_at, seen_at)
                )
                ranked_routes.append(((1 - failed_latest, int(success_at > 0), max(success_at, seen_at)), route_umo))
        for _, route_umo in sorted(ranked_routes, key=lambda pair: pair[0], reverse=True):
            add(route_umo)

        if isinstance(user, dict):
            add(user.get("preferred_delivery_umo"))
            add(user.get("last_inbound_umo"))
            add(user.get("last_proactive_delivery_umo"))
            add(user.get("umo"))
        add(self._default_private_umo_for_user_id(delivery_id))
        return candidates

    def _private_delivery_umo_platform_available(self, umo: str) -> bool | None:
        """Return whether a saved UMO points to a currently usable platform instance.

        ``None`` keeps compatibility with harnesses or startup states where no platform
        manager is available yet; ``False`` rejects stale instance IDs such as ``default``
        when AstrBot has a different active instance.
        """
        manager = getattr(getattr(self, "context", None), "platform_manager", None)
        if manager is None:
            return None
        try:
            platforms = list(manager.get_insts())
        except Exception:
            platforms = list(getattr(manager, "platform_insts", []) or [])
        if not platforms:
            return False
        prefix = _single_line(umo, 240).split(":", 1)[0]
        if not prefix:
            return False
        for platform in platforms:
            try:
                meta = platform.meta()
            except Exception:
                continue
            instance_id = {
                _single_line(getattr(meta, "id", ""), 80),
                _single_line(getattr(meta, "name", ""), 80),
            }
            if prefix not in instance_id:
                continue
            status = getattr(platform, "status", None)
            status_text = _single_line(
                getattr(status, "name", "") or getattr(status, "value", "") or status,
                40,
            ).lower()
            if status_text and "running" not in status_text and any(
                token in status_text for token in ("stop", "disabled", "closed", "error", "failed")
            ):
                return False
            return True
        return False

    def _private_delivery_umo_for_user_id(self, user_id: str) -> str:
        candidates = self._private_delivery_umo_candidates(user_id)
        return candidates[0] if candidates else ""

    def _private_delivery_route_status(
        self,
        user_id: str,
        user: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a read-only explanation of the active private delivery route."""
        canonical = self._canonical_private_user_id(str(user_id or "").strip())
        if not isinstance(user, dict):
            users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
            user = users.get(canonical) if isinstance(users, dict) and isinstance(users.get(canonical), dict) else {}
        selected = self._private_delivery_umo_for_user_id(canonical)
        bound = _single_line(user.get("bound_delivery_umo"), 240)
        explicit = self._private_delivery_alias_target(canonical)
        routes = user.get("private_delivery_routes") if isinstance(user.get("private_delivery_routes"), dict) else {}
        selected_item = routes.get(selected) if isinstance(routes.get(selected), dict) else {}
        success_at = _safe_float(selected_item.get("last_success_at"), 0)
        seen_at = _safe_float(selected_item.get("last_seen_at"), 0)
        failure_at = _safe_float(selected_item.get("last_failure_at"), 0)
        failure_count = _safe_int(selected_item.get("failure_count"), 0, 0)
        selected_recovered = max(success_at, seen_at) > failure_at

        source = "fallback"
        source_label = "平台兜底"
        if bound and selected == bound:
            source = "bound"
            source_label = "用户在当前私聊绑定"
        elif explicit and ":FriendMessage:" in explicit and selected == explicit:
            source = "explicit"
            source_label = "管理员指定完整会话"
        elif success_at > 0 and (failure_count <= 0 or selected_recovered):
            source = "success"
            source_label = "最近发送成功会话"
        elif (
            selected
            and (
                selected == _single_line(user.get("last_inbound_umo"), 240)
                or seen_at > 0
            )
            and (failure_count <= 0 or selected_recovered)
        ):
            source = "inbound"
            source_label = "最近实际入站会话"
        elif selected and selected in {
            _single_line(user.get("preferred_delivery_umo"), 240),
            _single_line(user.get("last_proactive_delivery_umo"), 240),
            _single_line(user.get("umo"), 240),
        }:
            source = "stored"
            source_label = "用户已保存会话"
        elif explicit:
            source = "mapped_id"
            source_label = "管理员指定目标 ID（平台兜底）"

        recent_failure_umo = ""
        recent_failure_item: dict[str, Any] = {}
        recent_failure_at = 0.0
        verified_count = 0
        for route_umo, raw in routes.items():
            if not isinstance(raw, dict):
                continue
            if _safe_float(raw.get("last_success_at"), 0) > 0 or _safe_float(raw.get("last_seen_at"), 0) > 0:
                verified_count += 1
            route_failure_at = _safe_float(raw.get("last_failure_at"), 0)
            if route_failure_at > recent_failure_at:
                recent_failure_at = route_failure_at
                recent_failure_umo = _single_line(route_umo, 240)
                recent_failure_item = raw
        recent_recovered_at = max(
            _safe_float(recent_failure_item.get("last_success_at"), 0),
            _safe_float(recent_failure_item.get("last_seen_at"), 0),
        )
        return {
            "umo": selected,
            "source": source,
            "source_label": source_label,
            "route_count": len(routes),
            "verified_route_count": verified_count,
            "explicit_target": explicit,
            "bound_umo": bound,
            "recent_error": _single_line(recent_failure_item.get("last_error"), 240),
            "recent_error_at": recent_failure_at,
            "recent_error_umo": recent_failure_umo,
            "recent_error_recovered": bool(recent_failure_at and recent_recovered_at > recent_failure_at),
        }

    def _bind_private_delivery_umo(
        self,
        user_id: str,
        user: dict[str, Any] | None,
        umo: str,
    ) -> tuple[bool, str]:
        if not isinstance(user, dict):
            return False, "当前用户资料不可用，暂时无法绑定主动消息会话。"
        clean_umo = _single_line(umo, 240)
        if not clean_umo or ":FriendMessage:" not in clean_umo or not self._private_umo_session_id(clean_umo):
            return False, "只能在需要接收主动消息的私聊窗口执行绑定。"
        self._note_private_user_umo(user_id, user, clean_umo)
        user["bound_delivery_umo"] = clean_umo
        user["preferred_delivery_umo"] = clean_umo
        user["umo"] = clean_umo
        return True, (
            "已绑定当前私聊为主动消息接收窗口。\n"
            f"会话：{clean_umo}\n"
            "现在可以打开陪伴面板的配置引导，刷新绑定状态后继续配置。"
        )

    def _unbind_private_delivery_umo(self, user: dict[str, Any] | None) -> tuple[bool, str]:
        if not isinstance(user, dict):
            return False, "当前用户资料不可用，暂时无法解绑。"
        bound = _single_line(user.pop("bound_delivery_umo", ""), 240)
        if not bound:
            return False, "当前没有人工绑定的主动消息会话，插件会继续自动选择可用路线。"
        if _single_line(user.get("preferred_delivery_umo"), 240) == bound:
            user.pop("preferred_delivery_umo", None)
        return True, "已取消人工绑定，之后会根据最近入站和发送结果自动选择可用会话。"

    def _format_private_delivery_binding_status(self, user_id: str, user: dict[str, Any] | None) -> str:
        route = self._private_delivery_route_status(user_id, user)
        selected = _single_line(route.get("umo"), 240) or "尚未形成可投递会话"
        bound = _single_line(route.get("bound_umo"), 240)
        binding_label = "自动选择"
        if bound:
            binding_label = "已绑定当前私聊" if route.get("source") == "bound" else "已绑定，但当前会话不可用"
        lines = [
            f"绑定状态：{binding_label}",
            f"当前会话：{selected}",
            f"路线来源：{_single_line(route.get('source_label'), 80) or '平台兜底'}",
        ]
        recent_error = _single_line(route.get("recent_error"), 200)
        if recent_error:
            recovered = "（已恢复）" if route.get("recent_error_recovered") else ""
            lines.append(f"最近错误{recovered}：{recent_error}")
        return "\n".join(lines)

    def _private_umo_matches_user_id(self, umo: str, user_id: str) -> bool:
        clean_umo = _single_line(umo, 180)
        clean_user_id = str(user_id or "").strip()
        if not clean_umo or not clean_user_id:
            return False
        if f":FriendMessage:{clean_user_id}" not in clean_umo:
            return False
        parser = getattr(self, "_parse_message_session", None)
        if callable(parser):
            try:
                return parser(clean_umo) is not None
            except Exception:
                return False
        return True

    def _note_private_user_umo(self, user_id: str, user: dict[str, Any] | None, umo: str) -> None:
        if not isinstance(user, dict):
            return
        clean_umo = _single_line(umo, 180)
        if not clean_umo:
            return
        user_id = self._canonical_private_user_id(str(user_id or user.get("user_id") or "").strip())
        user["last_inbound_umo"] = clean_umo
        self._remember_private_delivery_route(user, clean_umo, outcome="observed")
        delivery_id = self._private_delivery_user_id_for(user_id)
        inbound_session_id = self._private_umo_session_id(clean_umo)
        if delivery_id and delivery_id != user_id:
            if inbound_session_id == delivery_id:
                user["umo"] = clean_umo
                return
            delivery_umo = self._private_delivery_umo_for_user_id(user_id)
            if delivery_umo:
                user["umo"] = delivery_umo
            return
        if self._private_umo_matches_user_id(clean_umo, user_id):
            user["umo"] = clean_umo

    def _note_private_delivery_success(self, user_id: str, user: dict[str, Any] | None, umo: str) -> None:
        if not isinstance(user, dict):
            return
        self._remember_private_delivery_route(user, umo, outcome="success")
        delivery_id = self._private_delivery_user_id_for(user_id)
        if delivery_id and self._private_umo_matches_user_id(umo, delivery_id):
            user["umo"] = _single_line(umo, 240)

    def _note_private_delivery_failure(
        self,
        user_id: str,
        user: dict[str, Any] | None,
        umo: str,
        error: str = "",
    ) -> None:
        if not isinstance(user, dict):
            return
        self._remember_private_delivery_route(user, umo, outcome="failure", error=error)
        preferred = self._private_delivery_umo_for_user_id(user_id)
        if preferred and preferred != _single_line(umo, 240) and self._private_delivery_umo_is_verified(user_id, user, preferred):
            user["umo"] = preferred

    def _ensure_private_user_umo(self, user_id: str, user: dict[str, Any] | None) -> bool:
        if not isinstance(user, dict):
            return False
        user_id = str(user_id or user.get("user_id") or "").strip()
        fallback = self._private_delivery_umo_for_user_id(user_id)
        if not fallback:
            return False
        current = _single_line(user.get("umo"), 180)
        delivery_id = self._private_delivery_user_id_for(user_id)
        canonical_id = self._canonical_private_user_id(user_id)
        if (
            current
            and fallback != current
            and self._private_delivery_umo_is_verified(canonical_id, user, fallback)
        ):
            user["umo"] = fallback
            return True
        if delivery_id and delivery_id != canonical_id:
            expected_suffix = f":FriendMessage:{delivery_id}"
            if not current.endswith(expected_suffix):
                user["umo"] = fallback
                return True
        else:
            last_inbound_umo = _single_line(user.get("last_inbound_umo"), 180)
            if (
                last_inbound_umo
                and last_inbound_umo != current
                and self._private_umo_matches_user_id(last_inbound_umo, canonical_id)
            ):
                user["umo"] = last_inbound_umo
                return True
        if not current:
            user["umo"] = fallback
            return True
        parser = getattr(self, "_parse_message_session", None)
        if callable(parser):
            try:
                if parser(current) is None:
                    user["umo"] = fallback
                    return True
            except Exception:
                user["umo"] = fallback
                return True
        return False

    def _private_user_role(self, user: dict[str, Any] | None, user_id: str = "") -> str:
        if not isinstance(user, dict):
            return "friend"
        role_getter = getattr(self, "_ensure_private_user_role", None)
        if callable(role_getter):
            try:
                return role_getter(str(user_id or user.get("user_id") or ""), user)
            except Exception:
                pass
        normalizer = getattr(self, "_normalize_private_user_role", None)
        role = normalizer(user.get("relationship_role")) if callable(normalizer) else str(user.get("relationship_role") or "")
        return role if role in {"owner", "friend"} else "friend"

    def _user_profile_override_int(self, user: dict[str, Any], key: str) -> int | None:
        if not isinstance(user, dict):
            return None
        raw = user.get(key)
        if raw in (None, ""):
            return None
        value = _safe_int(raw, -1, -1)
        return value if value >= 0 else None

    def _effective_user_daily_limit(self, user: dict[str, Any]) -> int:
        override = self._user_profile_override_int(user, "proactive_daily_limit")
        max_daily_messages = self._runtime_max_daily_messages()
        if max_daily_messages <= 0 or override == 0:
            return 0
        user_limit = (
            min(self._PROACTIVE_DAILY_QUOTA_MAX, max_daily_messages)
            if override is None
            else min(self._PROACTIVE_USER_DAILY_QUOTA_MAX, max(0, override))
        )
        if not bool(_proactive_setting_value(self, "enable_custom_relationship_stage_policy", False)):
            return max(0, user_limit)
        view_getter = getattr(self, "_req041_relationship_snapshot_view", None)
        relationship_user = (
            view_getter(user, source="proactive_daily_limit") if callable(view_getter) else user
        )
        role = self._private_user_role(relationship_user)
        mode = str(relationship_user.get("relationship_mode") or "normal")
        violation = user.get("relationship_violation")
        recovery_settler = getattr(self, "_settle_relationship_violation_recovery", None)
        if isinstance(violation, dict) and callable(recovery_settler):
            recovery_settler(user, now=_now_ts())
            violation = user.get("relationship_violation")
        if str(role).strip().lower() != "owner" and isinstance(violation, dict) and _safe_int(violation.get("unrecovered_points"), 0, 0, 12) > 0:
            return 0
        relationship_is_distant = False
        if not (role == "owner" and mode == "owner_exclusive"):
            policy = (
                _proactive_setting_value(self, "relationship_stage_policy", None)
                if bool(_proactive_setting_value(self, "enable_custom_relationship_stage_policy", False))
                else None
            )
            stage = relationship_stage_for_score(relationship_user.get("relationship_score", 0), policy).get("phase", {})
            relationship_is_distant = _safe_int(relationship_user.get("relationship_score"), 0, -1200, 1200) < 0 or str(
                stage.get("key") or ""
            ) in {"deeply_distant", "strongly_distant", "distant"}
        if relationship_is_distant:
            return 0
        interaction = current_interaction_projection(
            user.get("current_interaction"),
            relationship_role=role,
            relationship_mode=mode,
            relationship_score=relationship_user.get("relationship_score"),
            normal_interaction_band_cap=_proactive_setting_value(self, "normal_interaction_band_cap", "warm"),
            now=_now_ts(),
        )
        if str(interaction.get("expression_band") or "relaxed") in {"avoidant", "hurt"}:
            dynamic_limit = 0
        else:
            # 未回应只逐步放大主动间隔；不要把软降频误当成每日硬额度。
            dynamic_limit = user_limit
        return max(0, min(user_limit, dynamic_limit))

    def _relationship_proactive_soft_target(self, user: dict[str, Any]) -> int:
        if not bool(_proactive_setting_value(self, "enable_custom_relationship_stage_policy", False)):
            return max(1, _safe_int(_proactive_setting_value(self, "max_daily_messages", 1), 1, 0, 30))
        view_getter = getattr(self, "_req041_relationship_snapshot_view", None)
        relationship_user = (
            view_getter(user, source="proactive_soft_target") if callable(view_getter) else user
        )
        role = self._private_user_role(relationship_user)
        mode = str(relationship_user.get("relationship_mode") or "normal")
        if role == "owner" and mode == "owner_exclusive":
            return max(1, _safe_int(_proactive_setting_value(self, "owner_exclusive_proactive_limit", 6), 6, 0, 30))
        violation = user.get("relationship_violation")
        recovery_settler = getattr(self, "_settle_relationship_violation_recovery", None)
        if isinstance(violation, dict) and callable(recovery_settler):
            recovery_settler(user, now=_now_ts())
            violation = user.get("relationship_violation")
        if isinstance(violation, dict) and _safe_int(violation.get("unrecovered_points"), 0, 0, 12) > 0:
            return 0
        policy = (
            _proactive_setting_value(self, "relationship_stage_policy", None)
            if bool(_proactive_setting_value(self, "enable_custom_relationship_stage_policy", False))
            else None
        )
        stage = relationship_stage_for_score(relationship_user.get("relationship_score", 0), policy).get("phase", {})
        if _safe_int(relationship_user.get("relationship_score"), 0, -1200, 1200) < 0 or str(stage.get("key") or "") in {
            "deeply_distant",
            "strongly_distant",
            "distant",
        }:
            return 0
        return max(1, _safe_int(stage.get("proactive_care_limit"), 1, 0, 30))

    def _runtime_max_daily_messages(self) -> int:
        runtime_value = _safe_int(
            _proactive_setting_value(self, "max_daily_messages", 8),
            8,
            0,
            self._PROACTIVE_DAILY_QUOTA_MAX,
        )
        # Legacy lightweight integrations may update only ``config``.  Keep
        # that live read when no persona resolver exists, without writing back
        # to the shared runtime attribute.
        if not callable(getattr(self, "persona_setting", None)):
            config = getattr(self, "config", None)
            getter = getattr(config, "get", None)
            if callable(getter):
                try:
                    runtime_value = _safe_int(
                        getter("max_daily_messages", runtime_value),
                        runtime_value,
                        0,
                        self._PROACTIVE_DAILY_QUOTA_MAX,
                    )
                except Exception:
                    pass
        if runtime_value <= 0:
            return 0
        effective_value = self._effective_proactive_int(
            "max_daily_messages",
            runtime_value,
            minimum=0,
            maximum=self._PROACTIVE_DAILY_QUOTA_MAX,
        )
        if effective_value <= 0:
            return 0
        return min(self._PROACTIVE_DAILY_QUOTA_MAX, effective_value)

    def _proactive_generation_disabled(self, user: dict[str, Any] | None = None) -> bool:
        if self._runtime_max_daily_messages() <= 0:
            return True
        if isinstance(user, dict) and self._user_profile_override_int(user, "proactive_daily_limit") == 0:
            return True
        return False

    def _suspend_user_proactive_generation(self, user: dict[str, Any]) -> bool:
        if not isinstance(user, dict):
            return False
        tracked = (
            "next_proactive_at",
            "planned_proactive_reason",
            "planned_proactive_action",
            "planned_proactive_source",
            "planned_proactive_kind",
            "planned_proactive_route_version",
            "planned_proactive_route_dedupe_key",
            "planned_proactive_route_review_profile",
            "planned_proactive_route_retry_profile",
            "planned_proactive_route_cancel_if_new_inbound",
            "planned_proactive_route_recent_chat_policy",
            "planned_proactive_route_allow_automatic_followup",
            "planned_proactive_route_disable_segmenting",
            "planned_proactive_response_expectation",
            "planned_proactive_origin_event_id",
            "planned_proactive_motive",
            "planned_proactive_topic",
            "planned_candidate_id",
            "proactive_impulses",
            "pending_followup_event",
            "suspended_proactive",
            "pending_proactive_send_retry",
            "proactive_sending",
        )
        before = {key: deepcopy(user.get(key)) for key in tracked}
        self._clear_pending_proactive_plan(user)
        user["proactive_impulses"] = []
        user["pending_followup_event"] = {}
        user["suspended_proactive"] = {}
        user["pending_proactive_send_retry"] = {}
        user["proactive_sending"] = False
        user["proactive_sending_started_at"] = 0
        return any(before.get(key) != user.get(key) for key in tracked)

    def _format_daily_limit_disabled_reason(self, user: dict[str, Any]) -> str:
        override = user.get("proactive_daily_limit", -1) if isinstance(user, dict) else -1
        runtime_value = _safe_int(
            _proactive_setting_value(self, "max_daily_messages", 0),
            0,
            0,
            self._PROACTIVE_DAILY_QUOTA_MAX,
        )
        config_value = runtime_value
        if not callable(getattr(self, "persona_setting", None)):
            config = getattr(self, "config", None)
            getter = getattr(config, "get", None)
            if callable(getter):
                try:
                    config_value = _safe_int(getter("max_daily_messages", runtime_value), runtime_value, 0, self._PROACTIVE_DAILY_QUOTA_MAX)
                except Exception:
                    config_value = runtime_value
        return f"每日上限为 0（用户覆盖={override}，运行中全局={runtime_value}，配置全局={config_value}）"

    def _effective_user_idle_minutes(self, user: dict[str, Any]) -> int:
        override = self._user_profile_override_int(user, "proactive_idle_minutes")
        if override is not None:
            return override
        base_idle = self._effective_proactive_int(
            "idle_minutes",
            _safe_int(_proactive_setting_value(self, "idle_minutes", 60), 60, 0, 1440),
            minimum=0,
            maximum=1440,
        )
        if self._private_user_role(user) == "friend":
            friend_floor = self._effective_proactive_int(
                "friend_idle_floor_minutes",
                0,
                minimum=0,
                maximum=1440,
            )
            base_idle = max(base_idle, friend_floor)
        tier_cap = _safe_int(self._proactive_quota_policy(user).get("idle_cap_minutes"), base_idle, 0, 1440)
        return max(0, min(base_idle, tier_cap)) if tier_cap > 0 else max(0, base_idle)

    def _effective_user_greeting_idle_minutes(self, user: dict[str, Any]) -> int:
        greeting_idle = _safe_int(
            _proactive_setting_value(self, "greeting_idle_minutes", 30),
            30,
            0,
            240,
        )
        if self._private_user_role(user) == "friend":
            friend_floor = self._effective_proactive_int(
                "friend_idle_floor_minutes",
                0,
                minimum=0,
                maximum=1440,
            )
            return max(greeting_idle, min(60, friend_floor))
        return max(0, greeting_idle)

    def _effective_user_min_interval_minutes(self, user: dict[str, Any]) -> int:
        override = self._user_profile_override_int(user, "proactive_min_interval_minutes")
        if override is not None:
            return override
        base_interval = self._effective_proactive_int(
            "min_interval_minutes",
            _safe_int(_proactive_setting_value(self, "min_interval_minutes", 120), 120, 0, 2880),
            minimum=0,
            maximum=2880,
        )
        if self._private_user_role(user) == "friend":
            friend_floor = self._effective_proactive_int(
                "friend_min_interval_floor_minutes",
                0,
                minimum=0,
                maximum=2880,
            )
            base_interval = max(base_interval, friend_floor)
        tier_cap = _safe_int(
            self._proactive_quota_policy(user).get("interval_cap_minutes"),
            base_interval,
            0,
            2880,
        )
        return max(0, min(base_interval, tier_cap)) if tier_cap > 0 else max(0, base_interval)

    def _effective_user_photo_daily_limit(self, user: dict[str, Any] | None = None) -> int:
        if isinstance(user, dict):
            if self._private_user_role(user) == "friend":
                return 0
            override = self._user_profile_override_int(user, "photo_daily_limit")
            if override is not None:
                return override
        return max(0, _safe_int(_proactive_setting_value(self, "photo_action_max_daily", 0), 0, 0))

    def _effective_user_screen_peek_daily_limit(self, user: dict[str, Any] | None = None) -> int:
        if isinstance(user, dict):
            if self._private_user_role(user) == "friend":
                return 0
            override = self._user_profile_override_int(user, "screen_peek_daily_limit")
            if override is not None:
                return override
        return max(0, _safe_int(_proactive_setting_value(self, "screen_peek_max_daily", 0), 0, 0))

    def _effective_user_poke_daily_limit(self, user: dict[str, Any] | None = None) -> int:
        if isinstance(user, dict):
            override = self._user_profile_override_int(user, "poke_daily_limit")
            if override is not None:
                return override
            if self._private_user_role(user) == "friend":
                return 0
        return max(0, _safe_int(_proactive_setting_value(self, "poke_action_max_times", 0), 0, 0))

    def _format_private_user_boundary_hint(self, user: dict[str, Any]) -> str:
        role = self._private_user_role(user)
        labeler = getattr(self, "_private_user_role_label", None)
        label = labeler(role) if callable(labeler) else ("主要用户" if role == "owner" else "次要用户")
        note = _single_line(user.get("proactive_boundary_note"), 180)
        if role == "owner":
            text = (
                "【当前私聊关系角色】\n"
                f"- 当前用户角色：{label}。\n"
                "- 可以延续人格中对主要用户的亲近、依赖和日常陪伴动机，但仍要尊重用户休息、忙碌和拒绝信号。"
            )
        else:
            text = (
                "【当前私聊关系角色】\n"
                f"- 当前用户角色：{label}。\n"
                "- 对方不是主要用户/恋人/专属陪伴目标。主动联系应像普通朋友：少量、具体、不过度亲密，不使用主要用户专属称呼、占有欲、撒娇索取或暧昧承诺。\n"
                "- 动机应以礼貌关心、共同话题、必要转告、轻分享为主；不要因为想贴近、想被哄、想确认对方在不在而频繁打扰。\n"
                "- 不给次要用户使用窥屏或主动生图能力；不要把主要用户或其他私聊对象的图片、生活碎片复用给次要用户。"
                "- 不对次要用户发起资料/资料归档推荐、资料归档分享、屏幕观察、群聊私下转述、私下创作分享或其他涉及隐私来源的主动。"
            )
        if note:
            text += f"\n- 用户级边界备注：{note}"
        return text

    def _friend_sensitive_proactive_reason(self, reason: Any) -> bool:
        normalized = str(reason or "").strip()
        return normalized in {
            "group_share",
            "creative_share",
            "weather_alert",
        }

    def _friend_sensitive_proactive_action(self, action: Any) -> bool:
        parts = {part.strip() for part in str(action or "").split("+") if part.strip()}
        return bool(parts & {"screen_peek", "photo_text"})

    def _friend_can_receive_proactive_reason(self, user: dict[str, Any] | None, reason: Any, action: Any = "") -> bool:
        if not isinstance(user, dict) or self._private_user_role(user) != "friend":
            return True
        return not (self._friend_sensitive_proactive_reason(reason) or self._friend_sensitive_proactive_action(action))

    def _sanitize_friend_proactive_plan_fields(
        self,
        user: dict[str, Any] | None,
        *,
        reason: str = "",
        action: str = "message",
        topic: str = "",
        motive: str = "",
    ) -> dict[str, str]:
        normalized_action = str(action or "message").strip() or "message"
        normalized_topic = _single_line(topic, 80)
        normalized_motive = self._normalize_internal_motive_text(_single_line(motive, 180))
        if not isinstance(user, dict) or self._private_user_role(user) != "friend":
            return {
                "reason": str(reason or "check_in"),
                "action": normalized_action,
                "topic": normalized_topic,
                "motive": normalized_motive,
            }
        normalized_reason = str(reason or "check_in")
        unanswered_level = self._friend_unanswered_downgrade_level(user)
        if unanswered_level >= 1 and self._friend_unanswered_should_remove_action(normalized_action):
            normalized_action = "message"
        if self._friend_sensitive_proactive_action(normalized_action):
            normalized_action = "message"
        sensitive_markers = (
            "screen_peek", "窥屏", "屏幕", "识屏", "偷看", "偷偷看", "瞄一眼", "看一眼",
            "观察你", "看你在忙", "看看你在干嘛", "看你在干嘛",
        )
        combined = f"{normalized_topic} {normalized_motive}"
        has_sensitive_action_text = any(token in combined for token in sensitive_markers)
        has_friend_interaction_text = self._friend_plan_has_private_interaction_text(combined)
        unanswered_patch = self._friend_unanswered_plan_patch(
            user,
            level=unanswered_level,
            reason=normalized_reason,
            action=normalized_action,
            topic=normalized_topic,
            motive=normalized_motive,
        )
        if unanswered_patch:
            normalized_reason = unanswered_patch["reason"]
            normalized_action = unanswered_patch["action"]
            normalized_topic = unanswered_patch["topic"]
            normalized_motive = unanswered_patch["motive"]
            combined = f"{normalized_topic} {normalized_motive}"
            has_sensitive_action_text = any(token in combined for token in sensitive_markers)
            has_friend_interaction_text = self._friend_plan_has_private_interaction_text(combined)
        if not has_sensitive_action_text and not has_friend_interaction_text:
            return {
                "reason": normalized_reason,
                "action": normalized_action,
                "topic": normalized_topic,
                "motive": normalized_motive,
            }
        if has_friend_interaction_text:
            if not normalized_topic or self._friend_plan_has_private_interaction_text(normalized_topic):
                normalized_topic = "顺手分享一点日常近况"
            normalized_motive = (
                "作为普通朋友轻轻分享一个不指向第三方私聊互动的小片段,不要求立刻回复"
                if str(reason or "") in {"", "check_in", "quiet_care", "state_share", "activity_share"}
                else "按次要用户关系做一次克制的普通文字分享,不写成和次要用户聊天或约见"
            )
            return {
                "reason": normalized_reason,
                "action": normalized_action,
                "topic": normalized_topic,
                "motive": normalized_motive,
            }
        topic_replacements = {
            "空档偷看一眼": "空档问一句",
            "偷看一眼": "问一句近况",
            "你这会儿在干嘛": "问一句近况",
        }
        for old, new in topic_replacements.items():
            normalized_topic = normalized_topic.replace(old, new)
        if not normalized_topic or any(token in normalized_topic for token in sensitive_markers):
            normalized_topic = "问一句近况"
        normalized_motive = (
            "作为次要用户关系想起对方可能正忙,只轻轻问一句,不要求立刻回复"
            if normalized_reason in {"", "check_in", "quiet_care", "state_share"}
            else "按次要用户关系顺手补一句,只做普通文字关心,不涉及屏幕观察"
        )
        return {
            "reason": normalized_reason,
            "action": normalized_action,
            "topic": normalized_topic,
            "motive": normalized_motive,
        }

    def _friend_unanswered_downgrade_level(self, user: dict[str, Any] | None, *, now: float | None = None) -> int:
        if not isinstance(user, dict) or self._private_user_role(user) != "friend":
            return 0
        level = 0
        ignored = _safe_int(user.get("ignored_streak"), 0, 0, 20)
        if ignored >= 3:
            level = 3
        elif ignored >= 2:
            level = 2
        elif ignored >= 1:
            level = 1
        check_now = _now_ts() if now is None else now
        awaiting_since = _safe_float(user.get("awaiting_reply_since"), 0)
        if awaiting_since > 0:
            hours = max(0.0, (check_now - awaiting_since) / 3600.0)
            if hours >= 24:
                level = max(level, 3)
            elif hours >= 10:
                level = max(level, 2)
            elif hours >= 4:
                level = max(level, 1)
        return level

    def _friend_unanswered_silence_reason(self, user: dict[str, Any] | None, *, now: float | None = None) -> str:
        if not isinstance(user, dict) or self._private_user_role(user) != "friend":
            return ""
        # 未回应只降低频率并把内容收敛为低压文字，不再把已授权用户永久停发。
        # 明确拒绝、休息和关系边界仍由统一互动/休息闸门处理。
        ignored = _safe_int(user.get("ignored_streak"), 0, 0, 20)
        check_now = _now_ts() if now is None else now
        awaiting_since = _safe_float(user.get("awaiting_reply_since"), 0)
        unanswered_hours = (check_now - awaiting_since) / 3600.0 if awaiting_since > 0 else 0.0
        if ignored >= 2 or unanswered_hours >= 24:
            user["friend_unanswered_silence_note"] = (
                f"次要用户连续 {ignored} 次未回应"
                + (f"，已等待约 {unanswered_hours:.1f} 小时" if unanswered_hours > 0 else "")
                + "；继续使用低压文字并渐进延长间隔，不自动停发"
            )
        else:
            user["friend_unanswered_silence_note"] = ""
        user["friend_unanswered_silenced_since"] = 0
        return ""

    def _block_friend_unanswered_pending_proactive(
        self,
        user: dict[str, Any],
        *,
        note: str,
        now: float | None = None,
    ) -> None:
        if not isinstance(user, dict) or not note:
            return
        check_now = _now_ts() if now is None else now
        safe_note = _single_line(note, 160)
        impulse_cleaner = getattr(self, "_cleanup_proactive_impulses", None)
        if callable(impulse_cleaner):
            try:
                for impulse in impulse_cleaner(user, now=check_now):
                    if not isinstance(impulse, dict):
                        continue
                    state = _single_line(impulse.get("state") or "queued", 24).lower()
                    source = _single_line(impulse.get("source"), 40)
                    if state not in {"queued", "deferred", "pending", ""} or source in {"timer", "troubleshooting", "simulation"}:
                        continue
                    impulse["state"] = "blocked"
                    impulse["last_status"] = "blocked"
                    impulse["last_note"] = safe_note
                    impulse["updated_ts"] = check_now
            except Exception as exc:
                logger.debug("[PrivateCompanion] 清理次要用户未回应主动念头失败: %s", _single_line(exc, 120))
        pool_cleaner = getattr(self, "_cleanup_proactive_candidate_pool", None)
        pending_checker = getattr(self, "_pending_candidate_status", None)
        candidate_user_getter = getattr(self, "_candidate_user_id", None)
        user_id = _single_line(user.get("user_id") or user.get("id"), 40)
        if callable(pool_cleaner) and callable(pending_checker) and callable(candidate_user_getter) and user_id:
            try:
                for candidate in pool_cleaner(now=check_now):
                    if not isinstance(candidate, dict):
                        continue
                    if candidate_user_getter(candidate) != user_id:
                        continue
                    source = _single_line(candidate.get("source"), 40)
                    if source in {"timer", "troubleshooting", "simulation"}:
                        continue
                    if not pending_checker(_single_line(candidate.get("status"), 24).lower()):
                        continue
                    candidate["status"] = "blocked"
                    candidate["note"] = safe_note
                    candidate["updated_ts"] = check_now
            except Exception as exc:
                logger.debug("[PrivateCompanion] 清理次要用户未回应主动候选失败: %s", _single_line(exc, 120))

    @staticmethod
    def _friend_unanswered_should_remove_action(action: str) -> bool:
        parts = {part.strip() for part in str(action or "").split("+") if part.strip()}
        return bool(parts & {"poke", "voice", "photo_text", "screen_peek"})

    def _friend_unanswered_plan_patch(
        self,
        user: dict[str, Any],
        *,
        level: int,
        reason: str,
        action: str,
        topic: str,
        motive: str,
    ) -> dict[str, str]:
        if level <= 0:
            return {}
        high_pressure_reasons = {
            "check_in",
            "quiet_care",
            "state_share",
            "activity_share",
            "background_schedule",
            "diary_share",
            "morning_greeting",
            "noon_greeting",
            "evening_greeting",
            "habit_awareness",
        }
        normalized_reason = str(reason or "check_in")
        normalized_action = "message" if self._friend_unanswered_should_remove_action(action) else (str(action or "message") or "message")
        if level == 1:
            if normalized_reason in high_pressure_reasons:
                normalized_reason = "quiet_care" if normalized_reason in {"check_in", "state_share", "habit_awareness"} else normalized_reason
            return {
                "reason": normalized_reason,
                "action": normalized_action,
                "topic": _single_line(topic, 80) or "轻一点的近况",
                "motive": "对方还没接话，放轻；不催不追问",
            }
        if level == 2:
            return {
                "reason": "quiet_care",
                "action": "message",
                "topic": "低压近况",
                "motive": "对方有一阵没回应了，低压；不连问",
            }
        return {
            "reason": "quiet_care",
            "action": "message",
            "topic": "留出空间",
            "motive": "连续没回应，退一步；留空间",
        }

    @staticmethod
    def _friend_plan_has_private_interaction_text(text: Any) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        patterns = (
            r"给.{0,16}(?:回了?消息|发了?消息|回信|回复了?|发私聊)",
            r"(?:回了?消息|发了?消息|回信|发私聊|私聊|聊天|互相吐槽|互相安慰)",
            r"(?:约饭|夜宵|见面|出门|一起(?:做|看|聊|吃|去|玩|散步|上课|写|打))",
            r"(?:朋友用户|朋友边界|朋友那边|朋友私聊|次要用户|次要用户边界|次要用户那边|次要用户私聊)",
        )
        return any(re.search(pattern, cleaned) for pattern in patterns)

    def _sync_configured_targets(self):
        for user_id in self._configured_target_ids():
            user = self._get_user(user_id)
            capabilities = user.get("unified_profile_capabilities")
            needs_initial_route = not isinstance(capabilities, dict)
            migrator = getattr(self, "_req036_migrate_configured_target_capability", None)
            migrated = bool(migrator(user_id, user)) if callable(migrator) else False
            capabilities = user.get("unified_profile_capabilities")
            if not isinstance(capabilities, dict) and not migrated:
                # ``target_user_ids`` is an administrator-managed legacy
                # permission source, not an inbound-DM signal.  Convert it
                # once when materializing a target record; future syncs only
                # read the frozen capability state and cannot reopen a user.
                req036_update_capabilities(
                    user,
                    {
                        "private_companion_enabled": True,
                        "proactive_private_enabled": _safe_int(user.get("proactive_daily_limit"), 0, 0) > 0,
                    },
                    actor_authorized=True,
                    grant_source="legacy_configured_target_migration",
                    actor_id="startup_migration",
                    target_identity=user_id,
                    reason_code="legacy_configured_target_migration",
                )
            capability = req036_capability_summary(user)
            user["enabled"] = True
            user["target_user"] = True
            user.setdefault("nickname", _proactive_setting_value(self, "default_nickname", "小星"))
            if needs_initial_route or migrated or capability.get("proactive_private_enabled"):
                self._ensure_private_user_umo(user_id, user)
            if self._user_enabled_for_proactive(user_id, user) and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=_now_ts())

    def _prime_enabled_user_schedules(self) -> bool:
        users = self.data.get("users", {})
        if not isinstance(users, dict):
            return False
        changed = False
        now = _now_ts()
        for raw_user in users.values():
            if not isinstance(raw_user, dict):
                continue
            raw_user_id = str(raw_user.get("user_id") or "")
            if not self._user_enabled_for_proactive(raw_user_id, raw_user):
                raw_user["enabled"] = True
                self._clear_pending_proactive_plan(raw_user)
                changed = True
                continue
            if self._ensure_private_user_umo(raw_user_id, raw_user):
                changed = True
            if not raw_user.get("umo"):
                continue
            if _safe_float(raw_user.get("next_proactive_at"), 0) > 0:
                if self._promote_earlier_daily_greeting_event(raw_user, now=now):
                    changed = True
                continue
            self._schedule_next_proactive(raw_user, now=now)
            changed = True
        return changed

    def _quiet_hours_end_timestamp(self, at_ts: float | None = None) -> float:
        quiet_hours = _proactive_setting_value(self, "quiet_hours", "23:00-08:30")
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", str(quiet_hours or ""))
        if not match:
            return 0.0
        sh, sm, eh, em = [int(part) for part in match.groups()]
        if not (0 <= sh <= 23 and 0 <= eh <= 23 and 0 <= sm <= 59 and 0 <= em <= 59):
            return 0.0
        start = sh * 60 + sm
        end = eh * 60 + em
        check_ts = _now_ts() if at_ts is None else float(at_ts)
        converter = getattr(self, "_environment_fromtimestamp", None)
        now = converter(check_ts) if callable(converter) else datetime.fromtimestamp(check_ts)
        current = now.hour * 60 + now.minute
        if start == end:
            return (now + timedelta(days=1)).replace(hour=eh, minute=em, second=0, microsecond=0).timestamp()
        if start < end:
            if not (start <= current < end):
                return 0.0
            return now.replace(hour=eh, minute=em, second=0, microsecond=0).timestamp()
        if current >= start:
            return (now + timedelta(days=1)).replace(hour=eh, minute=em, second=0, microsecond=0).timestamp()
        if current < end:
            return now.replace(hour=eh, minute=em, second=0, microsecond=0).timestamp()
        return 0.0

    def _is_quiet_time(self) -> bool:
        return self._quiet_hours_end_timestamp() > _now_ts()

    def _reset_daily_counter_if_needed(self, user: dict[str, Any]):
        today = _today_key()
        if user.get("sent_day") != today:
            user["sent_day"] = today
            user["sent_today"] = 0
            user["proactive_daypart_day"] = today
            user["proactive_daypart_counts"] = {}
        if user.get("photo_generated_day") != today:
            user["photo_generated_day"] = today
            user["photo_generated_today"] = 0
        if user.get("screen_peek_day") != today:
            user["screen_peek_day"] = today
            user["screen_peek_today"] = 0
            user["screen_peek_last_at"] = 0
        if user.get("greeting_sent_day") != today:
            user["greeting_sent_day"] = today
            user["greetings_sent"] = []
            user["greetings_suppressed_by_inbound"] = []
            user["morning_greeting_sent_at"] = 0
            user["morning_greeting_reply_at"] = 0
        if user.get("proactive_daypart_day") != today:
            user["proactive_daypart_day"] = today
            user["proactive_daypart_counts"] = {}

    def _note_morning_greeting_reply(self, user: dict[str, Any], *, now: float | None = None) -> bool:
        """Record the first inbound message after today's morning greeting."""
        self._reset_daily_counter_if_needed(user)
        reply_at = _now_ts() if now is None else now
        sent_at = _safe_float(user.get("morning_greeting_sent_at"), 0)
        previous_reply_at = _safe_float(user.get("morning_greeting_reply_at"), 0)
        if sent_at <= 0 or reply_at < sent_at or previous_reply_at >= sent_at:
            return False
        user["morning_greeting_reply_at"] = reply_at
        return True

    def _unanswered_slowdown_count(self, user: dict[str, Any]) -> int:
        ignored_streak = _safe_int(user.get("ignored_streak"), 0)
        start = self._effective_proactive_int(
            "unanswered_slowdown_start",
            _safe_int(_proactive_setting_value(self, "proactive_unanswered_slowdown_start", 1), 1, 1, 10),
            minimum=1,
            maximum=10,
        )
        return max(0, ignored_streak - start + 1)

    def _unanswered_interval_multiplier(self, user: dict[str, Any]) -> float:
        active_count = self._unanswered_slowdown_count(user)
        max_multiplier = self._effective_proactive_float(
            "unanswered_max_interval_multiplier",
            max(1.0, _safe_float(_proactive_setting_value(self, "proactive_unanswered_max_interval_multiplier", 2.2), 2.2, 1.0)),
            minimum=1.0,
            maximum=8.0,
        )
        raw_multiplier = min(max_multiplier, 1.0 + active_count * 0.35)
        weight = _safe_float(
            self._proactive_quota_policy(user).get("unanswered_interval_weight"),
            1.0,
            0.0,
        )
        return 1.0 + max(0.0, raw_multiplier - 1.0) * min(1.0, weight)

    def _effective_min_interval_seconds(self, user: dict[str, Any], *, kind: str = "") -> int:
        route_kind = kind or self._planned_proactive_kind(user)
        route_policy = self._proactive_kind_policy(route_kind)
        multiplier = (
            self._unanswered_interval_multiplier(user)
            * self._cycle_proactive_frequency_profile()["private_interval_multiplier"]
            * _safe_float(route_policy.get("interval_multiplier"), 1.0, 0.05)
        )
        return int(self._effective_user_min_interval_minutes(user) * 60 * multiplier)

    def _bot_proactive_drive(self, user: dict[str, Any] | None = None, *, now: float | None = None) -> dict[str, Any]:
        state = self.data.get("daily_state", {})
        if not isinstance(state, dict):
            state = {}
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias") or state.get("mood"), 24)
        note = _single_line(state.get("note"), 120)
        conditions = state.get("conditions")
        score = 0.55 + (energy - 55) / 220.0
        reasons: list[str] = [f"energy={energy}"]
        if mood in {"轻快", "兴奋", "松弛", "明亮", "活跃"}:
            score += 0.08
            reasons.append(f"心情{mood}")
        elif mood in {"安静", "疲惫", "低落", "收声", "困倦"}:
            score -= 0.09
            reasons.append(f"心情{mood}")
        if any(token in note for token in ("疲惫", "困", "低电量", "收声", "慢一点")):
            score -= 0.08
            reasons.append("状态偏收")
        if any(token in note for token in ("轻快", "有精神", "想说话", "灵感", "开心")):
            score += 0.07
            reasons.append("状态偏开")
        if isinstance(conditions, list):
            for cond in conditions[:4]:
                text = _single_line(cond.get("label") or cond.get("text") or cond.get("kind"), 40) if isinstance(cond, dict) else _single_line(cond, 40)
                if any(token in text for token in ("疲惫", "困", "安静", "低落", "身体不舒服")):
                    score -= 0.04
                elif any(token in text for token in ("兴奋", "开心", "灵感", "想分享")):
                    score += 0.04
        score = max(0.12, min(1.0, score))
        if score >= 0.72:
            label = "想开口"
        elif score <= 0.42:
            label = "想收着"
        else:
            label = "平稳"
        return {
            "score": score,
            "label": label,
            "detail": "；".join(reasons[:4]),
            "energy": energy,
            "mood": mood,
        }

    def _proactive_response_readiness(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        check_now = _now_ts() if now is None else now
        score = 0.54
        reasons: list[str] = []
        ignored_streak = _safe_int(user.get("ignored_streak"), 0, 0, 20)
        if ignored_streak:
            score -= min(0.32, ignored_streak * 0.08)
            reasons.append(f"未回应{ignored_streak}")
        last_reply_at = _safe_float(user.get("last_reply_at"), 0)
        if last_reply_at > 0:
            hours = (check_now - last_reply_at) / 3600.0
            if hours <= 6:
                score += 0.12
                reasons.append("刚有回应")
            elif hours <= 24:
                score += 0.06
                reasons.append("近一天回应过")
        awaiting_since = _safe_float(user.get("awaiting_reply_since"), 0)
        if awaiting_since > 0 and check_now - awaiting_since > 4 * 3600:
            score -= 0.08
            reasons.append("上一轮还悬着")
        score = max(0.05, min(1.0, score))
        if score >= 0.7:
            label = "温热"
        elif score <= 0.38:
            label = "偏冷"
        else:
            label = "普通"
        return {
            "score": score,
            "label": label,
            "detail": "；".join(reasons[:5]) or "回应节奏平稳",
        }

    def _relationship_proactive_temperature(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        drive: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compatibility projection derived from the unified expression decision."""
        check_now = _now_ts() if now is None else now
        drive = drive if isinstance(drive, dict) else self._bot_proactive_drive(user, now=check_now)
        response = self._proactive_response_readiness(user, now=check_now)
        readiness_score = (
            _safe_float(drive.get("score"), 0.55) * 0.48
            + _safe_float(response.get("score"), 0.55) * 0.52
        )
        quiet_hours_active = False
        quiet_end_getter = getattr(self, "_quiet_hours_end_timestamp", None)
        if callable(quiet_end_getter):
            try:
                quiet_hours_active = _safe_float(quiet_end_getter(check_now), 0) > check_now
            except Exception:
                quiet_hours_active = False
        expression_decision: dict[str, Any] = {}
        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        if callable(expression_builder):
            try:
                decision = expression_builder(
                    user,
                    proactive_candidate={
                        "eligible": True,
                        "dynamic_allowance": self._effective_user_daily_limit(user),
                        "readiness_score": int(max(0.0, min(1.0, readiness_score)) * 100),
                        "current_ts": check_now,
                    },
                    bot_state={"energy": drive.get("energy"), "mood": drive.get("mood")},
                    schedule={"quiet_hours": quiet_hours_active},
                    message_intent={"requested_content_tier": "normal"},
                    now=check_now,
                )
                expression_decision = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
            except Exception:
                expression_decision = {}
        expression_warmth = _safe_float(expression_decision.get("warmth"), 55, 0, 100) / 100.0
        score = response.get("score", 0.55) * 0.4 + expression_warmth * 0.6
        if expression_decision and _safe_int(expression_decision.get("proactive_budget"), 0, 0) <= 0:
            score = min(score, 0.2)
        score = max(0.05, min(1.0, score))
        label = "温热" if score >= 0.7 else "偏冷" if score <= 0.38 else "普通"
        reason_codes = expression_decision.get("reason_codes") if isinstance(expression_decision.get("reason_codes"), (list, tuple)) else []
        detail = "；".join(_single_line(item, 48) for item in reason_codes[:4]) or str(response.get("detail") or "统一表达平稳")
        return {
            "score": score,
            "label": label,
            "detail": detail,
            "expression_band": _single_line(expression_decision.get("expression_band"), 24) or "relaxed",
            "expression_decision": expression_decision,
            "response_readiness": response,
        }

    def _proactive_inner_readiness(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        check_now = _now_ts() if now is None else now
        drive = self._bot_proactive_drive(user, now=now)
        temperature = self._relationship_proactive_temperature(user, now=check_now, drive=drive)
        score = _safe_float(drive.get("score"), 0.55) * 0.48 + _safe_float(temperature.get("score"), 0.55) * 0.52
        expression_decision = temperature.get("expression_decision") if isinstance(temperature.get("expression_decision"), dict) else {}
        if expression_decision and _safe_int(expression_decision.get("proactive_budget"), 0, 0) <= 0:
            score = min(score, 0.2)
        motivation: dict[str, Any] = {}
        if bool(_proactive_setting_value(self, "enable_experimental_motivation_model", False)):
            motivation = self._experimental_proactive_motivation(user, now=now, drive=drive, temperature=temperature)
            modifier = (_safe_float(motivation.get("score"), 0.5) - 0.5) * 0.16
            score += modifier
        score = max(0.05, min(1.0, score))
        result = {
            "score": score,
            "label": f"{drive.get('label')}/{temperature.get('label')}",
            "detail": f"状态:{drive.get('detail')}; 关系:{temperature.get('detail')}",
            "drive": drive,
            "temperature": temperature,
        }
        if expression_decision:
            result["expression_decision"] = expression_decision
        if motivation:
            result["motivation"] = motivation
            result["detail"] = f"{result['detail']}; 动机:{motivation.get('label')} {motivation.get('detail')}"
        return result

    def _experimental_proactive_incentive(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        action = self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=40)
        source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        topic = _single_line(user.get("planned_proactive_topic"), 100)
        motive = _single_line(user.get("planned_proactive_motive"), 160)
        semantics: dict[str, Any] = {}
        semantic_getter = getattr(self, "_planned_proactive_semantics", None)
        if callable(semantic_getter):
            try:
                semantics = semantic_getter(user)
            except Exception:
                semantics = {}
        score = 0.5
        reasons: list[str] = []
        if reason in {"timer", "reminder", "pending_followup", "followup"} or source == "timer":
            score += 0.18
            reasons.append("任务/约定诱因")
        if reason in {"creative_share", "diary_share", "dream_share", "activity_share", "news_share", "web_exploration_share", "qzone_life_publish"}:
            score += 0.10
            reasons.append("有内容可分享")
        if reason in {"group_share", "atrelay_followup"}:
            score += 0.12
            reasons.append("外部互动线索")
        if reason in {"morning_greeting", "noon_greeting", "evening_greeting"}:
            sent_today = _safe_int(user.get("sent_today"), 0, 0, 100)
            last_reply_at = _safe_float(user.get("last_reply_at"), 0)
            check_now = _now_ts() if now is None else now
            if sent_today > 0 or (last_reply_at > 0 and check_now - last_reply_at <= 3 * 3600):
                score -= 0.16
                reasons.append("问候诱因已释放")
            else:
                score += 0.04
                reasons.append("时段问候")
        if reason in {"check_in", "quiet_care", ""}:
            score -= 0.03
            reasons.append("泛关心诱因较弱")
        if action in {"photo_text", "voice", "poke"}:
            score += 0.04
            reasons.append(f"{action}动作诱因")
        if self._private_user_role(user) == "friend" and action in {"photo_text", "screen_peek"}:
            score -= 0.18
            reasons.append("次要用户能力边界")
        concrete_text = f"{topic} {motive}"
        if len(re.sub(r"\s+", "", concrete_text)) >= 18 and not any(token in concrete_text for token in ("问一句近况", "打个招呼", "在不在", "忙不忙")):
            score += 0.07
            reasons.append("切口具体")
        semantic_score = _safe_float(semantics.get("score"), 0.5)
        semantic_pressure = _safe_float(semantics.get("pressure"), 0.4)
        score += (semantic_score - 0.5) * 0.10
        score -= max(0.0, semantic_pressure - 0.55) * 0.16
        ignored = _safe_int(user.get("ignored_streak"), 0, 0, 20)
        if ignored:
            score -= min(0.22, ignored * 0.055)
            reasons.append(f"未回应{ignored}")
        score = max(0.05, min(1.0, score))
        label = "诱因强" if score >= 0.68 else "诱因弱" if score <= 0.38 else "诱因普通"
        return {"score": score, "label": label, "detail": "；".join(reasons[:5]) or "无明显外部诱因"}

    def _experimental_proactive_arousal(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        state = self.data.get("daily_state", {})
        if not isinstance(state, dict):
            state = {}
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias") or state.get("mood"), 24)
        note = _single_line(state.get("note"), 160)
        arousal = 0.38 + energy / 180.0
        reasons = [f"energy={energy}"]
        if mood in {"兴奋", "轻快", "活跃", "明亮"}:
            arousal += 0.12
            reasons.append(f"心情{mood}")
        elif mood in {"疲惫", "困倦", "低落", "收声", "安静"}:
            arousal -= 0.12
            reasons.append(f"心情{mood}")
        if any(token in note for token in ("高压", "赶", "急", "兴奋", "停不下来")):
            arousal += 0.08
            reasons.append("状态偏高")
        if any(token in note for token in ("困", "疲惫", "低电量", "慢一点", "收声")):
            arousal -= 0.08
            reasons.append("状态偏低")
        ignored = _safe_int(user.get("ignored_streak"), 0, 0, 20)
        if ignored >= 2:
            arousal -= 0.06
            reasons.append("未回应降唤醒")
        arousal = max(0.0, min(1.0, arousal))
        fit = max(0.0, 1.0 - abs(arousal - 0.55) * 1.45)
        if arousal >= 0.78:
            label = "唤醒偏高"
        elif arousal <= 0.30:
            label = "唤醒偏低"
        else:
            label = "唤醒适中"
        return {"score": fit, "level": arousal, "label": label, "detail": "；".join(reasons[:4])}

    def _experimental_proactive_motivation(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        drive: dict[str, Any] | None = None,
        temperature: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        drive = drive if isinstance(drive, dict) else self._bot_proactive_drive(user, now=now)
        temperature = temperature if isinstance(temperature, dict) else self._relationship_proactive_temperature(user, now=now)
        incentive = self._experimental_proactive_incentive(user, now=now)
        arousal = self._experimental_proactive_arousal(user, now=now)
        drive_score = _safe_float(drive.get("score"), 0.55)
        temp_score = _safe_float(temperature.get("score"), 0.55)
        incentive_score = _safe_float(incentive.get("score"), 0.5)
        arousal_fit = _safe_float(arousal.get("score"), 0.5)
        score = drive_score * 0.25 + temp_score * 0.18 + incentive_score * 0.37 + arousal_fit * 0.20
        score = max(0.05, min(1.0, score))
        label = "适合行动" if score >= 0.66 else "先收住" if score <= 0.40 else "观望"
        detail = (
            f"驱力{drive_score:.2f} 诱因{incentive_score:.2f} "
            f"唤醒{_safe_float(arousal.get('level'), 0.55):.2f}/适配{arousal_fit:.2f}"
        )
        return {
            "score": score,
            "label": label,
            "detail": detail,
            "drive": drive,
            "temperature": temperature,
            "incentive": incentive,
            "arousal": arousal,
        }

    def _soft_daily_target(self, user: dict[str, Any]) -> float:
        daily_limit = self._effective_user_daily_limit(user)
        if daily_limit <= 0:
            return 0.0
        if bool(self._proactive_intensity_effect("ignore_soft_daily_target", False)):
            return float(daily_limit)
        role = self._private_user_role(user)
        relationship_target = min(daily_limit, self._relationship_proactive_soft_target(user))
        explicit_quota = self._user_profile_override_int(user, "proactive_daily_limit")
        if role == "owner" or explicit_quota is not None:
            # The configured daily limit should remain the main pacing signal for
            # primary users and explicit per-user quotas. Relationship state still
            # controls hard boundaries and influences tone/readiness.
            relationship_target = daily_limit
        if relationship_target <= 0:
            return 0.0
        state = self.data.get("daily_state", {})
        important_dates = self._get_relevant_important_dates()
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        active_conditions = state.get("conditions", []) if isinstance(state, dict) else []
        quota_ratio = _safe_float(self._proactive_quota_policy(user).get("target_ratio"), 0.9, 0.0)
        ratio = quota_ratio if role == "owner" or explicit_quota is not None else 0.68
        if energy > 80:
            ratio += 0.06
        elif energy < 40:
            ratio -= 0.02
        if isinstance(active_conditions, list) and active_conditions:
            ratio += min(0.1, len(active_conditions) * 0.03)
        if important_dates:
            ratio += 0.1 if _safe_int(important_dates[0].get("_days_until"), 0) == 0 else 0.05
        ratio = max(0.45, min(1.0 if role == "owner" or explicit_quota is not None else 0.95, ratio))
        if relationship_target == 1:
            ratio = max(ratio, 0.75)
        return max(0.6, relationship_target * ratio)

    def _daily_intensity_factor(self, user: dict[str, Any]) -> float:
        daily_limit = self._effective_user_daily_limit(user)
        if daily_limit <= 0:
            return 0.0
        sent_today = _safe_int(user.get("sent_today"), 0)
        soft_target = self._soft_daily_target(user)
        no_cost_mode = bool(self._proactive_intensity_effect("ignore_soft_daily_target", False))
        capacity_factor = min(2.6 if no_cost_mode else 1.35, 0.9 + daily_limit * (0.055 if no_cost_mode else 0.08))
        if soft_target <= 0:
            return max(0.35, capacity_factor)
        usage = sent_today / soft_target
        if usage < 0.2:
            pressure = 1.18
        elif usage < 0.5:
            pressure = 1.03
        elif usage < 0.85:
            pressure = 0.88
        elif usage < 1.0:
            pressure = 0.72
        else:
            pressure = 0.9 if no_cost_mode else 0.5
        readiness = self._proactive_inner_readiness(user)
        inner_factor = 0.74 + _safe_float(readiness.get("score"), 0.55) * 0.55
        quota_multiplier = _safe_float(
            self._proactive_quota_policy(user).get("moment_probability_multiplier"),
            1.0,
            0.0,
        )
        return max(
            0.25,
            min(2.4 if no_cost_mode else 1.8, capacity_factor * pressure * inner_factor * quota_multiplier),
        )

    def _fallback_proactive_delay_hours(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> tuple[float, float]:
        now_dt = self._environment_fromtimestamp(now or _now_ts())
        tier_policy = self._proactive_quota_policy(user)
        tier = _safe_int(tier_policy.get("tier"), 0, 0, 5)
        configured_range = tier_policy.get("delay_range_hours")

        def tune(delay: tuple[float, float]) -> tuple[float, float]:
            low, high = delay
            if tier <= 0 or not isinstance(configured_range, (list, tuple)) or len(configured_range) < 2:
                return low, high
            policy_low = max(0.05, _safe_float(configured_range[0], low, 0.05))
            policy_high = max(policy_low + 0.05, _safe_float(configured_range[1], high, policy_low + 0.05))
            if tier <= 2:
                return max(low, policy_low), max(max(low, policy_low) + 0.05, min(high, policy_high))
            tuned_low = max(policy_low, min(low, policy_high * 0.72))
            tuned_high = max(tuned_low + 0.05, min(high, policy_high))
            return tuned_low, tuned_high

        if self._private_user_role(user) == "friend":
            spread_delay = self._friend_proactive_spread_delay_hours(user, now=now_dt.timestamp())
            if spread_delay is not None:
                return tune(spread_delay)
        sent_today = _safe_int(user.get("sent_today"), 0)
        remaining_target = max(1, math.ceil(max(0.0, self._soft_daily_target(user) - sent_today)))
        readiness_score = _safe_float(self._proactive_inner_readiness(user, now=now_dt.timestamp()).get("score"), 0.55)
        if readiness_score < 0.38:
            return tune((2.5, 6.0) if self._private_user_role(user) != "friend" else (8.0, 16.0))
        counts = self._today_proactive_daypart_counts(user)
        current_bucket = self._proactive_daypart_bucket_for_minute(now_dt.hour * 60 + now_dt.minute)
        if current_bucket == "late_night" and _safe_int(counts.get("late_night"), 0, 0) >= 1:
            return tune((7.5, 10.5))
        if current_bucket == "evening" and _safe_int(counts.get("evening"), 0, 0) >= 1 and remaining_target <= 2:
            return tune((3.0, 5.0))

        if now_dt.hour < 12:
            if remaining_target >= 4:
                return tune((0.45, 1.4))
            if remaining_target >= 3:
                return tune((0.75, 2.0))
            if remaining_target >= 2:
                return tune((1.0, 2.8))
            return tune((1.6, 4.2))
        if now_dt.hour < 18:
            if remaining_target >= 3:
                return tune((0.55, 1.8))
            if remaining_target >= 2:
                return tune((0.9, 2.6))
            return tune((1.8, 4.5))
        if remaining_target >= 2:
            return tune((0.5, 1.6))
        return tune((0.9, 2.4))

    def _proactive_hour_activity_weights(self) -> list[float]:
        raw = _proactive_setting_value(self, "proactive_hour_activity_curve", "")
        values = list(raw) if isinstance(raw, (list, tuple)) else str(raw or "").replace("，", ",").split(",")
        parsed: list[float] = []
        for value in values[:24]:
            try:
                parsed.append(max(0.05, min(2.0, float(value))))
            except (TypeError, ValueError):
                parsed.append(1.0)
        if len(parsed) != 24:
            parsed = [0.22, 0.16, 0.12, 0.10, 0.10, 0.14, 0.28, 0.50, 0.66, 0.72, 0.78, 0.92, 1.0, 0.94, 0.82, 0.74, 0.78, 0.92, 1.0, 0.98, 0.88, 0.70, 0.48, 0.32]
        return parsed

    def _sample_proactive_timestamp(
        self,
        user: dict[str, Any],
        *,
        now: float,
        delay_hours: tuple[float, float],
        reason: str = "",
    ) -> float:
        """Sample future slots by activity preference instead of uniform wall time."""
        low = max(0.05, _safe_float(delay_hours[0], 0.25, 0.05))
        high = max(low + 0.05, _safe_float(delay_hours[1], low + 0.5, low + 0.05))
        start = now + low * 3600
        end = now + high * 3600
        weights = self._proactive_hour_activity_weights()
        chronotype_blend = getattr(self, "_chronotype_hour_weights", None)
        if callable(chronotype_blend):
            # 全局曲线与该用户自己的活跃直方图混合，冷启动（样本不足）时保持全局。
            weights = chronotype_blend(user, weights)
        slots: list[tuple[float, float]] = []
        slot = math.ceil(start / 1800.0) * 1800.0
        while slot <= end and len(slots) < 160:
            local = self._environment_fromtimestamp(slot)
            slots.append((slot, weights[local.hour]))
            slot += 1800.0
        if not slots:
            return now + random.uniform(low, high) * 3600
        return random.choices([item[0] for item in slots], weights=[item[1] for item in slots], k=1)[0]

    def _maybe_schedule_proactive_burst(
        self,
        user: dict[str, Any],
        *,
        now: float,
        reason: str,
        source: str,
        action: str,
        motive: str,
        topic: str,
    ) -> bool:
        if not bool(_proactive_setting_value(self, "enable_proactive_burst", False)) or bool(user.get("planned_proactive_burst")):
            return False
        if source in {"timer", "troubleshooting", "simulation", "weather_alert", "body_monitor", "environment_change"}:
            return False
        if reason in {"open_loop_followup", "activity_followup", "goodnight_screen_check"}:
            return False
        limit = self._effective_user_daily_limit(user)
        if limit <= 0 or _safe_int(user.get("sent_today"), 0, 0) + 1 >= limit:
            return False
        max_messages_getter = getattr(self, "_proactive_burst_max_messages", None)
        max_messages = (
            max_messages_getter()
            if callable(max_messages_getter)
            else _safe_int(_proactive_setting_value(self, "proactive_burst_max_messages", 2), 2, 2, 3)
        )
        current_index = _safe_int(user.get("proactive_burst_index"), 0, 0, max_messages)
        if current_index + 1 >= max_messages:
            return False
        low = max(10, _safe_int(_proactive_setting_value(self, "proactive_burst_gap_min_seconds", 45), 45, 10, 600))
        high = max(low, _safe_int(_proactive_setting_value(self, "proactive_burst_gap_max_seconds", 180), 180, low, 900))
        scheduled = now + random.uniform(low, high)
        user["next_proactive_at"] = scheduled
        user["planned_proactive_burst"] = True
        user["proactive_burst_index"] = current_index + 1
        user["proactive_burst_origin_id"] = _single_line(user.get("planned_proactive_impulse_id"), 20)
        user["planned_proactive_source"] = _single_line(source, 40) or "proactive"
        user["planned_proactive_reason"] = _single_line(reason, 40) or "check_in"
        user["planned_proactive_action"] = _single_line(action, 40) or "message"
        user["planned_proactive_topic"] = _single_line(topic, 80)
        burst_motive = f"{motive}；这是同一阵念头里的第{current_index + 2}条短消息，换一个角度，不重复上一条。"
        normalizer = getattr(self, "_normalize_internal_motive_text", None)
        normalized_burst_motive = normalizer(burst_motive) if callable(normalizer) else burst_motive
        user["planned_proactive_motive"] = _single_line(normalized_burst_motive, 180)
        active_span, grace_span = self._proactive_impulse_default_window_seconds(reason, source=source)
        user["planned_proactive_window_start_at"] = scheduled
        user["planned_proactive_best_until_at"] = scheduled + min(active_span, 20 * 60)
        user["planned_proactive_expire_at"] = scheduled + min(grace_span, 45 * 60)
        user["planned_proactive_delivery_state"] = "burst"
        return True

    def _proactive_burst_max_messages(self) -> int:
        return _safe_int(_proactive_setting_value(self, "proactive_burst_max_messages", 2), 2, 2, 3)

    def _friend_proactive_spread_delay_hours(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> tuple[float, float] | None:
        if self._private_user_role(user) != "friend":
            return None
        sent_today = _safe_int(user.get("sent_today"), 0)
        daily_limit = self._effective_user_daily_limit(user)
        if daily_limit <= 1 or sent_today <= 0:
            return None
        ignored_slowdown = self._unanswered_slowdown_count(user)
        max_cooldown = self._effective_proactive_float(
            "friend_unanswered_max_cooldown_hours",
            max(1.0, _safe_float(_proactive_setting_value(self, "friend_unanswered_max_cooldown_hours", 60.0), 60.0, 1.0)),
            minimum=1.0,
            maximum=168.0,
        )
        base_interval_hours = max(0.25, self._effective_user_min_interval_minutes(user) / 60.0)
        unanswered_floor = 0.0
        if ignored_slowdown > 0:
            unanswered_floor = min(max_cooldown, 1.5 * (2 ** min(ignored_slowdown - 1, 3)))

        def cap_delay(delay: tuple[float, float]) -> tuple[float, float]:
            low, high = delay
            high = min(max_cooldown, high)
            low = min(low, max(0.25, high))
            return (low, high)

        low_multiplier, high_multiplier = (0.9, 1.6)
        if sent_today <= 1:
            low_multiplier, high_multiplier = (0.85, 1.5)
        elif sent_today <= 2 and daily_limit >= 3:
            low_multiplier, high_multiplier = (1.0, 1.9)
        else:
            low_multiplier, high_multiplier = (1.2, 2.4)
        low = max(0.25, base_interval_hours * low_multiplier, unanswered_floor)
        high = max(low + 0.35, base_interval_hours * high_multiplier, unanswered_floor * 1.45)
        return cap_delay((low, high))

    def _delay_hours_until_local_window(
        self,
        now_dt: datetime,
        start_minute: int,
        end_minute: int,
    ) -> tuple[float, float]:
        base = datetime.combine(now_dt.date(), datetime.min.time(), tzinfo=now_dt.tzinfo)
        start_dt = base + timedelta(minutes=start_minute)
        end_dt = base + timedelta(minutes=end_minute)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        if end_dt <= now_dt + timedelta(minutes=20):
            start_dt += timedelta(days=1)
            end_dt += timedelta(days=1)
        start_dt = max(start_dt, now_dt + timedelta(hours=3))
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(minutes=90)
        min_hours = max(0.25, (start_dt - now_dt).total_seconds() / 3600)
        max_hours = max(min_hours + 0.5, (end_dt - now_dt).total_seconds() / 3600)
        return (min_hours, max_hours)

    def _current_emotion_gate_mode(self, user: dict[str, Any], *, now: float | None = None) -> str:
        projection = self._relationship_proactive_temperature(user, now=now).get("expression_decision")
        band = _single_line(projection.get("expression_band"), 24) if isinstance(projection, dict) else ""
        if band == "hurt":
            return "hurt"
        if band == "avoidant" and not _single_line(projection.get("blocker"), 40):
            return "refusing"
        return ""

    def _current_relationship_gate_mode(self, user: dict[str, Any], *, now: float | None = None) -> str:
        projection = self._relationship_proactive_temperature(user, now=now).get("expression_decision")
        if not isinstance(projection, dict):
            return ""
        if _single_line(projection.get("blocker"), 40) == "contact_boundary" or _single_line(projection.get("safety_mode"), 40).startswith("contact_boundary"):
            return "backoff"
        return ""

    @staticmethod
    def _proactive_reason_is_intimate(reason: str) -> bool:
        return str(reason or "") in {
            "insomnia_night",
            "state_share",
            "diary_share",
            "evening_greeting",
        }

    @staticmethod
    def _proactive_action_is_intimate(action: str) -> bool:
        parts = {part.strip() for part in str(action or "").split("+") if part.strip()}
        return bool(parts & {"poke", "voice", "photo_text", "screen_peek"})

    @staticmethod
    def _proactive_text_is_intimate(*parts: Any) -> bool:
        text = " ".join(_single_line(part, 120) for part in parts if _single_line(part, 120))
        return bool(re.search(r"贴贴|抱抱|亲亲|摸摸|揉揉|蹭蹭|逗你|撒娇|想你|黏|贴近|靠近|坏心思|亲密|睡前|床|小屁股", text, re.I))

    def _low_pressure_proactive_replacement(
        self,
        *,
        mode: str,
        reason: str,
        action: str,
        motive: str,
        topic: str = "",
    ) -> tuple[str, str, str, str]:
        if mode == "careful":
            return (
                "quiet_care",
                "message",
                "感觉用户这会儿可能有点累或压力,只低压地问一句,不追问、不要求回复",
                topic or "低压关心",
            )
        if mode in {"hurt", "refusing"}:
            return (
                "quiet_care",
                "message",
                "Bot 还在收敛情绪,只保留一条很短的低压关心；不贴近、不撒娇、不追问",
                topic or "收敛后的低压关心",
            )
        return reason, action, motive, topic

    def _apply_emotion_to_planned_proactive(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str,
        topic: str = "",
        scheduled: float | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        check_now = _now_ts() if now is None else now
        mode = self._current_emotion_gate_mode(user, now=check_now) or self._current_relationship_gate_mode(user, now=check_now)
        result = {
            "reason": reason,
            "action": action,
            "motive": motive,
            "topic": topic,
            "scheduled": scheduled,
            "mode": mode,
            "note": "",
            "blocked": False,
        }
        intimate = (
            self._proactive_reason_is_intimate(reason)
            or self._proactive_action_is_intimate(action)
            or self._proactive_text_is_intimate(reason, action, motive, topic)
        )
        if mode == "attached":
            if reason in {"check_in", "quiet_care"} and random.random() < 0.28:
                result["reason"] = "activity_share"
                result["motive"] = motive or "刚刚有个轻轻的小想法,想自然分享一下"
                result["topic"] = topic or "轻分享"
                result["note"] = "情绪 attached: 提高轻分享倾向"
            photo_probability = max(0.0, min(1.0, float(_proactive_setting_value(self, "proactive_photo_text_probability", 0.18))))
            if action == "message" and reason in {"activity_share", "diary_share", "background_schedule"} and self._photo_text_available(user) and random.random() < photo_probability:
                result["action"] = self._fallback_action_for_unavailable("photo_text", user)
                result["note"] = (result["note"] + "；" if result["note"] else "") + "情绪 attached: 轻分享可带图"
            return result
        if mode == "careful":
            if action != "message" or reason not in {"quiet_care", "check_in"} or intimate:
                new_reason, new_action, new_motive, new_topic = self._low_pressure_proactive_replacement(
                    mode=mode,
                    reason=reason,
                    action=action,
                    motive=motive,
                    topic=topic,
                )
                result.update(reason=new_reason, action=new_action, motive=new_motive, topic=new_topic)
                result["note"] = "关系 careful: 只保留低压关心"
            return result
        if mode in {"hurt", "refusing", "backoff"}:
            if str(user.get("planned_proactive_source") or "") == "timer":
                return result
            delay = 2.5 * 3600 if mode == "hurt" else 5.5 * 3600
            if scheduled and scheduled > 0:
                result["scheduled"] = max(scheduled, check_now + random.uniform(delay, delay + 2.5 * 3600))
            if intimate or action != "message" or mode in {"refusing", "backoff"}:
                new_reason, new_action, new_motive, new_topic = self._low_pressure_proactive_replacement(
                    mode="hurt" if mode == "hurt" else "refusing",
                    reason=reason,
                    action=action,
                    motive=motive,
                    topic=topic,
                )
                result.update(reason=new_reason, action=new_action, motive=new_motive, topic=new_topic)
                result["note"] = f"情绪 {mode}: 延后并清理亲密主动候选"
            elif mode == "hurt":
                result["note"] = "情绪 hurt: 候选延后"
            return result
        return result

    def _defer_or_clean_emotion_blocked_plan(self, user: dict[str, Any], *, now: float | None = None) -> str:
        check_now = _now_ts() if now is None else now
        mode = self._current_emotion_gate_mode(user, now=check_now) or self._current_relationship_gate_mode(user, now=check_now)
        if mode not in {"hurt", "refusing", "backoff"}:
            return "情绪/关系状态处于收敛期"
        if str(user.get("planned_proactive_source") or "") == "timer":
            return "情绪/关系状态处于收敛期,预约主动保留"
        reason = str(user.get("planned_proactive_reason") or "")
        action = str(user.get("planned_proactive_action") or "message")
        motive = _single_line(user.get("planned_proactive_motive"), 140)
        topic = _single_line(user.get("planned_proactive_topic"), 60)
        intimate = (
            self._proactive_reason_is_intimate(reason)
            or self._proactive_action_is_intimate(action)
            or self._proactive_text_is_intimate(reason, action, motive, topic)
        )
        expression_projection = self._relationship_proactive_temperature(user, now=check_now).get("expression_decision")
        gate_until = (
            _safe_float(expression_projection.get("proactive_cooldown_until"), 0)
            if isinstance(expression_projection, dict)
            else 0
        )
        base_after = max(check_now + 90 * 60, gate_until + random.uniform(15 * 60, 75 * 60))
        if intimate or mode in {"refusing", "backoff"}:
            self._mark_planned_candidate_status(user, "deferred", f"情绪 {mode}: 亲密主动候选已清理/延后")
            self._clear_pending_proactive_plan(user)
            if mode == "hurt":
                user["next_proactive_at"] = base_after + random.uniform(20 * 60, 90 * 60)
                user["planned_proactive_reason"] = "quiet_care"
                user["planned_proactive_action"] = "message"
                user["planned_proactive_source"] = "emotion_gate"
                user["planned_proactive_motive"] = "Bot 还在收敛情绪,只留一条很短的低压关心,不贴近也不追问"
                user["planned_proactive_topic"] = "情绪收敛后的低压关心"
                user["planned_proactive_impulse_id"] = ""
                user["planned_proactive_window_start_at"] = user["next_proactive_at"]
                active_span, grace_span = self._proactive_impulse_default_window_seconds(
                    user["planned_proactive_reason"],
                    source="emotion_gate",
                )
                user["planned_proactive_best_until_at"] = user["next_proactive_at"] + active_span
                user["planned_proactive_expire_at"] = user["next_proactive_at"] + active_span + grace_span
                semantics = self._planned_proactive_semantics(user)
                user["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
                user["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
                user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
                user["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
                self._store_planned_proactive_route_fields(
                    user,
                    {
                        "source": "emotion_gate",
                        "reason": user["planned_proactive_reason"],
                        "action": user["planned_proactive_action"],
                        "scheduled_ts": user["next_proactive_at"],
                        "topic": user["planned_proactive_topic"],
                        "motive": user["planned_proactive_motive"],
                    },
                )
                item = self._record_proactive_candidate(
                    str(user.get("user_id") or user.get("id") or ""),
                    {
                        "source": "emotion_gate",
                        "reason": user["planned_proactive_reason"],
                        "action": user["planned_proactive_action"],
                        "scheduled_ts": user["next_proactive_at"],
                        "topic": user["planned_proactive_topic"],
                        "motive": user["planned_proactive_motive"],
                        "score": 32,
                    },
                    status="accepted",
                    note="情绪 hurt: 恢复后低压关心候选",
                    user=user,
                )
                user["planned_candidate_id"] = item.get("id", "")
                saver = getattr(self, "_schedule_data_save", None)
                if callable(saver):
                    saver(sections={"users"})
                return "情绪 hurt 收敛中,亲密主动候选已延后"
            scheduler = getattr(self, "_schedule_next_proactive", None)
            if callable(scheduler):
                scheduler(user, now=base_after, delay_hours=(0.5, 2.0))
            if _safe_float(user.get("next_proactive_at"), 0) <= check_now:
                user["next_proactive_at"] = base_after
                user["planned_proactive_window_start_at"] = base_after
                user["planned_proactive_best_until_at"] = base_after + 45 * 60
                user["planned_proactive_expire_at"] = base_after + 90 * 60
            saver = getattr(self, "_schedule_data_save", None)
            if callable(saver):
                saver(sections={"users"})
            return f"情绪/关系 {mode} 收敛中,亲密主动候选已清理"
        defer = getattr(self, "_defer_or_replace_planned_impulse", None)
        if callable(defer):
            delay_minutes = max(1.0, (base_after - check_now) / 60)
            defer(
                user,
                now=check_now,
                note=f"情绪 {mode}: 主动候选延后",
                delay_minutes=(delay_minutes, delay_minutes + 30.0),
                block_current=False,
            )
        else:
            self._mark_planned_candidate_status(user, "deferred", f"情绪 {mode}: 主动候选延后")
            user["next_proactive_at"] = max(_safe_float(user.get("next_proactive_at"), 0), base_after)
        saver = getattr(self, "_schedule_data_save", None)
        if callable(saver):
            saver(sections={"users"})
        return f"情绪 {mode} 收敛中,主动候选已延后"

    def _normalize_existing_plan_for_emotion(self, user: dict[str, Any], *, now: float | None = None) -> str:
        check_now = _now_ts() if now is None else now
        if str(user.get("planned_proactive_source") or "") == "timer":
            return ""
        reason = str(user.get("planned_proactive_reason") or "")
        action = str(user.get("planned_proactive_action") or "message")
        motive = _single_line(user.get("planned_proactive_motive"), 140)
        topic = _single_line(user.get("planned_proactive_topic"), 60)
        scheduled = _safe_float(user.get("next_proactive_at"), 0)
        adjusted = self._apply_emotion_to_planned_proactive(
            user,
            reason=reason,
            action=action,
            motive=motive,
            topic=topic,
            scheduled=scheduled,
            now=check_now,
        )
        note = _single_line(adjusted.get("note"), 160)
        if not note:
            return ""
        user["planned_proactive_reason"] = str(adjusted.get("reason") or reason)
        user["planned_proactive_action"] = str(adjusted.get("action") or action)
        user["planned_proactive_motive"] = _single_line(adjusted.get("motive"), 140) or motive
        user["planned_proactive_topic"] = _single_line(adjusted.get("topic"), 60) or topic
        user["next_proactive_at"] = _safe_float(adjusted.get("scheduled"), scheduled)
        self._mark_planned_candidate_status(user, "accepted", note)
        saver = getattr(self, "_schedule_data_save", None)
        if callable(saver):
            saver(sections={"users"})
        return note

    def _friend_proactive_scheduled_too_early(
        self,
        user: dict[str, Any],
        scheduled_at: float,
    ) -> bool:
        if self._private_user_role(user) != "friend" or scheduled_at <= 0:
            return False
        last_sent = _safe_float(user.get("last_sent"), 0)
        if last_sent <= 0:
            return False
        return scheduled_at - last_sent < self._effective_min_interval_seconds(user)

    def _random_proactive_impulse_context(
        self,
        user: dict[str, Any],
        *,
        now: float,
    ) -> dict[str, Any]:
        state = self.data.get("daily_state", {})
        if not isinstance(state, dict):
            state = {}
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias") or state.get("mood"), 24)
        note = _single_line(state.get("note"), 120)
        ignored_streak = _safe_int(user.get("ignored_streak"), 0, 0, 20)
        awaiting_since = _safe_float(user.get("awaiting_reply_since"), 0)
        last_sent = _safe_float(user.get("last_sent"), 0)
        reasons: list[str] = []
        suggest_soft_reason = False
        if awaiting_since > 0:
            silent_hours = (now - awaiting_since) / 3600.0
            if silent_hours >= 3.0:
                reasons.append(f"已等待回复 {silent_hours:.1f}h")
                suggest_soft_reason = True
        elif ignored_streak >= 1 and last_sent > 0 and now - last_sent >= 3 * 3600:
            reasons.append(f"未回应 {ignored_streak} 次")
            suggest_soft_reason = True
        low_energy = energy <= 45
        quiet_mood = mood in {"安静", "疲惫", "低落", "收声", "困倦"}
        if low_energy or quiet_mood or any(token in note for token in ("疲惫", "困", "低电量", "收声", "慢一点", "安静")):
            reasons.append(f"状态偏低({energy}/{mood or '平稳'})")
            suggest_soft_reason = True
        if not reasons:
            reasons.append("当前没有更高优先级事件，可生成一个自然、具体、低压力的日常念头")
        return {
            "allowed": True,
            "reasons": reasons,
            "suggest_soft_reason": suggest_soft_reason,
        }

    def _queue_event_driven_proactive_impulses(
        self,
        user: dict[str, Any],
        *,
        now: float,
    ) -> int:
        queued = 0
        event_sources = (
            ("pending_followup", self._pick_pending_followup_event(user, now)),
            ("open_loop", self._pick_open_loop_followup_event(user, now)),
            ("birthday_celebration", self._pick_birthday_celebration_event(user, now)),
            ("special_day_ritual", self._pick_special_day_greeting_event(user, now=now)),
            ("night_care", self._pick_insomnia_night_event(user, now=now)),
            ("meal_care", self._pick_meal_care_event(user, now=now)),
            ("daily_greeting", self._pick_daily_greeting_event(user, now)),
            ("mobile_location", self._pick_mobile_location_arrival_event(user, now=now)),
            ("anonymous_area", self._pick_mobile_anonymous_area_event(user, now=now)),
            ("birthday_curiosity", self._pick_birthday_curiosity_event(user, now)),
            ("habit", self._habit_proactive_event_for_user(user, now=now)),
            ("state", self._pick_state_need_event(user, now=now)),
            ("story", self._pick_story_plan_event(now, user=user)),
        )
        for source, event in event_sources:
            if not isinstance(event, dict):
                continue
            social_relay_note = self._unverified_social_relay_plan_reason(
                event,
                source=source,
                has_trigger=bool(_single_line(event.get("trigger_message_id"), 120)),
            )
            if social_relay_note:
                self._record_proactive_candidate(
                    str(user.get("user_id") or user.get("id") or ""),
                    {
                        "source": source,
                        "reason": _single_line(event.get("reason"), 40) or "check_in",
                        "action": _single_line(event.get("action"), 40) or "message",
                        "scheduled_ts": _safe_float(event.get("_scheduled_ts"), now),
                        "topic": _single_line(event.get("topic"), 80),
                        "motive": _single_line(event.get("motive"), 180),
                        "score": 0,
                    },
                    status="blocked",
                    note=social_relay_note,
                    user=user,
                )
                continue
            reason = _single_line(event.get("reason"), 40) or "check_in"
            motive = _single_line(event.get("motive"), 140)
            action = _single_line(event.get("action"), 40)
            if not action:
                if not motive:
                    motive = self._choose_proactive_motive(reason, user, planned_event=event)
                action = self._choose_action_for_reason(reason, user, motive=motive)
            elif not motive:
                motive = self._choose_proactive_motive(reason, user, action=action, planned_event=event)
            action = self._maybe_upgrade_planned_message_action(
                action,
                reason=reason,
                user=user,
                motive=motive,
                planned_event=event,
            )
            topic = _single_line(event.get("topic"), 60) or self._choose_proactive_topic(reason, user)
            if self._action_has_photo_text(action) and self._private_user_role(user) != "friend":
                photo_patch = self._photo_text_plan_field_patch(
                    reason=reason,
                    topic=topic,
                    motive=motive,
                    planned_event=event,
                )
                topic = _single_line(photo_patch.get("topic"), 60) or topic
                motive = _single_line(photo_patch.get("motive"), 140) or motive
            if self._private_user_role(user) == "friend":
                friend_safe = self._sanitize_friend_proactive_plan_fields(
                    user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                )
                reason = friend_safe["reason"]
                action = friend_safe["action"]
                topic = friend_safe["topic"]
                motive = friend_safe["motive"]
            candidate = dict(event)
            candidate["origin_event_id"] = self._proactive_origin_event_id(event, source=source)
            candidate["reason"] = reason
            candidate["action"] = action
            candidate["topic"] = topic
            candidate["motive"] = motive
            impulse = self._candidate_to_impulse(user, candidate, source=source, now=now)
            if not isinstance(impulse, dict):
                for key in ("lifecycle_status", "lifecycle_note", "lifecycle_updated_at", "expired_at"):
                    if key in candidate:
                        event[key] = candidate.get(key)
                continue
            rest_until = self._proactive_rest_block_until(
                user,
                now=now,
                reason=reason,
                source=source,
            )
            if rest_until > now and _safe_float(impulse.get("window_start_at"), 0) < rest_until:
                shift = rest_until - _safe_float(impulse.get("window_start_at"), 0) + random.uniform(20 * 60, 90 * 60)
                impulse["window_start_at"] = _safe_float(impulse.get("window_start_at"), 0) + shift
                impulse["preferred_ts"] = _safe_float(impulse.get("preferred_ts"), 0) + shift
                impulse["best_until_at"] = _safe_float(impulse.get("best_until_at"), 0) + shift
                impulse["expire_at"] = _safe_float(impulse.get("expire_at"), 0) + shift
            busy_gate = getattr(self, "_busy_reply_proactive_block_until", None)
            busy_until = 0.0
            if callable(busy_gate):
                try:
                    busy_until = _safe_float(
                        busy_gate(user, now=now, reason=reason, source=source),
                        0.0,
                    )
                except Exception:
                    busy_until = 0.0
            if busy_until > now and _safe_float(impulse.get("window_start_at"), 0) < busy_until:
                shift = busy_until - _safe_float(impulse.get("window_start_at"), 0)
                impulse["window_start_at"] = _safe_float(impulse.get("window_start_at"), 0) + shift
                impulse["preferred_ts"] = _safe_float(impulse.get("preferred_ts"), 0) + shift
                impulse["best_until_at"] = _safe_float(impulse.get("best_until_at"), 0) + shift
                impulse["expire_at"] = _safe_float(impulse.get("expire_at"), 0) + shift
            if self._queue_proactive_impulse(user, impulse):
                queued += 1
                if source == "anonymous_area":
                    pending = user.get("mobile_anonymous_area_pending")
                    if isinstance(pending, dict):
                        pending["candidate_at"] = now
        return queued

    def _queue_random_proactive_impulse(
        self,
        user: dict[str, Any],
        *,
        now: float,
        delay_hours: tuple[float, float],
    ) -> dict[str, Any] | None:
        context = self._random_proactive_impulse_context(user, now=now)
        if not bool(context.get("allowed")):
            return None
        reason = self._choose_planned_reason()
        if bool(context.get("suggest_soft_reason")) and reason in {"check_in", "state_share"}:
            reason = "quiet_care"
        motive = self._choose_proactive_motive(reason, user)
        action = self._choose_action_for_reason(reason, user, motive=motive)
        action = self._maybe_upgrade_planned_message_action(
            action,
            reason=reason,
            user=user,
            motive=motive,
            planned_event=None,
        )
        scheduled = self._sample_proactive_timestamp(
            user,
            now=now,
            delay_hours=delay_hours,
            reason=reason,
        )
        scheduled = self._move_timestamp_into_reason_window(scheduled, reason, user)
        topic = self._choose_proactive_topic(reason, user)
        emotion_adjustment = self._apply_emotion_to_planned_proactive(
            user,
            reason=reason,
            action=action,
            motive=motive,
            topic=topic,
            scheduled=scheduled,
            now=now,
        )
        reason = str(emotion_adjustment.get("reason") or reason)
        action = str(emotion_adjustment.get("action") or action)
        motive = _single_line(emotion_adjustment.get("motive"), 140) or motive
        topic = _single_line(emotion_adjustment.get("topic"), 60) or topic
        scheduled = _safe_float(emotion_adjustment.get("scheduled"), scheduled)
        if self._action_has_photo_text(action) and self._private_user_role(user) != "friend":
            photo_patch = self._photo_text_plan_field_patch(
                reason=reason,
                topic=topic,
                motive=motive,
            )
            topic = _single_line(photo_patch.get("topic"), 60) or topic
            motive = _single_line(photo_patch.get("motive"), 140) or motive
        if self._private_user_role(user) == "friend":
            friend_safe = self._sanitize_friend_proactive_plan_fields(
                user,
                reason=reason,
                action=action,
                topic=topic,
                motive=motive,
            )
            reason = friend_safe["reason"]
            action = friend_safe["action"]
            topic = friend_safe["topic"]
            motive = friend_safe["motive"]
        vague_seek_user = (
            str(action or "message") == "message"
            and self._is_vague_seek_user_motive(reason, action, motive, topic)
        )
        if vague_seek_user:
            scheduled = max(scheduled, now + random.uniform(1.5 * 3600, 3.5 * 3600))
            scheduled = self._move_timestamp_into_reason_window(scheduled, reason, user)
        active_span, grace_span = self._proactive_impulse_default_window_seconds(reason, source="random")
        impulse = self._build_proactive_impulse(
            user,
            reason=reason,
            action=action,
            motive=motive,
            topic=topic,
            source="random",
            window_start_at=scheduled,
            preferred_ts=scheduled,
            best_until_at=scheduled + active_span,
            expire_at=scheduled + active_span + grace_span,
        )
        if vague_seek_user:
            impulse["salience"] = min(_safe_float(impulse.get("salience"), 0.4), 0.34)
            impulse["urgency"] = min(_safe_float(impulse.get("urgency"), 0.3), 0.22)
        return self._queue_proactive_impulse(user, impulse)

    def _proactive_window_timezone(self) -> str:
        return (
            _single_line(
                getattr(self, "environment_perception_timezone", ""),
                64,
            )
            or "Asia/Shanghai"
        )

    def _invalidate_timezone_derived_state(
        self,
        previous_timezone: str = "",
        current_timezone: str = "",
        *,
        schedule_save: bool = True,
    ) -> dict[str, Any]:
        """Invalidate derived wall-clock state without touching explicit timers."""

        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {"changed": False, "sections": []}
        current = _single_line(
            current_timezone or self._proactive_window_timezone(),
            64,
        ) or "Asia/Shanghai"
        runtime = data.get("proactive_runtime")
        if not isinstance(runtime, dict):
            runtime = {}
            data["proactive_runtime"] = runtime
        stored = _single_line(runtime.get("window_timezone"), 64)
        previous = _single_line(previous_timezone, 64) or stored
        if not previous:
            runtime["window_timezone"] = current
            sections = {"proactive_runtime"}
            if schedule_save:
                saver = getattr(self, "_schedule_data_save", None)
                if callable(saver):
                    saver(sections=sections, delay=0.1)
            return {
                "changed": True,
                "initialized": True,
                "sections": sorted(sections),
                "cleared_plans": 0,
                "blocked_candidates": 0,
            }
        if previous == current:
            runtime["window_timezone"] = current
            return {"changed": False, "sections": []}

        now = _now_ts()
        exempt_sources = {"timer", "troubleshooting", "simulation"}
        blocked_candidates = 0
        pool = data.get("proactive_candidate_pool")
        if isinstance(pool, list):
            for candidate in pool:
                if not isinstance(candidate, dict):
                    continue
                status = _single_line(candidate.get("status"), 24).lower()
                lifecycle = _single_line(
                    candidate.get("lifecycle_status"),
                    24,
                ).lower()
                source = self._normalize_legacy_proactive_text(
                    candidate.get("source"),
                    limit=40,
                )
                if (
                    source in exempt_sources
                    or status in {"sent", "blocked", "skipped", "expired", "cancelled", "dropped"}
                    or lifecycle in {"skipped", "expired"}
                ):
                    continue
                candidate["status"] = "blocked"
                candidate["lifecycle_status"] = "skipped"
                candidate["note"] = "运行时区已变化，旧时间窗口已作废"
                candidate["lifecycle_note"] = "运行时区已变化，旧时间窗口已作废"
                candidate["updated_ts"] = now
                candidate["lifecycle_updated_at"] = now
                blocked_candidates += 1

        cleared_plans = 0
        users = data.get("users")
        if isinstance(users, dict):
            for user in users.values():
                if not isinstance(user, dict):
                    continue
                impulses = user.get("proactive_impulses")
                if isinstance(impulses, list):
                    for impulse in impulses:
                        if not isinstance(impulse, dict):
                            continue
                        source = self._normalize_legacy_proactive_text(
                            impulse.get("source"),
                            limit=40,
                        )
                        state = _single_line(impulse.get("state"), 24).lower()
                        if source in exempt_sources or state not in {"", "queued", "deferred"}:
                            continue
                        impulse["state"] = "blocked"
                        impulse["last_status"] = "blocked"
                        impulse["last_note"] = "运行时区已变化，旧时间窗口已作废"
                        impulse["updated_ts"] = now
                planned_source = self._normalize_legacy_proactive_text(
                    user.get("planned_proactive_source"),
                    limit=40,
                )
                has_plan = bool(
                    _safe_float(user.get("next_proactive_at"), 0)
                    or planned_source
                    or _single_line(user.get("planned_candidate_id"), 40)
                )
                if has_plan and planned_source not in exempt_sources:
                    self._clear_pending_proactive_plan(user)
                    user.pop("planned_weather_alert_context", None)
                    user.pop("planned_environment_change_context", None)
                    cleared_plans += 1

        terminal_history: dict[str, Any] = {}
        alert_state = data.get("weather_alert_awareness")
        if isinstance(alert_state, dict) and isinstance(
            alert_state.get("terminal_event_identities"),
            dict,
        ):
            terminal_history = dict(alert_state["terminal_event_identities"])
        data["weather_alert_awareness"] = {
            "initialized": False,
            "baseline_ids": [],
            "pending_events": [],
            "terminal_event_identities": terminal_history,
            "next_check_at": 0,
            "config_key": "",
            "window_timezone": current,
        }
        data["environment_change_awareness"] = {
            "initialized": False,
            "next_check_at": 0,
            "window_timezone": current,
        }
        data["daily_weather"] = {}
        data["weather_alerts"] = {}
        runtime["window_timezone"] = current
        runtime["timezone_changed_at"] = now
        runtime["previous_window_timezone"] = previous
        sections = {
            "users",
            "proactive_candidate_pool",
            "proactive_runtime",
            "daily_weather",
            "weather_alerts",
            "weather_alert_awareness",
            "environment_change_awareness",
        }
        if schedule_save:
            saver = getattr(self, "_schedule_data_save", None)
            if callable(saver):
                saver(sections=sections, delay=0.1)
        logger.info(
            "[PrivateCompanion] 运行时区变化，已作废旧派生窗口: from=%s to=%s plans=%s candidates=%s",
            previous,
            current,
            cleared_plans,
            blocked_candidates,
        )
        return {
            "changed": True,
            "initialized": False,
            "sections": sorted(sections),
            "cleared_plans": cleared_plans,
            "blocked_candidates": blocked_candidates,
        }

    def _schedule_next_proactive(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        delay_hours: tuple[float, float] | None = None,
    ):
        user_id = str(user.get("user_id") or user.get("id") or "")
        if not self._user_enabled_for_proactive(user_id, user):
            self._clear_pending_proactive_plan(user)
            return
        if self._proactive_generation_disabled(user):
            self._suspend_user_proactive_generation(user)
            return
        now = now or _now_ts()
        timer_event = self._get_active_llm_timer(user)
        if not (isinstance(timer_event, dict) and self._llm_timer_can_use_internal_scheduler(timer_event)):
            silence_reason_getter = getattr(self, "_friend_unanswered_silence_reason", None)
            silence_reason = silence_reason_getter(user, now=now) if callable(silence_reason_getter) else ""
            if silence_reason:
                self._block_friend_unanswered_pending_proactive(user, note=silence_reason, now=now)
                self._clear_pending_proactive_plan(user)
                logger.info(
                    "[PrivateCompanion] 次要用户连续未回应,停止安排普通主动: user=%s ignored=%s reason=%s",
                    _single_line(user_id, 40) or "unknown",
                    _safe_int(user.get("ignored_streak"), 0, 0),
                    _single_line(silence_reason, 120),
                )
                return
        if delay_hours is None:
            delay_hours = self._fallback_proactive_delay_hours(user, now=now)
        delay_factor = self._effective_proactive_float("delay_factor", 1.0, minimum=0.2, maximum=1.0)
        if delay_hours is not None and delay_factor < 1.0:
            delay_hours = (
                max(0.05, delay_hours[0] * delay_factor),
                max(0.08, delay_hours[1] * delay_factor),
            )
        intensity_factor = self._daily_intensity_factor(user)
        if delay_hours is not None and intensity_factor > 0:
            widen = max(0.85, min(1.8, 1.25 - intensity_factor * 0.45))
            delay_hours = (delay_hours[0] * widen, delay_hours[1] * widen)
        if delay_hours is not None:
            cycle_multiplier = self._cycle_proactive_frequency_profile()["private_interval_multiplier"]
            if cycle_multiplier > 1.0:
                delay_hours = (
                    delay_hours[0] * cycle_multiplier,
                    delay_hours[1] * cycle_multiplier,
                )
        planned_event = self._pick_best_planned_event(user, now)
        default_reason = (
            str(planned_event.get("reason") or "")
            if isinstance(planned_event, dict)
            else self._choose_planned_reason()
        ) or "check_in"
        if isinstance(timer_event, dict) and self._llm_timer_can_use_internal_scheduler(timer_event):
            timer_scheduled = _safe_float(timer_event.get("scheduled_ts"), 0)
            if timer_scheduled > now:
                user["next_proactive_at"] = timer_scheduled
                user["planned_proactive_reason"] = _single_line(timer_event.get("reason"), 40) or default_reason
                user["planned_proactive_action"] = _single_line(timer_event.get("action"), 24) or "message"
                user["planned_proactive_source"] = "timer"
                user["planned_proactive_motive"] = self._normalize_internal_motive_text(
                    _single_line(timer_event.get("motive"), 140)
                )
                user["planned_proactive_topic"] = _single_line(timer_event.get("topic"), 60) or (
                    _single_line(planned_event.get("topic"), 60)
                    if isinstance(planned_event, dict)
                    else self._choose_proactive_topic(default_reason, user)
                )
                user["planned_proactive_impulse_id"] = ""
                user["planned_proactive_window_start_at"] = timer_scheduled
                user["planned_proactive_window_timezone"] = self._proactive_window_timezone()
                active_span, grace_span = self._proactive_impulse_default_window_seconds(
                    user["planned_proactive_reason"],
                    source="timer",
                )
                user["planned_proactive_best_until_at"] = timer_scheduled + active_span
                user["planned_proactive_expire_at"] = timer_scheduled + active_span + grace_span
                semantics = self._planned_proactive_semantics(user)
                user["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
                user["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
                user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
                user["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
                self._set_planned_proactive_trigger(
                    user,
                    message_id=_single_line(timer_event.get("trigger_message_id"), 120),
                    umo=_single_line(timer_event.get("trigger_umo"), 160),
                    created_at=_safe_float(timer_event.get("trigger_ts"), 0),
                )
                user["planned_event_chain"] = (
                    []
                    if self._private_user_role(user) == "friend"
                    else list(timer_event.get("chain") or [])
                    if isinstance(timer_event.get("chain"), list)
                    else []
                )
                user["planned_opener_mode"] = ""
                user["planned_followup_kind"] = ""
                user["planned_proactive_quota_exempt"] = False
                self._store_planned_proactive_route_fields(user, timer_event)
                item = self._record_proactive_candidate(
                    str(user.get("user_id") or user.get("id") or ""),
                    {
                        "source": "timer",
                        "reason": user["planned_proactive_reason"],
                        "action": user["planned_proactive_action"],
                        "scheduled_ts": timer_scheduled,
                        "topic": user["planned_proactive_topic"],
                        "motive": user["planned_proactive_motive"],
                        "score": 100,
                    },
                    status="accepted",
                    note="用户预约/定时主动",
                    user=user,
                )
                user["planned_candidate_id"] = item.get("id", "")
                return
        self._cleanup_proactive_impulses(user, now=now)
        self._queue_event_driven_proactive_impulses(user, now=now)
        active_impulses = [
            item
            for item in self._cleanup_proactive_impulses(user, now=now)
            if isinstance(item, dict) and str(item.get("state") or "queued") in {"queued", "deferred"}
        ]
        if not active_impulses:
            self._queue_random_proactive_impulse(user, now=now, delay_hours=delay_hours)
        if not self._materialize_best_proactive_impulse(user, now=now):
            self._clear_pending_proactive_plan(user)

    def _promote_earlier_daily_greeting_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> bool:
        if str(user.get("planned_proactive_source") or "") == "timer":
            return False
        current_next = _safe_float(user.get("next_proactive_at"), 0)
        if current_next <= 0:
            return False
        now = now or _now_ts()
        events = []
        if bool(_proactive_setting_value(self, "enable_daily_greetings", True)):
            events.append(self._pick_daily_greeting_event(user, now))
        if bool(_proactive_setting_value(self, "enable_meal_care_proactive", True)):
            events.append(self._pick_meal_care_event(user, now=now))
        events.extend(
            (
                self._pick_birthday_celebration_event(user, now),
                self._pick_special_day_greeting_event(user, now=now),
                self._pick_insomnia_night_event(user, now=now),
            )
        )
        valid_events = [item for item in events if isinstance(item, dict)]
        if not valid_events:
            return False
        event = min(valid_events, key=lambda item: self._timestamp_from_story_event(item, str(item.get("reason") or "check_in")))
        reason = str(event.get("reason") or "")
        priority_reasons = {"birthday_celebration", "special_day_greeting", "insomnia_night"}
        if not (
            self._is_sticky_greeting_reason(reason)
            or bool(event.get("_daily_meal_care"))
            or reason in priority_reasons
        ):
            return False
        source = _single_line(event.get("_proactive_source"), 40)
        if not source:
            source = "daily_greeting" if event.get("_daily_greeting") else "meal_care"
        prepared, _invalid_reason = self._prepare_proactive_candidate_window(
            event,
            reason=reason,
            source=source,
            now=now,
        )
        if not isinstance(prepared, dict):
            return False
        event = prepared
        scheduled = _safe_float(
            event.get("scheduled_ts"),
            self._timestamp_from_story_event(event, reason),
        )
        if scheduled <= 0 or scheduled >= current_next - 60:
            return False
        action = str(event.get("action") or "message")
        motive = _single_line(event.get("motive"), 120) or self._choose_proactive_motive(
            reason,
            user,
            action=action,
            planned_event=event,
        )
        self._reset_planned_proactive_delivery_state(user)
        user["next_proactive_at"] = scheduled
        user["planned_proactive_reason"] = reason
        user["planned_proactive_action"] = action
        user["planned_proactive_source"] = source
        user["planned_proactive_motive"] = motive
        user["planned_proactive_topic"] = _single_line(event.get("topic"), 60)
        user["planned_proactive_impulse_id"] = ""
        user["planned_proactive_window_start_at"] = _safe_float(event.get("window_start_at"), scheduled)
        user["planned_proactive_window_timezone"] = _single_line(
            event.get("window_timezone"),
            64,
        ) or self._proactive_window_timezone()
        user["planned_proactive_best_until_at"] = _safe_float(event.get("best_until_at"), scheduled)
        user["planned_proactive_expire_at"] = _safe_float(event.get("expire_at"), scheduled)
        # 该入口会替换当前计划，但不消费原念头；不能让新问候继续引用旧候选 ID。
        user["planned_candidate_id"] = ""
        semantics = self._planned_proactive_semantics(user)
        user["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
        user["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
        user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
        user["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
        self._clear_planned_proactive_trigger(user)
        user["planned_event_chain"] = [] if self._private_user_role(user) == "friend" else (
            list(event.get("chain") or []) if isinstance(event.get("chain"), list) else []
        )
        user["planned_opener_mode"] = ""
        user["planned_followup_kind"] = ""
        user["planned_proactive_quota_exempt"] = bool(event.get("_free_screen_peek"))
        self._store_planned_proactive_route_fields(user, {**event, "source": source})
        context_key = _single_line(event.get("context_key"), 60)
        context = event.get("context")
        if context_key and isinstance(context, dict):
            user[context_key] = dict(context)
        return True

    def _is_proactive_plan_stale(self, user: dict[str, Any], *, now: float | None = None) -> bool:
        next_at = _safe_float(user.get("next_proactive_at"), 0)
        if next_at <= 0:
            return False
        check_now = _now_ts() if now is None else now
        return check_now - next_at > _safe_int(
            _proactive_setting_value(self, "max_proactive_plan_lag_minutes", 180),
            180,
            5,
            1440,
        ) * 60

    def _reset_planned_proactive_delivery_state(self, user: dict[str, Any]) -> None:
        user["planned_proactive_origin_at"] = 0
        user["planned_proactive_origin_key"] = ""
        user["planned_proactive_freshness"] = ""
        user["planned_proactive_delivery_state"] = ""

    def _clear_pending_proactive_plan(self, user: dict[str, Any]) -> None:
        current_impulse_id = _single_line(user.get("planned_proactive_impulse_id"), 20)
        user.pop("body_monitor_health_context", None)
        impulses = user.get("proactive_impulses")
        if current_impulse_id and isinstance(impulses, list):
            for impulse in impulses:
                if not isinstance(impulse, dict) or _single_line(impulse.get("id"), 20) != current_impulse_id:
                    continue
                if _single_line(impulse.get("source"), 40) == "body_monitor":
                    impulse.pop("context", None)
                    impulse["context_key"] = ""
                break
        user["next_proactive_at"] = 0
        user["planned_proactive_reason"] = ""
        user["planned_proactive_action"] = ""
        user["planned_proactive_source"] = ""
        user["planned_proactive_kind"] = ""
        user["planned_proactive_route_version"] = 0
        user["planned_proactive_route_dedupe_key"] = ""
        user["planned_proactive_route_review_profile"] = ""
        user["planned_proactive_route_retry_profile"] = ""
        user["planned_proactive_route_cancel_if_new_inbound"] = True
        user["planned_proactive_route_recent_chat_policy"] = ""
        user["planned_proactive_route_allow_automatic_followup"] = False
        user["planned_proactive_route_disable_segmenting"] = False
        user["planned_proactive_response_expectation"] = ""
        user["planned_proactive_burst"] = False
        user["proactive_burst_index"] = 0
        user["proactive_burst_origin_id"] = ""
        user["planned_proactive_origin_event_id"] = ""
        user["planned_proactive_route_preflight_action"] = ""
        user["planned_proactive_route_preflight_note"] = ""
        user["planned_proactive_motive"] = ""
        user["planned_proactive_topic"] = ""
        user["planned_proactive_impulse_id"] = ""
        user["planned_mobile_location_transition_key"] = ""
        user["planned_mobile_location_event_type"] = ""
        user["planned_proactive_window_start_at"] = 0
        user["planned_proactive_window_timezone"] = ""
        user["planned_proactive_best_until_at"] = 0
        user["planned_proactive_expire_at"] = 0
        self._reset_planned_proactive_delivery_state(user)
        user["planned_proactive_semantic_kind"] = ""
        user["planned_proactive_anchor_type"] = ""
        user["planned_proactive_semantic_score"] = 0
        user["planned_proactive_semantic_note"] = ""
        user["planned_proactive_model_judge_signature"] = ""
        user["planned_proactive_model_judge_result"] = {}
        user["planned_proactive_model_judge_at"] = 0
        user["planned_event_chain"] = []
        user["planned_opener_mode"] = ""
        user["planned_followup_kind"] = ""
        user["planned_proactive_quota_exempt"] = False
        user["planned_candidate_id"] = ""
        self._clear_planned_proactive_trigger(user)

    def _maintenance_failure_cooldown_seconds(self, label: str) -> float:
        if label in {"日常状态", "今日日程", "当前细化", "日记", "每日巡视", "创作推进"}:
            return 30 * 60
        return 5 * 60

    def _maintenance_task_blocked_by_failure(self, label: str, *, now: float | None = None) -> str:
        state = getattr(self, "_maintenance_failure_cooldowns", None)
        if not isinstance(state, dict):
            return ""
        key = self._maintenance_failure_key(label)
        item = state.get(key)
        if not isinstance(item, dict):
            return ""
        check_now = _now_ts() if now is None else now
        until = _safe_float(item.get("until"), 0, 0)
        if until <= check_now:
            state.pop(key, None)
            return ""
        error = _single_line(item.get("error"), 120)
        return f"{label} 失败冷却中（{self._format_elapsed(until - check_now)}后重试" + (f"，上次错误：{error}" if error else "") + "）"

    def _record_maintenance_task_failure(self, label: str, exc: Exception) -> None:
        state = getattr(self, "_maintenance_failure_cooldowns", None)
        if not isinstance(state, dict):
            state = {}
            self._maintenance_failure_cooldowns = state
        now = _now_ts()
        state[self._maintenance_failure_key(label)] = {
            "until": now + self._maintenance_failure_cooldown_seconds(label),
            "error": _single_line(exc, 180),
            "failed_at": now,
        }

    def _clear_maintenance_task_failure(self, label: str) -> None:
        state = getattr(self, "_maintenance_failure_cooldowns", None)
        if isinstance(state, dict):
            state.pop(self._maintenance_failure_key(label), None)

    def _maintenance_failure_key(self, label: str) -> str:
        active_getter = getattr(self, "_active_persona_scope", None)
        persona_id = str(active_getter() if callable(active_getter) else "").strip()
        return f"{persona_id}:{label}" if persona_id else label

    def _scheduler_maintenance_tasks(self) -> tuple[tuple[str, Any], ...]:
        tasks = (
            ("日常状态", self._ensure_daily_state),
            ("今日日程", self._ensure_daily_plan),
            ("日程归档", self._run_agenda_maintenance_tick),
            ("当前细化", self._ensure_detail_enhancement),
            ("当前在线感", self._ensure_current_detail_presence_status),
            ("日记", self._ensure_daily_diary),
            ("每日巡视", self._ensure_daily_review),
            ("每日穿搭", self._ensure_daily_outfit_photo),
            ("创作推进", self._maybe_advance_creative_projects),
            ("个人目标", self._maybe_settle_personal_goals),
            ("备忘便签", self._maybe_process_memo_notes),
            ("天气预警", self._maybe_refresh_weather_alerts),
            ("环境突变", self._maybe_refresh_environment_change),
            ("余额感知", self._maybe_refresh_balance_awareness),
            ("晚安识屏", self._maybe_process_goodnight_screen_checks),
            ("被动注入缓存", self._refresh_passive_injection_cache),
        )
        if not self._proactive_generation_disabled():
            return tasks
        passive_labels = {
            "日常状态",
            "今日日程",
            "日程归档",
            "当前细化",
            "当前在线感",
            "日记",
            "每日巡视",
            "天气预警",
            "晚安识屏",
            "被动注入缓存",
        }
        return tuple(item for item in tasks if item[0] in passive_labels)

    async def _run_agenda_maintenance_tick(self) -> list[dict[str, Any]]:
        """Settle local windows, archive compact projections, then drain outbox."""
        tick = getattr(self, "_agenda_maintenance_tick", None)
        settled: Any = []
        if callable(tick):
            settled = tick()
            if inspect.isawaitable(settled):
                settled = await settled
        snapshots = [item for item in settled if isinstance(item, dict)] if isinstance(settled, list) else []
        snapshot_recorder = getattr(self, "_memory_companion_record_agenda_snapshot", None)
        reconciliation_recorder = getattr(self, "_memory_companion_record_agenda_reconciliation", None)
        history = self.data.get("agenda_reconciliation_history") if isinstance(getattr(self, "data", None), dict) else []
        for snapshot in snapshots:
            if callable(snapshot_recorder):
                try:
                    await snapshot_recorder(snapshot)
                except Exception as exc:
                    logger.debug("[PrivateCompanion] C3 agenda snapshot archival failed: %s", _single_line(exc, 160))
            if callable(reconciliation_recorder) and isinstance(history, list):
                snapshot_id = _single_line(snapshot.get("snapshot_id"), 160)
                for reconciliation in reversed(history):
                    if not isinstance(reconciliation, dict):
                        continue
                    refs = reconciliation.get("source_refs") if isinstance(reconciliation.get("source_refs"), list) else []
                    if snapshot_id and snapshot_id not in refs:
                        continue
                    try:
                        await reconciliation_recorder(reconciliation)
                    except Exception as exc:
                        logger.debug("[PrivateCompanion] C3 agenda reconciliation archival failed: %s", _single_line(exc, 160))
                    break
        flusher = getattr(self, "_memory_companion_flush_bot_personal_outbox", None)
        if callable(flusher):
            try:
                await flusher(limit=24)
            except Exception as exc:
                logger.debug("[PrivateCompanion] C3 Bot Personal outbox delivery failed: %s", _single_line(exc, 160))
        if snapshots and callable(getattr(self, "_schedule_data_save", None)):
            self._schedule_data_save(
                sections={"window_snapshots", "agenda_reconciliation_history"},
                delay=0.5,
            )
        return snapshots

    def _scheduler_persona_ids(self) -> list[str]:
        active_getter = getattr(self, "_active_persona_scope", None)
        active = str(active_getter() if callable(active_getter) else "").strip()
        if not bool(getattr(self, "enable_multi_persona_mode", False)):
            return [""]
        primary_getter = getattr(self, "_primary_persona_id", None)
        try:
            primary = str(primary_getter() or "").strip() if callable(primary_getter) else ""
        except Exception:
            primary = ""
        primary = primary or str(getattr(self, "plugin_specific_persona_id", "") or "").strip()
        configured_getter = getattr(self, "_persona_config_profile_ids", None)
        configured_profiles = list(configured_getter() if callable(configured_getter) else [])
        enabled_getter = getattr(self, "_configured_multi_persona_ids", None)
        enabled_ids = set(enabled_getter() if callable(enabled_getter) else [])
        ids = [primary, *(pid for pid in configured_profiles if pid in enabled_ids)]
        enabled = list(dict.fromkeys(item for item in ids if item))
        if active and active in enabled:
            return [active]
        return enabled or [""]

    async def _run_scheduler_cycle(self, *, immediate: bool = False) -> None:
        active_getter = getattr(self, "_active_persona_scope", None)
        current = str(active_getter() if callable(active_getter) else "").strip()
        for persona_id in self._scheduler_persona_ids():
            token = None
            if persona_id and persona_id != current:
                activator = getattr(self, "_activate_persona_id", None)
                token = activator(persona_id) if callable(activator) else None
            try:
                await self._tick()
                for label, task_factory in self._scheduler_maintenance_tasks():
                    try:
                        if self._maintenance_task_blocked_by_failure(label):
                            continue
                        await task_factory()
                        self._clear_maintenance_task_failure(label)
                    except Exception as exc:
                        self._record_maintenance_task_failure(label, exc)
                        logger.warning(
                            "[PrivateCompanion] %s维护步骤失败,已跳过: persona=%s task=%s error=%s",
                            "主动链即时" if immediate else "主动循环",
                            persona_id or "single",
                            label,
                            _single_line(exc, 160),
                        )
            finally:
                if token is not None:
                    deactivator = getattr(self, "_deactivate_persona_for_event", None)
                    if callable(deactivator):
                        deactivator(token)

    async def _scheduler_loop(self):
        while not self._stop_event.is_set():
            try:
                timeout = self._next_scheduler_timeout()
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=timeout
                )
            except asyncio.TimeoutError:
                await self._run_scheduler_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[PrivateCompanion] 主动消息循环异常: {e}", exc_info=True)

    def _mobile_location_watch_user_ids(self) -> list[str]:
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(users, dict):
            return []
        owner_getter = getattr(self, "_relationship_owner_user_ids", None)
        target_getter = getattr(self, "_configured_target_ids", None)
        allowed = {
            _single_line(item, 120)
            for getter in (owner_getter, target_getter)
            if callable(getter)
            for item in (getter() or ())
            if _single_line(item, 120)
        }
        if not allowed:
            allowed = {
                _single_line(key, 120)
                for key, value in users.items()
                if isinstance(value, dict) and value.get("reality_touch_consent")
            }
        return [
            user_id
            for user_id, user in users.items()
            if _single_line(user_id, 120) in allowed
            and isinstance(user, dict)
            and bool(user.get("enabled", True))
        ]

    @staticmethod
    def _anonymous_area_token(scene: dict[str, Any]) -> str:
        """Return an opaque kilometre-scale token; never persist raw location data."""
        if not isinstance(scene, dict):
            return ""
        area = _single_line(scene.get("area_label"), 100)
        if not area:
            return ""
        # Keep the durable token at city/district granularity. The raw area
        # label is never stored, and no coordinate is needed for this social
        # cue; a broader token also avoids pretending to recognise a venue.
        return hashlib.sha256(area.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def _anonymous_area_is_stable(scene: dict[str, Any]) -> bool:
        if not isinstance(scene, dict) or not scene.get("available"):
            return False
        if scene.get("presence_state") in {"at_place", "departing", "arriving", "in_transit"}:
            return False
        return not bool(scene.get("in_motion"))

    def _anonymous_area_runtime_store(self) -> dict[str, dict[str, Any]]:
        store = getattr(self, "_mobile_anonymous_area_runtime", None)
        if not isinstance(store, dict):
            store = {}
            self._mobile_anonymous_area_runtime = store
        return store

    def _anonymous_area_visit_records(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        records = user.get("mobile_anonymous_area_visits")
        if not isinstance(records, list):
            records = []
            user["mobile_anonymous_area_visits"] = records
        return [item for item in records if isinstance(item, dict)]

    @staticmethod
    def _mobile_location_humanization_budget_available(
        user: dict[str, Any],
        *,
        now: float,
    ) -> bool:
        """Keep location-derived social cues from piling up in one hour."""
        last_at = _safe_float(user.get("last_mobile_location_humanization_at"), 0.0)
        return last_at <= 0 or now - last_at >= _MOBILE_LOCATION_HUMANIZATION_BUDGET_SECONDS

    def _observe_mobile_anonymous_area(
        self,
        user: dict[str, Any],
        scene: dict[str, Any],
        *,
        now: float | None = None,
    ) -> bool:
        """Track coarse unmarked-area dwell without writing every GPS sample."""
        if not isinstance(user, dict):
            return False
        check_now = _now_ts() if now is None else float(now)
        user_id = _single_line(user.get("user_id") or user.get("id"), 120)
        if not user_id:
            return False
        store = self._anonymous_area_runtime_store()
        current = store.get(user_id) if isinstance(store.get(user_id), dict) else {}
        token = self._anonymous_area_token(scene) if self._anonymous_area_is_stable(scene) else ""
        previous_token = _single_line(current.get("token"), 40)
        last_seen = _safe_float(current.get("last_seen_at"), 0.0)
        changed = False

        def finish_previous() -> None:
            nonlocal changed
            if not previous_token:
                return
            dwell_seconds = max(0.0, last_seen - _safe_float(current.get("started_at"), last_seen))
            policy_getter = getattr(self, "_proactive_quota_policy", None)
            policy = policy_getter(user) if callable(policy_getter) else {}
            tier = _safe_int(policy.get("tier"), 3, 1, 5) if isinstance(policy, dict) else 3
            threshold = _ANONYMOUS_AREA_DWELL_THRESHOLDS_SECONDS.get(tier, 10**9)
            visits = self._anonymous_area_visit_records(user)
            visit = next((item for item in visits if _single_line(item.get("token"), 40) == previous_token), None)
            familiar = _safe_int(visit.get("count"), 0, 0) >= 3 if isinstance(visit, dict) else False
            if dwell_seconds >= threshold or familiar:
                user["mobile_anonymous_area_pending"] = {
                    "token": previous_token,
                    "left_at": check_now,
                    "dwell_minutes": int(round(dwell_seconds / 60.0)),
                    "visit_count": _safe_int(visit.get("count"), 1, 1) if isinstance(visit, dict) else 1,
                    "familiar": familiar,
                    "expires_at": check_now + _ANONYMOUS_AREA_PENDING_TTL_SECONDS,
                }
                changed = True
            store.pop(user_id, None)

        if not token:
            finish_previous()
            return changed
        if previous_token and previous_token != token:
            finish_previous()
            current = {}
            previous_token = ""
        if previous_token and last_seen > 0 and check_now - last_seen > _ANONYMOUS_AREA_STABLE_GAP_SECONDS:
            finish_previous()
            current = {}
            previous_token = ""
        if not previous_token:
            started_at = check_now
            current = {"token": token, "started_at": started_at, "last_seen_at": check_now}
            store[user_id] = current
            visits = self._anonymous_area_visit_records(user)
            visit = next((item for item in visits if _single_line(item.get("token"), 40) == token), None)
            if not isinstance(visit, dict) or check_now - _safe_float(visit.get("last_visit_at"), 0.0) > _ANONYMOUS_AREA_VISIT_GAP_SECONDS:
                if not isinstance(visit, dict):
                    visit = {"token": token, "count": 0, "first_visit_at": check_now}
                    visits.append(visit)
                visit["count"] = min(20, _safe_int(visit.get("count"), 0, 0) + 1)
                visit["last_visit_at"] = check_now
                user["mobile_anonymous_area_visits"] = visits[-8:]
                changed = True
            return changed
        current["last_seen_at"] = check_now
        return changed

    def _pick_mobile_anonymous_area_event(
        self,
        user: dict[str, Any],
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Turn a completed anonymous-area visit into a delayed, low-pressure thought."""
        if not isinstance(user, dict) or self._private_user_role(user) == "friend":
            return None
        check_now = _now_ts() if now is None else float(now)
        if not self._mobile_location_humanization_budget_available(user, now=check_now):
            return None
        pending = user.get("mobile_anonymous_area_pending")
        if not isinstance(pending, dict) or _safe_float(pending.get("expires_at"), 0.0) <= check_now:
            if isinstance(pending, dict) and pending:
                user["mobile_anonymous_area_pending"] = {}
            return None
        candidate_at = _safe_float(pending.get("candidate_at"), 0.0)
        if candidate_at > 0 and check_now - candidate_at < 24 * 3600:
            return None
        familiar = bool(pending.get("familiar"))
        dwell_minutes = _safe_int(pending.get("dwell_minutes"), 0, 0, 24 * 60)
        if familiar:
            return {
                "date": _today_key(),
                "window": self._window_from_delay_minutes(20, width_minutes=70),
                "reason": "anonymous_area_familiarity",
                "action": "message",
                "why": "用户最近几次在相似的未命名区域停留，离开后自然产生一点熟悉感",
                "topic": "最近好像有个常去的地方",
                "motive": "不是想查问位置，只是最近几次都想起对方似乎有个常去的地方，想轻轻聊起",
                "scene": "用户离开一个最近重复到访的匿名区域后",
                "tone": "像聊天时忽然注意到，不追问地点名称",
                "impulse": "先分享一点模糊的熟悉感，把命名权留给用户",
                "_scheduled_ts": check_now + random.uniform(12 * 60, 42 * 60),
                "context_key": "anonymous_area_context",
                "context": {"visit_count": _safe_int(pending.get("visit_count"), 3, 1), "after_departure": True},
                "origin_event_id": f"anonymous-area-familiarity:{pending.get('token')}:{int(pending.get('left_at') or check_now)}",
                "followup_kind": "anonymous_area_familiarity",
            }
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(20, width_minutes=70),
            "reason": "anonymous_area_dwell",
            "action": "message",
            "why": "用户在未命名区域稳定停留了一段时间，离开后想用不打扰的方式关心一下",
            "topic": "刚才在外面待了挺久",
            "motive": "刚才好像在外面待了挺久，离开一会儿后想轻轻问问今天还顺不顺",
            "scene": "用户离开一个未命名区域后的回程余韵",
            "tone": "轻一点，不暴露位置感知，不追问具体地点",
            "impulse": "先关心感受，不把位置本身说成话题",
            "_scheduled_ts": check_now + random.uniform(12 * 60, 42 * 60),
            "context_key": "anonymous_area_context",
            "context": {"dwell_minutes": dwell_minutes, "after_departure": True},
            "origin_event_id": f"anonymous-area-dwell:{pending.get('token')}:{int(pending.get('left_at') or check_now)}",
            "followup_kind": "anonymous_area_dwell",
        }

    async def _mobile_location_watch_once(
        self,
        *,
        now: float | None = None,
        user_ids: set[str] | None = None,
    ) -> bool:
        api_getter = getattr(self, "_reality_companion_api", None)
        if callable(api_getter) and api_getter() is None:
            return False
        scene_getter = getattr(self, "_mobile_user_proactive_scene", None)
        user_getter = getattr(self, "_get_user", None)
        if not callable(scene_getter):
            return False
        check_now = _now_ts() if now is None else now
        triggered = False
        changed = False
        watched_user_ids = self._mobile_location_watch_user_ids()
        if user_ids is not None:
            watched_user_ids = [user_id for user_id in watched_user_ids if user_id in user_ids]
        for user_id in watched_user_ids:
            users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
            user = users.get(user_id) if isinstance(users, dict) else None
            if not isinstance(user, dict) and callable(user_getter):
                try:
                    user = user_getter(user_id)
                except Exception:
                    user = None
            if not isinstance(user, dict):
                continue
            try:
                scene = scene_getter(user, now=check_now)
            except TypeError:
                scene = scene_getter(user)
            except Exception:
                continue
            anonymous_changed = self._observe_mobile_anonymous_area(user, scene, now=check_now)
            changed = changed or anonymous_changed
            transition_key = _single_line(scene.get("transition_key"), 80) if isinstance(scene, dict) else ""
            if not transition_key:
                user["mobile_location_watch_initialized"] = True
                user["mobile_location_watch_pending_key"] = ""
                user["mobile_location_watch_pending_count"] = 0
                continue
            previous_key = _single_line(user.get("mobile_location_watch_transition_key"), 80)
            user["mobile_location_watch_transition_key"] = transition_key
            changed = changed or previous_key != transition_key
            if not bool(user.get("mobile_location_watch_initialized")):
                user["mobile_location_watch_initialized"] = True
                user["mobile_location_watch_pending_key"] = transition_key
                user["mobile_location_watch_pending_count"] = 0
                user["mobile_location_watch_triggered_key"] = transition_key
                continue
            pending_key = _single_line(user.get("mobile_location_watch_pending_key"), 80)
            pending_count = _safe_int(user.get("mobile_location_watch_pending_count"), 0, 0)
            if pending_key != transition_key:
                pending_key = transition_key
                pending_count = 1
            else:
                pending_count += 1
            user["mobile_location_watch_pending_key"] = pending_key
            user["mobile_location_watch_pending_count"] = pending_count
            already_triggered = _single_line(user.get("mobile_location_watch_triggered_key"), 80) == transition_key
            # The Android client already requires consecutive stable fixes before
            # reporting a confirmed place. One new semantic transition is therefore
            # enough to wake planning; waiting for a duplicate upload can lose the
            # event when foreground sharing is closed shortly after arrival.
            if pending_count >= 1 and not already_triggered and bool(scene.get("recent_transition")):
                user["mobile_location_priority_key"] = transition_key
                user["mobile_location_priority_until"] = check_now + 180
                user["mobile_location_watch_triggered_key"] = transition_key
                triggered = True
        if changed:
            saver = getattr(self, "_schedule_data_save", None)
            if callable(saver):
                saver(sections={"users"}, delay=0.5)
        if triggered:
            kicker = getattr(self, "_kick_proactive_loop_once", None)
            if callable(kicker):
                await kicker()
        return triggered

    async def _handle_mobile_location_update(self, user_id: Any) -> dict[str, Any]:
        """Process one gateway location event without trusting it as a send command."""
        normalized = _single_line(user_id, 120)
        if not normalized:
            return {"handled": False, "reason": "user_missing"}
        try:
            triggered = await self._mobile_location_watch_once(
                now=_now_ts(),
                user_ids={normalized},
            )
        except Exception as exc:
            logger.debug("[PrivateCompanion] 手机位置事件处理暂时失败: %s", _single_line(exc, 160))
            return {"handled": False, "reason": "watch_failed"}
        return {"handled": True, "triggered": bool(triggered)}

    async def _mobile_location_watch_loop(self):
        while not self._stop_event.is_set():
            try:
                await self._mobile_location_watch_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("[PrivateCompanion] 移动位置主动监视暂时失败: %s", _single_line(exc, 160))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                continue

    async def _kick_proactive_loop_once(self) -> None:
        try:
            await self._run_scheduler_cycle(immediate=True)
        except Exception as e:
            logger.warning(f"[PrivateCompanion] 主动链即时唤醒失败: {e}", exc_info=True)

    def _next_scheduler_timeout(self) -> float:
        active_getter = getattr(self, "_active_persona_scope", None)
        active = str(active_getter() if callable(active_getter) else "").strip()
        persona_getter = getattr(self, "_scheduler_persona_ids", None)
        persona_ids = list(persona_getter() if callable(persona_getter) else [""])
        timeout_getter = getattr(self, "_next_scheduler_timeout_for_active_persona", None)

        def next_for_active_persona() -> float:
            if callable(timeout_getter):
                return float(timeout_getter())
            return float(ProactiveMixin._next_scheduler_timeout_for_active_persona(self))

        if active or persona_ids == [""]:
            return next_for_active_persona()
        timeouts: list[float] = []
        activator = getattr(self, "_activate_persona_id", None)
        deactivator = getattr(self, "_deactivate_persona_for_event", None)
        for persona_id in persona_ids:
            token = activator(persona_id) if callable(activator) else None
            try:
                timeouts.append(next_for_active_persona())
            finally:
                if token is not None and callable(deactivator):
                    deactivator(token)
        interval = _safe_float(_proactive_setting_value(self, "check_interval_seconds", 60), 60, 1.0)
        return min(timeouts) if timeouts else max(30.0, interval)

    def _next_scheduler_timeout_for_active_persona(self) -> float:
        base = max(30.0, _safe_float(_proactive_setting_value(self, "check_interval_seconds", 60), 60, 1.0))
        now = _now_ts()
        nearest_due_in: float | None = None
        users = self.data.get("users", {})
        if isinstance(users, dict):
            for raw_user in users.values():
                if not isinstance(raw_user, dict):
                    continue
                if not raw_user.get("umo"):
                    continue
                next_at = _safe_float(raw_user.get("next_proactive_at"), 0)
                due_times = [next_at]
                if bool(_proactive_setting_value(self, "enable_goodnight_screen_check", False)):
                    due_times.append(_safe_float(raw_user.get("goodnight_screen_check_due_at"), 0))
                for due_at in due_times:
                    if due_at <= 0:
                        continue
                    due_in = max(0.0, due_at - now)
                    if nearest_due_in is None or due_in < nearest_due_in:
                        nearest_due_in = due_in

        if nearest_due_in is None:
            detail_due_in = self._next_detail_due_in_seconds(now)
            if detail_due_in is not None:
                nearest_due_in = detail_due_in

        memo_due_getter = getattr(self, "_next_memo_due_in_seconds", None)
        memo_due_in = memo_due_getter(now) if callable(memo_due_getter) else None
        if memo_due_in is not None and (nearest_due_in is None or memo_due_in < nearest_due_in):
            nearest_due_in = memo_due_in
        elif bool(_proactive_setting_value(self, "enable_detail_enhancement", True)):
            detail_due_in = self._next_detail_due_in_seconds(now)
            if detail_due_in is not None and detail_due_in < nearest_due_in:
                nearest_due_in = detail_due_in

        diary_due_getter = getattr(self, "_next_daily_diary_due_in_seconds", None)
        diary_due_in = diary_due_getter(now) if callable(diary_due_getter) else None
        if diary_due_in is not None and (nearest_due_in is None or diary_due_in < nearest_due_in):
            nearest_due_in = diary_due_in

        review_due_getter = getattr(self, "_next_daily_review_due_in_seconds", None)
        review_due_in = review_due_getter(now) if callable(review_due_getter) else None
        if review_due_in is not None and (nearest_due_in is None or review_due_in < nearest_due_in):
            nearest_due_in = review_due_in

        if nearest_due_in is None:
            return max(35.0, min(base, random.uniform(base * 0.55, base * 0.95)))
        if nearest_due_in <= 20:
            return max(3.0, nearest_due_in + random.uniform(0.8, 3.2))
        if nearest_due_in <= 90:
            return max(8.0, nearest_due_in * random.uniform(0.35, 0.7))
        if nearest_due_in <= 6 * 60:
            return max(20.0, min(base * 0.5, nearest_due_in * random.uniform(0.18, 0.42)))
        return max(35.0, min(base, random.uniform(base * 0.55, base * 0.95)))
