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

class ProactiveMixin:
    """主动消息调度"""

    _PROACTIVE_INTENSITY_PRESETS: dict[str, dict[str, Any]] = {
        "off": {
            "label": "关闭预设",
            "description": "沿用手动配置，不覆盖任何主动频率参数。",
            "effects": {},
        },
        "balanced": {
            "label": "标准偏主动",
            "description": "略微提高主动触达，仍保持低打扰边界。",
            "effects": {
                "max_daily_messages": 8,
                "idle_minutes": 45,
                "min_interval_minutes": 90,
                "unanswered_slowdown_start": 2,
                "unanswered_max_interval_multiplier": 1.8,
                "friend_unanswered_max_cooldown_hours": 36,
                "delay_factor": 0.82,
                "proactive_persona_judge_send_threshold": 56,
                "proactive_review_strength": "lenient",
                "group_wakeup_cooldown_seconds": 60,
                "group_high_intensity_cooldown_seconds": 120,
                "group_interject_min_interval_minutes": 120,
                "group_interject_max_daily": 3,
            },
        },
        "high_private": {
            "label": "私聊高频",
            "description": "明显提高私聊主动频率，适合希望 Bot 经常来找的用户。",
            "effects": {
                "max_daily_messages": 12,
                "idle_minutes": 20,
                "min_interval_minutes": 35,
                "unanswered_slowdown_start": 3,
                "unanswered_max_interval_multiplier": 1.35,
                "friend_unanswered_max_cooldown_hours": 18,
                "delay_factor": 0.55,
                "proactive_persona_judge_send_threshold": 48,
                "proactive_review_strength": "lenient",
                "group_wakeup_cooldown_seconds": 60,
                "group_high_intensity_cooldown_seconds": 120,
                "group_interject_min_interval_minutes": 120,
                "group_interject_max_daily": 3,
            },
        },
        "high_group": {
            "label": "群聊活跃",
            "description": "降低群聊唤醒和插话冷却，让 Bot 更容易参与群聊。",
            "effects": {
                "max_daily_messages": 8,
                "idle_minutes": 45,
                "min_interval_minutes": 90,
                "unanswered_slowdown_start": 2,
                "unanswered_max_interval_multiplier": 1.8,
                "friend_unanswered_max_cooldown_hours": 36,
                "delay_factor": 0.8,
                "proactive_persona_judge_send_threshold": 55,
                "proactive_review_strength": "lenient",
                "group_wakeup_cooldown_seconds": 35,
                "group_high_intensity_cooldown_seconds": 75,
                "group_interject_min_interval_minutes": 45,
                "group_interject_max_daily": 8,
            },
        },
        "live": {
            "label": "在线陪伴",
            "description": "短间隔高频陪伴感；仍会尊重免打扰、休息、拒绝和隐私闸门。",
            "effects": {
                "max_daily_messages": 12,
                "idle_minutes": 10,
                "min_interval_minutes": 20,
                "unanswered_slowdown_start": 4,
                "unanswered_max_interval_multiplier": 1.2,
                "friend_unanswered_max_cooldown_hours": 12,
                "delay_factor": 0.38,
                "proactive_persona_judge_send_threshold": 44,
                "proactive_review_strength": "lenient",
                "group_wakeup_cooldown_seconds": 20,
                "group_high_intensity_cooldown_seconds": 45,
                "group_interject_min_interval_minutes": 20,
                "group_interject_max_daily": 12,
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
            getattr(self, "proactive_intensity_preset", "off")
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
            _safe_int(getattr(self, "group_wakeup_cooldown_seconds", 90), 90, 0, 3600),
            minimum=0,
            maximum=3600,
        )

    def _effective_group_high_intensity_cooldown_seconds(self) -> int:
        return self._effective_proactive_int(
            "group_high_intensity_cooldown_seconds",
            _safe_int(getattr(self, "group_high_intensity_cooldown_seconds", 150), 150, 30, 1800),
            minimum=30,
            maximum=1800,
        )

    def _effective_group_interject_min_interval_minutes(self) -> int:
        return self._effective_proactive_int(
            "group_interject_min_interval_minutes",
            _safe_int(getattr(self, "group_interject_min_interval_minutes", 180), 180, 10, 1440),
            minimum=10,
            maximum=1440,
        )

    def _effective_group_interject_max_daily(self) -> int:
        return self._effective_proactive_int(
            "group_interject_max_daily",
            _safe_int(getattr(self, "group_interject_max_daily", 2), 2, 0, 12),
            minimum=0,
            maximum=12,
        )

    def _effective_proactive_persona_judge_send_threshold(self) -> int:
        return self._effective_proactive_int(
            "proactive_persona_judge_send_threshold",
            _safe_int(getattr(self, "proactive_persona_judge_send_threshold", 62), 62, 0, 100),
            minimum=0,
            maximum=100,
        )

    def _effective_proactive_review_strength(self) -> str:
        value = str(self._proactive_intensity_effect("proactive_review_strength", "") or "").strip().lower()
        if value in {"lenient", "balanced", "strict"}:
            return value
        configured = str(getattr(self, "proactive_review_strength", "lenient") or "lenient").strip().lower()
        return configured if configured in {"lenient", "balanced", "strict"} else "lenient"

    def _configured_target_ids(self) -> list[str]:
        raw = self.target_user_ids
        if isinstance(raw, str):
            parts = re.split(r"[,\s,、;；]+", raw)
        elif isinstance(raw, list):
            parts = raw
        else:
            parts = []
        ids = []
        for part in parts:
            user_id = str(part).strip()
            if user_id and user_id.isdigit() and not self._is_bot_self_user_id(user_id) and user_id not in ids:
                ids.append(user_id)
        return ids

    def _user_enabled_for_proactive(self, user_id: str, user: dict[str, Any] | None = None) -> bool:
        if not isinstance(user, dict):
            return False
        if user.get("enabled") is False or user.get("manual_disabled"):
            return False
        return self._is_target_private_user(user_id, user)

    def _default_private_umo_for_user_id(self, user_id: str) -> str:
        user_id = str(user_id or "").strip()
        if not user_id.isdigit() or self._is_bot_self_user_id(user_id):
            return ""
        platform = _single_line(getattr(self, "target_platform", ""), 40) or "aiocqhttp"
        return f"{platform}:FriendMessage:{user_id}"

    def _private_delivery_user_id_for(self, user_id: str) -> str:
        canonical = self._canonical_private_user_id(str(user_id or "").strip())
        aliases = getattr(self, "private_user_delivery_aliases", {}) or {}
        target = str(aliases.get(canonical) or "").strip()
        if target and target.isdigit() and not self._is_bot_self_user_id(target):
            return target
        return canonical

    def _private_delivery_umo_for_user_id(self, user_id: str) -> str:
        return self._default_private_umo_for_user_id(self._private_delivery_user_id_for(user_id))

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
        delivery_id = self._private_delivery_user_id_for(user_id)
        if delivery_id and delivery_id != user_id:
            delivery_umo = self._private_delivery_umo_for_user_id(user_id)
            user["umo"] = delivery_umo
            return
        if self._private_umo_matches_user_id(clean_umo, user_id):
            user["umo"] = clean_umo

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
        if override is not None:
            return override
        max_daily_messages = self._runtime_max_daily_messages()
        if self._private_user_role(user) == "friend":
            return min(max_daily_messages, 2) if max_daily_messages > 0 else 0
        return max(0, max_daily_messages)

    def _runtime_max_daily_messages(self) -> int:
        runtime_value = _safe_int(getattr(self, "max_daily_messages", 8), 8, 0, 12)
        if runtime_value > 0:
            return self._effective_proactive_int("max_daily_messages", runtime_value, minimum=0, maximum=12)
        config = getattr(self, "config", None)
        getter = getattr(config, "get", None)
        if callable(getter):
            try:
                configured_value = _safe_int(getter("max_daily_messages", runtime_value), runtime_value, 0, 12)
                if configured_value > 0:
                    self.max_daily_messages = configured_value
                    return self._effective_proactive_int("max_daily_messages", configured_value, minimum=0, maximum=12)
            except Exception:
                pass
        return runtime_value

    def _format_daily_limit_disabled_reason(self, user: dict[str, Any]) -> str:
        override = user.get("proactive_daily_limit", -1) if isinstance(user, dict) else -1
        runtime_value = _safe_int(getattr(self, "max_daily_messages", 0), 0, 0, 12)
        config_value = runtime_value
        config = getattr(self, "config", None)
        getter = getattr(config, "get", None)
        if callable(getter):
            try:
                config_value = _safe_int(getter("max_daily_messages", runtime_value), runtime_value, 0, 12)
            except Exception:
                config_value = runtime_value
        return f"每日上限为 0（用户覆盖={override}，运行中全局={runtime_value}，配置全局={config_value}）"

    def _effective_user_idle_minutes(self, user: dict[str, Any]) -> int:
        override = self._user_profile_override_int(user, "proactive_idle_minutes")
        if override is not None:
            return override
        base_idle = self._effective_proactive_int(
            "idle_minutes",
            _safe_int(getattr(self, "idle_minutes", 60), 60, 0, 1440),
            minimum=0,
            maximum=1440,
        )
        if self._private_user_role(user) == "friend":
            return max(base_idle, 180)
        return max(0, base_idle)

    def _effective_user_greeting_idle_minutes(self, user: dict[str, Any]) -> int:
        if self._private_user_role(user) == "friend":
            return max(self.greeting_idle_minutes, 120)
        return max(0, self.greeting_idle_minutes)

    def _effective_user_min_interval_minutes(self, user: dict[str, Any]) -> int:
        override = self._user_profile_override_int(user, "proactive_min_interval_minutes")
        if override is not None:
            return override
        base_interval = self._effective_proactive_int(
            "min_interval_minutes",
            _safe_int(getattr(self, "min_interval_minutes", 120), 120, 0, 2880),
            minimum=0,
            maximum=2880,
        )
        if self._private_user_role(user) == "friend":
            return max(base_interval, 360)
        return max(0, base_interval)

    def _effective_user_photo_daily_limit(self, user: dict[str, Any] | None = None) -> int:
        if isinstance(user, dict):
            if self._private_user_role(user) == "friend":
                return 0
            override = self._user_profile_override_int(user, "photo_daily_limit")
            if override is not None:
                return override
        return max(0, self.photo_action_max_daily)

    def _effective_user_screen_peek_daily_limit(self, user: dict[str, Any] | None = None) -> int:
        if isinstance(user, dict):
            if self._private_user_role(user) == "friend":
                return 0
            override = self._user_profile_override_int(user, "screen_peek_daily_limit")
            if override is not None:
                return override
        return max(0, self.screen_peek_max_daily)

    def _effective_user_poke_daily_limit(self, user: dict[str, Any] | None = None) -> int:
        if isinstance(user, dict):
            override = self._user_profile_override_int(user, "poke_daily_limit")
            if override is not None:
                return override
            if self._private_user_role(user) == "friend":
                return 0
        return max(0, self.poke_action_max_times)

    def _format_private_user_boundary_hint(self, user: dict[str, Any]) -> str:
        role = self._private_user_role(user)
        labeler = getattr(self, "_private_user_role_label", None)
        label = labeler(role) if callable(labeler) else ("主人" if role == "owner" else "朋友")
        note = _single_line(user.get("proactive_boundary_note"), 180)
        if role == "owner":
            text = (
                "【当前私聊关系角色】\n"
                f"- 当前用户角色：{label}。\n"
                "- 可以延续人格中对主人的亲近、依赖和日常陪伴动机，但仍要尊重用户休息、忙碌和拒绝信号。"
            )
        else:
            text = (
                "【当前私聊关系角色】\n"
                f"- 当前用户角色：{label}。\n"
                "- 对方不是主人/恋人/专属陪伴目标。主动联系应像普通朋友：少量、具体、不过度亲密，不使用主人专属称呼、占有欲、撒娇索取或暧昧承诺。\n"
                "- 动机应以礼貌关心、共同话题、必要转告、轻分享为主；不要因为想贴近、想被哄、想确认对方在不在而频繁打扰。\n"
                "- 不给朋友使用窥屏或主动生图能力；不要把主人或其他私聊对象的图片、生活碎片复用给朋友。"
                "- 不对朋友发起本子/夹层阅读推荐、私密阅读分享、屏幕观察、群聊私下转述、私下创作分享或其他涉及隐私来源的主动。"
            )
        if note:
            text += f"\n- 用户级边界备注：{note}"
        return text

    def _friend_sensitive_proactive_reason(self, reason: Any) -> bool:
        normalized = str(reason or "").strip()
        return normalized in {
            "group_share",
            "jm_cosmos_share",
            "jm_cosmos_recommendation_request",
            "creative_share",
        }

    def _friend_sensitive_proactive_action(self, action: Any) -> bool:
        parts = {part.strip() for part in str(action or "").split("+") if part.strip()}
        return bool(parts & {"screen_peek", "photo_text", "jm_cosmos_read"})

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
                else "按朋友关系做一次克制的普通文字分享,不写成和朋友用户聊天或约见"
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
            "作为朋友想起对方可能正忙,只轻轻问一句,不要求立刻回复"
            if normalized_reason in {"", "check_in", "quiet_care", "state_share"}
            else "按朋友关系顺手补一句,只做普通文字关心,不涉及屏幕观察"
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

    @staticmethod
    def _friend_unanswered_should_remove_action(action: str) -> bool:
        parts = {part.strip() for part in str(action or "").split("+") if part.strip()}
        return bool(parts & {"poke", "voice", "photo_text", "screen_peek", "jm_cosmos_read"})

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
                "motive": "对方前面还没接话,作为朋友把主动放轻一点；只顺手留一句,不催、不追问、不要求立刻回复",
            }
        if level == 2:
            return {
                "reason": "quiet_care",
                "action": "message",
                "topic": "低压近况",
                "motive": "对方已经有一阵没有回应,这次只保留一条很短的低压关心；不贴近、不连问、不要求回复",
            }
        return {
            "reason": "quiet_care",
            "action": "message",
            "topic": "留出空间",
            "motive": "连续没有回应时,朋友关系要主动退一步；如果还要发,只放一小句很轻的话,说完就把空间留给对方",
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
            r"(?:朋友用户|朋友边界|朋友那边|朋友私聊)",
        )
        return any(re.search(pattern, cleaned) for pattern in patterns)

    def _sync_configured_targets(self):
        for user_id in self._configured_target_ids():
            user = self._get_user(user_id)
            if user.get("manual_disabled"):
                self._clear_pending_proactive_plan(user)
                continue
            user["enabled"] = True
            user["target_user"] = True
            user.setdefault("nickname", self.default_nickname)
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
                raw_user["enabled"] = False
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

    def _is_quiet_time(self) -> bool:
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", self.quiet_hours)
        if not match:
            return False
        sh, sm, eh, em = [int(part) for part in match.groups()]
        start = sh * 60 + sm
        end = eh * 60 + em
        now = datetime.now()
        current = now.hour * 60 + now.minute
        if start == end:
            return True
        if start < end:
            return start <= current < end
        return current >= start or current < end

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
        if user.get("proactive_daypart_day") != today:
            user["proactive_daypart_day"] = today
            user["proactive_daypart_counts"] = {}

    def _unanswered_slowdown_count(self, user: dict[str, Any]) -> int:
        ignored_streak = _safe_int(user.get("ignored_streak"), 0)
        start = self._effective_proactive_int(
            "unanswered_slowdown_start",
            _safe_int(getattr(self, "proactive_unanswered_slowdown_start", 1), 1, 1, 10),
            minimum=1,
            maximum=10,
        )
        return max(0, ignored_streak - start + 1)

    def _unanswered_interval_multiplier(self, user: dict[str, Any]) -> float:
        active_count = self._unanswered_slowdown_count(user)
        max_multiplier = self._effective_proactive_float(
            "unanswered_max_interval_multiplier",
            max(1.0, _safe_float(getattr(self, "proactive_unanswered_max_interval_multiplier", 2.2), 2.2, 1.0)),
            minimum=1.0,
            maximum=8.0,
        )
        return min(max_multiplier, 1.0 + active_count * 0.35)

    def _effective_min_interval_seconds(self, user: dict[str, Any]) -> int:
        multiplier = self._unanswered_interval_multiplier(user)
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
        }

    def _relationship_proactive_temperature(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        check_now = _now_ts() if now is None else now
        score = 0.54
        reasons: list[str] = []
        relationship_score = _safe_int(user.get("relationship_score"), 0, -100, 300)
        if relationship_score:
            score += max(-0.08, min(0.14, relationship_score / 500.0))
            reasons.append(f"关系分{relationship_score}")
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
        rel_state = user.get("relationship_state")
        mode = str(rel_state.get("mode") or "") if isinstance(rel_state, dict) else ""
        if mode == "attached":
            score += 0.08
            reasons.append("关系贴近")
        elif mode == "careful":
            score -= 0.08
            reasons.append("关系谨慎")
        elif mode in {"hurt", "refusing", "backoff"}:
            score -= 0.22
            reasons.append(f"关系{mode}")
        if self._private_user_role(user) == "friend":
            score -= 0.04
            reasons.append("朋友边界")
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
            "detail": "；".join(reasons[:5]) or "互动平稳",
        }

    def _proactive_inner_readiness(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        drive = self._bot_proactive_drive(user, now=now)
        temperature = self._relationship_proactive_temperature(user, now=now)
        score = _safe_float(drive.get("score"), 0.55) * 0.48 + _safe_float(temperature.get("score"), 0.55) * 0.52
        score = max(0.05, min(1.0, score))
        return {
            "score": score,
            "label": f"{drive.get('label')}/{temperature.get('label')}",
            "detail": f"状态:{drive.get('detail')}; 关系:{temperature.get('detail')}",
            "drive": drive,
            "temperature": temperature,
        }

    def _soft_daily_target(self, user: dict[str, Any]) -> float:
        daily_limit = self._effective_user_daily_limit(user)
        if daily_limit <= 0:
            return 0.0
        state = self.data.get("daily_state", {})
        important_dates = self._get_relevant_important_dates()
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        active_conditions = state.get("conditions", []) if isinstance(state, dict) else []
        ratio = 0.68
        if energy > 80:
            ratio += 0.06
        elif energy < 40:
            ratio -= 0.02
        if isinstance(active_conditions, list) and active_conditions:
            ratio += min(0.1, len(active_conditions) * 0.03)
        if important_dates:
            ratio += 0.1 if _safe_int(important_dates[0].get("_days_until"), 0) == 0 else 0.05
        ratio = max(0.45, min(0.95, ratio))
        if daily_limit == 1:
            ratio = max(ratio, 0.75)
        return max(0.6, daily_limit * ratio)

    def _daily_intensity_factor(self, user: dict[str, Any]) -> float:
        daily_limit = self._effective_user_daily_limit(user)
        if daily_limit <= 0:
            return 0.0
        sent_today = _safe_int(user.get("sent_today"), 0)
        soft_target = self._soft_daily_target(user)
        capacity_factor = min(1.35, 0.9 + daily_limit * 0.08)
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
            pressure = 0.5
        readiness = self._proactive_inner_readiness(user)
        inner_factor = 0.74 + _safe_float(readiness.get("score"), 0.55) * 0.55
        return max(0.25, min(1.5, capacity_factor * pressure * inner_factor))

    def _fallback_proactive_delay_hours(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> tuple[float, float]:
        now_dt = self._environment_fromtimestamp(now or _now_ts())
        if self._private_user_role(user) == "friend":
            spread_delay = self._friend_proactive_spread_delay_hours(user, now=now_dt.timestamp())
            if spread_delay is not None:
                return spread_delay
        sent_today = _safe_int(user.get("sent_today"), 0)
        remaining_target = max(1, math.ceil(max(0.0, self._soft_daily_target(user) - sent_today)))
        readiness_score = _safe_float(self._proactive_inner_readiness(user, now=now_dt.timestamp()).get("score"), 0.55)
        if readiness_score < 0.38:
            return (2.5, 6.0) if self._private_user_role(user) != "friend" else (8.0, 16.0)
        counts = self._today_proactive_daypart_counts(user)
        current_bucket = self._proactive_daypart_bucket_for_minute(now_dt.hour * 60 + now_dt.minute)
        if current_bucket == "late_night" and _safe_int(counts.get("late_night"), 0, 0) >= 1:
            return (7.5, 10.5)
        if current_bucket == "evening" and _safe_int(counts.get("evening"), 0, 0) >= 1 and remaining_target <= 2:
            return (3.0, 5.0)

        if now_dt.hour < 12:
            if remaining_target >= 4:
                return (0.45, 1.4)
            if remaining_target >= 3:
                return (0.75, 2.0)
            if remaining_target >= 2:
                return (1.0, 2.8)
            return (1.6, 4.2)
        if now_dt.hour < 18:
            if remaining_target >= 3:
                return (0.55, 1.8)
            if remaining_target >= 2:
                return (0.9, 2.6)
            return (1.8, 4.5)
        if remaining_target >= 2:
            return (0.5, 1.6)
        return (0.9, 2.4)

    def _friend_proactive_spread_delay_hours(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> tuple[float, float] | None:
        if self._private_user_role(user) != "friend":
            return None
        now_dt = self._environment_fromtimestamp(now or _now_ts())
        sent_today = _safe_int(user.get("sent_today"), 0)
        daily_limit = self._effective_user_daily_limit(user)
        if daily_limit <= 1 or sent_today <= 0:
            return None
        ignored_slowdown = self._unanswered_slowdown_count(user)
        max_cooldown = self._effective_proactive_float(
            "friend_unanswered_max_cooldown_hours",
            max(1.0, _safe_float(getattr(self, "friend_unanswered_max_cooldown_hours", 60.0), 60.0, 1.0)),
            minimum=1.0,
            maximum=168.0,
        )

        def cap_delay(delay: tuple[float, float]) -> tuple[float, float]:
            low, high = delay
            high = min(max_cooldown, high)
            low = min(low, max(1.0, high))
            return (low, high)

        if ignored_slowdown >= 4:
            return cap_delay((36.0, 60.0))
        if ignored_slowdown >= 3:
            return cap_delay((20.0, 36.0))
        if ignored_slowdown >= 2:
            return cap_delay((12.0, 24.0))
        minute = now_dt.hour * 60 + now_dt.minute
        if sent_today <= 1:
            if ignored_slowdown >= 1:
                return cap_delay((8.0, 14.0))
            if minute < 14 * 60:
                return self._delay_hours_until_local_window(now_dt, 14 * 60, 17 * 60)
            if minute < 18 * 60:
                return self._delay_hours_until_local_window(now_dt, 19 * 60, 21 * 60 + 20)
            return (6.0, 11.0)
        if sent_today <= 2 and daily_limit >= 3:
            if ignored_slowdown >= 1:
                return cap_delay((10.0, 18.0))
            if minute < 18 * 60 + 30:
                return self._delay_hours_until_local_window(now_dt, 18 * 60 + 40, 21 * 60 + 20)
            return (8.0, 14.0)
        return (10.0, 18.0)

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
        if not bool(getattr(self, "enable_emotion_simulation", True)):
            return ""
        rel_state = user.get("relationship_state")
        if not isinstance(rel_state, dict):
            return ""
        check_now = _now_ts() if now is None else now
        mode = str(rel_state.get("mode") or "")
        if mode in {"hurt", "refusing"} and _safe_float(rel_state.get("hurt_until"), 0) <= check_now:
            return ""
        if mode in {"hurt", "refusing", "attached"}:
            return mode
        return ""

    def _current_relationship_gate_mode(self, user: dict[str, Any], *, now: float | None = None) -> str:
        rel_state = user.get("relationship_state")
        if not isinstance(rel_state, dict):
            return ""
        check_now = _now_ts() if now is None else now
        mode = str(rel_state.get("mode") or "")
        if mode == "backoff" and bool(getattr(self, "enable_relationship_state_machine", True)):
            return "backoff" if _safe_float(rel_state.get("backoff_until"), 0) > check_now else ""
        if mode == "careful" and bool(getattr(self, "enable_relationship_state_machine", True)):
            return "careful"
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
        return bool(parts & {"poke", "voice", "photo_text", "screen_peek", "jm_cosmos_read"})

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
                result["motive"] = motive or "刚刚有个轻轻的小念头,想自然分享一下"
                result["topic"] = topic or "轻分享"
                result["note"] = "情绪 attached: 提高轻分享倾向"
            photo_probability = max(0.0, min(1.0, float(getattr(self, "proactive_photo_text_probability", 0.18))))
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
        rel_state = user.get("relationship_state") if isinstance(user.get("relationship_state"), dict) else {}
        hurt_until = _safe_float(rel_state.get("hurt_until"), 0)
        backoff_until = _safe_float(rel_state.get("backoff_until"), 0)
        gate_until = max(hurt_until, backoff_until)
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
                active_span, grace_span = self._proactive_impulse_default_window_seconds(user["planned_proactive_reason"])
                user["planned_proactive_best_until_at"] = user["next_proactive_at"] + active_span
                user["planned_proactive_expire_at"] = user["next_proactive_at"] + active_span + grace_span
                semantics = self._planned_proactive_semantics(user)
                user["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
                user["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
                user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
                user["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
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
                    saver()
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
                saver()
            return f"情绪/关系 {mode} 收敛中,亲密主动候选已清理"
        self._mark_planned_candidate_status(user, "deferred", f"情绪 {mode}: 主动候选延后")
        user["next_proactive_at"] = max(_safe_float(user.get("next_proactive_at"), 0), base_after)
        saver = getattr(self, "_schedule_data_save", None)
        if callable(saver):
            saver()
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
            saver()
        return note

    def _friend_proactive_scheduled_too_early(
        self,
        user: dict[str, Any],
        scheduled_at: float,
    ) -> bool:
        if self._private_user_role(user) != "friend" or scheduled_at <= 0:
            return False
        daily_limit = self._effective_user_daily_limit(user)
        sent_today = _safe_int(user.get("sent_today"), 0)
        if daily_limit <= 1 or sent_today <= 0:
            return False
        ignored_streak = _safe_int(user.get("ignored_streak"), 0)
        if ignored_streak >= 2:
            last_sent = _safe_float(user.get("last_sent"), 0)
            if last_sent > 0 and scheduled_at - last_sent < 12 * 3600:
                return True
        if ignored_streak >= 3:
            last_sent = _safe_float(user.get("last_sent"), 0)
            if last_sent > 0 and scheduled_at - last_sent < 20 * 3600:
                return True
        now_dt = self._environment_now()
        scheduled_dt = self._environment_fromtimestamp(scheduled_at)
        if scheduled_dt.date() != now_dt.date():
            return False
        minute = scheduled_dt.hour * 60 + scheduled_dt.minute
        if sent_today >= 2 and daily_limit >= 3:
            return minute < 18 * 60 + 30
        if sent_today >= 1:
            return minute < 14 * 60
        return False

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
        return {
            "allowed": bool(reasons),
            "reasons": reasons,
            "suggest_soft_reason": suggest_soft_reason,
        }

    def _queue_event_driven_proactive_impulses(
        self,
        user: dict[str, Any],
        *,
        now: float,
    ) -> int:
        rest_until = self._user_rest_silence_until(user, now=now)
        queued = 0
        event_sources = (
            ("pending_followup", self._pick_pending_followup_event(user, now)),
            ("daily_greeting", self._pick_daily_greeting_event(user, now)),
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
            candidate["reason"] = reason
            candidate["action"] = action
            candidate["topic"] = topic
            candidate["motive"] = motive
            impulse = self._candidate_to_impulse(user, candidate, source=source, now=now)
            if not isinstance(impulse, dict):
                continue
            if rest_until > now and _safe_float(impulse.get("window_start_at"), 0) < rest_until and source != "timer":
                shift = rest_until - _safe_float(impulse.get("window_start_at"), 0) + random.uniform(20 * 60, 90 * 60)
                impulse["window_start_at"] = _safe_float(impulse.get("window_start_at"), 0) + shift
                impulse["preferred_ts"] = _safe_float(impulse.get("preferred_ts"), 0) + shift
                impulse["best_until_at"] = _safe_float(impulse.get("best_until_at"), 0) + shift
                impulse["expire_at"] = _safe_float(impulse.get("expire_at"), 0) + shift
            self._queue_proactive_impulse(user, impulse)
            queued += 1
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
        scheduled = now + random.uniform(delay_hours[0] * 3600, delay_hours[1] * 3600)
        scheduled = self._move_timestamp_into_reason_window(scheduled, reason)
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
            scheduled = self._move_timestamp_into_reason_window(scheduled, reason)
        active_span, grace_span = self._proactive_impulse_default_window_seconds(reason)
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
        now = now or _now_ts()
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
        timer_event = self._get_active_llm_timer(user)
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
                active_span, grace_span = self._proactive_impulse_default_window_seconds(user["planned_proactive_reason"])
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

    def _user_rest_silence_until(self, user: dict[str, Any], *, now: float | None = None) -> float:
        check_now = _now_ts() if now is None else now
        rest_until = _safe_float(user.get("user_rest_until"), 0)
        if rest_until <= 0:
            return 0.0
        if rest_until <= check_now:
            user["user_rest_until"] = 0
            user["user_rest_reason"] = ""
            user["user_rest_set_at"] = 0
            return 0.0
        return rest_until

    @staticmethod
    def _user_rest_text_is_meta_discussion(cleaned: str) -> bool:
        if not cleaned:
            return False
        return bool(
            re.search(
                r"(?:关键词|关键字|正则|规则|命中|误判|拦截|挡了|工具|日志|之前对话|历史消息|提示词|注入|主动问候|主动消息|用户反馈|反馈|bug)",
                cleaned,
            )
            or re.search(r"(?:为什么|怎么|是否|会不会|是不是).{0,40}(?:晚安|睡|休息|别回|打扰)", cleaned)
        )

    @staticmethod
    def _user_rest_text_is_quoted_or_report(cleaned: str) -> bool:
        if not cleaned:
            return False
        return bool(
            re.search(r"(?:他说|她说|它说|bot说|模型说|原文|内容是|比如|例如|类似|这句|那句)", cleaned)
            or any(mark in cleaned for mark in ("“", "”", '"', "'"))
        )

    def _user_rest_signal_should_block_current_reply(self, text: str) -> bool:
        cleaned = _single_line(text, 260).lower()
        if not cleaned:
            return False
        if self._user_rest_text_is_meta_discussion(cleaned) or self._user_rest_text_is_quoted_or_report(cleaned):
            return False
        compact = re.sub(r"\s+", "", cleaned)
        no_reply_boundary = r"(?:了|啦|吧|我|这(?:个|条|句|段)(?:消息|话|话题|内容|问题)?|这(?:条)?消息|本条消息|消息|哈|噢|哦|$|[，。！？,.!?])"
        no_reply = re.search(
            r"(?:不用|不必|无需|别|不要|先别|暂时别|今晚别|今天别)(?:再)?(?:回(?:复)?|理我|搭理我|接话|说话|出声)"
            + no_reply_boundary,
            compact,
        )
        proactive_only = re.search(
            r"(?:别|不要|先别|暂时别|今晚别|今天别).{0,10}主动.{0,8}(?:打扰|吵|发消息|找我|回(?:复)?|理我|搭理我|接话|说话)",
            compact,
        )
        if proactive_only and not no_reply:
            return False
        hard_quiet = re.search(
            r"(?:别|不要|先别|暂时别|今晚别|今天别).{0,10}(?:打扰|吵我|叫我|主动|发消息|找我)"
            r"|(?:让我|叫我).{0,6}(?:安静|清静|静一静)"
            r"|(?:闭嘴|别说话|不要说话|安静点)",
            compact,
        )
        return bool(no_reply or hard_quiet)

    def _next_user_rest_morning_ts(self, *, now: float) -> float:
        timezone_name = _single_line(getattr(self, "environment_perception_timezone", ""), 64) or "Asia/Shanghai"
        try:
            tz = zoneinfo.ZoneInfo(timezone_name)
        except Exception:
            tz = zoneinfo.ZoneInfo("Asia/Shanghai")
        current = datetime.fromtimestamp(now, tz)
        target = current.replace(hour=8, minute=30, second=0, microsecond=0)
        if target.timestamp() <= now + 3600:
            target += timedelta(days=1)
        return max(target.timestamp(), now + 6 * 3600)

    def _detect_user_rest_silence_until(self, text: str, *, now: float | None = None) -> float:
        cleaned = _single_line(text, 260).lower()
        if not cleaned:
            return 0.0
        check_now = _now_ts() if now is None else now
        # Keyword/rule discussions should not become real proactive-message
        # silence. Otherwise a stray "我休息" in debugging text can block greetings.
        if self._user_rest_text_is_meta_discussion(cleaned):
            return 0.0
        quoted_or_report = self._user_rest_text_is_quoted_or_report(cleaned)
        cancel_pattern = (
            r"(?:我|俺|咱|人家).{0,10}(?:醒了|起床了|睡醒了|不睡了|回来了|可以聊)"
            r"|(?:睡醒了|起床了|不睡了|可以聊了|回来了)"
        )
        if re.search(cancel_pattern, cleaned):
            return -1.0
        if quoted_or_report:
            return 0.0
        no_reply_boundary = r"(?:了|啦|吧|我|这(?:个|条|句|段)(?:消息|话|话题|内容|问题)?|这(?:条)?消息|本条消息|消息|哈|噢|哦|$|[，。！？,.!?])"
        hard_quiet = re.search(
            r"(?:别|不要|先别|暂时别|今晚别|今天别).{0,10}(?:打扰|吵|主动|发消息|找我)"
            r"|(?:不用|不必|无需|别|不要|先别|暂时别|今晚别|今天别)(?:再)?(?:回(?:复)?|理我|搭理我|接话|说话|出声)"
            + no_reply_boundary,
            cleaned,
        )
        tomorrow = re.search(r"(?:明天|明早|早上)再(?:聊|说|回|看|找我)", cleaned)
        sleep = re.search(
            r"(?:晚安|睡觉去了|先睡了|去睡了|睡了哈|睡啦|我睡了|我先睡|我去睡|我要睡|我准备睡|我困了先睡|困死了先睡|补觉去了|我要补觉|先补觉)",
            cleaned,
        )
        nap = re.search(
            r"(?:我|俺|咱|人家).{0,10}(?:要|先|去|准备|现在|马上)?(?:午休|眯一会|歇会儿?|躺会儿?|休息一下|休息会儿?)",
            cleaned,
        )
        rest = re.search(
            r"(?:我|俺|咱|人家).{0,10}(?:要|先|去|准备|现在|马上)(?:休息(?:一下|会儿?|一会儿?)?|歇一下|躺一下|缓一会儿?)",
            cleaned,
        )
        if hard_quiet or tomorrow or sleep:
            return self._next_user_rest_morning_ts(now=check_now)
        if nap:
            return check_now + 90 * 60
        if rest:
            return check_now + 2 * 3600
        return 0.0

    def _apply_user_rest_silence_from_message(
        self,
        user: dict[str, Any],
        text: str,
        *,
        now: float | None = None,
    ) -> bool:
        check_now = _now_ts() if now is None else now
        rest_until = self._detect_user_rest_silence_until(text, now=check_now)
        if rest_until < 0:
            if _safe_float(user.get("user_rest_until"), 0) > check_now:
                user["user_rest_until"] = 0
                user["user_rest_reason"] = ""
                user["user_rest_set_at"] = 0
                logger.info("[PrivateCompanion] 用户休息静默已解除: user=%s", user.get("user_id") or user.get("id") or "")
                return True
            return False
        if rest_until <= check_now:
            return False
        user["user_rest_until"] = rest_until
        user["user_rest_reason"] = _single_line(text, 120)
        user["user_rest_set_at"] = check_now
        if str(user.get("planned_proactive_source") or "") != "timer":
            self._clear_pending_proactive_plan(user)
        logger.info(
            "[PrivateCompanion] 已记录用户休息静默: user=%s until=%s reason=%s",
            user.get("user_id") or user.get("id") or "",
            self._environment_fromtimestamp(rest_until).strftime("%m-%d %H:%M"),
            _single_line(text, 80),
        )
        return True

    def _promote_earlier_daily_greeting_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> bool:
        if not self.enable_daily_greetings:
            return False
        if str(user.get("planned_proactive_source") or "") == "timer":
            return False
        current_next = _safe_float(user.get("next_proactive_at"), 0)
        if current_next <= 0:
            return False
        now = now or _now_ts()
        event = self._pick_daily_greeting_event(user, now)
        if not isinstance(event, dict):
            return False
        reason = str(event.get("reason") or "")
        if not self._is_sticky_greeting_reason(reason):
            return False
        scheduled = self._timestamp_from_story_event(event, reason)
        if scheduled <= 0 or scheduled >= current_next - 60:
            return False
        action = str(event.get("action") or "message")
        motive = _single_line(event.get("motive"), 120) or self._choose_proactive_motive(
            reason,
            user,
            action=action,
            planned_event=event,
        )
        user["next_proactive_at"] = scheduled
        user["planned_proactive_reason"] = reason
        user["planned_proactive_action"] = action
        user["planned_proactive_source"] = "event"
        user["planned_proactive_motive"] = motive
        user["planned_proactive_topic"] = _single_line(event.get("topic"), 60)
        user["planned_proactive_impulse_id"] = ""
        user["planned_proactive_window_start_at"] = scheduled
        active_span, grace_span = self._proactive_impulse_default_window_seconds(reason)
        user["planned_proactive_best_until_at"] = scheduled + active_span
        user["planned_proactive_expire_at"] = scheduled + active_span + grace_span
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
        return True

    def _is_proactive_plan_stale(self, user: dict[str, Any], *, now: float | None = None) -> bool:
        next_at = _safe_float(user.get("next_proactive_at"), 0)
        if next_at <= 0:
            return False
        check_now = _now_ts() if now is None else now
        return check_now - next_at > self.max_proactive_plan_lag_minutes * 60

    def _clear_pending_proactive_plan(self, user: dict[str, Any]) -> None:
        user["next_proactive_at"] = 0
        user["planned_proactive_reason"] = ""
        user["planned_proactive_action"] = ""
        user["planned_proactive_source"] = ""
        user["planned_proactive_motive"] = ""
        user["planned_proactive_topic"] = ""
        user["planned_proactive_impulse_id"] = ""
        user["planned_proactive_window_start_at"] = 0
        user["planned_proactive_best_until_at"] = 0
        user["planned_proactive_expire_at"] = 0
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
        if label in {"日常状态", "今日日程", "当前细化", "日记", "创作推进"}:
            return 30 * 60
        return 5 * 60

    def _maintenance_task_blocked_by_failure(self, label: str, *, now: float | None = None) -> str:
        state = getattr(self, "_maintenance_failure_cooldowns", None)
        if not isinstance(state, dict):
            return ""
        item = state.get(label)
        if not isinstance(item, dict):
            return ""
        check_now = _now_ts() if now is None else now
        until = _safe_float(item.get("until"), 0, 0)
        if until <= check_now:
            state.pop(label, None)
            return ""
        error = _single_line(item.get("error"), 120)
        return f"{label} 失败冷却中（{self._format_elapsed(until - check_now)}后重试" + (f"，上次错误：{error}" if error else "") + "）"

    def _record_maintenance_task_failure(self, label: str, exc: Exception) -> None:
        state = getattr(self, "_maintenance_failure_cooldowns", None)
        if not isinstance(state, dict):
            state = {}
            self._maintenance_failure_cooldowns = state
        now = _now_ts()
        state[label] = {
            "until": now + self._maintenance_failure_cooldown_seconds(label),
            "error": _single_line(exc, 180),
            "failed_at": now,
        }

    def _clear_maintenance_task_failure(self, label: str) -> None:
        state = getattr(self, "_maintenance_failure_cooldowns", None)
        if isinstance(state, dict):
            state.pop(label, None)

    async def _scheduler_loop(self):
        while not self._stop_event.is_set():
            try:
                timeout = self._next_scheduler_timeout()
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=timeout
                )
            except asyncio.TimeoutError:
                await self._tick()
                for label, task_factory in (
                    ("日常状态", self._ensure_daily_state),
                    ("今日日程", self._ensure_daily_plan),
                    ("当前细化", self._ensure_detail_enhancement),
                    ("当前在线感", self._ensure_current_detail_presence_status),
                    ("日记", self._ensure_daily_diary),
                    ("每日穿搭", self._ensure_daily_outfit_photo),
                    ("创作推进", self._maybe_advance_creative_projects),
                    ("被动注入缓存", self._refresh_passive_injection_cache),
                ):
                    try:
                        if self._maintenance_task_blocked_by_failure(label):
                            continue
                        await task_factory()
                        self._clear_maintenance_task_failure(label)
                    except Exception as exc:
                        self._record_maintenance_task_failure(label, exc)
                        logger.warning("[PrivateCompanion] 主动循环维护步骤失败,已跳过: %s error=%s", label, _single_line(exc, 160))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[PrivateCompanion] 主动消息循环异常: {e}", exc_info=True)

    async def _kick_proactive_loop_once(self) -> None:
        try:
            await self._tick()
            for label, task_factory in (
                ("日常状态", self._ensure_daily_state),
                ("今日日程", self._ensure_daily_plan),
                ("当前细化", self._ensure_detail_enhancement),
                ("当前在线感", self._ensure_current_detail_presence_status),
                ("日记", self._ensure_daily_diary),
                ("每日穿搭", self._ensure_daily_outfit_photo),
                ("创作推进", self._maybe_advance_creative_projects),
                ("被动注入缓存", self._refresh_passive_injection_cache),
            ):
                try:
                    if self._maintenance_task_blocked_by_failure(label):
                        continue
                    await task_factory()
                    self._clear_maintenance_task_failure(label)
                except Exception as exc:
                    self._record_maintenance_task_failure(label, exc)
                    logger.warning("[PrivateCompanion] 主动链即时维护步骤失败,已跳过: %s error=%s", label, _single_line(exc, 160))
        except Exception as e:
            logger.warning(f"[PrivateCompanion] 主动链即时唤醒失败: {e}", exc_info=True)

    def _next_scheduler_timeout(self) -> float:
        base = max(30.0, float(self.check_interval_seconds))
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
                if next_at <= 0:
                    continue
                due_in = max(0.0, next_at - now)
                if nearest_due_in is None or due_in < nearest_due_in:
                    nearest_due_in = due_in

        if nearest_due_in is None:
            detail_due_in = self._next_detail_due_in_seconds(now)
            if detail_due_in is not None:
                nearest_due_in = detail_due_in
        elif self.enable_detail_enhancement:
            detail_due_in = self._next_detail_due_in_seconds(now)
            if detail_due_in is not None and detail_due_in < nearest_due_in:
                nearest_due_in = detail_due_in

        if nearest_due_in is None:
            return max(35.0, min(base, random.uniform(base * 0.55, base * 0.95)))
        if nearest_due_in <= 20:
            return max(3.0, nearest_due_in + random.uniform(0.8, 3.2))
        if nearest_due_in <= 90:
            return max(8.0, nearest_due_in * random.uniform(0.35, 0.7))
        if nearest_due_in <= 6 * 60:
            return max(20.0, min(base * 0.5, nearest_due_in * random.uniform(0.18, 0.42)))
        return max(35.0, min(base, random.uniform(base * 0.55, base * 0.95)))
