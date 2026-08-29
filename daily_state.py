# -*- coding: utf-8 -*-
"""
DailyStateMixin — 日程、状态、天气、日记、技能成长和计时器
"""
from __future__ import annotations

import asyncio
import ast
import base64
import gc
import hashlib
import html
import inspect
import importlib
import json
import math
import os
import random
import re
import sqlite3
import shutil
import sys
import time
import unicodedata
import uuid
import zoneinfo
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable
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
from astrbot.core.star.star_handler import EventType, star_handlers_registry
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .conversation_prompt_section import prompt_section

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
from .helpers import _date_key, _memory_archive_warning, _normalize_outbound_punctuation_flow, _normalize_photo_subject_owner, _now_ts, _path_text, _photo_subject_owner_prompt_label, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks, _today_key, normalize_legacy_tag_text
from .model_routing import CURRENT_MODEL_REPLACEMENT_SOURCES, find_route, scope_allows
from .persona_config import runtime_persona_setting
from .story_authority import story_legacy_sync_operation
from .conversation_injection_plan import (
    PLACEMENT_DYNAMIC_SYSTEM,
    PLACEMENT_TURN_TAIL,
    get_conversation_injection_plan,
)
from .domains.affect.affect_modulation import compose_affect_modulation
from .daily_state_tick import DailyStateTickMixin
from .memo_notes import memo_note_due_state, memo_note_sort_key, normalize_memo_note
from .agenda_contracts import normalize_plan_item
from .planning import (
    build_daily_plan_prompt,
    build_detail_enhancement_prompt,
    evaluate_detail_quality,
    format_plan_for_diary,
    generate_daily_plan,
    generate_detail_enhancement,
    get_schedule_planning_prompt,
    normalize_long_term_events,
    normalize_story_items,
    normalize_story_plan,
    pick_detail_segment,
)
from .logging_util import get_module_logger

logger = get_module_logger(__name__)


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

DEFAULT_PERSONA_PROMPT_FALLBACK = "未读取到 AstrBot 默认人格。请保持简洁、温和、有边界,不额外创造新身份。"


def _openmeteo_weather_description(code: Any) -> str:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "未知"
    if code == 0:
        return "晴天"
    if code == 1:
        return "少云"
    if code == 2:
        return "多云"
    if code == 3:
        return "阴天"
    if code in {45, 48}:
        return "雾"
    if 51 <= code <= 55:
        return "毛毛雨"
    if code in {56, 57}:
        return "冻毛毛雨"
    if 61 <= code <= 65:
        return "降雨"
    if code in {66, 67}:
        return "冻雨"
    if 71 <= code <= 77:
        return "降雪"
    if 80 <= code <= 82:
        return "阵雨"
    if code in {85, 86}:
        return "阵雪"
    if 95 <= code <= 99:
        return "雷暴"
    return "未知"


# 和风天气预警接口使用独立的 API Host，并支持 JWT 或 API Key 认证。
# 保留颜色等级的顺序，供缓存层和上层提示词按最低等级筛选；解析层
# 始终保留完整数据。
_QWEATHER_ALERT_COLOR_RANK = {
    "蓝": 0,
    "蓝色": 0,
    "blue": 0,
    "yellow": 1,
    "黄": 1,
    "黄色": 1,
    "orange": 2,
    "橙": 2,
    "橙色": 2,
    "red": 3,
    "红": 3,
    "红色": 3,
}
_QWEATHER_ALERT_SEVERITY_RANK = {
    "unknown": 0,
    "minor": 1,
    "moderate": 2,
    "severe": 3,
    "extreme": 4,
}


def _qweather_alert_text(value: Any, limit: int = 512) -> str:
    """Normalize a provider field without allowing multiline/oversized cache data."""

    text = _single_line(value, limit * 2)
    if not text:
        return ""
    return text[:limit]


def _qweather_alert_first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return ""


def _qweather_alert_string_list(value: Any, *, limit: int = 16, item_limit: int = 80) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if isinstance(item, dict):
            item = _qweather_alert_first(item, "code", "name", "type")
        text = _qweather_alert_text(item, item_limit)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _qweather_alert_color(value: Any) -> tuple[str, str]:
    """Return a human-readable color and its provider code."""

    code = ""
    name = ""
    if isinstance(value, dict):
        code = _qweather_alert_text(value.get("code"), 32).lower()
        name = _qweather_alert_text(value.get("name"), 32)
    else:
        name = _qweather_alert_text(value, 32)
        code = name.lower()
    # QWeather's current API returns color.code (blue/yellow/orange/red), while
    # older integrations and some compatible providers return Chinese labels.
    aliases = {
        "blue": "蓝色",
        "yellow": "黄色",
        "orange": "橙色",
        "red": "红色",
        "bluealert": "蓝色",
        "yellowalert": "黄色",
        "orangealert": "橙色",
        "redalert": "红色",
    }
    normalized_code = aliases.get(code, code)
    if normalized_code in {"蓝", "蓝色"}:
        name = "蓝色"
    elif normalized_code in {"黄", "黄色"}:
        name = "黄色"
    elif normalized_code in {"橙", "橙色"}:
        name = "橙色"
    elif normalized_code in {"红", "红色"}:
        name = "红色"
    elif not name:
        name = normalized_code
    return _qweather_alert_text(name, 32), _qweather_alert_text(code, 32)


def _qweather_alert_rank(value: Any) -> int:
    text = _qweather_alert_text(value, 32).strip().lower()
    if text in _QWEATHER_ALERT_COLOR_RANK:
        return _QWEATHER_ALERT_COLOR_RANK[text]
    # A severity value is useful for non-Chinese/global warning feeds.
    return _QWEATHER_ALERT_SEVERITY_RANK.get(text, 0)


LEGACY_DEFAULT_NEWS_SOURCES = "\\n".join(
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


class DailyStateMixin(DailyStateTickMixin):
    """日程、状态、天气、日记、技能成长和计时器"""

    def _save_daily_state_sections(self, sections: set[str]) -> None:
        saver = getattr(self, "_save_data_sync", None)
        if not callable(saver):
            return
        try:
            saver(sections=sections)
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc) or "sections" not in str(exc):
                raise
            saver()

    def _daily_generation_lock(self, attribute: str) -> asyncio.Lock:
        scope = self._daily_generation_scope()
        if scope:
            locks_attribute = f"{attribute}_by_scope"
            locks = getattr(self, locks_attribute, None)
            if not isinstance(locks, dict):
                locks = {}
                setattr(self, locks_attribute, locks)
            lock = locks.get(scope)
            if not isinstance(lock, asyncio.Lock):
                lock = asyncio.Lock()
                locks[scope] = lock
            return lock
        lock = getattr(self, attribute, None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            setattr(self, attribute, lock)
        return lock

    def _daily_generation_scope(self) -> str:
        getter = getattr(self, "_active_persona_scope", None)
        return str(getter() if callable(getter) else "").strip()

    def _daily_force_result_cache(self, attribute: str) -> dict[str, dict[str, Any]]:
        cache = getattr(self, attribute, None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, attribute, cache)
        return cache

    def _sync_detail_enhancement_day_locked(
        self,
        plan_date: Any,
        *,
        reset: bool = False,
    ) -> bool:
        """Keep live detail snapshots bound to the plan that owns them."""
        date_key = _single_line(plan_date, 16)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_key):
            return False
        enhanced = self.data.get("detail_enhanced_segments")
        changed = (
            bool(reset)
            or _single_line(self.data.get("detail_enhanced_day"), 16) != date_key
            or not isinstance(enhanced, dict)
        )
        if not changed:
            return False
        self.data["detail_enhanced_day"] = date_key
        self.data["detail_enhanced_segments"] = {}
        story = self.data.get("daily_story_plan")
        if bool(reset) or (
            isinstance(story, dict)
            and _single_line(story.get("date"), 16) not in {"", date_key}
        ):
            self.data["daily_story_plan"] = {}
        return True

    def _detail_enhanced_segments_for_plan_date(
        self,
        plan_date: Any,
        enhanced: Any = None,
        *,
        detail_day: Any = None,
    ) -> dict[str, Any]:
        date_key = _single_line(plan_date, 16)
        recorded_day = self.data.get("detail_enhanced_day") if detail_day is None else detail_day
        if not date_key or _single_line(recorded_day, 16) != date_key:
            return {}
        source = enhanced if isinstance(enhanced, dict) else self.data.get("detail_enhanced_segments")
        if not isinstance(source, dict):
            return {}
        current: dict[str, Any] = {}
        for raw_key, snapshot in source.items():
            key = str(raw_key or "")
            keyed = re.match(r"^(\d{4}-\d{2}-\d{2}):", key)
            if keyed and keyed.group(1) != date_key:
                continue
            if isinstance(snapshot, dict):
                current[key] = snapshot
        return current

    def _next_detail_due_in_seconds(self, now: float | None = None) -> float | None:
        if not runtime_persona_setting(self, "enable_detail_enhancement", False):
            return None
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return None
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            enhanced = {}
        segments = self._collect_detail_segments(plan, enhanced)
        if not segments:
            return None
        now_dt = self._environment_fromtimestamp(now or _now_ts())
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return None
        lead = max(0, _safe_int(runtime_persona_setting(self, "detail_enhancement_lead_minutes", 3), 3, 0))
        candidates: list[float] = []
        for segment in segments:
            start = _safe_int(segment.get("start"), 0)
            due_minute = max(0, start - lead)
            if due_minute <= now_minutes:
                return 0.0
            due_dt = datetime.combine(now_dt.date(), datetime.min.time(), tzinfo=now_dt.tzinfo) + timedelta(minutes=due_minute)
            candidates.append(max(0.0, due_dt.timestamp() - (now or _now_ts())))
        if not candidates:
            return None
        return min(candidates)

    async def _ensure_daily_plan(self, force: bool = False) -> dict[str, Any] | None:
        if not runtime_persona_setting(self, "enable_daily_plan", True) and not force:
            return None

        await self._ensure_daily_state(force=force)
        today = _today_key()
        async with self._data_lock:
            current_plan = self.data.setdefault("daily_plan", {})
            current_plan_date = _single_line(current_plan.get("date"), 16) if isinstance(current_plan, dict) else ""
            detail_day_changed = bool(
                current_plan_date
                and self._is_plan_date_active(current_plan_date)
                and self._sync_detail_enhancement_day_locked(current_plan_date)
            )
            known_users = [
                user for user in self.data.get("users", {}).values() if isinstance(user, dict) and user.get("umo")
            ]
            if not force and current_plan.get("date") == today:
                plan_changed = self._sanitize_daily_plan_inplace(current_plan)
                if plan_changed:
                    self._refresh_daily_state_location_from_plan(plan=current_plan)
                if plan_changed or detail_day_changed:
                    self._save_daily_state_sections(
                        sections={
                            "daily_plan",
                            "daily_state",
                            "detail_enhanced_day",
                            "detail_enhanced_segments",
                            "daily_story_plan",
                        }
                    )
                source = _single_line(current_plan.get("source"), 40).lower()
                retry_after = _safe_float(current_plan.get("retry_after"), 0.0)
                fallback_retry_due = source.startswith("fallback") and (
                    retry_after <= 0 or _now_ts() >= retry_after
                )
                if not fallback_retry_due:
                    return current_plan
            if (
                not force
                and current_plan.get("date") != today
                and self._is_plan_date_active(current_plan.get("date"))
            ):
                plan_changed = self._sanitize_daily_plan_inplace(current_plan)
                if plan_changed:
                    self._refresh_daily_state_location_from_plan(plan=current_plan)
                if plan_changed or detail_day_changed:
                    self._save_daily_state_sections(
                        sections={
                            "daily_plan",
                            "daily_state",
                            "detail_enhanced_day",
                            "detail_enhanced_segments",
                            "daily_story_plan",
                        }
                    )
                return current_plan
            active_persona = str(
                getattr(self, "_active_persona_scope", lambda: "")() or ""
            ).strip()
            primary_persona = str(
                getattr(self, "_primary_persona_id", lambda: "")() or ""
            ).strip()
            configured_empty_secondary = bool(
                getattr(self, "enable_multi_persona_mode", False)
                and active_persona
                and active_persona != primary_persona
                and callable(getattr(self, "_persona_config_exists", None))
                and self._persona_config_exists(active_persona)
            )
            if not force and not known_users and not configured_empty_secondary:
                return current_plan if current_plan.get("date") == today else None
            if not force and not self._is_daily_plan_due():
                if self._is_plan_date_active(current_plan.get("date")):
                    plan_changed = self._sanitize_daily_plan_inplace(current_plan)
                    if plan_changed:
                        self._refresh_daily_state_location_from_plan(plan=current_plan)
                    if plan_changed or detail_day_changed:
                        self._save_daily_state_sections(
                            sections={
                                "daily_plan",
                                "daily_state",
                                "detail_enhanced_day",
                                "detail_enhanced_segments",
                                "daily_story_plan",
                            }
                        )
                    return current_plan
                return None

        generation_lock = self._daily_generation_lock("_daily_plan_generation_lock")
        async with generation_lock:
            # The initial eligibility check intentionally happens before the
            # model call, but another caller may have generated the plan while
            # we were waiting. Re-check the shared plan inside the generation
            # lock so one day/persona only consumes one model request.
            if not force:
                async with self._data_lock:
                    current_plan = self.data.get("daily_plan")
                    if isinstance(current_plan, dict) and _single_line(current_plan.get("date"), 16) == today:
                        source = _single_line(current_plan.get("source"), 40).lower()
                        retry_after = _safe_float(current_plan.get("retry_after"), 0.0)
                        fallback_retry_due = source.startswith("fallback") and (
                            retry_after <= 0 or _now_ts() >= retry_after
                        )
                        if not fallback_retry_due:
                            return current_plan

            plan = await self._generate_daily_plan()
            async with self._data_lock:
                self.data["daily_plan"] = plan
                self._sync_detail_enhancement_day_locked(plan.get("date"), reset=True)
                self._refresh_daily_state_location_from_plan(plan=plan)
                self._save_daily_state_sections(
                    sections={
                        "daily_plan",
                        "daily_state",
                        "detail_enhanced_day",
                        "detail_enhanced_segments",
                        "daily_story_plan",
                    }
                )
        outfit_generator = getattr(self, "_ensure_daily_outfit_photo", None)
        if callable(outfit_generator):
            try:
                await outfit_generator()
            except Exception as exc:
                logger.warning(
                    "今日日程已保存,但每日穿搭照片生成失败: %s",
                    _single_line(exc, 180),
                )
        await self._ensure_daily_news_reading(force=force)
        return plan

    async def _ensure_daily_diary(self, force: bool = False) -> dict[str, Any] | None:
        request_started = time.monotonic()
        scope = self._daily_generation_scope()
        force_cache = self._daily_force_result_cache("_daily_diary_force_results_by_scope")
        lock = self._daily_generation_lock("_daily_diary_generation_lock")
        async with lock:
            completed_entry = force_cache.get(scope, {})
            if force and _safe_float(completed_entry.get("completed_at"), 0) >= request_started:
                completed = completed_entry.get("result")
                if isinstance(completed, dict):
                    return completed
            diary = await self._ensure_daily_diary_once(force=force)
            if force and isinstance(diary, dict):
                force_cache[scope] = {
                    "result": diary,
                    "completed_at": time.monotonic(),
                }
            return diary

    async def _ensure_daily_diary_once(self, force: bool = False) -> dict[str, Any] | None:
        if not runtime_persona_setting(self, "enable_daily_diary", True) and not force:
            return None
        request_day = _today_key()
        async with self._data_lock:
            delete_revision_at_start = self._daily_diary_delete_revision()
            if not force and self.data.get("diary_generated_day") == request_day:
                return None
            if not force and self._daily_diary_was_manually_deleted(request_day):
                return None
            if not force and self.data.get("daily_diary_failed_day") == request_day:
                failed_at = _safe_float(self.data.get("daily_diary_failed_at"), 0, 0)
                if failed_at > 0 and _now_ts() - failed_at < 30 * 60:
                    return None
            if not force and not self._is_daily_diary_due():
                return None

        try:
            diary = await self._generate_daily_diary()
        except Exception as exc:
            async with self._data_lock:
                self.data["daily_diary_failed_day"] = request_day
                self.data["daily_diary_failed_at"] = _now_ts()
                self.data["daily_diary_last_error"] = _single_line(exc, 180)
                self._save_data_sync(
                    sections={
                        "daily_diary_failed_day",
                        "daily_diary_failed_at",
                        "daily_diary_last_error",
                    }
                )
            if force:
                raise
            logger.warning(
                "生成今日日记失败,已进入30分钟冷却避免重复请求: %s",
                _single_line(exc, 180),
            )
            return None

        diary_day = _single_line(diary.get("date"), 16) if isinstance(diary, dict) else ""
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", diary_day):
            diary_day = _today_key()
            if isinstance(diary, dict):
                diary["date"] = diary_day
        memory_payload: dict[str, str] | None = None
        async with self._data_lock:
            deleted_now = (
                self._daily_diary_was_manually_deleted(request_day)
                or self._daily_diary_was_manually_deleted(diary_day)
            )
            deleted_during_generation = (
                deleted_now
                and self._daily_diary_delete_revision() != delete_revision_at_start
            )
            if deleted_now and (not force or deleted_during_generation):
                logger.info(
                    "日记生成期间该日期被手动删除，已丢弃生成结果: request_day=%s diary_day=%s force=%s",
                    request_day,
                    diary_day,
                    force,
                )
                return None
            diaries = self.data.setdefault("bot_diaries", [])
            if isinstance(diaries, dict):
                migrated_diaries: list[Any] = []
                for stored_date, stored_diary in diaries.items():
                    fallback_date = _single_line(stored_date, 64)
                    if isinstance(stored_diary, dict):
                        migrated_diary = deepcopy(stored_diary)
                        if fallback_date and not _single_line(migrated_diary.get("date"), 64):
                            migrated_diary["date"] = fallback_date
                    elif isinstance(stored_diary, str):
                        migrated_diary = {"body": stored_diary}
                        if fallback_date:
                            migrated_diary["date"] = fallback_date
                    else:
                        # Keep uncommon JSON-compatible legacy values recoverable instead of dropping them.
                        migrated_diary = {"legacy_value": deepcopy(stored_diary)}
                        if fallback_date:
                            migrated_diary["date"] = fallback_date
                    migrated_diaries.append(migrated_diary)
                diaries = migrated_diaries
                self.data["bot_diaries"] = diaries
                logger.info(
                    "已无损迁移旧字典日记存储: entries=%s",
                    len(diaries),
                )
            elif not isinstance(diaries, list):
                logger.error(
                    "日记记录结构异常，已保留原数据并放弃写入本次生成结果: storage=%s",
                    type(diaries).__name__,
                )
                return None
            if not force and self.data.get("diary_generated_day") == diary_day:
                return next(
                    (
                        item
                        for item in reversed(diaries)
                        if isinstance(item, dict) and _single_line(item.get("date"), 16) == diary_day
                    ),
                    None,
                )
            if force:
                refreshed: list[Any] = []
                replaced = False
                for item in diaries:
                    if isinstance(item, dict) and _single_line(item.get("date"), 16) == diary_day:
                        if not replaced:
                            refreshed.append(diary)
                            replaced = True
                        continue
                    refreshed.append(item)
                if not replaced:
                    refreshed.append(diary)
                diaries[:] = refreshed
            else:
                diaries.append(diary)
            max_entries = max(1, _safe_int(runtime_persona_setting(self, "max_diary_entries", 14), 14, 1))
            del diaries[:-max_entries]
            # Mark the diary as generated before optional enrichment so a post-process
            # bug cannot make the scheduler call the LLM again and again.
            previous_generated_day = _single_line(self.data.get("diary_generated_day"), 16)
            self.data["diary_generated_day"] = max(previous_generated_day, diary_day)
            deleted_days = self.data.get("daily_diary_deleted_days")
            if isinstance(deleted_days, list):
                self.data["daily_diary_deleted_days"] = [
                    value
                    for value in deleted_days
                    if _single_line(value, 16) != diary_day
                ]
            self.data["daily_diary_failed_day"] = ""
            self.data["daily_diary_failed_at"] = 0
            self.data["daily_diary_last_error"] = ""
            try:
                self.data["dream_fragments"] = self._merge_dream_fragment_pool(
                    diary.get("dream_fragments", []) if isinstance(diary, dict) else []
                )
                self.data["daily_diary_postprocess_error"] = ""
            except Exception as exc:
                self.data["daily_diary_postprocess_error"] = _single_line(exc, 180)
                logger.warning(
                    "今日日记已保存,但梦境碎片合并失败: %s",
                    _single_line(exc, 180),
                )
            dream_fragments = diary.get("dream_fragments", []) if isinstance(diary, dict) else []
            if isinstance(dream_fragments, list) and dream_fragments:
                fragment = dream_fragments[0] if isinstance(dream_fragments[0], dict) else {}
                content = _single_line(fragment.get("content") or fragment.get("text") or fragment.get("dream"), 600)
                if content:
                    memory_payload = {
                        "content": content,
                        "mood": _single_line(fragment.get("mood") or fragment.get("emotion"), 40),
                        "dream_type": _single_line(fragment.get("type") or fragment.get("theme"), 40),
                    }
            story_plan = diary.get("story_plan") if isinstance(diary, dict) else None
            if isinstance(story_plan, dict):
                self.data["daily_story_plan"] = story_plan
            self._save_data_sync(
                sections={
                    "bot_diaries",
                    "diary_generated_day",
                    "daily_diary_deleted_days",
                    "daily_diary_failed_day",
                    "daily_diary_failed_at",
                    "daily_diary_last_error",
                    "daily_diary_postprocess_error",
                    "dream_fragments",
                    "daily_story_plan",
                }
            )
        diary_recorder = getattr(self, "_memory_companion_record_daily_diary", None)
        if callable(diary_recorder):
            try:
                archive_result = await diary_recorder(diary)
            except Exception as exc:
                archive_result = {
                    "ok": False,
                    "state": "degraded",
                    "error_code": type(exc).__name__,
                }
                logger.warning("Bot Personal 日记归档失败: %s", _single_line(exc, 160))
            if isinstance(diary, dict) and isinstance(archive_result, dict):
                async with self._data_lock:
                    diary["memory_archive"] = dict(archive_result)
                    self._save_data_sync(
                        sections={
                            "bot_diaries",
                            "bot_personal_outbox",
                            "bot_personal_archive_revisions",
                        }
                    )
        if memory_payload:
            try:
                await self._memory_companion_record_dream_fragment(**memory_payload)
            except Exception:
                pass
        return diary

    async def _ensure_detail_enhancement(self, force: bool = False) -> dict[str, Any] | None:
        if not runtime_persona_setting(self, "enable_detail_enhancement", False) and not force:
            return None
        async with self._data_lock:
            plan = dict(self.data.get("daily_plan", {}))
            plan_date = str(plan.get("date") or "")
            if not self._is_plan_date_active(plan_date):
                return None
            self._sync_detail_enhancement_day_locked(plan_date)
            state = dict(self.data.get("daily_state", {}))
            enhanced = self.data.setdefault("detail_enhanced_segments", {})
            if not isinstance(enhanced, dict):
                enhanced = {}
                self.data["detail_enhanced_segments"] = enhanced
            sanitized_existing = False
            if self._sanitize_detail_enhanced_segments_inplace(enhanced):
                sanitized_existing = True
            story_plan_existing = self.data.get("daily_story_plan", {})
            if isinstance(story_plan_existing, dict) and self._sanitize_story_plan_social_facts_inplace(story_plan_existing):
                sanitized_existing = True
            segments = self._collect_due_detail_segments(plan, enhanced, force=force)
            if not segments:
                if sanitized_existing:
                    self._save_data_sync(
                        sections={"detail_enhanced_segments", "daily_story_plan"}
                    )
                return None
            for segment in segments:
                generation_id = uuid.uuid4().hex
                segment["_generation_id"] = generation_id
                enhanced[segment["key"]] = {
                    "status": "generating",
                    "started_at": self._environment_now().strftime("%H:%M"),
                    "started_ts": _now_ts(),
                    "generation_id": generation_id,
                }
            self._save_data_sync(sections={"detail_enhanced_segments"})

        last_detail = None
        for segment in segments:
            try:
                detail = await self._generate_detail_enhancement(segment, plan, state)
                if not isinstance(detail.get("today_events"), list) or not detail.get("today_events"):
                    raise RuntimeError("日程细化结果为空或无法解析")
            except Exception as exc:
                now_ts = _now_ts()
                retry_after_ts = now_ts + 30 * 60
                failure_is_current = False
                async with self._data_lock:
                    failure_is_current = self._detail_generation_is_current(
                        segment,
                        str(segment.get("_generation_id") or ""),
                    )
                    if failure_is_current:
                        enhanced = self.data.setdefault("detail_enhanced_segments", {})
                        if not isinstance(enhanced, dict):
                            enhanced = {}
                            self.data["detail_enhanced_segments"] = enhanced
                        retry_after = self._environment_fromtimestamp(retry_after_ts).strftime("%H:%M")
                        enhanced[segment["key"]] = {
                            "status": "failed",
                            "updated_at": self._environment_now().strftime("%H:%M"),
                            "error": _single_line(exc, 180),
                            "retry_after": retry_after,
                            "retry_after_ts": retry_after_ts,
                            "summary": "这一段细化生成失败，稍后会自动重试。",
                            "today_events": [],
                            "proactive_events": [],
                            "state_variables": [],
                            "presence_status": {},
                            "interaction_updates": [],
                            "coverage_repair_done": bool(segment.get("_coverage_repair")),
                        }
                        self._save_data_sync(sections={"detail_enhanced_segments"})
                    else:
                        retry_after = ""
                if failure_is_current:
                    logger.warning(
                        "日程细化生成失败,已标记为可重试: segment=%s retry_after=%s error=%s",
                        _single_line(segment.get("key"), 80),
                        retry_after,
                        _single_line(exc, 180),
                    )
                else:
                    logger.info(
                        "日程细化失败结果已过期,不再回写: segment=%s error=%s",
                        _single_line(segment.get("key"), 80),
                        _single_line(exc, 180),
                    )
                if force and failure_is_current:
                    raise
                continue
            self._sanitize_detail_snapshot_for_segment_inplace(
                detail,
                segment,
                field=f"detail_enhanced_segments.{segment.get('key') or 'current'}",
            )
            async with self._data_lock:
                if not self._detail_generation_is_current(
                    segment,
                    str(segment.get("_generation_id") or ""),
                ):
                    continue
                story_plan = self.data.setdefault("daily_story_plan", {})
                if not isinstance(story_plan, dict) or story_plan.get("date") != plan_date:
                    story_plan = {
                        "date": plan_date,
                        "today_events": [],
                        "proactive_events": [],
                        "long_term_events": [],
                    }
                    self.data["daily_story_plan"] = story_plan
                self._merge_detail_enhancement(story_plan, detail)
                self._sanitize_story_plan_social_facts_inplace(story_plan)
                enhanced = self.data.setdefault("detail_enhanced_segments", {})
                enhanced[segment["key"]] = {
                    "status": "done",
                    "updated_at": self._environment_now().strftime("%H:%M"),
                    "summary": _single_line(detail.get("summary"), 120),
                    "summary_basis": self._normalize_schedule_basis(detail.get("summary_basis"), default=["coarse_plan"]),
                    "summary_confidence": min(1.0, _safe_float(detail.get("summary_confidence"), 0.75)),
                    "location": _single_line(detail.get("location"), 60),
                    "location_basis": self._normalize_schedule_basis(detail.get("location_basis"), default=["coarse_plan"]),
                    "location_confidence": min(1.0, _safe_float(detail.get("location_confidence"), 0.72)),
                    "today_events": detail.get("today_events", []),
                    "proactive_events": detail.get("proactive_events", []),
                    "state_variables": detail.get("state_variables", []),
                    "presence_status": detail.get("presence_status", {}),
                    "quality": detail.get("quality", {}),
                    "interaction_updates": [],
                    "coverage_repair_done": bool(segment.get("_coverage_repair")),
                }
                self._sanitize_detail_enhanced_segments_inplace(enhanced)
                meal_entries = self._append_self_meal_log(
                    self._collect_self_meal_events_from_detail(segment=segment, plan=plan, detail=detail),
                    segment=segment,
                    plan=plan,
                )
                self._remember_detail_enhancement_history(plan_date, enhanced, story_plan)
                self._refresh_daily_state_location_from_plan(
                    plan=plan,
                    detail=detail,
                    segment=segment,
                )
                self._reschedule_users_for_new_detail_events(segment)
                self._save_data_sync(
                    sections={
                        "daily_plan",
                        "daily_state",
                        "detail_enhanced_segments",
                        "detail_enhanced_history",
                        "daily_story_plan",
                        "daily_story_plan_history",
                        "users",
                        "self_meal_log",
                    }
                )
                last_detail = detail
            for meal_entry in meal_entries:
                await self._memory_companion_record_self_meal(meal_entry)
            if meal_entries:
                self._schedule_data_save(sections={"self_meal_log"})
            await self._apply_detail_presence_status(segment, detail)
        return last_detail

    def _meal_log_date_key(self, ts: float | None = None) -> str:
        try:
            return self._environment_fromtimestamp(ts or _now_ts()).strftime("%Y-%m-%d")
        except Exception:
            return _today_key()

    def _meal_log_iso_time(self, ts: float | None = None) -> str:
        try:
            return self._environment_fromtimestamp(ts or _now_ts()).isoformat(timespec="seconds")
        except Exception:
            return datetime.fromtimestamp(ts or _now_ts()).isoformat(timespec="seconds")

    def _extract_self_meal_events_from_text(
        self,
        text: Any,
        *,
        default_meal: str = "",
        source: str = "",
    ) -> list[dict[str, Any]]:
        raw = _single_line(text, 260)
        if not raw:
            return []
        if not any(token in raw for token in ("吃", "喝", "点了", "煮了", "做了", "买了", "饭", "餐", "夜宵", "便当", "外卖")):
            return []
        if re.search(r"(想吃|想喝|要不要|吃什么|吃啥|没吃|还没吃|准备吃|等会吃|待会吃|可能吃|可以吃|推荐|建议)", raw):
            return []
        action_match = re.search(
            r"(?:我|她|星缘)?(?:刚刚|刚|已经|中午|晚上|早上|午后|夜里|下午|早餐|午餐|晚餐|夜宵|这顿)?"
            r"(?:吃了|吃过|吃完|喝了|点了|煮了|做了|买了|啃了|咬了|尝了|解决了)"
            r"([^，。；、\n]{1,36})",
            raw,
        )
        meal_match = re.search(r"(早餐|早饭|午餐|午饭|晚餐|晚饭|夜宵|加餐|下午茶)", raw)
        meal = _single_line((meal_match.group(1) if meal_match else "") or default_meal, 20)
        food = ""
        if action_match:
            food = _single_line(action_match.group(1), 40)
            food = re.sub(r"^(点|些|个|一点|一点儿|一份|一碗|一杯|一口|点儿)", "", food).strip()
            food = re.sub(r"(之后|以后|然后|顺手|才发现|的时候).*$", "", food).strip()
        if not food:
            simple = re.search(r"(?:早餐|早饭|午餐|午饭|晚餐|晚饭|夜宵|下午茶)[^，。；、\n]{0,8}(?:是|吃|喝|点)([^，。；、\n]{1,32})", raw)
            if simple:
                food = _single_line(simple.group(1), 40)
        if not food or food in {"饭", "东西", "一点", "点东西"}:
            return []
        return [
            {
                "meal": meal or "加餐",
                "food": food,
                "source": _single_line(source, 40),
                "evidence": raw,
            }
        ]

    def _collect_self_meal_events_from_detail(
        self,
        *,
        segment: dict[str, Any],
        plan: dict[str, Any],
        detail: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(detail, dict):
            return []
        default_meal = ""
        item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
        schedule_text = " ".join(
            _single_line(part, 120)
            for part in (
                item.get("time") if isinstance(item, dict) else "",
                item.get("activity") if isinstance(item, dict) else "",
                detail.get("summary"),
            )
            if _single_line(part, 120)
        )
        if any(token in schedule_text for token in ("早餐", "早饭")):
            default_meal = "早餐"
        elif any(token in schedule_text for token in ("午餐", "午饭", "中午")):
            default_meal = "午餐"
        elif any(token in schedule_text for token in ("晚餐", "晚饭", "晚上")):
            default_meal = "晚餐"
        elif "夜宵" in schedule_text:
            default_meal = "夜宵"
        rows: list[dict[str, Any]] = []
        rows.extend(self._extract_self_meal_events_from_text(detail.get("summary"), default_meal=default_meal, source="detail.summary"))
        for list_key in ("today_events", "state_variables"):
            raw_items = detail.get(list_key)
            if not isinstance(raw_items, list):
                continue
            for raw in raw_items[:12]:
                if isinstance(raw, dict):
                    text = raw.get("event") or raw.get("text") or raw.get("name") or raw.get("value") or raw.get("note")
                else:
                    text = raw
                rows.extend(self._extract_self_meal_events_from_text(text, default_meal=default_meal, source=f"detail.{list_key}"))
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for meal_event in rows:
            key = f"{meal_event.get('meal')}:{meal_event.get('food')}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append(meal_event)
        return deduped[:4]

    def _append_self_meal_log(
        self,
        meal_events: list[dict[str, Any]],
        *,
        segment: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not meal_events:
            return []
        now_ts = _now_ts()
        date_text = _single_line((plan or {}).get("date"), 16) or self._meal_log_date_key(now_ts)
        time_text = ""
        if isinstance(segment, dict):
            item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
            time_text = _single_line(item.get("time") if isinstance(item, dict) else "", 20)
        log = self.data.setdefault("self_meal_log", [])
        if not isinstance(log, list):
            log = []
            self.data["self_meal_log"] = log
        existing_ids = {str(item.get("id") or "") for item in log if isinstance(item, dict)}
        added: list[dict[str, Any]] = []
        for meal_event in meal_events:
            meal = _single_line(meal_event.get("meal"), 20) or "加餐"
            food = _single_line(meal_event.get("food"), 60)
            if not food:
                continue
            base_id = hashlib.sha1(f"{date_text}|{time_text}|{meal}|{food}".encode("utf-8", errors="ignore")).hexdigest()[:16]
            meal_id = f"meal-{base_id}"
            if meal_id in existing_ids:
                continue
            entry = {
                "id": meal_id,
                "date": date_text,
                "time": time_text,
                "ts": now_ts,
                "occurred_at": self._meal_log_iso_time(now_ts),
                "meal": meal,
                "food": food,
                "source": _single_line(meal_event.get("source"), 40),
                "evidence": _single_line(meal_event.get("evidence"), 180),
                "memory_recorded": False,
                "memory_id": "",
            }
            log.append(entry)
            existing_ids.add(meal_id)
            added.append(entry)
        if len(log) > 160:
            del log[:-160]
        return added

    async def _memory_companion_record_self_meal(self, entry: dict[str, Any]) -> None:
        if not isinstance(entry, dict) or entry.get("memory_recorded"):
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_persona_life", None) if bridge is not None else None
        if not callable(recorder):
            return
        date_text = _single_line(entry.get("date"), 16)
        time_text = _single_line(entry.get("time"), 20)
        meal = _single_line(entry.get("meal"), 20) or "加餐"
        food = _single_line(entry.get("food"), 60)
        if not food:
            return
        when = " ".join(part for part in (date_text, time_text) if part)
        content = f"Bot 在{when or date_text or '今天'}的{meal}吃了{food}。"
        try:
            memory_id = await recorder(
                content=content,
                scope="unknown",
                session_id="private_companion:self_meal",
                message_id=_single_line(entry.get("id"), 120),
                memory_id=f"private_companion_{_single_line(entry.get('id'), 80)}",
                metadata={
                    "date": date_text,
                    "time": time_text,
                    "event_type": "self_meal",
                    "action_label": "进食记录",
                    "meal": meal,
                    "food": food,
                    "evidence": _single_line(entry.get("evidence"), 180),
                    "source": _single_line(entry.get("source"), 40),
                    "query_anchors": ["self_meal", "吃了什么", "刚才吃了什么", "午餐", "晚餐", "早餐", "夜宵", food],
                },
                source_plugin="private_companion",
                confidence=0.78,
                importance=0.5,
                tags=["self_meal", "persona_life", "food", meal],
                occurred_at=_single_line(entry.get("occurred_at"), 80),
            )
        except Exception as exc:
            logger.debug("MemoryCompanion 进食记忆写入失败: %s", _single_line(exc, 120))
            return
        entry["memory_recorded"] = True
        entry["memory_id"] = _single_line(memory_id, 120)

    def _collect_detail_segments(
        self,
        plan: dict[str, Any],
        enhanced: dict[str, Any],
        *,
        include_cancelled: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return []
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            return []
        starts = self._normalized_plan_item_starts(items)
        parsed = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            start = starts[index] if index < len(starts) else None
            if start is None:
                continue
            parsed.append((index, start, item))
        if not parsed:
            return []
        segments: list[dict[str, Any]] = []
        for pos, (index, start, item) in enumerate(parsed):
            if not include_cancelled and self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            key = f"{plan.get('date')}:{index}:{item.get('time')}"
            if self._detail_enhancement_snapshot_blocks_generation(enhanced.get(key) if isinstance(enhanced, dict) else None):
                continue
            next_start = (
                parsed[pos + 1][1]
                if pos + 1 < len(parsed)
                else None
            )
            end = self._plan_item_end_minutes(start, item, next_start=next_start)
            segments.append(
                {
                    "key": key,
                    "plan_date": str(plan.get("date") or ""),
                    "index": index,
                    "start": start,
                    "end": end,
                    "previous_item": next(
                        (
                            candidate[2]
                            for candidate in reversed(parsed[:pos])
                            if self._normalize_schedule_lifecycle_status(candidate[2].get("lifecycle_status")) != "cancelled"
                        ),
                        None,
                    ),
                    "item": item,
                    "next_item": next(
                        (
                            candidate[2]
                            for candidate in parsed[pos + 1 :]
                            if self._normalize_schedule_lifecycle_status(candidate[2].get("lifecycle_status")) != "cancelled"
                        ),
                        None,
                    ),
                }
            )
        return segments

    @staticmethod
    def _schedule_segment_selector_cn_number(value: Any) -> int | None:
        text = str(value or "").strip().replace("兩", "两").replace("〇", "零")
        if not text:
            return None
        if text.isdigit():
            return int(text)
        digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if text in digits:
            return digits[text]
        if "十" in text:
            left, _, right = text.partition("十")
            tens = digits.get(left, 1) if left else 1
            ones = digits.get(right, 0) if right else 0
            return tens * 10 + ones
        return None

    def _schedule_segment_selector_minutes(self, selector: str) -> list[int]:
        compact = re.sub(r"\s+", "", str(selector or ""))
        match = re.search(
            r"(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|今晚|夜里)?"
            r"(\d{1,2}|[零〇一二两兩三四五六七八九十]{1,3})"
            r"(?:[:：点點时時])"
            r"(\d{1,2}|半|一刻|三刻)?",
            compact,
        )
        if not match:
            return []
        period = str(match.group(1) or "")
        hour = self._schedule_segment_selector_cn_number(match.group(2))
        minute_text = str(match.group(3) or "")
        if hour is None:
            return []
        if minute_text == "半":
            minute = 30
        elif minute_text == "一刻":
            minute = 15
        elif minute_text == "三刻":
            minute = 45
        else:
            minute = _safe_int(minute_text, 0, 0, 59)
        if hour > 23:
            return []
        if period in {"凌晨"} and hour == 12:
            hour = 0
        elif period in {"中午", "下午", "傍晚", "晚上", "今晚", "夜里"}:
            if hour == 12:
                hour = 12 if period == "中午" else 0
            elif hour < 12:
                hour += 12
        elif period in {"早上", "早晨", "上午"} and hour == 12:
            hour = 0
        primary = hour * 60 + minute
        if period or hour == 0 or hour > 12:
            return [primary]
        alternate = primary + 12 * 60
        return [primary, alternate] if alternate < 24 * 60 else [primary]

    @staticmethod
    def _schedule_segment_selector_text(value: Any) -> str:
        text = unicodedata.normalize("NFKC", _single_line(value, 160)).lower()
        text = re.sub(
            r"(?:今天|今日|今儿|当天|这一段|这个|那一段|那个|时段|时间段|日程|安排|计划|细化|活动|任务)",
            "",
            text,
        )
        text = re.sub(
            r"(?:凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|今晚|夜里)?"
            r"(?:\d{1,2}|[零〇一二两兩三四五六七八九十]{1,3})"
            r"(?:[:：点點时時])(?:\d{1,2}|半|一刻|三刻)?",
            "",
            text,
        )
        text = re.sub(r"[\s\-_—:：，,。.!！?？'\"“”‘’（）()【】\[\]]+", "", text)
        return text

    def _schedule_segment_label(self, segment: dict[str, Any]) -> str:
        item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
        start = self._minutes_to_hhmm(_safe_int(segment.get("start"), 0))
        end = self._minutes_to_hhmm(_safe_int(segment.get("end"), 0))
        activity = _single_line(item.get("activity"), 80) or "未命名日程"
        status = self._normalize_schedule_lifecycle_status(item.get("lifecycle_status"))
        suffix = "（已取消）" if status == "cancelled" else ""
        return f"{start}-{end} {activity}{suffix}"

    def _resolve_daily_plan_segment_selector(
        self,
        selector: Any,
        *,
        plan: dict[str, Any] | None = None,
        include_cancelled: bool = True,
    ) -> tuple[dict[str, Any] | None, str]:
        current_plan = plan if isinstance(plan, dict) else self.data.get("daily_plan", {})
        if not isinstance(current_plan, dict) or not current_plan.get("items"):
            return None, "今天还没有可操作的日程。"
        segments = self._collect_detail_segments(current_plan, {}, include_cancelled=include_cancelled)
        if not segments:
            return None, "今天还没有可操作的日程段。"
        raw = _single_line(selector, 160)
        if not raw:
            return None, "请指定时间或活动，例如“陪伴 删除日程 15:00”或“陪伴 重置日程 整理房间”。"

        exact_key = next((segment for segment in segments if _single_line(segment.get("key"), 120) == raw), None)
        if exact_key:
            return exact_key, ""
        compact = re.sub(r"\s+", "", raw.lower())
        if compact in {"当前", "现在", "此刻", "正在进行", "当前段", "这一段", "这段"}:
            current = self._current_detail_segment_for_update()
            if current:
                return current, ""
            return None, "当前没有正在进行或即将开始的日程段。"

        ordinal_match = re.search(
            r"(?:第\s*(\d{1,2}|[一二两三四五六七八九十]{1,3})\s*(?:个|项|段)?|"
            r"(\d{1,2}|[一二两三四五六七八九十]{1,3})\s*(?:个|项|段))",
            raw,
        )
        if ordinal_match:
            ordinal = self._schedule_segment_selector_cn_number(ordinal_match.group(1) or ordinal_match.group(2))
            if ordinal is not None and 1 <= ordinal <= len(segments):
                return segments[ordinal - 1], ""

        requested_minutes = self._schedule_segment_selector_minutes(raw)
        time_matches: list[dict[str, Any]] = []
        if requested_minutes:
            exact_starts = [
                segment
                for segment in segments
                if any(_safe_int(segment.get("start"), -1) % (24 * 60) == minute for minute in requested_minutes)
            ]
            if exact_starts:
                time_matches = exact_starts
            else:
                time_matches = [
                    segment
                    for segment in segments
                    if any(
                        _safe_int(segment.get("start"), 0) <= minute < _safe_int(segment.get("end"), 0)
                        or _safe_int(segment.get("start"), 0) <= minute + 24 * 60 < _safe_int(segment.get("end"), 0)
                        for minute in requested_minutes
                    )
                ]

        activity_query = self._schedule_segment_selector_text(raw)
        activity_matches: list[dict[str, Any]] = []
        if activity_query:
            for segment in segments:
                item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
                activity = self._schedule_segment_selector_text(item.get("activity"))
                if activity_query == activity or activity_query in activity or activity in activity_query:
                    activity_matches.append(segment)

        matches = time_matches
        if activity_matches:
            intersection = [segment for segment in time_matches if segment in activity_matches]
            matches = intersection or activity_matches if time_matches else activity_matches
        if len(matches) == 1:
            return matches[0], ""
        if len(matches) > 1:
            choices = "；".join(self._schedule_segment_label(segment) for segment in matches[:5])
            return None, f"匹配到多段日程，请再具体一点：{choices}"

        choices = "；".join(self._schedule_segment_label(segment) for segment in segments[:8])
        return None, f"没有找到“{raw}”对应的日程。今天可选：{choices}"

    async def _cancel_daily_plan_segment_by_selector(self, selector: Any, *, reason: str = "用户通过聊天命令取消该日程段") -> tuple[bool, str]:
        async with self._data_lock:
            segment, error = self._resolve_daily_plan_segment_selector(selector, include_cancelled=True)
            if not segment:
                return False, error
            key = _single_line(segment.get("key"), 120)
            plan = self.data.get("daily_plan", {})
            items = plan.get("items") if isinstance(plan, dict) else None
            index = _safe_int(segment.get("index"), -1, minimum=-1)
            item = items[index] if isinstance(items, list) and 0 <= index < len(items) and isinstance(items[index], dict) else None
            if not isinstance(item, dict):
                return False, "该日程段已经不存在。"
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                return True, f"这段日程之前已经取消：{self._schedule_segment_label(segment)}"
            label = self._schedule_segment_label(segment)
            item["lifecycle_status"] = "cancelled"
            item["changed_at"] = self._environment_now().strftime("%H:%M")
            item["change_reason"] = _single_line(reason, 120)
            item.pop("_detail_generation_id", None)
            self._sync_detail_enhancement_day_locked(plan.get("date"))
            enhanced = self.data.setdefault("detail_enhanced_segments", {})
            if not isinstance(enhanced, dict):
                enhanced = {}
                self.data["detail_enhanced_segments"] = enhanced
            cancelled = deepcopy(enhanced.get(key)) if isinstance(enhanced.get(key), dict) else {
                "status": "done",
                "summary": "这一段已取消。",
                "today_events": [],
                "proactive_events": [],
                "state_variables": [],
            }
            for event in list(cancelled.get("today_events") or []) + list(cancelled.get("proactive_events") or []):
                if isinstance(event, dict):
                    event["lifecycle_status"] = "cancelled"
            cancelled["status"] = "cancelled"
            cancelled["summary"] = _single_line(cancelled.get("summary"), 120) or "这一段已取消。"
            cancelled["cancelled_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
            for field in ("generation_id", "previous_item_state", "retry_after", "retry_after_ts"):
                cancelled.pop(field, None)
            enhanced[key] = cancelled
            story = self._rebuild_story_plan_from_detail_snapshots(str(plan.get("date") or _today_key()))
            self._remember_detail_enhancement_history(str(plan.get("date") or _today_key()), enhanced, story)
            self._save_data_sync(
                sections={
                    "daily_plan",
                    "detail_enhanced_day",
                    "detail_enhanced_segments",
                    "detail_enhanced_history",
                    "daily_story_plan",
                    "daily_story_plan_history",
                }
            )
            return True, f"已取消：{label}"

    async def _regenerate_daily_plan_segment_by_selector(
        self,
        selector: Any,
        generator: Any,
        *,
        reason: str = "用户通过聊天命令重新细化该日程段",
    ) -> tuple[bool, str, dict[str, Any]]:
        if not callable(generator):
            return False, "当前没有可用的日程细化生成器。", {}
        previous_snapshot: dict[str, Any] = {}
        previous_item_state: dict[str, tuple[bool, Any]] = {}
        generation_id = ""
        key = ""
        segment: dict[str, Any] = {}
        plan: dict[str, Any] = {}
        try:
            async with self._data_lock:
                live_plan = self.data.get("daily_plan", {})
                segment, error = self._resolve_daily_plan_segment_selector(
                    selector,
                    plan=live_plan if isinstance(live_plan, dict) else {},
                    include_cancelled=True,
                )
                if not segment:
                    return False, error, {}
                key = _single_line(segment.get("key"), 120)
                plan = deepcopy(live_plan)
                segment = next(
                    (
                        candidate
                        for candidate in self._collect_detail_segments(plan, {}, include_cancelled=True)
                        if _single_line(candidate.get("key"), 120) == key
                    ),
                    segment,
                )
                state = deepcopy(self.data.get("daily_state", {}))
                items = live_plan.get("items") if isinstance(live_plan, dict) else None
                index = _safe_int(segment.get("index"), -1, minimum=-1)
                live_item = items[index] if isinstance(items, list) and 0 <= index < len(items) and isinstance(items[index], dict) else None
                if not isinstance(live_item, dict):
                    return False, "该日程段已经不存在。", {}
                self._sync_detail_enhancement_day_locked(plan.get("date"))
                enhanced = self.data.setdefault("detail_enhanced_segments", {})
                if not isinstance(enhanced, dict):
                    enhanced = {}
                    self.data["detail_enhanced_segments"] = enhanced
                previous_snapshot = deepcopy(enhanced.get(key)) if isinstance(enhanced.get(key), dict) else {}
                if (
                    _single_line(previous_snapshot.get("status"), 24) == "generating"
                    and self._detail_enhancement_snapshot_blocks_generation(previous_snapshot)
                ):
                    return False, "该时间段正在细化中，请等待当前生成完成后再试。", {}
                for field in ("lifecycle_status", "changed_at", "change_reason", "_detail_generation_id"):
                    previous_item_state[field] = (field in live_item, deepcopy(live_item.get(field)))
                generation_id = uuid.uuid4().hex
                live_item["lifecycle_status"] = "changed"
                live_item["changed_at"] = self._environment_now().strftime("%H:%M")
                live_item["change_reason"] = _single_line(reason, 120)
                live_item["_detail_generation_id"] = generation_id
                segment_item = segment.get("item") if isinstance(segment.get("item"), dict) else None
                if isinstance(segment_item, dict):
                    segment_item["lifecycle_status"] = "changed"
                enhanced[key] = {
                    "status": "generating",
                    "started_at": self._environment_now().strftime("%H:%M"),
                    "started_ts": time.time(),
                    "regenerated": True,
                    "generation_id": generation_id,
                }
                self._save_data_sync(
                    sections={"daily_plan", "detail_enhanced_day", "detail_enhanced_segments"}
                )

            detail = await generator(self, segment, plan, state)
            if not isinstance(detail, dict) or not isinstance(detail.get("today_events"), list) or not detail.get("today_events"):
                raise RuntimeError("局部重生成未返回可用的细化事件")

            async with self._data_lock:
                if not self._detail_generation_is_current(segment, generation_id):
                    return False, "该时间段已被取消、替换或由更新的操作接管，本次迟到结果未写入。", {}
                enhanced = self.data.setdefault("detail_enhanced_segments", {})
                enhanced[key] = {
                    "status": "done",
                    "updated_at": self._environment_now().strftime("%H:%M"),
                    "summary": _single_line(detail.get("summary"), 120),
                    "summary_basis": self._normalize_schedule_basis(detail.get("summary_basis"), default=["coarse_plan"]),
                    "summary_confidence": min(1.0, _safe_float(detail.get("summary_confidence"), 0.75)),
                    "location": _single_line(detail.get("location"), 60),
                    "location_basis": self._normalize_schedule_basis(detail.get("location_basis"), default=["coarse_plan"]),
                    "location_confidence": min(1.0, _safe_float(detail.get("location_confidence"), 0.72)),
                    "today_events": detail.get("today_events", []),
                    "proactive_events": detail.get("proactive_events", []),
                    "state_variables": detail.get("state_variables", []),
                    "presence_status": detail.get("presence_status", {}),
                    "quality": detail.get("quality", {}),
                    "interaction_updates": previous_snapshot.get("interaction_updates", []),
                    "regenerated": True,
                    "regenerated_at": self._environment_now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                self._sanitize_detail_enhanced_segments_inplace(enhanced)
                story = self._rebuild_story_plan_from_detail_snapshots(str(plan.get("date") or _today_key()))
                self._remember_detail_enhancement_history(str(plan.get("date") or _today_key()), enhanced, story)
                current_plan = self.data.get("daily_plan", {})
                current_items = current_plan.get("items") if isinstance(current_plan, dict) else None
                index = _safe_int(segment.get("index"), -1, minimum=-1)
                current_item = current_items[index] if isinstance(current_items, list) and 0 <= index < len(current_items) and isinstance(current_items[index], dict) else None
                if isinstance(current_item, dict) and _single_line(current_item.get("_detail_generation_id"), 64) == generation_id:
                    current_item.pop("_detail_generation_id", None)
                self._refresh_daily_state_location_from_plan(
                    plan=current_plan if isinstance(current_plan, dict) else plan,
                    detail=detail,
                    segment=segment,
                )
                self._save_data_sync(
                    sections={
                        "daily_plan",
                        "daily_state",
                        "detail_enhanced_day",
                        "detail_enhanced_segments",
                        "detail_enhanced_history",
                        "daily_story_plan",
                        "daily_story_plan_history",
                    }
                )
                label = self._schedule_segment_label(segment)
            return True, f"已重新细化：{label}", detail
        except Exception as exc:
            logger.warning("聊天命令局部重生成日程细化失败: %s", exc, exc_info=True)
            async with self._data_lock:
                enhanced = self.data.setdefault("detail_enhanced_segments", {})
                if isinstance(enhanced, dict) and key and generation_id and self._detail_generation_is_current(segment, generation_id):
                    restored = previous_snapshot or {
                        "status": "failed",
                        "today_events": [],
                        "proactive_events": [],
                        "state_variables": [],
                    }
                    restored["regeneration_error"] = _single_line(exc, 180)
                    restored["regeneration_failed_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
                    enhanced[key] = restored
                    live_plan = self.data.get("daily_plan", {})
                    live_items = live_plan.get("items") if isinstance(live_plan, dict) else None
                    index = _safe_int(segment.get("index"), -1, minimum=-1)
                    live_item = live_items[index] if isinstance(live_items, list) and 0 <= index < len(live_items) and isinstance(live_items[index], dict) else None
                    if isinstance(live_item, dict) and _single_line(live_item.get("_detail_generation_id"), 64) == generation_id:
                        for field, (existed, value) in previous_item_state.items():
                            if existed:
                                live_item[field] = value
                            else:
                                live_item.pop(field, None)
                    self._save_data_sync(
                        sections={"daily_plan", "detail_enhanced_segments"}
                    )
            return False, _single_line(exc, 180) or "局部重生成失败。", {}

    def _detail_generation_is_current(self, segment: dict[str, Any], generation_id: str) -> bool:
        key = _single_line(segment.get("key"), 120)
        if not key or not generation_id:
            return False
        enhanced = self.data.get("detail_enhanced_segments", {})
        snapshot = enhanced.get(key) if isinstance(enhanced, dict) else None
        if not isinstance(snapshot, dict):
            return False
        if _single_line(snapshot.get("status"), 24) != "generating":
            return False
        if _single_line(snapshot.get("generation_id"), 64) != generation_id:
            return False
        live_plan = self.data.get("daily_plan", {})
        plan_date = _single_line(segment.get("plan_date"), 16)
        if not isinstance(live_plan, dict) or _single_line(live_plan.get("date"), 16) != plan_date:
            return False
        items = live_plan.get("items")
        index = _safe_int(segment.get("index"), -1, minimum=-1)
        if not isinstance(items, list) or not (0 <= index < len(items)) or not isinstance(items[index], dict):
            return False
        item = items[index]
        expected_key = f"{plan_date}:{index}:{item.get('time')}"
        if expected_key != key:
            return False
        return self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) != "cancelled"

    def _detail_enhancement_snapshot_blocks_generation(self, snapshot: Any) -> bool:
        if not isinstance(snapshot, dict):
            return False
        status = _single_line(snapshot.get("status"), 24)
        if status in {"done", "cancelled"}:
            return True
        if status == "failed":
            retry_after_ts = _safe_float(snapshot.get("retry_after_ts"), 0)
            return retry_after_ts > _now_ts()
        if status == "generating":
            started_ts = _safe_float(snapshot.get("started_ts"), 0)
            if started_ts > 0:
                return _now_ts() - started_ts < 30 * 60
            started_at = _single_line(snapshot.get("started_at"), 8)
            started_minutes = self._parse_hhmm_to_minutes(started_at)
            if started_minutes is None:
                return False
            elapsed_minutes = self._environment_now_minutes() - started_minutes
            if elapsed_minutes < 0:
                elapsed_minutes += 24 * 60
            return elapsed_minutes < 30
        if status:
            return False
        return bool(snapshot.get("summary") or snapshot.get("today_events") or snapshot.get("proactive_events"))

    def _collect_due_detail_segments(
        self,
        plan: dict[str, Any],
        enhanced: dict[str, Any],
        *,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        segments = self._collect_detail_segments(plan, enhanced if isinstance(enhanced, dict) else {})
        if not segments:
            return []
        if force:
            picked = self._current_detail_segment_for_update() or self._pick_detail_segment(plan, {})
            return [picked] if isinstance(picked, dict) else segments[:1]
        due = [segment for segment in segments if self._detail_segment_is_due(segment)]
        if due:
            return due[:1]

        story_plan = self.data.get("daily_story_plan", {})
        if not isinstance(story_plan, dict):
            story_plan = {}
        repaired: list[dict[str, Any]] = []
        all_segments = self._collect_detail_segments(plan, {})
        for segment in all_segments:
            if not self._detail_segment_is_due(segment):
                continue
            key = str(segment.get("key") or "")
            status = enhanced.get(key) if isinstance(enhanced, dict) else None
            if not isinstance(status, dict) or status.get("status") != "done":
                continue
            if status.get("coverage_repair_done"):
                continue
            if self._detail_segment_has_story_coverage(segment, story_plan):
                continue
            repaired_segment = dict(segment)
            repaired_segment["_coverage_repair"] = True
            repaired.append(repaired_segment)
        return repaired[:1]

    def _detail_segment_is_due(self, segment: dict[str, Any]) -> bool:
        if not isinstance(segment, dict):
            return False
        plan_date = str(self.data.get("daily_plan", {}).get("date") or "")
        now_minutes = self._effective_plan_now_minutes(plan_date)
        if now_minutes is None:
            return False
        start = _safe_int(segment.get("start"), 0)
        end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
        lead = max(0, _safe_int(runtime_persona_setting(self, "detail_enhancement_lead_minutes", 3), 3, 0))
        return start - lead <= now_minutes < end

    def _detail_segment_has_story_coverage(
        self,
        segment: dict[str, Any],
        story_plan: dict[str, Any],
    ) -> bool:
        if not isinstance(segment, dict) or not isinstance(story_plan, dict):
            return False
        start = _safe_int(segment.get("start"), 0)
        end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
        for key in ("today_events", "proactive_events"):
            raw_items = story_plan.get(key, [])
            if not isinstance(raw_items, list):
                continue
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                item_start, item_end = self._parse_window_minutes(str(item.get("window") or ""))
                if item_start is None or item_end is None:
                    continue
                if item_end < item_start:
                    item_end += 24 * 60
                if item_start < end and item_end > start:
                    return True
        return False

    def _pick_detail_segment(
        self, plan: dict[str, Any], enhanced: dict[str, Any]
    ) -> dict[str, Any] | None:
        return pick_detail_segment(self, plan, enhanced)

    async def _generate_detail_enhancement(
        self,
        segment: dict[str, Any],
        plan: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        return await generate_detail_enhancement(self, segment, plan, state)

    def _merge_detail_enhancement(
        self, story_plan: dict[str, Any], detail: dict[str, Any]
    ) -> None:
        for key, limit in (
            ("today_events", 16),
            ("proactive_events", 12),
            ("long_term_events", 6),
        ):
            existing = story_plan.setdefault(key, [])
            if not isinstance(existing, list):
                existing = []
                story_plan[key] = existing
            additions = detail.get(key, [])
            if isinstance(additions, list):
                existing.extend(
                    item
                    for item in additions
                    if isinstance(item, dict)
                    and self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) != "cancelled"
                )
                story_plan[key] = self._trim_story_plan_items(key, existing, limit)

    def _rebuild_story_plan_from_detail_snapshots(self, plan_date: str) -> dict[str, Any]:
        rebuilt: dict[str, Any] = {
            "date": _single_line(plan_date, 16),
            "today_events": [],
            "proactive_events": [],
            "long_term_events": [],
        }
        enhanced = self._detail_enhanced_segments_for_plan_date(plan_date)
        for snapshot in enhanced.values():
            if snapshot.get("status") != "done":
                continue
            self._merge_detail_enhancement(rebuilt, snapshot)
        self._sanitize_story_plan_social_facts_inplace(rebuilt)
        self.data["daily_story_plan"] = rebuilt
        return rebuilt

    def _remember_detail_enhancement_history(
        self,
        date_text: str,
        enhanced: dict[str, Any],
        story_plan: dict[str, Any],
    ) -> None:
        date_key = _single_line(date_text, 16)
        if not date_key:
            return
        history = self.data.setdefault("detail_enhanced_history", [])
        if not isinstance(history, list):
            history = []
            self.data["detail_enhanced_history"] = history
        history[:] = [
            old
            for old in history
            if not (isinstance(old, dict) and _single_line(old.get("date"), 16) == date_key)
        ]
        history.append(
            {
                "date": date_key,
                "updated_at": self._environment_now().strftime("%Y-%m-%d %H:%M"),
                "segments": dict(enhanced or {}),
            }
        )
        del history[:-14]

        story_history = self.data.setdefault("daily_story_plan_history", [])
        if not isinstance(story_history, list):
            story_history = []
            self.data["daily_story_plan_history"] = story_history
        story_history[:] = [
            old
            for old in story_history
            if not (isinstance(old, dict) and _single_line(old.get("date"), 16) == date_key)
        ]
        compact_story = dict(story_plan or {})
        compact_story["date"] = date_key
        story_history.append(compact_story)
        del story_history[:-14]

    def _trim_story_plan_items(
        self,
        key: str,
        items: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        normalized = [item for item in items if isinstance(item, dict)]
        if not normalized:
            return []
        seen: set[tuple[Any, ...]] = set()
        deduped: list[dict[str, Any]] = []
        for item in normalized:
            identity = self._story_plan_item_identity(key, item)
            if identity in seen:
                continue
            seen.add(identity)
            deduped.append(item)
        if key == "long_term_events":
            return deduped[-limit:]
        ordered = sorted(deduped, key=self._story_plan_item_sort_key)
        if len(ordered) <= limit:
            return ordered
        return self._pick_story_items_with_coverage(ordered, limit)

    def _story_plan_item_identity(self, key: str, item: dict[str, Any]) -> tuple[Any, ...]:
        if key == "today_events":
            return (
                _single_line(item.get("window"), 20),
                _single_line(item.get("event"), 80),
            )
        if key == "proactive_events":
            return (
                _single_line(item.get("window"), 20),
                _single_line(item.get("reason"), 40),
                _single_line(item.get("action"), 40),
                _single_line(item.get("topic"), 80),
            )
        return (
            _single_line(item.get("title"), 80),
            _single_line(item.get("status"), 80),
        )

    def _story_plan_item_sort_key(self, item: dict[str, Any]) -> tuple[int, int, str]:
        start, end = self._parse_window_minutes(str(item.get("window") or ""))
        start_value = start if start is not None else 99_999
        end_value = end if end is not None else start_value
        if end_value < start_value:
            end_value += 24 * 60
        text = _single_line(
            item.get("event") or item.get("topic") or item.get("title"),
            80,
        )
        return (start_value, end_value, text)

    def _pick_story_items_with_coverage(
        self,
        ordered: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        total = len(ordered)
        if total <= limit:
            return ordered
        now_minutes = self._environment_now_minutes()
        selected: set[int] = {0, total - 1}
        closest_index = min(
            range(total),
            key=lambda idx: self._story_item_time_distance(ordered[idx], now_minutes),
        )
        for idx in range(max(0, closest_index - 2), min(total, closest_index + 3)):
            selected.add(idx)
        if limit == 1:
            selected = {closest_index}
        else:
            for slot in range(limit):
                selected.add(round(slot * (total - 1) / max(1, limit - 1)))
        if len(selected) < limit:
            for idx in range(total):
                selected.add(idx)
                if len(selected) >= limit:
                    break
        return [ordered[idx] for idx in sorted(selected)[:limit]]

    def _story_item_time_distance(self, item: dict[str, Any], now_minutes: int) -> int:
        start, end = self._parse_window_minutes(str(item.get("window") or ""))
        if start is None or end is None:
            return 99_999
        if end < start:
            end += 24 * 60
        current = now_minutes
        if current < start and end > 24 * 60:
            current += 24 * 60
        if start <= current < end:
            return 0
        return min(abs(current - start), abs(current - end))

    def _reschedule_users_for_new_detail_events(self, segment: dict[str, Any]) -> None:
        users = self.data.get("users", {})
        if not isinstance(users, dict):
            return
        now = _now_ts()
        start = _safe_int(segment.get("start"), 0) * 60
        end = _safe_int(segment.get("end"), 0) * 60
        for user in users.values():
            if not isinstance(user, dict) or not user.get("umo"):
                continue
            next_at = _safe_float(user.get("next_proactive_at"), 0)
            if next_at <= 0:
                self._schedule_next_proactive(user, now=now)
                continue
            dt = self._environment_fromtimestamp(next_at)
            seconds_today = dt.hour * 3600 + dt.minute * 60 + dt.second
            if not (start <= seconds_today <= end):
                self._schedule_next_proactive(user, now=now)

    async def _apply_detail_presence_status(
        self,
        segment: dict[str, Any],
        detail: dict[str, Any] | None = None,
    ) -> None:
        if not self.enable_qq_presence_sync:
            return
        status = (detail or {}).get("presence_status") if isinstance(detail, dict) else None
        if not isinstance(status, dict):
            key = str((segment or {}).get("key") or "")
            enhanced = self.data.get("detail_enhanced_segments", {})
            snapshot = enhanced.get(key) if isinstance(enhanced, dict) else None
            status = snapshot.get("presence_status") if isinstance(snapshot, dict) else None
        # An omitted/unchanged status still matters when moving away from a
        # status that this plugin applied for the previous detail segment.
        # Treat it as a transition request, while leaving unrelated manual
        # QQ status changes untouched.
        if not isinstance(status, dict):
            status = {"mode": "unchanged"}
        key = str((segment or {}).get("key") or "")
        state = self.data.setdefault("qq_presence_state", {})
        if not isinstance(state, dict):
            state = {}
            self.data["qq_presence_state"] = state
        mode = str(status.get("mode") or status.get("status") or "unchanged").strip().lower()
        if mode in {"away", "invisible", "dnd", "do_not_disturb", "离开", "隐身", "请勿打扰", "勿扰"}:
            mode = "online"
        custom_text = _single_line(
            status.get("custom_text")
            or status.get("wording")
            or status.get("text")
            or status.get("label")
            or status.get("自定义状态")
            or status.get("文案"),
            28,
        )
        custom_sync_enabled = bool(getattr(self, "enable_qq_custom_presence_sync", False))
        custom_note = ""
        if mode in {"busy", "忙碌"}:
            if custom_sync_enabled:
                mode = "custom"
                custom_text = custom_text or "专注中"
            else:
                mode = "busy"
                custom_text = ""
                custom_note = "自定义短状态未开启，已改用标准忙碌"
        if mode in {"sleep", "睡觉", "睡眠"}:
            if custom_sync_enabled:
                mode = "custom"
                custom_text = custom_text or "休息中"
            else:
                return
        if mode in {"custom", "自定义", "自定义状态"} and not custom_sync_enabled:
            # A disabled custom-status feature must not clear a status managed
            # manually or by another QQ client.  Treat this plan as unchanged.
            return
        if mode in {"custom", "自定义", "自定义状态"} and not custom_text:
            return
        same_presence = (
            str(state.get("mode") or "") == mode
            and str(state.get("custom_text") or "") == custom_text
        )
        elapsed = _now_ts() - _safe_float(state.get("updated_at"), 0)
        previous_detail_key = str(state.get("detail_key") or "")
        detail_changed = bool(key and previous_detail_key and previous_detail_key != key)
        same_plan = (
            str(state.get("date") or "") == _today_key()
            and str(state.get("plan_date") or "") == str(self.data.get("detail_enhanced_day") or "")
            and previous_detail_key == key
        )
        if same_presence and not detail_changed and (
            (same_plan and bool(state.get("ok", False)) and elapsed < 10 * 60)
            or (not bool(state.get("ok", False)) and elapsed < 60 * 60)
        ):
            return
        if mode in {"", "unchanged", "keep", "保持", "不变"}:
            # Only reset a status with an explicit plugin ownership marker.
            # This avoids turning a user's manually selected QQ status into
            # online merely because the next schedule segment is quiet.
            if not detail_changed or not bool(state.get("managed_by_plugin", bool(previous_detail_key))):
                return
            if str(state.get("mode") or "") == "online" and not str(state.get("custom_text") or ""):
                state["detail_key"] = key
                state["date"] = _today_key()
                state["plan_date"] = str(self.data.get("detail_enhanced_day") or "")
                self._save_daily_state_sections({"qq_presence_state"})
                return
            ok, note = await self._set_qq_online_presence("online")
            state["detail_key"] = key
            state["date"] = _today_key()
            state["plan_date"] = str(self.data.get("detail_enhanced_day") or "")
            state["mode"] = "online"
            state["custom_text"] = ""
            state["reason"] = "当前日程段未要求自定义状态"
            state["updated_at"] = _now_ts()
            state["ok"] = bool(ok)
            state["note"] = _single_line(note, 120)
            state["managed_by_plugin"] = True
            self._save_daily_state_sections({"qq_presence_state"})
            return
        if mode in {"custom", "自定义", "自定义状态"}:
            ok, note = await self._set_qq_custom_presence(custom_text)
            mode = "custom"
            if not ok:
                note = f"{note}；未追加在线状态，保持账号原状态"
        else:
            ok, note = await self._set_qq_online_presence(mode)
        if custom_note:
            note = f"{note}；{custom_note}" if note else custom_note
        state["detail_key"] = key
        state["date"] = _today_key()
        state["plan_date"] = str(self.data.get("detail_enhanced_day") or "")
        state["mode"] = mode
        state["custom_text"] = custom_text
        state["reason"] = _single_line(status.get("reason"), 80)
        state["updated_at"] = _now_ts()
        state["ok"] = bool(ok)
        state["note"] = _single_line(note, 120)
        state["managed_by_plugin"] = True
        self._save_daily_state_sections({"qq_presence_state"})

    async def _ensure_current_detail_presence_status(self) -> None:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or str(plan.get("date") or "") != _today_key():
            return
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            return
        segment = self._current_detail_segment_for_update()
        if not segment:
            return
        snapshot = enhanced.get(str(segment.get("key") or ""))
        if not isinstance(snapshot, dict) or snapshot.get("status") != "done":
            if self._refresh_daily_state_location_from_plan(plan=plan, segment=segment):
                self._save_daily_state_sections({"daily_state"})
            if self.enable_qq_presence_sync:
                # Clear a previous plugin-managed segment status while the
                # new segment is still being generated. The completed detail
                # will apply its own status when it becomes available.
                await self._apply_detail_presence_status(segment, {})
            return
        if self._refresh_daily_state_location_from_plan(plan=plan, detail=snapshot, segment=segment):
            self._save_daily_state_sections({"daily_state"})
        if not self.enable_qq_presence_sync:
            return
        await self._apply_detail_presence_status(segment, snapshot)

    def _daily_diary_was_manually_deleted(self, day: Any) -> bool:
        date_key = _single_line(day, 16)
        if not date_key:
            return False
        deleted_days = self.data.get("daily_diary_deleted_days")
        if isinstance(deleted_days, str):
            deleted_days = [deleted_days]
        if not isinstance(deleted_days, list):
            return False
        return any(_single_line(value, 16) == date_key for value in deleted_days)

    def _daily_diary_delete_revision(self) -> int:
        try:
            return max(0, int(self.data.get("daily_diary_delete_revision") or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    def _is_daily_diary_due(self) -> bool:
        diary_minutes = self._parse_hhmm_to_minutes(runtime_persona_setting(self, "daily_diary_time", "23:10"))
        if diary_minutes is None:
            diary_minutes = 23 * 60 + 10
        now = self._environment_now()
        return now.hour * 60 + now.minute >= diary_minutes

    def _next_daily_diary_due_in_seconds(self, now: float | None = None) -> float | None:
        """Return the next diary maintenance deadline without creating another timer."""
        if not runtime_persona_setting(self, "enable_daily_diary", True):
            return None
        check_now = _safe_float(now, _now_ts())
        now_dt = self._environment_fromtimestamp(check_now)
        today = now_dt.strftime("%Y-%m-%d")
        if _single_line(self.data.get("diary_generated_day"), 16) == today:
            return None
        if self._daily_diary_was_manually_deleted(today):
            return None

        diary_minutes = self._parse_hhmm_to_minutes(runtime_persona_setting(self, "daily_diary_time", "23:10"))
        if diary_minutes is None:
            diary_minutes = 23 * 60 + 10
        due_dt = datetime.combine(
            now_dt.date(),
            datetime.min.time(),
            tzinfo=now_dt.tzinfo,
        ) + timedelta(minutes=diary_minutes)
        due_at = due_dt.timestamp()

        if _single_line(self.data.get("daily_diary_failed_day"), 16) == today:
            failed_at = _safe_float(self.data.get("daily_diary_failed_at"), 0, 0)
            if failed_at > 0:
                due_at = max(due_at, failed_at + 30 * 60)
        return max(0.0, due_at - check_now)

    async def _generate_daily_diary(self) -> dict[str, Any]:
        await self._ensure_yesterday_conversation_summary()
        return await generate_daily_diary(self)

    def _fallback_diary_payload(self, evidence: list[dict[str, str]] | None = None) -> dict[str, Any]:
        return fallback_diary_payload(self, evidence=evidence)

    def _polish_diary_text(self, text: Any, *, field: str = "body") -> str:
        cleaned = _single_line(text, 900 if field == "body" else 180)
        if not cleaned:
            return ""
        cleaned = re.sub(
            r"状态(?:大概|大约)?(?:是|偏|比较)?([^,，。；;]{1,10})[,，]\s*能量(?:大概|大约|大抵)?(?:是|在|停在|约)?\s*\d{1,3}\s*/\s*100(?:\s*左右)?[,，]?\s*适合[^。；;，,]{0,30}(?:推进|节奏|小事)",
            r"整个人还有点\1,就慢慢把注意力放回眼前的小事",
            cleaned,
        )
        cleaned = re.sub(
            r"今天(?:整体|大概)?偏([^,，。；;]{1,10})[,，]\s*能量(?:大概|大约|大抵)?(?:是|在|停在|约)?\s*\d{1,3}\s*/\s*100(?:\s*左右)?[,，]?\s*适合[^。；;，,]{0,30}(?:推进|节奏|小事)",
            r"今天有点\1,就慢慢把注意力放回眼前的小事",
            cleaned,
        )
        cleaned = re.sub(r"能量(?:大概|大约|大抵)?(?:是|在|停在|约)?\s*\d{1,3}\s*/\s*100(?:\s*左右)?", "精神还有点起伏", cleaned)
        cleaned = re.sub(r"能量(?:大概|大约|大抵)?(?:是|在|停在|约)?\s*\d{1,3}(?:\s*左右)?", "精神还有点起伏", cleaned)
        cleaned = re.sub(r"状态(?:大概|大约)?(?:是|偏|比较)?([^,，。；;]{1,10})", r"整个人有点\1", cleaned)
        cleaned = re.sub(r"今天(?:整体|大概)?偏([^,，。；;]{1,10})", r"今天有点\1", cleaned)
        cleaned = re.sub(r"状态(?:大概|大约)?(?:是|偏|比较)?([^,，。；;]{0,8}),?适合[^。；;，,]{0,20}(?:推进|节奏)", r"整个人有点\1", cleaned)
        cleaned = re.sub(r"今天(?:整体|大概)?偏([^,，。；;]{1,8}),?适合[^。；;，,]{0,20}(?:推进|节奏)", r"今天有点偏\1", cleaned)
        cleaned = re.sub(r"(?:先)?确认了?一下自己的状态", "在床边缓了一会儿", cleaned)
        cleaned = re.sub(r"适合(?:保持|继续)?(?:温和|平稳|稳定)?(?:慢慢)?(?:推进|推着走|节奏)", "可以慢一点来", cleaned)
        cleaned = re.sub(r"平稳推进", "慢慢来", cleaned)
        cleaned = re.sub(r"可分享(?:的)?(?:碎片|句子)[:：]?", "", cleaned)
        cleaned = re.sub(r"(?:主动计划|插件|模型|生成器|内部状态|状态报告)[:：]?", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,；;")
        if field == "share":
            cleaned = cleaned.rstrip("。.!！？")
            if any(marker in cleaned for marker in ("适合", "推进", "状态")) and not any(marker in cleaned for marker in ("醒", "梦", "路", "雨", "风", "杯", "灯", "窗", "课", "饭", "困")):
                cleaned = "今天有点慢半拍,想等遇到新的小事再讲给你听"
            return _single_line(cleaned, 90)
        if field == "summary":
            if any(marker in cleaned for marker in ("精神还有点起伏", "状态", "适合")) and not any(marker in cleaned for marker in ("醒", "梦", "路", "雨", "风", "杯", "灯", "窗", "课", "饭", "困")):
                cleaned = "把手边的一件小事慢慢收好，心里也腾出了一点位置。"
            return _single_line(cleaned, 120)
        return _single_line(cleaned, 520)

    def _polish_diary_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        polished = dict(payload)
        polished["summary"] = self._polish_diary_text(polished.get("summary"), field="summary")
        polished["body"] = self._polish_diary_text(polished.get("body"), field="body")
        polished["share_seed"] = self._polish_diary_text(polished.get("share_seed"), field="share")
        if not polished["summary"]:
            polished["summary"] = "今天留下的具体记录不多"
        if not polished["body"]:
            polished["body"] = "今天没有留下足够具体、可以确认的经历，就先如实记到这里。"
        return polished

    def _generate_fallback_long_term_events(self, state: dict[str, Any]) -> list[dict[str, str]]:
        events = self._generate_state_linked_long_term_events()
        if events:
            return events[:3]
        mood = _single_line(state.get("mood_bias"), 20) if isinstance(state, dict) else "平稳"
        return [
            {
                "title": "今日状态延续",
                "status": f"今天整体偏{mood},适合保持平稳节奏",
                "next_hint": "后续可根据对话自然延伸",
                "phase": "steady",
                "tendency": "状态更可能保持稳定或逐步回升",
            }
        ]

    def _normalize_story_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        return normalize_story_plan(self, payload)

    def _balance_proactive_events_for_day(
        self,
        events: list[dict[str, Any]],
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        for raw in events:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            if str(item.get("reason") or "") == "state_share":
                item["reason"] = "quiet_care"
            for key, fallback in (
                ("topic", "短短说一句"),
                ("why", "生活里刚好空出一点缝隙"),
                ("motive", "刚好停了一下，想短短说一句"),
                ("impulse", "想短短说一句"),
            ):
                item[key] = _single_line(item.get(key), 100) or fallback
            if str(item.get("action") or "message") == "message":
                item["action"] = self._preferred_action_for_story_event(item)
            prepared.append(item)
        if not prepared:
            return []
        ordered = sorted(prepared, key=self._story_plan_item_sort_key)
        buckets = ["morning", "noon", "afternoon", "evening", "late_night"]
        by_bucket: dict[str, list[dict[str, Any]]] = {bucket: [] for bucket in buckets}
        for item in ordered:
            bucket = self._proactive_daypart_bucket_for_event(item)
            if bucket in by_bucket:
                by_bucket[bucket].append(item)
        selected: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()

        def add(item: dict[str, Any]) -> None:
            if len(selected) >= limit:
                return
            identity = self._story_plan_item_identity("proactive_events", item)
            if identity in seen:
                return
            seen.add(identity)
            selected.append(item)

        for bucket in buckets:
            if by_bucket[bucket]:
                add(by_bucket[bucket][0])
        for bucket in buckets:
            cap = 1 if bucket == "late_night" else 2
            count = sum(1 for item in selected if self._proactive_daypart_bucket_for_event(item) == bucket)
            for item in by_bucket[bucket][1:]:
                if count >= cap:
                    break
                add(item)
                count += 1
        remaining = sorted(
            ordered,
            key=lambda item: (
                0 if str(item.get("action") or "message") != "message" else 1,
                self._event_priority(item),
                self._story_plan_item_sort_key(item),
            ),
        )
        for item in remaining:
            if len(selected) >= limit:
                break
            add(item)
        return sorted(selected, key=self._story_plan_item_sort_key)

    def _preferred_action_for_story_event(self, event: dict[str, Any]) -> str:
        reason = str(event.get("reason") or "check_in")
        text = " ".join(
            _single_line(event.get(key), 80)
            for key in ("topic", "why", "scene", "motive", "impulse")
        )
        if self._photo_text_available() and (
            reason in {"activity_share", "diary_share", "background_schedule", "noon_greeting", "evening_greeting"}
            or any(token in text for token in self._visual_share_tokens())
        ):
            return "photo_text"
        if self._screen_glance_available() and reason in {"check_in", "quiet_care", "background_schedule"}:
            return "screen_peek"
        if self._voice_available() and reason in {"quiet_care", "diary_share", "insomnia_night", "evening_greeting"}:
            return "voice"
        if self._poke_available() and reason in {"check_in", "quiet_care", "morning_greeting", "evening_greeting"}:
            return "poke"
        return "message"

    def _generate_morning_linked_proactive_events(self) -> list[dict[str, Any]]:
        state = self.data.get("daily_state", {})
        if not isinstance(state, dict):
            return []
        sleep_text = str(state.get("sleep") or "")
        conditions = state.get("conditions", [])
        if not isinstance(conditions, list):
            conditions = []

        morning_start, morning_end = 8 * 60 + 20, 9 * 60 + 50
        window_getter = getattr(self, "_morning_greeting_window", None)
        if callable(window_getter):
            try:
                candidate_start, candidate_end = window_getter()
                if 0 <= candidate_start < candidate_end <= 24 * 60:
                    morning_start, morning_end = candidate_start, candidate_end
            except Exception:
                pass

        def morning_window(*, delay_minutes: int, span_minutes: int) -> str:
            latest_start = max(morning_start, morning_end - 8)
            start = min(morning_start + max(0, delay_minutes), latest_start)
            end = min(morning_end, max(start + 8, start + max(8, span_minutes)))
            return f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"

        events: list[dict[str, Any]] = []
        if any(token in sleep_text for token in ("赖床", "闹钟", "起得有点迟", "还没完全开机", "懵懵", "有点懵")):
            events.append(
                {
                    "window": morning_window(delay_minutes=8, span_minutes=30),
                    "reason": "morning_greeting",
                    "action": "message",
                    "why": "迷迷糊糊醒来，虽然还想再睡，但先轻轻说声早安",
                    "topic": "赖床间隙的早安",
                    "motive": "迷迷糊糊醒来，虽然还想再睡，但先轻轻说声早安",
                    "scene": "睡意依旧，不想起床",
                    "tone": "迷糊",
                    "impulse": "虽然打算继续睡，但想轻轻说声早安",
                    "chain": [
                        {"kind": "name_only_opener"},
                        {"kind": "if_no_reply", "after_minutes": 80, "reason": "check_in", "topic": "赖床醒来", "motive": "回笼觉结束，看看用户是先醒了还是依旧在睡", "tone": "耐心等待"},
                        {"kind": "if_still_no_reply", "after_minutes": 140, "reason": "morning_greeting", "topic": "催用户起床", "motive": "用户依旧没有回应你的消息，该催用户起床了", "tone": "调侃"},
                    ],
                    "mood": "迷糊",
                }
            )
        elif any(token in sleep_text for token in ("睡得很浅", "半夜醒", "一晚上都在做梦", "失眠")):
            events.append(
                {
                    "window": morning_window(delay_minutes=6, span_minutes=28),
                    "reason": "morning_greeting",
                    "action": "message",
                    "why": "醒来还带着一点睡意时,迷迷糊糊先发一声早安。",
                    "topic": "没完全醒的早安",
                    "motive": "人还没完全清醒,但还是先想打个招呼",
                    "scene": "人还带着睡意的时候",
                    "tone": "迟钝",
                    "impulse": "想轻轻说声早安",
                    "chain": [
                        {"kind": "name_only_opener"},
                        {"kind": "if_no_reply", "after_minutes": 90, "reason": "check_in", "topic": "早安余韵", "motive": "已经清醒过来，但刚刚和用户说的早安还没得到回应,猜测用户还在休息", "tone": "耐心等待"},
                        {"kind": "if_still_no_reply", "after_minutes": 150, "reason": "morning_greeting", "topic": "催用户起床", "motive": "用户依旧没有回应你的消息，该催用户起床了", "tone": "调侃"},
                    ],
                    "mood": "迟钝",
                }
            )
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        if energy >= 62 and random.random() < 0.45:
            events.append(
                {
                    "window": morning_window(delay_minutes=3, span_minutes=24),
                    "reason": "morning_greeting",
                    "action": "message",
                    "why": "睡得很好,习惯性地想去打个招呼。",
                    "topic": "早安",
                    "motive": "昨晚睡得很好，刚醒来就去和用户打个招呼",
                    "scene": "刚从床上爬起来的时候",
                    "tone": "清爽",
                    "impulse": "想轻轻说早安",
                    "chain": [
                        {"kind": "name_only_opener"},
                        {"kind": "if_no_reply", "after_minutes": 85, "reason": "check_in", "topic": "早安余韵", "motive": "刚刚和用户说了早安但没得到回应,猜测用户还在休息", "tone": "耐心等待"},
                        {"kind": "if_still_no_reply", "after_minutes": 145, "reason": "morning_greeting", "topic": "催用户起床", "motive": "用户依旧没有回应你的消息，该催用户起床了", "tone": "调侃"},
                    ],
                    "mood": "清爽",
                }
            )
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            title = str(cond.get("title") or "")
            label = str(cond.get("label") or "")
            if "睡眠延续" in title and random.random() < 0.55:
                events.append(
                    {
                        "window": morning_window(delay_minutes=10, span_minutes=30),
                        "reason": "morning_greeting",
                        "action": "message",
                        "why": "睡意延续到白天,有种半梦半醒的感觉",
                        "topic": "刚醒来后脑子晕乎乎的",
                        "motive": "依旧带着睡意的早安问候",
                        "scene": "依旧带着睡意",
                        "tone": "半梦半醒",
                        "impulse": "醒来迷迷糊糊的，想轻轻说早安",
                        "mood": _single_line(cond.get("mood"), 20) or "迟钝",
                    }
                )
                break
            if any(token in label for token in ("赖床", "闹钟", "起得有点迟")):
                events.append(
                    {
                        "window": morning_window(delay_minutes=6, span_minutes=32),
                        "reason": "morning_greeting",
                        "action": "message",
                        "why": "早晨发生了一点生活小插曲，和用户抱怨一句或打个招呼。",
                        "topic": "早晨的生活小插曲",
                        "motive": "早上折腾了一下,想来找你吐个小槽",
                        "scene": "被早晨的小事故折腾了一下之后",
                        "tone": "迷糊又有点乱",
                        "impulse": "想顺手分享早上的生活小插曲",
                        "mood": "迷糊",
                    }
                )
                break
        return events[:2]

    def _generate_daypart_linked_proactive_events(self) -> list[dict[str, Any]]:
        state = self.data.get("daily_state", {})
        if not isinstance(state, dict):
            return []
        weather = self._weather_summary_text(self.data.get("daily_weather", {}))
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        sleep_text = str(state.get("sleep") or "")
        events: list[dict[str, Any]] = []
        if 36 <= energy <= 68 and random.random() < 0.58:
            events.append(
                {
                    "window": "12:10-13:30",
                    "reason": "noon_greeting",
                    "action": "message",
                    "why": "中午有些犯困，想短短打声招呼。",
                    "topic": "午后犯困",
                    "motive": "中午这会儿有点犯困，想短短说句话",
                    "scene": "午后犯困的时候",
                    "tone": "懒洋洋",
                    "impulse": "想趁午后休息时短短说一句",
                    "mood": "懒洋洋",
                }
            )
        if any(token in weather for token in ("晚霞", "晴", "阳光", "多云")) and random.random() < 0.52:
            events.append(
                {
                    "window": "17:20-19:10",
                    "reason": "activity_share",
                    "action": "photo_text" if self._photo_text_available() else "message",
                    "why": "傍晚天色好看时，想拍一张路上的画面给你看。",
                    "topic": "傍晚路上",
                    "motive": "傍晚路上的天色很好看，想拍给你看看",
                    "scene": "傍晚走在路上时",
                    "tone": "松弛",
                    "impulse": "想顺手分享傍晚路上的画面",
                    "mood": "松弛",
                }
            )
        if 45 <= energy <= 82 and random.random() < 0.46:
            events.append(
                {
                    "window": "15:20-17:10",
                    "reason": "check_in",
                    "action": "message",
                    "why": "下午短暂休息时，想轻轻问一句用户那边怎么样。",
                    "topic": "下午短暂休息",
                    "motive": "下午节奏缓下来一点，想看看用户是不是也能休息一下",
                    "scene": "下午短暂休息的时候",
                    "tone": "平静",
                    "impulse": "好奇用户在做什么",
                    "mood": "微松",
                }
            )
        if random.random() < 0.48:
            topic = self._pick_life_thought_topic("activity_share")
            action = "photo_text" if self._photo_text_available() and random.random() < 0.16 else "message"
            events.append(
                {
                    "window": "14:40-18:40" if 12 <= self._environment_now().hour < 18 else "19:20-21:40",
                    "reason": "activity_share",
                    "action": action,
                    "why": "日常里突然冒出一个小想法，想短短说一句。",
                    "topic": topic,
                    "motive": f"刚刚想到“{topic}”，想顺手分享一下",
                    "scene": "闲下来的时候",
                    "tone": "自然",
                    "impulse": "想把刚冒出来的小想法顺口提一下",
                    "mood": "微妙",
                }
            )
        if any(token in sleep_text for token in ("失眠", "睡得很浅", "半夜醒", "一晚上都在做梦")) and random.random() < 0.5:
            events.append(
                {
                    "window": "22:10-23:25",
                    "reason": "quiet_care",
                    "action": "message",
                    "why": "睡前还没完全困下来，想随便聊两句",
                    "topic": "睡前还没困下来",
                    "motive": "明明快该睡了，但还是想找用户说说话",
                    "scene": "准备睡觉但还没困下来的时候",
                    "tone": "平静",
                    "impulse": "想在睡前和用户聊天",
                    "mood": "安静",
                }
            )
        if energy < 42 and random.random() < 0.42:
            events.append(
                {
                    "window": "19:40-21:10",
                    "reason": "quiet_care",
                    "action": "message",
                    "why": "累了一天之后，想在睡前和用户聊聊天",
                    "topic": "一天快结束时",
                    "motive": "今天快结束了，睡前想聊两句",
                    "scene": "一天快结束的时候",
                    "tone": "疲惫",
                    "impulse": "想在睡前和用户聊天",
                    "mood": "疲惫",
                }
            )
        return events[:3]

    def _normalize_event_motive(self, item: dict[str, Any]) -> str:
        direct = _single_line(item.get("motive"), 80)
        if direct:
            return self._normalize_internal_motive_text(direct)
        reason = _single_line(item.get("reason"), 40)
        action = _single_line(item.get("action"), 20)
        topic = _single_line(item.get("topic"), 50)
        why = _single_line(item.get("why"), 80)
        scene = _single_line(item.get("scene"), 60)
        tone = _single_line(item.get("tone"), 24)
        impulse = _single_line(item.get("impulse"), 80)
        if impulse:
            return self._normalize_internal_motive_text(impulse)
        base = {
            "insomnia_night": "夜里还没睡着，想短短留一句",
            "state_share": "当前状态有变化,想让你知道",
            "quiet_care": "想到用户，想确认一下用户那边怎么样",
            "activity_share": "遇到一段可以分享的日常内容",
            "diary_share": "整理今日记录时想到可以分享",
            "important_date_share": "有个重要时间点值得提前提醒",
            "background_schedule": "当前日程有一点可以自然提到",
            "check_in": "刚好停下来,想看看那边有没有空",
            "morning_greeting": "早上这会儿想先把一句招呼放过去",
            "noon_greeting": "中午松下来时想短短说一句",
            "evening_greeting": "晚上慢下来时想先来你这边说一句",
        }.get(reason, "刚好停下来,想到可以短短说一句")
        if action == "screen_peek":
            base = "刚好有点空，想看看那边是不是还在忙"
        elif action == "photo_text":
            base = "刚刚看到的画面想分享一下"
        elif action == "poke":
            base = "想做一次轻量提醒"
        elif action == "voice":
            base = "这会儿更适合用语音表达"
        if topic and any(token in topic for token in ("日记", "笔记", "碎片", "念头", "半句", "想法")):
            base = "整理记录时发现一段适合分享的内容"
        elif topic and any(token in topic for token in self._visual_share_tokens()):
            base = "眼前有个具体小画面适合顺手分享"
        elif topic and any(token in topic for token in ("雨", "天气", "晚霞", "阳光")):
            base = "当前天气内容适合分享"
        elif why and len(why) <= 30:
            base = why
        if scene and tone:
            base = f"{scene}里有个可以自然提到的小切口"
        elif scene:
            base = f"{scene}里有个可以自然提到的小切口"
        elif tone and not topic:
            base = "这会儿适合短短说一句,状态只留在语气里"
        return self._normalize_internal_motive_text(_single_line(base, 80))

    def _dedupe_proactive_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in events:
            if not isinstance(item, dict):
                continue
            key = "|".join(
                [
                    _single_line(item.get("window"), 20),
                    _single_line(item.get("reason"), 40),
                    _single_line(item.get("action"), 20),
                    _single_line(item.get("topic"), 80),
                ]
            )
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _proactive_topic_signature(self, *parts: Any) -> str:
        normalized_parts: list[str] = []
        address_prefix = re.compile(
            r"^(?:[嗯唔哦噢诶欸啊呀哎嘿嗨]+[。！？!?…~～，,\s]*)?"
            r"(?:[\w\u4e00-\u9fffぁ-んァ-ヶー]{1,10}(?:大人|老师|主人|哥哥|姐姐|同学|宝宝|宝贝)"
            r"[，,、：:\s~～…]*|(?!(?:今天|现在|刚才|刚刚|这会儿|早上|中午|晚上|外面|天气|最近|等下|待会)[，,、：:])"
            r"[\w\u4e00-\u9fffぁ-んァ-ヶー]{1,3}[，,、：:]\s*)"
        )
        for part in parts:
            value = _single_line(part, 160)
            if not value:
                continue
            # 收件人称呼不是主题。先去掉句首称呼，避免不同内容仅因反复称呼
            # 同一用户而被误判为重复。
            normalized_parts.append(address_prefix.sub("", value, count=1).strip() or value)
        text = " ".join(normalized_parts)
        if not text:
            return ""
        school_stress_markers = (
            "上课", "课", "物理", "老师", "点名", "叫上去", "做题", "抓到",
            "发呆", "心跳", "紧张", "差点", "讲台",
        )
        if sum(1 for token in school_stress_markers if token in text) >= 2:
            return "school_class_anxiety"
        food_markers = ("食堂", "午饭", "中午", "菜", "咸", "吃")
        if sum(1 for token in food_markers if token in text) >= 2:
            return "noon_food_share"
        weather_markers = (
            "外面下雨", "外面下雪", "天气", "天晴", "晴吗", "晴天", "下雨", "没下雨",
            "雨声", "雨停", "雨雪停", "小雨", "中雨", "大雨", "阵雨", "雷雨", "雷暴", "降雨",
            "阴天", "天阴", "阴阴", "多云", "放晴", "太阳", "阳光", "晚霞", "天色", "气温",
            "降温", "升温", "起风", "风声", "下雪", "雪天", "雾霾",
        )
        if any(token in text for token in weather_markers):
            # 普通天气换一种说法仍是同一个主动话题。结构化预警和实时
            # 环境变化在候选层按事件指纹去重，不依赖这里放行。
            return "ordinary_weather_topic"
        image_markers = ("图", "图片", "照片", "拍", "自拍", "画面")
        if sum(1 for token in image_markers if token in text) >= 2:
            return "photo_share"
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", text)
        stopwords = {
            "刚才", "现在", "今天", "这个", "那个", "一下", "一点", "有点", "还是",
            "没有", "已经", "时候", "用户", "对方", "主动", "消息", "这会儿",
            "内容", "第一", "时间", "看到", "喜欢", "希望", "继续", "话题", "换个",
            "说过", "讲过", "提过", "聊过", "发过", "前面", "之前", "刚刚",
        }
        kept: list[str] = []
        def add_anchor(value: str) -> None:
            anchor = str(value or "").strip()
            if len(anchor) < 2 or anchor in stopwords:
                return
            if re.fullmatch(r"[了啦呀呢嘛吗吧啊哦噢诶嗯]+", anchor):
                return
            if re.fullmatch(r"[年月日点分秒上下左右前后早晚中午今晚昨今明]+", anchor):
                return
            if anchor not in kept:
                kept.append(anchor)

        for token in tokens:
            if re.fullmatch(r"[A-Za-z0-9_]{3,}", token):
                add_anchor(token.lower())
                continue
            cleaned = re.sub(r"(的时候|时候|一下|一点|了|啦|呀|呢|嘛|吗|吧|啊|哦|噢|诶|嗯)$", "", token)
            add_anchor(cleaned)
            if len(cleaned) >= 3:
                for size in (2, 3):
                    for index in range(0, max(0, len(cleaned) - size + 1)):
                        add_anchor(cleaned[index : index + size])
        return "|".join(kept)

    def _cleanup_recent_proactive_topics(self, user: dict[str, Any], *, now: float | None = None) -> list[dict[str, Any]]:
        now = now or _now_ts()
        raw = user.get("recent_proactive_topics", [])
        if not isinstance(raw, list):
            raw = []
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        kept: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            signature = str(item.get("signature") or "")
            visible_text = _single_line(item.get("text"), 240)
            derived_signature = self._proactive_topic_signature(visible_text) if visible_text else ""
            if signature == "morning_weather_check" or derived_signature == "ordinary_weather_topic":
                signature = "ordinary_weather_topic"
                item["signature"] = signature
            if signature == "ordinary_weather_topic":
                configured_minutes = self._proactive_dedup_window_minutes("weather", 1080)
                retention = 30 * 24 * 3600 if configured_minutes <= 0 else max(18 * 3600, configured_minutes * 60)
            else:
                configured_minutes = self._proactive_dedup_window_minutes("sent", 240)
                retention = 30 * 24 * 3600 if configured_minutes <= 0 else max(6 * 3600, configured_minutes * 60)
            if now - _safe_float(item.get("ts"), 0) > retention:
                continue
            if callable(meta_leak_checker) and (
                meta_leak_checker(str(item.get("text") or ""))
                or meta_leak_checker(str(item.get("signature") or ""))
            ):
                continue
            kept.append(item)
        user["recent_proactive_topics"] = kept[-12:]
        return user["recent_proactive_topics"]

    def _proactive_dedup_window_minutes(self, kind: str, default: int) -> int:
        key = (
            "proactive_dedup_weather_window_minutes"
            if kind == "weather"
            else "proactive_dedup_last_message_window_minutes"
            if kind == "last_message"
            else "proactive_dedup_sent_window_minutes"
        )
        raw = runtime_persona_setting(self, key, default)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return max(0, int(default))

    @staticmethod
    def _proactive_dedup_age_allowed(age: float, window_minutes: int) -> bool:
        return window_minutes <= 0 or age <= window_minutes * 60

    def _topic_signature_similar(self, left: str, right: str, *, use_proactive_dedup_config: bool = False) -> bool:
        if not left or not right:
            return False
        if left == right:
            return True
        left_set = {part for part in left.split("|") if part}
        right_set = {part for part in right.split("|") if part}
        if not left_set or not right_set:
            return False
        common = left_set & right_set
        smaller_size = min(len(left_set), len(right_set))
        min_shared_tokens = 1
        overlap_floor = 0.0
        if use_proactive_dedup_config:
            try:
                min_shared_tokens = max(1, min(4, int(runtime_persona_setting(self, "proactive_dedup_min_shared_tokens", 1))))
            except (TypeError, ValueError):
                min_shared_tokens = 1
            try:
                overlap_floor = max(0.0, min(1.0, float(runtime_persona_setting(self, "proactive_dedup_min_overlap_ratio", 0.0))))
            except (TypeError, ValueError):
                overlap_floor = 0.0
        if smaller_size <= 2:
            return len(common) >= min_shared_tokens
        overlap = len(common) / smaller_size
        return bool(
            (len(common) >= 2 and overlap >= max(0.5, overlap_floor))
            or (len(common) >= 3 and overlap >= max(0.3, overlap_floor))
            or (len(common) >= 4 and overlap >= max(0.18, overlap_floor))
            or (len(common) >= 6 and overlap >= overlap_floor)
        )

    def _recent_proactive_topic_repeated(self, user: dict[str, Any], signature: str, *, now: float | None = None) -> bool:
        if not signature:
            return False
        check_now = now or _now_ts()
        for item in self._cleanup_recent_proactive_topics(user, now=check_now):
            item_signature = str(item.get("signature") or "")
            is_weather = signature == "ordinary_weather_topic" or item_signature == "ordinary_weather_topic"
            window_minutes = self._proactive_dedup_window_minutes(
                "weather" if is_weather else "sent",
                1080 if is_weather else 240,
            )
            if not self._proactive_dedup_age_allowed(check_now - _safe_float(item.get("ts"), 0), window_minutes):
                continue
            if self._topic_signature_similar(signature, item_signature, use_proactive_dedup_config=True):
                return True
        return False

    def _remember_proactive_topic(self, user: dict[str, Any], *, text: str = "", topic: str = "", motive: str = "") -> None:
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        if callable(meta_leak_checker) and (
            meta_leak_checker(text) or meta_leak_checker(topic) or meta_leak_checker(motive)
        ):
            logger.warning("跳过记录疑似工具循环摘要的主动话题记忆")
            return
        signature = self._proactive_topic_signature(text, topic, motive)
        if not signature:
            return
        recent = self._cleanup_recent_proactive_topics(user)
        recent.append(
            {
                "ts": _now_ts(),
                "signature": signature,
                "text": _single_line(text or topic or motive, 120),
            }
        )
        del recent[:-12]

    def _proactive_dedup_enabled_policies(self) -> frozenset[str]:
        raw_value = runtime_persona_setting(self, "proactive_dedup_policies", None)
        if raw_value is None:
            return frozenset({"semantic", "content_fingerprint", "life_event"})
        raw = str(raw_value).strip().lower()
        return frozenset(part for part in re.split(r"[,，;；\s]+", raw) if part)

    def _recent_proactive_text_duplicate_reason(
        self,
        user: dict[str, Any],
        *,
        text: str = "",
        topic: str = "",
        motive: str = "",
        now: float | None = None,
    ) -> str:
        if not bool(runtime_persona_setting(self, "proactive_dedup_enabled", True)):
            return ""
        signature = self._proactive_topic_signature(text, topic, motive)
        if not signature:
            return ""
        check_now = now or _now_ts()
        for item in self._cleanup_recent_proactive_topics(user, now=check_now):
            old_signature = str(item.get("signature") or "")
            if not self._topic_signature_similar(signature, old_signature, use_proactive_dedup_config=True):
                continue
            age = check_now - _safe_float(item.get("ts"), 0)
            duplicate_window = self._proactive_dedup_window_minutes(
                "weather" if signature == "ordinary_weather_topic" else "sent",
                1080 if signature == "ordinary_weather_topic" else 240,
            )
            if not self._proactive_dedup_age_allowed(age, duplicate_window):
                continue
            old_text = _single_line(item.get("text"), 80)
            if signature == "ordinary_weather_topic":
                return f"近期已经主动聊过天气" + (f"：{old_text}" if old_text else "")
            return f"近 {max(1, int(age // 60))} 分钟已发送相似主动" + (f"：{old_text}" if old_text else "")
        last_message = _single_line(_strip_internal_message_blocks(user.get("last_companion_message")), 500)
        # last_reply_at is inbound user activity, so it must never make an old
        # companion message look newly delivered.
        last_at = _safe_float(user.get("last_companion_message_at"), 0)
        sending_started_at = _safe_float(user.get("proactive_sending_started_at"), 0)
        unconfirmed_current_candidate = bool(
            sending_started_at > 0
            and last_at >= sending_started_at
            and user.get("proactive_sending")
        )
        if (
            bool(runtime_persona_setting(self, "proactive_dedup_last_message_enabled", True))
            and not unconfirmed_current_candidate
            and last_message
            and last_at > 0
            and self._proactive_dedup_age_allowed(
                check_now - last_at,
                self._proactive_dedup_window_minutes("last_message", 240),
            )
        ):
            last_signature = self._proactive_topic_signature(last_message)
            if self._topic_signature_similar(signature, last_signature, use_proactive_dedup_config=True):
                age = check_now - last_at
                return f"近 {max(1, int(age // 60))} 分钟聊天里已经说过相似内容：{_single_line(last_message, 80)}"
        return ""

    def _pending_proactive_send_retry(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any] | None:
        payload = user.get("pending_proactive_send_retry") if isinstance(user, dict) else None
        if not isinstance(payload, dict) or not payload.get("active"):
            return None
        current = _now_ts() if now is None else float(now)
        if _safe_float(payload.get("expires_at"), 0) <= current:
            self._clear_pending_proactive_send_retry(user)
            return None
        delivery_key_getter = getattr(self, "_planned_proactive_delivery_key", None)
        current_delivery_key = delivery_key_getter(user) if callable(delivery_key_getter) else ""
        retry_delivery_key = _single_line(payload.get("delivery_key"), 80)
        retry_freshness = _single_line(payload.get("freshness"), 24)
        retry_profile = _single_line(payload.get("route_retry_profile"), 32) or "normal"
        cancel_if_new_inbound = bool(payload.get("route_cancel_if_new_inbound", True))
        retry_fresh_until = _safe_float(payload.get("fresh_until_at"), 0)
        retry_activity_at = _safe_float(payload.get("private_activity_at"), 0)
        retry_inbound_count = _safe_int(payload.get("private_inbound_count"), 0)
        current_activity_at = self._latest_private_user_activity_ts(user)
        current_inbound_count = _safe_int(user.get("private_inbound_count"), 0)
        if (
            not retry_delivery_key
            or retry_delivery_key != current_delivery_key
            or (retry_profile == "normal" and retry_freshness != "durable")
            or retry_fresh_until <= current
            or (
                cancel_if_new_inbound
                and (current_activity_at > retry_activity_at or current_inbound_count > retry_inbound_count)
            )
        ):
            self._clear_pending_proactive_send_retry(user)
            return None
        image_path = str(payload.get("image_path") or "").strip()
        text = _single_line(payload.get("text"), 1200)
        validator = getattr(self, "_validate_proactive_outbound_candidate", None)
        if callable(validator):
            try:
                validation = validator(
                    text,
                    image_path=image_path,
                    reason=_single_line(payload.get("reason"), 40),
                    action=_single_line(payload.get("action"), 40),
                    source="retry_load",
                )
            except Exception:
                validation = {"decision": "send", "text": text}
            decision = str(validation.get("decision") or "send")
            if decision == "drop":
                self._clear_pending_proactive_send_retry(user)
                return None
            if decision == "rewrite":
                text = _single_line(validation.get("text"), 1200)
                payload["text"] = text
        if image_path and not re.match(r"^(?:https?://|file://|data:)", image_path, flags=re.I):
            try:
                if not Path(image_path).exists():
                    self._clear_pending_proactive_send_retry(user)
                    return None
            except Exception:
                self._clear_pending_proactive_send_retry(user)
                return None
        if not text and not image_path:
            self._clear_pending_proactive_send_retry(user)
            return None
        return payload

    def _clear_pending_proactive_send_retry(self, user: dict[str, Any]) -> None:
        if isinstance(user, dict):
            user["pending_proactive_send_retry"] = {}

    def _abandon_failed_proactive_retry_candidate(
        self,
        user: dict[str, Any],
        *,
        note: str,
        now: float,
        delay_hours: tuple[float, float],
    ) -> None:
        self._clear_pending_proactive_send_retry(user)
        self._mark_planned_candidate_status(user, "dropped", note)
        self._clear_pending_proactive_plan(user)
        self._schedule_next_proactive(user, now=now, delay_hours=delay_hours)

    def _store_or_advance_proactive_send_retry(
        self,
        user: dict[str, Any],
        *,
        text: str,
        image_path: str,
        extra_components: list[Any],
        reason: str,
        action: str,
        action_summary: str,
        error_text: str,
        photo_subject_owner: str = "",
        now: float | None = None,
    ) -> str:
        if not isinstance(user, dict):
            return "无法保存待重发内容"
        current = _now_ts() if now is None else float(now)
        delivery_snapshot_getter = getattr(self, "_ensure_planned_proactive_delivery_state", None)
        delivery_snapshot = delivery_snapshot_getter(user, now=current) if callable(delivery_snapshot_getter) else {}
        freshness = _single_line(delivery_snapshot.get("freshness"), 24) if isinstance(delivery_snapshot, dict) else ""
        delivery_key = _single_line(delivery_snapshot.get("key"), 80) if isinstance(delivery_snapshot, dict) else ""
        existing = user.get("pending_proactive_send_retry")
        previous_count = _safe_int(existing.get("retry_count"), 0, 0, 10) if isinstance(existing, dict) else 0
        retry_count = previous_count + 1
        retry_profile = _single_line(user.get("planned_proactive_route_retry_profile"), 32) or "normal"
        retry_limit = 4 if retry_profile == "until_expiry" else 2
        clean_error = _single_line(error_text, 180)
        error_hint = ""
        if clean_error:
            compact_error = clean_error.lower()
            if "retcode=1200" in compact_error and "eventchecker" in compact_error:
                error_hint = "QQ/NTQQ 拒绝发送（目标当前不可私聊或客户端临时异常）"
            elif "timeout" in compact_error:
                error_hint = "平台发送超时"
            elif "actionfailed" in compact_error or "failed" in compact_error:
                error_hint = "平台发送失败"
            else:
                error_hint = clean_error
        if retry_count > retry_limit:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，待重发内容连续失败，已放弃复用并重新排程",
                now=current,
                delay_hours=(12, 24),
            )
            return "发送失败，待重发内容连续失败，已放弃复用并重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        if (freshness != "durable" and retry_profile == "normal") or not delivery_key:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，当前候选依赖即时语境，已放弃复用并重新编排",
                now=current,
                delay_hours=(1.5, 4.0),
            )
            return "发送失败，当前候选依赖即时语境，已放弃复用并重新编排" + (f"；原因：{error_hint}" if error_hint else "")
        if extra_components:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，包含复杂组件，已放弃复用并重新排程",
                now=current,
                delay_hours=(6, 12),
            )
            return "发送失败，包含复杂组件，未缓存待重发内容，已延后重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        clean_text = _single_line(text, 1200)
        clean_image = _path_text(image_path, 1000)
        if not clean_text and not clean_image:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，无可复用内容，已放弃复用并重新排程",
                now=current,
                delay_hours=(6, 12),
            )
            return "发送失败，无可复用内容，已延后重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        validator = getattr(self, "_validate_proactive_outbound_candidate", None)
        unsafe_retry_text = False
        if callable(validator):
            try:
                validation = validator(
                    clean_text,
                    image_path=clean_image,
                    extra_components=extra_components,
                    reason=reason,
                    action=action,
                    source="retry_store",
                )
            except Exception:
                validation = {"decision": "send", "text": clean_text}
            decision = str(validation.get("decision") or "send")
            if decision == "drop":
                unsafe_retry_text = True
            elif decision == "rewrite":
                clean_text = _single_line(validation.get("text"), 1200)
        else:
            meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
            instruction_leak_checker = getattr(self, "_is_proactive_instruction_leak_text", None)
            try:
                unsafe_retry_text = bool(clean_text) and (
                    (callable(meta_leak_checker) and meta_leak_checker(clean_text))
                    or (callable(instruction_leak_checker) and instruction_leak_checker(clean_text))
                    or self._is_proactive_delivery_receipt_text(clean_text)
                )
            except Exception:
                unsafe_retry_text = False
        if unsafe_retry_text:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，候选正文疑似内部提示词/执行指令泄漏，已放弃复用并重新排程",
                now=current,
                delay_hours=(2, 6),
            )
            return "发送失败，候选正文疑似内部提示词/执行指令泄漏，已放弃复用并重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        if not clean_text and not clean_image:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，清理后无可复用内容，已放弃复用并重新排程",
                now=current,
                delay_hours=(6, 12),
            )
            return "发送失败，清理后无可复用内容，已延后重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        if retry_profile == "until_expiry":
            retry_delay_seconds = 3 * 60 if retry_count <= 1 else 8 * 60
        elif retry_profile == "short_lived":
            retry_delay_seconds = 2 * 60 if retry_count <= 1 else 5 * 60
        elif retry_profile == "while_anchor_live":
            retry_delay_seconds = 5 * 60 if retry_count <= 1 else 12 * 60
        else:
            retry_delay_seconds = 8 * 60 if retry_count <= 1 else 20 * 60
        planned_expire_at = _safe_float(delivery_snapshot.get("expire_at"), 0) if isinstance(delivery_snapshot, dict) else 0
        fresh_until_at = min(current + 72 * 3600, planned_expire_at) if planned_expire_at > current else current
        if fresh_until_at <= current + retry_delay_seconds:
            self._abandon_failed_proactive_retry_candidate(
                user,
                note="发送失败，候选在下一次重试前会失效，已放弃复用并重新编排",
                now=current,
                delay_hours=(1.5, 4.0),
            )
            return "发送失败，候选在下一次重试前会失效，已重新编排" + (f"；原因：{error_hint}" if error_hint else "")
        user["pending_proactive_send_retry"] = {
            "active": True,
            "created_at": _safe_float(existing.get("created_at"), current) if isinstance(existing, dict) else current,
            "updated_at": current,
            "expires_at": current + 72 * 3600,
            "fresh_until_at": fresh_until_at,
            "retry_count": retry_count,
            "text": clean_text,
            "image_path": clean_image,
            "reason": _single_line(reason, 40) or "check_in",
            "action": _single_line(action, 40) or "message",
            "action_summary": _single_line(action_summary, 500),
            "photo_subject_owner": _normalize_photo_subject_owner(photo_subject_owner),
            "last_error": clean_error,
            "delivery_key": delivery_key,
            "freshness": freshness,
            "route_retry_profile": retry_profile,
            "route_cancel_if_new_inbound": bool(
                user.get("planned_proactive_route_cancel_if_new_inbound", True)
            ),
            "private_activity_at": self._latest_private_user_activity_ts(user),
            "private_inbound_count": _safe_int(user.get("private_inbound_count"), 0),
        }
        user["next_proactive_at"] = current + retry_delay_seconds
        user["planned_proactive_window_start_at"] = user["next_proactive_at"]
        user["planned_proactive_delivery_state"] = "retrying"
        return f"发送失败，已保留待重发内容，约 {max(1, int(retry_delay_seconds // 60))} 分钟后第 {retry_count} 次重试" + (f"；原因：{error_hint}" if error_hint else "")

    def _activity_share_global_signature(self, user: dict[str, Any], *, text: str = "", action_summary: str = "") -> str:
        state = self.data.get("daily_state", {})
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        parts: list[Any] = [
            user.get("planned_proactive_topic"),
            user.get("planned_proactive_motive"),
            action_summary,
        ]
        if isinstance(current_item, dict):
            parts.extend(
                [
                    current_item.get("time"),
                    current_item.get("activity"),
                    current_item.get("message_seed"),
                ]
            )
        if isinstance(state, dict):
            parts.extend(
                [
                    state.get("activity"),
                    state.get("current_activity"),
                    state.get("message_seed"),
                    state.get("mood_bias"),
                ]
            )
        parts.append(text)
        signature = self._proactive_topic_signature(*parts)
        if signature:
            return signature
        raw = " ".join(_single_line(part, 120) for part in parts if _single_line(part, 120))
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16] if raw else ""

    def _cleanup_global_activity_share_topics(self, *, now: float | None = None) -> list[dict[str, Any]]:
        check_now = now or _now_ts()
        runtime = self.data.setdefault("proactive_runtime", {})
        if not isinstance(runtime, dict):
            runtime = {}
            self.data["proactive_runtime"] = runtime
        raw = runtime.get("recent_activity_shares")
        if not isinstance(raw, list):
            raw = []
        kept = [
            item for item in raw
            if isinstance(item, dict) and check_now - _safe_float(item.get("ts"), 0) <= 90 * 60
        ]
        runtime["recent_activity_shares"] = kept[-12:]
        return runtime["recent_activity_shares"]

    def _activity_share_recently_sent_elsewhere(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        text: str = "",
        action_summary: str = "",
        now: float | None = None,
    ) -> str:
        signature = self._activity_share_global_signature(user, text=text, action_summary=action_summary)
        if not signature:
            return ""
        for item in self._cleanup_global_activity_share_topics(now=now):
            if str(item.get("user_id") or "") == str(user_id):
                continue
            if self._topic_signature_similar(signature, str(item.get("signature") or "")):
                return _single_line(item.get("text"), 80) or "同一日常碎片刚刚已分享给其他私聊对象"
        return ""

    def _remember_global_activity_share(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        text: str = "",
        action_summary: str = "",
    ) -> None:
        signature = self._activity_share_global_signature(user, text=text, action_summary=action_summary)
        if not signature:
            return
        recent = self._cleanup_global_activity_share_topics()
        recent.append(
            {
                "ts": _now_ts(),
                "user_id": str(user_id),
                "signature": signature,
                "text": _single_line(text or user.get("planned_proactive_topic") or user.get("planned_proactive_motive"), 120),
            }
        )
        del recent[:-12]

    def _activity_share_duplicate_block_remaining(self, user: dict[str, Any], *, now: float | None = None) -> float:
        check_now = now or _now_ts()
        until = _safe_float(user.get("activity_share_duplicate_block_until"), 0)
        return max(0.0, until - check_now)

    def _block_duplicate_activity_share_for_user(
        self,
        user: dict[str, Any],
        *,
        duplicate_note: str = "",
        now: float | None = None,
        seconds: float = 90 * 60,
    ) -> None:
        check_now = now or _now_ts()
        user["activity_share_duplicate_block_until"] = check_now + max(60.0, float(seconds or 0))
        user["activity_share_duplicate_block_note"] = _single_line(duplicate_note, 120)
        user["last_activity_share_duplicate_block_at"] = check_now

    def _format_recent_proactive_topics_hint(self, user: dict[str, Any]) -> str:
        recent = self._cleanup_recent_proactive_topics(user)
        if not recent:
            return ""
        lines: list[str] = []
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        for item in recent[-4:]:
            text = _single_line(item.get("text"), 80)
            if not text:
                continue
            if callable(meta_leak_checker) and meta_leak_checker(text):
                continue
            when = self._format_timestamp_elapsed(item.get("ts"))
            lines.append(f"- {when}说过：{text}")
        if any(str(item.get("signature") or "") == "ordinary_weather_topic" for item in recent):
            lines.append("- 最近已经用天气开过话题；除非本轮原因是刚发生的环境突变或官方预警，否则这次不要再写天气、气温、下雨、天色，也不要追问对方那边的天气。")
        return "\n".join(lines)

    def _normalize_story_items(self, raw_items: Any, text_key: str) -> list[dict[str, Any]]:
        return normalize_story_items(self, raw_items, text_key)

    def _normalize_long_term_events(self, raw_items: Any) -> list[dict[str, str]]:
        return normalize_long_term_events(self, raw_items)

    def _dedupe_long_term_events(self, events: list[dict[str, str]]) -> list[dict[str, str]]:
        deduped: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in events:
            if not isinstance(item, dict):
                continue
            key = _single_line(item.get("title"), 80)
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _generate_state_linked_long_term_events(self) -> list[dict[str, str]]:
        state = self.data.get("daily_state", {})
        if not isinstance(state, dict):
            return []
        weather = self._weather_summary_text(self.data.get("daily_weather", {}))
        conditions = state.get("conditions", [])
        if not isinstance(conditions, list):
            return []
        candidates: list[dict[str, str]] = []
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            phase = _single_line(cond.get("phase"), 24)
            kind = _single_line(cond.get("kind"), 24)
            label = _single_line(cond.get("label"), 80)
            if kind == "health" and phase == "mild_discomfort":
                candidates.extend(
                    [
                        {
                            "title": "降低当日活动强度",
                            "status": "轻微不适,适合降低活动强度",
                            "next_hint": "若休息或收到关心反馈,后续更可能进入恢复阶段",
                            "phase": phase,
                            "tendency": "倾向缓慢恢复,也可能延续为轻微不适",
                        },
                        {
                            "title": "观察不适是否缓解",
                            "status": label or "当前仍有轻微不适",
                            "next_hint": "若晚些时候状态回升,主动分享意愿可能增加",
                            "phase": phase,
                            "tendency": "恢复倾向受休息与关心反馈影响",
                        },
                    ]
                )
            elif kind in {"recovery_afterglow", "health_tail"} or phase in {"afterglow", "tail"}:
                candidates.extend(
                    [
                        {
                            "title": "观察是否回到稳定节奏",
                            "status": "状态正在回稳,但仍有轻微波动",
                            "next_hint": "若外部环境和情绪稳定,后续分享意愿可能上升",
                            "phase": phase or kind,
                            "tendency": "倾向回稳,也可能残留轻微尾声",
                        }
                    ]
                )
            if kind == "sleep" and phase == "sleep_debt":
                candidates.append(
                    {
                        "title": "留意睡眠债恢复情况",
                        "status": "精神能量未满,白天反应可能偏慢",
                        "next_hint": "若白天恢复顺利,晚间表达会更轻松；否则保持低强度",
                        "phase": phase,
                        "tendency": "倾向先延续低能量,再逐步回稳",
                    }
                )
            if kind in {"care_warmth", "soft_afterglow"}:
                candidates.append(
                    {
                        "title": "记录关心反馈后的回暖",
                        "status": "收到关心反馈后,语气可能更柔和",
                        "next_hint": "若互动氛围稳定,后续轻分享意愿可能增加",
                        "phase": phase or kind,
                        "tendency": "倾向回稳,小概率保留轻度正向余波",
                    }
                )
        if weather != "暂无天气信息" and any(token in weather for token in ("晴", "阳光", "多云", "晚霞")) and random.random() < 0.45:
            candidates.append(
                {
                    "title": "留意傍晚会不会有值得拍下来的天色",
                    "status": f"天气提供了可用于生活背景的外部线索：{weather}",
                    "next_hint": "若当时情绪稳定,可能提高 photo_text 分享概率",
                    "phase": "weather_bonus",
                    "tendency": "倾向轻量分享,不倾向正式开启长对话",
                }
            )
        picked: list[dict[str, str]] = []
        for item in candidates:
            chance = 0.55
            tendency = str(item.get("tendency") or "")
            if "回稳" in tendency:
                chance = 0.45
            if "拖一阵" in tendency:
                chance = 0.4
            if random.random() < chance:
                picked.append(item)
        return picked[:3]

    def _generate_weather_linked_proactive_events(self) -> list[dict[str, Any]]:
        weather = self._weather_summary_text(self.data.get("daily_weather", {}))
        if weather == "暂无天气信息":
            return []
        events: list[dict[str, Any]] = []
        if any(token in weather for token in ("雨", "阵雨", "雷", "小雨", "中雨", "大雨")) and random.random() < 0.24:
            events.append(
                {
                    "source": "weather_context",
                    "weather_linked": True,
                    "window": self._pick_weather_window("rain"),
                    "reason": "activity_share",
                    "action": "message",
                    "why": f"外面在下雨，想短短提一句。{weather}",
                    "topic": "外面下雨了",
                    "motive": "听见外面下雨，想短短提一声",
                    "mood": "安静",
                }
            )
        if any(token in weather for token in ("晴", "阳光", "多云", "晚霞")) and random.random() < 0.12:
            events.append(
                {
                    "source": "weather_context",
                    "weather_linked": True,
                    "window": self._pick_weather_window("clear"),
                    "reason": "activity_share",
                    "action": "message",
                    "why": f"外面的天色有点好看，想短短提一句。{weather}",
                    "topic": "天色有点好看",
                    "motive": "外面天色不错",
                    "mood": "松弛",
                }
            )
        return events[:1]

    def _pick_weather_window(self, weather_kind: str) -> str:
        hour = self._environment_now().hour
        if weather_kind == "rain":
            if 6 <= hour < 11:
                return "08:20-10:40"
            if 11 <= hour < 17:
                return "12:40-16:30"
            if 17 <= hour < 23:
                return "18:10-21:10"
            return "09:00-10:30"
        if 16 <= hour < 20:
            return "17:10-19:20"
        return "15:30-18:10"

    def _format_plan_for_diary(self, plan: dict[str, Any]) -> str:
        return format_plan_for_diary(self, plan)

    async def _ensure_daily_state(
        self,
        force: bool = False,
        *,
        skip_conversation_summary: bool = False,
        passive_fast: bool = False,
    ) -> dict[str, Any]:
        request_started = time.monotonic()
        scope = self._daily_generation_scope()
        force_cache = self._daily_force_result_cache("_daily_state_force_results_by_scope")
        lock = self._daily_generation_lock("_daily_state_generation_lock")
        async with lock:
            completed_entry = force_cache.get(scope, {})
            if force and _safe_float(completed_entry.get("completed_at"), 0) >= request_started:
                completed = completed_entry.get("result")
                if isinstance(completed, dict):
                    return completed
            state = await self._ensure_daily_state_once(
                force=force,
                skip_conversation_summary=skip_conversation_summary,
                passive_fast=passive_fast,
            )
            if force and isinstance(state, dict):
                force_cache[scope] = {
                    "result": state,
                    "completed_at": time.monotonic(),
                }
            return state

    async def _ensure_daily_state_once(
        self,
        force: bool = False,
        *,
        skip_conversation_summary: bool = False,
        passive_fast: bool = False,
    ) -> dict[str, Any]:
        today = _today_key()
        if passive_fast and not force:
            cached_state = self.data.get("daily_state", {})
            if isinstance(cached_state, dict) and cached_state.get("date") == today:
                cached_weather = self.data.get("daily_weather", {})
                weather = cached_weather if isinstance(cached_weather, dict) and cached_weather.get("date") == today else {
                    "date": today,
                    "prompt": "暂无天气信息",
                    "source": "passive_fast",
                }
                if not runtime_persona_setting(self, "enable_humanized_states", True):
                    state = dict(DEFAULT_HUMANIZED_STATE)
                    state.update(self._base_state_values())
                    state["date"] = today
                    state["weather"] = self._weather_summary_text(weather)
                    return state
                async with self._data_lock:
                    before = json.dumps(self.data.get("daily_state", {}), ensure_ascii=False, sort_keys=True, default=str)
                    deleted_sections = self._cleanup_expired_conditions() or set()
                    self._ensure_time_based_hunger_condition()
                    state = self._compose_state_from_conditions(weather)
                    after = json.dumps(state, ensure_ascii=False, sort_keys=True, default=str)
                    if before != after or deleted_sections:
                        self.data["daily_state"] = state
                        save_sections = {
                            "daily_state",
                            "state_conditions",
                            "body_cycle_state",
                        } - set(deleted_sections)
                        self._save_data_sync(
                            sections=save_sections,
                            deleted_sections=deleted_sections,
                        )
                    return state
            cached_weather = self.data.get("daily_weather", {})
            weather = cached_weather if isinstance(cached_weather, dict) and cached_weather.get("date") == today else {
                "date": today,
                "prompt": "暂无天气信息",
                "source": "passive_fast",
            }
            if not runtime_persona_setting(self, "enable_humanized_states", True):
                state = dict(DEFAULT_HUMANIZED_STATE)
                state.update(self._base_state_values())
                state["date"] = today
                state["weather"] = self._weather_summary_text(weather)
                return state
            return self._compose_state_from_conditions(weather)
        weather = await self._ensure_weather_context(force=force)
        await self._ensure_yesterday_screen_diary_context(force=force)
        if not skip_conversation_summary:
            await self._ensure_yesterday_conversation_summary(force=force)
        async with self._data_lock:
            if not runtime_persona_setting(self, "enable_humanized_states", True) and not force:
                state = dict(DEFAULT_HUMANIZED_STATE)
                state.update(self._base_state_values())
                state["date"] = today
                state["weather"] = self._weather_summary_text(weather)
                self.data["daily_state"] = state
                self._save_data_sync(sections={"daily_state"})
                return state

            needs_generation = force or self.data.get("state_generated_day") != today
            if not needs_generation:
                deleted_sections = self._cleanup_expired_conditions() or set()
                self._ensure_time_based_hunger_condition()
                state = self._compose_state_from_conditions(weather)
                self.data["daily_state"] = state
                save_sections = {
                    "daily_state",
                    "state_conditions",
                    "body_cycle_state",
                    "hunger_window_attempts",
                } - set(deleted_sections)
                self._save_data_sync(
                    sections=save_sections,
                    deleted_sections=deleted_sections,
                )
                return state

        generation_day = _today_key()
        deferred_updates: dict[str, Any] = {}
        generated_conditions = await self._generate_state_conditions(
            weather,
            deferred_state_updates=deferred_updates,
        )

        async with self._data_lock:
            deleted_sections: set[str] = set()
            if not force and self.data.get("state_generated_day") == generation_day:
                deleted_sections = self._cleanup_expired_conditions() or set()
            else:
                deleted_sections = self._cleanup_expired_conditions() or set()
                if force:
                    self.data["state_conditions"] = []
                dream_pick = deferred_updates.get("dream_pick")
                if isinstance(dream_pick, tuple):
                    self._remember_daily_dream_pick(dream_pick)
                discomfort_roll_date = deferred_updates.get("cycle_discomfort_roll_date")
                if discomfort_roll_date:
                    cycle_meta = self.data.get("body_cycle_state")
                    cycle_meta = dict(cycle_meta) if isinstance(cycle_meta, dict) else {}
                    cycle_meta["last_discomfort_roll_date"] = discomfort_roll_date
                    self.data["body_cycle_state"] = cycle_meta
                body_cycle_conditions = deferred_updates.get("body_cycle_conditions", [])
                if isinstance(body_cycle_conditions, list):
                    for condition in body_cycle_conditions:
                        if isinstance(condition, dict):
                            self._record_body_cycle_episode(condition)
                conditions = self.data.setdefault("state_conditions", [])
                if not isinstance(conditions, list):
                    conditions = []
                    self.data["state_conditions"] = conditions
                conditions.extend(generated_conditions)
                self.data["state_generated_day"] = generation_day
            self._ensure_time_based_hunger_condition()
            state = self._compose_state_from_conditions(weather)
            self.data["daily_state"] = state
            save_sections = {
                "daily_state",
                "state_conditions",
                "state_generated_day",
                "daily_dream",
                "body_cycle_state",
                "hunger_window_attempts",
            } - set(deleted_sections)
            self._save_data_sync(
                sections=save_sections,
                deleted_sections=deleted_sections,
            )
            return state

    async def _generate_state_conditions(
        self,
        weather: dict[str, Any] | None = None,
        *,
        deferred_state_updates: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        intensity = _safe_float(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100) / 100
        persona_profile = self._persona_state_profile()
        now_dt = self._environment_now()
        current_minute = now_dt.hour * 60 + now_dt.minute

        sleep_pool = [
            ("睡得很踏实", "平稳", 0, 8),
            ("昨晚睡得很浅,半夜醒了好几次", "迟钝", -16, 10),
            ("失眠了,翻来覆去很久才睡着", "敏感", -24, 14),
            ("一晚上都在做梦,醒过来却记不清", "恍惚", -18, 12),
            ("赖床赖得有点久,懵懵的", "迷糊", -14, 8),
            ("闹钟没叫醒我,起来还有点懵", "慌乱", -17, 7),
        ]
        dream_pool = [
            ("没有记住梦", "平稳", 0, 2),
            ("梦里一直在找一件放错地方的小东西,醒来还残着一点没找完的感觉", "恍惚", -6, 5),
            ("梦见走过一段很安静的路,路灯和风声都很近", "柔和", 4, 4),
            ("梦里反复听见一句没听清的话,醒来后胸口还有点闷", "低落", -10, 7),
        ]
        hunger_pool = [
            ("无饥饿感", "平稳", 0, 3),
            ("饿,想吃东西", "粘人", -4, 2),
            ("胃口不好", "低落", -8, 3),
            ("想吃甜的", "柔软", 1, 2),
        ]
        cycle_pool = [
            ("不处于生理期", "平稳", 0, 24),
            ("生理期前,身体感受更敏锐,耐受度稍低", "敏感", -18, 24),
            ("处于生理期,身体舒适度与能量偏低", "疲惫", -24, 72),
        ]

        def pick(pool: list[tuple[str, str, int, int]], special_chance: float = 0.35) -> tuple[str, str, int, int]:
            if random.random() > special_chance * max(0.2, intensity):
                return pool[0]
            return random.choice(pool[1:])

        sleep_pick = pick(sleep_pool, 0.42)
        enhanced_dream = None
        if bool(runtime_persona_setting(self, "enable_enhanced_dreams", False)):
            enhanced_dream = await self._generate_enhanced_dream_pick(weather)
        dream_pick = enhanced_dream or pick(dream_pool, 0.55)
        if deferred_state_updates is None:
            self._remember_daily_dream_pick(dream_pick)
        else:
            deferred_state_updates["dream_pick"] = dream_pick
        hunger_pick = pick(hunger_pool, 0.22)
        specs = [
            ("sleep", "睡眠", *sleep_pick),
            ("dream", "梦境", *dream_pick),
        ]
        if persona_profile.get("allow_hunger", True):
            specs.append(("hunger", "饥饿", *hunger_pick))
        if persona_profile.get("allow_cycle", False):
            skip_cycle_spec = False
            if self._advanced_cycle_enabled():
                meta = self.data.get("body_cycle_state", {})
                anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0) if isinstance(meta, dict) else 0
                active_advanced_cycle = any(
                    isinstance(cond, dict)
                    and str(cond.get("kind") or "") == "body_cycle"
                    and str(cond.get("phase") or "") in self._ADVANCED_CYCLE_PHASES
                    and _safe_float(cond.get("start_ts"), 0) <= _now_ts() < _safe_float(cond.get("end_ts"), 0)
                    for cond in (self.data.get("state_conditions", []) or [])
                )
                # The anchored continuous timeline owns phase progression once
                # started, so no daily random cycle pick is needed anymore.
                skip_cycle_spec = anchor_ts > 0 or active_advanced_cycle
            if not skip_cycle_spec:
                cycle_spec = (
                    self._pick_advanced_cycle_spec(intensity)
                    if self._advanced_cycle_enabled()
                    else self._pick_body_cycle_spec(cycle_pool, intensity)
                )
                specs.append(("body_cycle", "周期", *cycle_spec))
        else:
            specs.append(("body_cycle", "周期", *cycle_pool[0]))

        diary_tags = self._recent_diary_tags()
        weather_text = self._weather_summary_text(weather)
        if persona_profile.get("allow_health", True):
            health_causes = self._build_health_causes(
                sleep_label=sleep_pick[0],
                weather_text=weather_text,
                diary_tags=diary_tags,
            )
            health_spec = self._pick_health_spec(health_causes, intensity, weather_text)
            if health_spec is not None:
                specs.append(("health", "健康", *health_spec))
        if "失眠" in diary_tags and random.random() < 0.35:
            specs.append(("sleep", "睡眠延续", "昨晚的失眠感还没完全散掉", "迟钝", -12, 8))
        if persona_profile.get("allow_health", True) and "生病" in diary_tags and random.random() < 0.4:
            specs.append(("health", "健康延续", "身体像还在恢复,反应慢半拍", "疲惫", -14, 18, "前两天的不舒服还没完全退掉"))
        if "低能量" in diary_tags and random.random() < 0.35:
            specs.append(("sleep", "能量延续", "昨天的低电量拖到今天早上", "安静", -10, 6))
        if "好梦" in diary_tags and random.random() < 0.3:
            specs.append(("dream", "梦境余温", "梦里留下了一点柔和的亮色", "柔和", 4, 5))
        screen_diary_spec = self._screen_diary_state_condition_spec()
        if screen_diary_spec is not None:
            specs.append(screen_diary_spec)

        conditions = []
        for spec in specs:
            extras: dict[str, Any] = {}
            if len(spec) >= 7:
                kind, title, label, mood, energy_delta, duration_hours, cause = spec[:7]
                extras["cause"] = cause
            else:
                kind, title, label, mood, energy_delta, duration_hours = spec[:6]
            cycle_phase = self._infer_body_cycle_phase(label) if kind == "body_cycle" else ""
            advanced_cycle_phase = self._advanced_cycle_enabled() and cycle_phase in self._ADVANCED_CYCLE_PHASES
            if energy_delta == 0 and kind not in {"sleep", "dream"} and not advanced_cycle_phase:
                continue
            if kind == "health" and energy_delta < 0:
                extras["on_end_transition"] = "health_relief"
                extras["phase"] = "mild_discomfort"
            if kind == "sleep" and energy_delta <= -16:
                extras["on_end_transition"] = "sleep_rebound"
                extras["phase"] = "sleep_debt"
            if kind == "body_cycle" and cycle_phase != "cycle":
                extras["phase"] = cycle_phase
                extras["episode_key"] = f"body-cycle-{_today_key()}"
                if cycle_phase in self._ADVANCED_CYCLE_PHASES:
                    extras["transition_options"] = self._advanced_cycle_transition_options(cycle_phase)
                elif extras["phase"] == "pre":
                    extras["transition_options"] = [{"to": "body_period", "base_weight": 0.72}, {"to": "stable", "base_weight": 0.28}]
                elif extras["phase"] == "period":
                    extras["transition_options"] = [{"to": "body_recovery", "base_weight": 0.65}, {"to": "stable", "base_weight": 0.35}]
            effective_energy_delta = (
                int(energy_delta)
                if advanced_cycle_phase
                else int(energy_delta * max(0.4, intensity))
            )
            extras["transition_options"] = self._build_transition_options(
                kind=kind,
                energy_delta=effective_energy_delta,
                cause=str(extras.get("cause") or ""),
                on_end_transition=str(extras.get("on_end_transition") or ""),
            ) or extras.get("transition_options", [])
            condition = self._make_condition(
                kind=kind,
                title=title,
                label=label,
                mood=mood,
                energy_delta=effective_energy_delta,
                duration_hours=duration_hours,
                intensity=random.randint(35, 90),
                **extras,
            )
            if kind == "body_cycle" and cycle_phase != "cycle":
                if deferred_state_updates is None:
                    self._record_body_cycle_episode(condition)
                else:
                    deferred_state_updates.setdefault("body_cycle_conditions", []).append(condition)
            conditions.append(condition)
        dream_aftertaste = self._build_dream_aftertaste_condition(dream_pick)
        if dream_aftertaste is not None:
            conditions.append(dream_aftertaste)
        discomfort_condition = self._maybe_pick_cycle_discomfort(deferred_state_updates)
        if discomfort_condition is not None:
            conditions.append(discomfort_condition)
        if 0 <= current_minute < 5 * 60:
            late_night_pool = [
                ("夜里还没完全安静下来,眼睛和脑子都慢半拍", "困倦", -14, 4),
                ("这个点还醒着,困意和清醒混在一起", "恍惚", -12, 3),
                ("已经很晚了,精神有点发飘,只想把声音放轻", "疲惫", -10, 5),
            ]
            label, mood, energy_delta, duration_hours = random.choice(late_night_pool)
            conditions.append(
                self._make_condition(
                    kind="sleep",
                    title="夜深未眠",
                    label=label,
                    mood=mood,
                    energy_delta=int(energy_delta * max(0.55, intensity)),
                    duration_hours=duration_hours,
                    intensity=random.randint(45, 88),
                    phase="late_night_awake",
                    transition_options=[
                        {"to": "sleep_afterglow", "base_weight": 0.35},
                        {"to": "sleep_tail", "base_weight": 0.2},
                        {"to": "stable", "base_weight": 0.45},
                    ],
                )
            )
        return conditions

    def _ensure_time_based_hunger_condition(self) -> None:
        profile = self._persona_state_profile()
        if not profile.get("allow_hunger", True):
            return
        if any(str(cond.get("kind") or "") == "hunger" for cond in self._get_active_conditions()):
            return
        if _safe_float(self.data.get("last_food_state_feedback_at"), 0) + 90 * 60 > _now_ts():
            return
        now_dt = self._environment_now()
        minute = now_dt.hour * 60 + now_dt.minute
        windows = [
            ("breakfast", 7 * 60, 9 * 60 + 30, "饿,想吃热的", "柔软", -4, 2),
            ("lunch", 11 * 60, 13 * 60 + 40, "饿,想吃东西", "走神", -6, 2),
            ("afternoon", 15 * 60, 17 * 60, "想吃甜的", "柔软", 2, 2),
            ("dinner", 17 * 60 + 30, 20 * 60, "饿,想吃热的", "粘人", -5, 3),
            ("late_snack", 21 * 60 + 30, 23 * 60 + 30, "有点想吃东西", "松散", -3, 2),
        ]
        matched = next((item for item in windows if item[1] <= minute <= item[2]), None)
        if not matched:
            return
        window_id, _start, _end, label, mood, energy_delta, duration_hours = matched
        attempts = self.data.get("hunger_window_attempts")
        if not isinstance(attempts, dict):
            attempts = {}
        today = _today_key()
        generated = attempts.get("generated")
        if not isinstance(generated, list):
            generated = []
        generated = [
            item for item in generated
            if isinstance(item, dict) and str(item.get("date") or "") == today
        ][-5:]
        if len(generated) >= 2:
            attempts["generated"] = generated
            self.data["hunger_window_attempts"] = attempts
            return
        last_generated_ts = max((_safe_float(item.get("ts"), 0) for item in generated), default=0.0)
        if last_generated_ts and _now_ts() - last_generated_ts < 4 * 3600:
            attempts["generated"] = generated
            self.data["hunger_window_attempts"] = attempts
            return
        attempt_key = f"{today}:{window_id}"
        if attempts.get("last_key") == attempt_key:
            return
        attempts["last_key"] = attempt_key
        attempts["last_attempt_ts"] = _now_ts()
        self.data["hunger_window_attempts"] = attempts
        intensity = max(0.0, min(1.0, _safe_float(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100) / 100))
        chance = 0.25 + 0.30 * intensity
        if window_id in {"afternoon", "late_snack"}:
            chance *= 0.65
        if random.random() > chance:
            return
        self.data.setdefault("state_conditions", []).append(
            self._make_condition(
                kind="hunger",
                title="饭点",
                label=label,
                mood=mood,
                energy_delta=int(energy_delta * max(0.55, intensity)),
                duration_hours=duration_hours,
                intensity=random.randint(45, 82),
                phase=window_id,
                cause="饭点自然波动",
            )
        )
        generated.append({"date": today, "window": window_id, "ts": _now_ts()})
        attempts["generated"] = generated[-5:]
        attempts["last_generated_ts"] = _now_ts()
        self.data["hunger_window_attempts"] = attempts

    _ADVANCED_CYCLE_PHASES = (
        "menstrual",
        "follicular",
        "pre_ovulation",
        "ovulation",
        "luteal",
        "pms",
    )
    _ADVANCED_CYCLE_TRANSITIONS = {
        "menstrual": "body_follicular",
        "follicular": "body_pre_ovulation",
        "pre_ovulation": "body_ovulation",
        "ovulation": "body_luteal",
        "luteal": "body_pms",
        "pms": "body_menstrual",
    }
    _ADVANCED_CYCLE_INTENSITY_MEDIANS = {
        "menstrual": -12.0,
        "follicular": 0.0,
        "pre_ovulation": 7.5,
        "ovulation": 9.0,
        "luteal": 4.5,
        "pms": -7.5,
    }
    _ADVANCED_CYCLE_PHASE_NAMES = {
        "menstrual": "月经期",
        "follicular": "卵泡期",
        "pre_ovulation": "排卵前期",
        "ovulation": "排卵期",
        "luteal": "黄体期",
        "pms": "PMS 期",
    }
    _ADVANCED_CYCLE_DISCOMFORT_SPECS = {
        "痛经": {
            "phases": {"menstrual"},
            "label": "今天有点痛经，小腹闷闷地不舒服",
            "mood": "疲惫",
            "energy_delta": -14,
            "duration_hours": 6,
            "weight": 4,
        },
        "头痛": {
            "phases": {"menstrual", "pms"},
            "label": "头有点闷痛，注意力不太集中",
            "mood": "迟钝",
            "energy_delta": -10,
            "duration_hours": 5,
            "weight": 3,
        },
        "腰酸": {
            "phases": {"menstrual", "luteal"},
            "label": "腰有点酸，不太想久坐",
            "mood": "疲惫",
            "energy_delta": -8,
            "duration_hours": 6,
            "weight": 3,
        },
        "乏力": {
            "phases": {"menstrual", "luteal", "pms"},
            "label": "身上没什么力气，动作慢半拍",
            "mood": "困倦",
            "energy_delta": -12,
            "duration_hours": 8,
            "weight": 4,
        },
        "情绪低落": {
            "phases": {"pms"},
            "label": "情绪有点低，不太想说话",
            "mood": "低落",
            "energy_delta": -6,
            "duration_hours": 5,
            "weight": 2,
        },
        "恶心": {
            "phases": {"menstrual", "pms"},
            "label": "胃里有点泛恶心，不太想吃东西",
            "mood": "虚弱",
            "energy_delta": -9,
            "duration_hours": 4,
            "weight": 1,
        },
    }

    def _advanced_cycle_enabled(self) -> bool:
        return bool(runtime_persona_setting(self, "enable_advanced_cycle_strategy", False))

    def _infer_body_cycle_phase(self, label: str) -> str:
        text = str(label or "")
        upper_text = text.upper()
        if "PMS" in upper_text or "经前综合征" in text:
            return "pms"
        if "排卵前期" in text:
            return "pre_ovulation"
        if "月经期" in text:
            return "menstrual"
        if "卵泡期" in text:
            return "follicular"
        if "排卵期" in text:
            return "ovulation"
        if "黄体期" in text:
            return "luteal"
        if "生理期后" in text or "恢复" in text:
            return "recovery"
        if "前" in text:
            return "pre"
        if "生理期" in text:
            return "period"
        return "cycle"

    def _body_cycle_max_hours(self, phase: str, label: str = "") -> int:
        phase = str(phase or self._infer_body_cycle_phase(label))
        advanced_hours = self._advanced_cycle_phase_hours(phase)
        if advanced_hours is not None:
            return advanced_hours
        if phase == "period":
            return 72
        if phase in {"pre", "recovery"}:
            return 24
        return 48

    def _body_cycle_interval_seconds(self) -> int:
        if self._advanced_cycle_enabled():
            return self._advanced_cycle_total_days() * 86400
        return random.randint(25, 34) * 86400

    def _advanced_cycle_phase_days(self, phase: str) -> int:
        defaults = {
            "menstrual": 5,
            "follicular": 5,
            "pre_ovulation": 3,
            "ovulation": 1,
            "luteal": 8,
            "pms": 6,
        }
        attributes = {
            "menstrual": "advanced_cycle_menstrual_days",
            "follicular": "advanced_cycle_follicular_days",
            "pre_ovulation": "advanced_cycle_pre_ovulation_days",
            "ovulation": "advanced_cycle_ovulation_days",
            "luteal": "advanced_cycle_luteal_days",
            "pms": "advanced_cycle_pms_days",
        }
        default = defaults.get(phase, 1)
        attribute = attributes.get(phase, "")
        return _safe_int(runtime_persona_setting(self, attribute, default), default, 1, 30) if attribute else default

    def _advanced_cycle_phase_hours(self, phase: str) -> int | None:
        if phase not in self._ADVANCED_CYCLE_PHASES:
            return None
        return self._advanced_cycle_phase_days(phase) * 24

    def _advanced_cycle_total_days(self) -> int:
        return sum(self._advanced_cycle_phase_days(phase) for phase in self._ADVANCED_CYCLE_PHASES)

    def _advanced_cycle_offset_signature(self, offset: int) -> str:
        durations = ",".join(str(self._advanced_cycle_phase_days(phase)) for phase in self._ADVANCED_CYCLE_PHASES)
        return f"{max(0, int(offset))}:{durations}"

    def _advanced_cycle_position_from_offset(self, offset: int) -> tuple[str, int]:
        total_days = max(1, self._advanced_cycle_total_days())
        cycle_day = ((max(1, int(offset)) - 1) % total_days) + 1
        cursor = 0
        for phase in self._ADVANCED_CYCLE_PHASES:
            phase_days = self._advanced_cycle_phase_days(phase)
            if cycle_day <= cursor + phase_days:
                return phase, cycle_day - cursor
            cursor += phase_days
        return "pms", self._advanced_cycle_phase_days("pms")

    def _advanced_cycle_day_of_phase(self, phase: str, day_in_phase: int) -> int:
        """Map a phase plus its day index to the absolute cycle day."""
        cursor = 0
        for candidate in self._ADVANCED_CYCLE_PHASES:
            if candidate == phase:
                return cursor + max(1, int(day_in_phase))
            cursor += self._advanced_cycle_phase_days(candidate)
        return 1

    def _advanced_cycle_runtime(self) -> dict[str, Any]:
        """Derive the current six-phase position for display and continuity.

        The stored cycle anchor timestamp is the authoritative continuous
        timeline: it always yields the current phase and day, even when the
        bot was offline or no body_cycle condition is currently active. Active
        conditions are only used as a fallback for old data without an anchor.

        Returns:
            Phase position details, or an empty dict when the strategy is off
            or the cycle has not started yet.
        """
        if not self._advanced_cycle_enabled():
            return {}
        now = _now_ts()
        meta = self.data.get("body_cycle_state")
        anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0) if isinstance(meta, dict) else 0
        phase = ""
        day_in_phase = 0
        if anchor_ts > 0:
            cycle_day = int((now - anchor_ts) // 86400) + 1
            phase, day_in_phase = self._advanced_cycle_position_from_offset(cycle_day)
        else:
            # Legacy fallback for historical data created before the anchor
            # existed. The anchor is always seeded on first enable now, so this
            # branch only matters while migrating old conditions.
            conditions = self.data.get("state_conditions", [])
            if isinstance(conditions, list):
                for cond in conditions:
                    if not isinstance(cond, dict) or str(cond.get("kind") or "") != "body_cycle":
                        continue
                    cond_phase = str(cond.get("phase") or "")
                    if cond_phase not in self._ADVANCED_CYCLE_PHASES:
                        continue
                    start_ts = _safe_float(cond.get("start_ts"), 0)
                    end_ts = _safe_float(cond.get("end_ts"), 0)
                    if start_ts <= now < end_ts:
                        phase = cond_phase
                        day_in_phase = int((now - start_ts) // 86400) + 1
                        break
            if not phase:
                return {}
        phase_days = self._advanced_cycle_phase_days(phase)
        day_in_phase = max(1, min(phase_days, int(day_in_phase)))
        label, mood, energy_delta, _ = self._advanced_cycle_phase_spec(phase)
        next_phase = self._ADVANCED_CYCLE_TRANSITIONS.get(phase, "")
        next_phase = self._ADVANCED_CYCLE_TRANSITIONS.get(phase, "").removeprefix("body_")
        return {
            "phase": phase,
            "phase_name": self._ADVANCED_CYCLE_PHASE_NAMES.get(phase, phase),
            "day_in_phase": day_in_phase,
            "phase_days": phase_days,
            "cycle_day": self._advanced_cycle_day_of_phase(phase, day_in_phase),
            "cycle_days": self._advanced_cycle_total_days(),
            "mood": _single_line(mood, 20),
            "energy_delta": int(energy_delta),
            "label": _single_line(label, 160),
            "next_phase": next_phase,
            "next_phase_name": self._ADVANCED_CYCLE_PHASE_NAMES.get(next_phase, ""),
        }

    def _active_cycle_discomfort_conditions(self) -> list[dict[str, Any]]:
        now = _now_ts()
        items: list[dict[str, Any]] = []
        conditions = self.data.get("state_conditions", [])
        if not isinstance(conditions, list):
            return items
        for cond in conditions:
            if not isinstance(cond, dict) or str(cond.get("kind") or "") != "cycle_discomfort":
                continue
            if _safe_float(cond.get("start_ts"), 0) <= now < _safe_float(cond.get("end_ts"), 0):
                items.append(
                    {
                        "type": _single_line(cond.get("phase"), 12) or "经期不适",
                        "label": _single_line(cond.get("label"), 80),
                        "mood": _single_line(cond.get("mood"), 12),
                    }
                )
        return items

    def _maybe_pick_cycle_discomfort(self, deferred_state_updates: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Roll once per day for a menstrual discomfort episode on the current phase.

        Only runs when the discomfort simulation, the advanced six-phase
        strategy and the persona cycle allowance are all enabled, and only
        during phases allowed per discomfort type. Rolls at most once per
        calendar day and skips the roll while another discomfort condition is
        still active.

        Returns:
            A cycle_discomfort condition dict, or None when skipped.
        """
        if not bool(runtime_persona_setting(self, "advanced_cycle_discomfort_simulation", False)):
            return None
        if not self._persona_state_profile().get("allow_cycle", False):
            return None
        intensity = _safe_int(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100)
        if intensity <= 0:
            return None
        meta = self.data.get("body_cycle_state")
        meta = dict(meta) if isinstance(meta, dict) else {}
        if meta.get("last_discomfort_roll_date") == _today_key():
            return None
        runtime = self._advanced_cycle_runtime()
        phase = runtime.get("phase") if runtime else ""
        if phase not in self._ADVANCED_CYCLE_PHASES:
            return None
        now = _now_ts()
        conditions = self.data.get("state_conditions", [])
        if isinstance(conditions, list):
            for cond in conditions:
                if (
                    isinstance(cond, dict)
                    and str(cond.get("kind") or "") == "cycle_discomfort"
                    and _safe_float(cond.get("end_ts"), 0) > now
                ):
                    return None
        # One roll attempt per day regardless of the outcome, so a failed roll
        # does not give the phase extra chances later the same day.
        if deferred_state_updates is None:
            meta["last_discomfort_roll_date"] = _today_key()
            self.data["body_cycle_state"] = meta
        else:
            deferred_state_updates["cycle_discomfort_roll_date"] = _today_key()
        chance = _safe_int(runtime_persona_setting(self, "advanced_cycle_discomfort_chance", 55), 55, 0, 100)
        if chance <= 0 or random.random() > chance / 100.0:
            return None
        raw_types = str(runtime_persona_setting(self, "advanced_cycle_discomfort_types", "痛经,头痛,腰酸,乏力") or "痛经,头痛,腰酸,乏力")
        requested = {token.strip() for token in raw_types.replace("，", ",").split(",") if token.strip()}
        candidates = [
            (name, spec)
            for name, spec in self._ADVANCED_CYCLE_DISCOMFORT_SPECS.items()
            if name in requested and phase in spec.get("phases", set())
        ]
        if not candidates:
            return None
        name, spec = random.choices(
            candidates,
            weights=[int(spec.get("weight") or 1) for _, spec in candidates],
            k=1,
        )[0]
        energy_delta = int((spec.get("energy_delta") or 0) * max(0.5, intensity / 50.0))
        return self._make_condition(
            kind="cycle_discomfort",
            title="经期不适",
            label=_single_line(spec.get("label"), 80),
            mood=_single_line(spec.get("mood"), 12) or "疲惫",
            energy_delta=energy_delta,
            duration_hours=_safe_int(spec.get("duration_hours"), 6, 1, 24),
            intensity=random.randint(45, max(46, min(92, 40 + intensity))),
            cause="生理周期阶段伴随不适",
            phase=name,
            episode_key=f"cycle-discomfort-{_today_key()}",
        )

    def _advanced_cycle_linked_energy(self, phase: str) -> int:
        median = self._ADVANCED_CYCLE_INTENSITY_MEDIANS.get(phase, 0.0)
        intensity = _safe_int(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100)
        return int(round(median * (intensity / 50.0)))

    def _advanced_cycle_phase_spec(self, phase: str) -> tuple[str, str, int, int]:
        defaults = {
            "menstrual": ("处于月经期，身体更容易疲倦，情绪感受稍敏锐", "疲惫", -12),
            "follicular": ("处于卵泡期，精力平稳回升，心情逐渐轻快", "轻快", 0),
            "pre_ovulation": ("处于排卵前期，身体逐渐轻盈，精力有所上升", "期待", 8),
            "ovulation": ("处于排卵期，精力较充足，社交意愿稍有增强", "明朗", 9),
            "luteal": ("处于黄体期，精力尚可，情绪整体平稳", "平稳", 5),
            "pms": ("处于 PMS 期，精力有所下降，情绪波动稍明显", "敏感", -8),
        }
        attributes = {
            "menstrual": ("advanced_cycle_menstrual_prompt", "advanced_cycle_menstrual_mood", "advanced_cycle_menstrual_energy"),
            "follicular": ("advanced_cycle_follicular_prompt", "advanced_cycle_follicular_mood", "advanced_cycle_follicular_energy"),
            "pre_ovulation": ("advanced_cycle_pre_ovulation_prompt", "advanced_cycle_pre_ovulation_mood", "advanced_cycle_pre_ovulation_energy"),
            "ovulation": ("advanced_cycle_ovulation_prompt", "advanced_cycle_ovulation_mood", "advanced_cycle_ovulation_energy"),
            "luteal": ("advanced_cycle_luteal_prompt", "advanced_cycle_luteal_mood", "advanced_cycle_luteal_energy"),
            "pms": ("advanced_cycle_pms_prompt", "advanced_cycle_pms_mood", "advanced_cycle_pms_energy"),
        }
        selected_phase = phase if phase in defaults else "menstrual"
        default_prompt, default_mood, default_energy = defaults[selected_phase]
        prompt_attr, mood_attr, energy_attr = attributes[selected_phase]
        label = _single_line(runtime_persona_setting(self, prompt_attr, default_prompt), 160) or default_prompt
        mood = _single_line(runtime_persona_setting(self, mood_attr, default_mood), 20) or default_mood
        energy_delta = (
            self._advanced_cycle_linked_energy(selected_phase)
        if bool(runtime_persona_setting(self, "advanced_cycle_link_intensity", False))
            else _safe_int(runtime_persona_setting(self, energy_attr, default_energy), default_energy, -50, 30)
        )
        return label, mood, energy_delta, self._advanced_cycle_phase_days(selected_phase) * 24

    def _advanced_cycle_transition_options(self, phase: str) -> list[dict[str, Any]]:
        target = self._ADVANCED_CYCLE_TRANSITIONS.get(phase, "")
        return [{"to": target, "base_weight": 1.0}] if target else []

    def _advanced_cycle_condition(
        self,
        phase: str,
        *,
        episode_key: str = "",
        cause: str = "周期阶段自然推进",
        duration_hours: int | None = None,
    ) -> dict[str, Any]:
        label, mood, energy_delta, configured_hours = self._advanced_cycle_phase_spec(phase)
        return self._make_condition(
            kind="body_cycle",
            title="周期",
            label=label,
            mood=mood,
            energy_delta=energy_delta,
            duration_hours=max(1, int(duration_hours or configured_hours)),
            intensity=max(35, _safe_int(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100)),
            cause=cause,
            phase=phase,
            episode_key=episode_key or f"body-cycle-{_today_key()}",
            transition_options=self._advanced_cycle_transition_options(phase),
        )

    def _pick_advanced_cycle_spec(self, intensity: float) -> tuple[str, str, int, int]:
        neutral = ("不处于生理期", "平稳", 0, 24)
        if self._body_cycle_generation_blocked():
            return neutral
        meta = self.data.get("body_cycle_state", {})
        anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0) if isinstance(meta, dict) else 0
        if anchor_ts > 0:
            # Once the continuous timeline is anchored, phase progression is
            # deterministic; a random new-cycle pick would shift it backwards.
            return neutral
        now = _now_ts()
        expected_ts = _safe_float(meta.get("next_expected_start_ts"), 0) if isinstance(meta, dict) else 0
        if expected_ts > 0:
            days_late = max(0.0, (now - expected_ts) / 86400)
            chance = min(0.75, 0.22 + days_late * 0.14) * max(0.35, min(1.15, intensity))
        else:
            chance = 0.10 * max(0.35, min(1.2, intensity))
        if random.random() > chance:
            return neutral
        return self._advanced_cycle_phase_spec("menstrual")

    def _body_cycle_generation_blocked(self, now: float | None = None) -> bool:
        now = _now_ts() if now is None else now
        meta = self.data.get("body_cycle_state", {})
        if isinstance(meta, dict):
            expected_ts = _safe_float(meta.get("next_expected_start_ts"), 0)
            if expected_ts > 0 and now < expected_ts - 2 * 86400:
                return True
            if expected_ts <= 0 and _safe_float(meta.get("last_end_ts"), 0) + 18 * 86400 > now:
                return True
        conditions = self.data.get("state_conditions", [])
        if not isinstance(conditions, list):
            return False
        recent_floor = now - 14 * 86400
        for cond in conditions:
            if not isinstance(cond, dict) or str(cond.get("kind") or "") != "body_cycle":
                continue
            start_ts = _safe_float(cond.get("start_ts"), 0)
            end_ts = _safe_float(cond.get("end_ts"), 0)
            if end_ts > now or max(start_ts, end_ts) >= recent_floor:
                return True
        return False

    def _pick_body_cycle_spec(
        self,
        cycle_pool: list[tuple[str, str, int, int]],
        intensity: float,
    ) -> tuple[str, str, int, int]:
        neutral = cycle_pool[0]
        if self._body_cycle_generation_blocked():
            return neutral
        now = _now_ts()
        meta = self.data.get("body_cycle_state", {})
        expected_ts = _safe_float(meta.get("next_expected_start_ts"), 0) if isinstance(meta, dict) else 0
        if expected_ts > 0:
            days_late = max(0.0, (now - expected_ts) / 86400)
            chance = min(0.65, 0.18 + days_late * 0.12) * max(0.35, min(1.15, intensity))
        else:
            chance = 0.085 * max(0.35, min(1.2, intensity))
        if random.random() > chance:
            return neutral
        return random.choices(cycle_pool[1:], weights=[0.45, 0.55], k=1)[0]

    def _record_body_cycle_episode(self, cond: dict[str, Any]) -> None:
        start_ts = _safe_float(cond.get("start_ts"), _now_ts())
        end_ts = _safe_float(cond.get("end_ts"), start_ts)
        phase = str(cond.get("phase") or self._infer_body_cycle_phase(str(cond.get("label") or "")))
        previous = self.data.get("body_cycle_state")
        meta = dict(previous) if isinstance(previous, dict) else {}
        payload = {
            "last_start_ts": start_ts,
            "last_end_ts": end_ts,
            "next_expected_start_ts": start_ts + self._body_cycle_interval_seconds(),
            "last_phase": phase,
            "last_label": _single_line(cond.get("label"), 80),
        }
        # Episode reconciliation rewrites this record whenever the bot
        # catches up after downtime. Keep the daily discomfort dedup marker
        # across those rewrites so one calendar day still gets one roll.
        if meta.get("last_discomfort_roll_date"):
            payload["last_discomfort_roll_date"] = _single_line(
                meta.get("last_discomfort_roll_date"), 16
            )
        if phase in self._ADVANCED_CYCLE_PHASES:
            payload["strategy"] = "advanced"
            previous_anchor = _safe_float(meta.get("cycle_anchor_ts"), 0)
            if phase == "menstrual" and previous_anchor <= 0:
                payload["cycle_anchor_ts"] = start_ts
            elif previous_anchor > 0:
                payload["cycle_anchor_ts"] = previous_anchor
            if phase != "menstrual" and _safe_float(meta.get("last_start_ts"), 0) > 0:
                payload["last_start_ts"] = _safe_float(meta.get("last_start_ts"), start_ts)
                payload["next_expected_start_ts"] = _safe_float(
                    meta.get("next_expected_start_ts"),
                    payload["last_start_ts"] + self._advanced_cycle_total_days() * 86400,
                )
            for key in ("manual_offset", "manual_offset_signature", "manual_offset_phase", "manual_offset_day_in_phase"):
                if key in meta:
                    payload[key] = meta[key]
        else:
            payload["strategy"] = "legacy"
        self.data["body_cycle_state"] = payload

    def _remember_daily_dream_pick(self, dream_pick: tuple[str, str, int, int] | None) -> None:
        if not dream_pick:
            return
        label = _single_line(dream_pick[0], 120)
        if not label:
            return
        payload = getattr(self, "_last_generated_dream_payload", None)
        if not isinstance(payload, dict) or _single_line(payload.get("label"), 120) != label:
            payload = {}
        factors = payload.get("factors", [])
        if not isinstance(factors, list):
            factors = []
        normalized_factors = [_single_line(item, 30) for item in factors[:8] if _single_line(item, 30)]
        if not normalized_factors:
            normalized_factors = self._build_dream_memory_fragments(count=6)
        content = _single_line(payload.get("content"), 1000)
        if not content:
            factor_hint = "、".join(normalized_factors[:4]) or "一些断续的生活碎片"
            content = (
                f"梦里像从{factor_hint}开始,场景没有交代清楚就慢慢换了地方。"
                f"{label}那种感觉一直挂着,中间有些画面接不上,但醒来时还记得自己在梦里顺着它走了一段。"
            )
        self.data["daily_dream"] = {
            "date": _today_key(),
            "label": label,
            "dream_type": _single_line(payload.get("dream_type"), 40) or "碎片梦",
            "factors": normalized_factors,
            "content": content,
            "afterglow": _single_line(payload.get("afterglow"), 220) or label,
            "mood": _single_line(dream_pick[1], 20) or "平稳",
            "energy_delta": _safe_int(dream_pick[2], 0, -30, 20),
            "duration_hours": _safe_int(dream_pick[3], 0, 0, 24),
            "generated_at": self._environment_now().strftime("%Y-%m-%d %H:%M"),
        }

    def _remembered_daily_dream_label(self) -> str:
        raw = self.data.get("daily_dream")
        if not isinstance(raw, dict) or raw.get("date") != _today_key():
            return ""
        label = _single_line(raw.get("label"), 120)
        if label and label != "没有记住梦":
            return label
        return ""

    def _dream_afterglow_strength(self, dream_pick: tuple[str, str, int, int]) -> float:
        mode = str(runtime_persona_setting(self, "dream_afterglow_mode", "auto") or "auto")
        if mode == "轻":
            return 0.7
        if mode == "标准":
            return 1.0
        if mode == "明显":
            return 1.35
        label = str(dream_pick[0] or "")
        energy_delta = abs(int(dream_pick[2] or 0))
        if any(token in label for token in ("不舒服", "追", "黑", "掉下去", "迷路", "醒来后有一点黏着感")):
            return 1.15
        if energy_delta >= 10:
            return 1.1
        if energy_delta <= 2:
            return 0.75
        return 0.95

    def _build_dream_aftertaste_condition(
        self,
        dream_pick: tuple[str, str, int, int],
    ) -> dict[str, Any] | None:
        label = str(dream_pick[0] or "")
        mood = str(dream_pick[1] or "平稳")
        if not label or label == "没有记住梦":
            return None
        strength = self._dream_afterglow_strength(dream_pick)
        if random.random() > min(0.92, 0.45 + strength * 0.2):
            return None
        if any(token in label for token in ("不舒服", "追", "恐怖", "黑", "掉下去", "迷路", "醒来后有一点黏着感")):
            return self._make_condition(
                kind="dream_aftertaste",
                title="梦后的不安残留",
                label="梦里的那点不安还没完全褪干净",
                mood="恍惚" if mood in {"恍惚", "低落", "敏感"} else "敏感",
                energy_delta=-max(2, int(round(4 * strength))),
                duration_hours=max(2, int(round(6 * strength))),
                intensity=min(92, int(50 + 20 * strength)),
                cause=f"梦境余韵：{_single_line(label, 40)}",
                phase="dream_aftertaste",
            )
        if any(token in label for token in ("发光", "柔", "亮色", "温暖", "春梦", "暧昧", "怀旧")):
            return self._make_condition(
                kind="dream_aftertaste",
                title="梦后的余温",
                label="梦里的余温还轻轻黏着一点",
                mood="柔和" if mood not in {"平稳", "中性"} else "安静",
                energy_delta=max(1, int(round(2 * strength))),
                duration_hours=max(2, int(round(5 * strength))),
                intensity=min(88, int(46 + 16 * strength)),
                cause=f"梦境余韵：{_single_line(label, 40)}",
                phase="dream_aftertaste",
            )
        return self._make_condition(
            kind="dream_aftertaste",
            title="梦后的朦胧残影",
            label="梦里的画面还没完全从脑子里退下去",
            mood="恍惚" if mood == "平稳" else mood,
            energy_delta=-max(1, int(round(2 * strength))),
            duration_hours=max(2, int(round(4 * strength))),
            intensity=min(82, int(42 + 16 * strength)),
            cause=f"梦境余韵：{_single_line(label, 40)}",
            phase="dream_aftertaste",
        )

    def _build_health_causes(
        self,
        *,
        sleep_label: str,
        weather_text: str,
        diary_tags: set[str],
    ) -> list[str]:
        causes: list[str] = []
        if sleep_label not in {"睡眠平稳", "睡得很踏实"} and random.random() < 0.7:
            causes.append("昨晚没睡踏实")
        if any(tag in diary_tags for tag in {"失眠", "低能量"}) and random.random() < 0.45:
            causes.append("前一天状态就有点透支")
        weather_lower = str(weather_text or "").lower()
        if any(token in weather_text for token in ("降雨", "小雨", "中雨", "大雨", "阴", "多云")) and random.random() < 0.4:
            causes.append("空气有点潮,身上那股乏劲更明显")
        if any(token in weather_text for token in ("风", "降温", "冷")) and random.random() < 0.55:
            causes.append("吹了点风,身上容易发空")
        temp_match = re.search(r"(-?\d+(?:\.\d+)?)\s*°C", weather_lower)
        if temp_match:
            try:
                temp = float(temp_match.group(1))
            except ValueError:
                temp = 20.0
            if temp <= 10 and random.random() < 0.55:
                causes.append("天气偏冷,早上容易着凉")
            elif temp >= 30 and random.random() < 0.35:
                causes.append("天气闷热,整个人有点蔫")
        return causes

    def _pick_health_spec(
        self, causes: list[str], intensity: float, weather_text: str
    ) -> tuple[str, str, int, int, str] | None:
        if not causes:
            return None
        chance = min(0.42, 0.12 + len(causes) * 0.1 * max(0.5, intensity))
        if random.random() > chance:
            return None
        cause_text = ",".join(dict.fromkeys(causes[:2]))
        pool = [
            ("喉咙有点发紧,今天想少说重话", "安静", -10, 24),
            ("头有点沉,做事想放慢一点", "疲惫", -14, 18),
            ("像有点发虚,反应会慢半拍", "疲惫", -18, 30),
        ]
        label, mood, energy_delta, duration_hours = random.choice(pool)
        if "闷热" in cause_text and "喉咙" in label:
            label = "有点发闷,只想把动作放轻一点"
        if "潮" in cause_text and "头有点沉" in label:
            label = "身上有点沉,今天想把事情做轻一点"
        return label, mood, energy_delta, duration_hours, cause_text

    def _build_transition_options(
        self,
        *,
        kind: str,
        energy_delta: int,
        cause: str,
        on_end_transition: str,
    ) -> list[dict[str, Any]]:
        if on_end_transition == "health_relief":
            return [
                {"to": "recovery_afterglow", "base_weight": 0.45},
                {"to": "stable", "base_weight": 0.4},
                {"to": "health_tail", "base_weight": 0.15},
            ]
        if on_end_transition == "sleep_rebound":
            return [
                {"to": "sleep_afterglow", "base_weight": 0.35},
                {"to": "stable", "base_weight": 0.5},
                {"to": "sleep_tail", "base_weight": 0.15},
            ]
        if kind == "care_warmth":
            return [
                {"to": "stable", "base_weight": 0.8},
                {"to": "soft_afterglow", "base_weight": 0.2},
            ]
        return []

    def _make_condition(
        self,
        *,
        kind: str,
        title: str,
        label: str,
        mood: str,
        energy_delta: int,
        duration_hours: int,
        intensity: int,
        cause: str = "",
        on_end_transition: str = "",
        phase: str = "",
        episode_key: str = "",
        transition_options: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        start_ts = _now_ts()
        return {
            "id": f"{kind}-{int(start_ts)}-{random.randint(1000, 9999)}",
            "kind": kind,
            "title": title,
            "label": label,
            "mood": mood,
            "energy_delta": energy_delta,
            "intensity": intensity,
            "start_ts": start_ts,
            "end_ts": start_ts + duration_hours * 3600,
            "duration_hours": duration_hours,
            "cause": cause,
            "on_end_transition": on_end_transition,
            "phase": phase,
            "episode_key": episode_key,
            "transition_options": list(transition_options or []),
        }

    def _infer_manual_state_mood(self, text: str) -> str:
        raw = str(text or "")
        mapping = [
            (("累", "疲惫", "困", "没电"), "疲惫"),
            (("烦", "乱", "躁", "闷"), "烦闷"),
            (("病", "难受", "不舒服", "头疼", "发烧"), "虚弱"),
            (("饿", "胃口", "嘴馋"), "黏人"),
            (("开心", "轻快", "高兴", "兴奋"), "轻快"),
            (("紧张", "慌", "忐忑"), "紧张"),
            (("安静", "困倦", "恍惚"), "安静"),
        ]
        for markers, mood in mapping:
            if any(marker in raw for marker in markers):
                return mood
        return "平稳"

    def _infer_manual_state_energy_delta(self, text: str) -> int:
        raw = str(text or "")
        if any(token in raw for token in ("开心", "轻快", "高兴", "兴奋")):
            return 6
        if any(token in raw for token in ("病", "难受", "不舒服", "发烧", "头疼")):
            return -16
        if any(token in raw for token in ("累", "疲惫", "困", "没电")):
            return -10
        if any(token in raw for token in ("烦", "乱", "躁", "闷")):
            return -8
        return -4 if any(token in raw for token in ("紧张", "慌")) else 0

    async def _add_manual_state(self, value: str) -> tuple[bool, str]:
        raw = str(value or "").strip()
        if not raw:
            return False, "请这样填写：陪伴 增添状态 有点累了|8"
        label_part, sep, hours_part = raw.partition("|")
        label = _single_line(label_part, 80)
        if not label:
            return False, "状态描述不能为空。"
        profile = self._persona_state_profile()
        hunger_like = any(token in label for token in ("饿", "胃口", "嘴馋", "馋", "想吃", "吃点", "吃些"))
        health_like = any(token in label for token in ("病", "难受", "不舒服", "发烧", "头疼", "头痛", "咳", "感冒"))
        if hunger_like and not profile.get("allow_hunger", True):
            return False, "当前配置未开启饥饿/胃口状态。"
        if health_like and not profile.get("allow_health", True):
            return False, "当前配置未开启健康/不适状态。"
        duration_hours = _safe_int(hours_part.strip() if sep else 12, 12, 1, 72)
        mood = self._infer_manual_state_mood(label)
        energy_delta = self._infer_manual_state_energy_delta(label)
        await self._ensure_daily_state()
        async with self._data_lock:
            conditions = self.data.setdefault("state_conditions", [])
            if not isinstance(conditions, list):
                self.data["state_conditions"] = []
                conditions = self.data["state_conditions"]
            conditions.append(
                self._make_condition(
                    kind="manual_state",
                    title="手动增添状态",
                    label=label,
                    mood=mood,
                    energy_delta=energy_delta,
                    duration_hours=duration_hours,
                    intensity=60,
                    cause="由用户手动增添",
                    phase="manual",
                )
            )
            state = self._compose_state_from_conditions(self.data.get("daily_weather", {}))
            self.data["daily_state"] = state
            self._save_data_sync(sections={"daily_state", "state_conditions"})
        return True, f"已增添状态：{label}（约持续 {duration_hours} 小时）"

    def _recent_diary_tags(self) -> set[str]:
        return recent_diary_tags(self)

    def _recent_diary_context(self, count: int = 3) -> str:
        return recent_diary_context(self, count)

    def _normalize_dream_fragment_item(self, raw: Any) -> dict[str, Any] | None:
        return normalize_dream_fragment_item(self, raw)

    def _dream_fragment_effective_weight(self, fragment: dict[str, Any], now_ts: float | None = None) -> float:
        return dream_fragment_effective_weight(self, fragment, now_ts=now_ts)

    def _normalize_dream_fragment_pool(self, fragments: Any, *, now_ts: float | None = None) -> list[dict[str, Any]]:
        return normalize_dream_fragment_pool(self, fragments, now_ts=now_ts)

    def _extract_weighted_dream_fragments(self, payload: Any) -> list[dict[str, Any]]:
        return extract_weighted_dream_fragments(self, payload)

    def _fallback_dream_fragments_for_diary(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        return fallback_dream_fragments_for_diary(self, state)

    def _merge_dream_fragment_pool(self, new_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return merge_dream_fragment_pool(self, new_items)

    def _weighted_unique_fragment_sample(
        self,
        fragments: list[dict[str, Any]],
        *,
        count: int,
    ) -> list[str]:
        return weighted_unique_fragment_sample(self, fragments, count=count)

    def _build_dream_memory_fragments(self, count: int = 8) -> list[str]:
        return build_dream_memory_fragments(self, count)

    def _dream_theme_specs(self) -> list[tuple[str, str]]:
        return dream_theme_specs(self)

    async def _generate_enhanced_dream_pick(
        self,
        weather: dict[str, Any] | None = None,
    ) -> tuple[str, str, int, int] | None:
        return await generate_enhanced_dream_pick(self, weather)

    def _environment_weather_observation(self, weather: dict[str, Any] | None) -> dict[str, Any]:
        text = self._weather_summary_text(weather)
        if text == "暂无天气信息":
            return {}
        compact = text.lower()
        category = "other"
        severity = 1
        category_rules = (
            ("thunder", 4, ("雷暴", "雷阵雨", "打雷", "雷电")),
            ("heavy_rain", 4, ("暴雨", "大暴雨", "特大暴雨", "大雨")),
            ("snow", 4, ("暴雪", "大雪", "中雪", "小雪", "雨夹雪", "降雪")),
            ("dust", 4, ("沙尘暴", "扬沙", "浮尘")),
            ("rain", 3, ("阵雨", "中雨", "小雨", "降雨", "下雨", "雨天")),
            ("fog", 3, ("大雾", "浓雾", "雾霾", "雾")),
            ("wind", 3, ("大风", "强风", "狂风", "阵风")),
            ("clear", 1, ("晴朗", "晴天", "晴")),
            ("cloud", 1, ("阴天", "多云", "阴")),
        )
        for candidate, candidate_severity, markers in category_rules:
            if any(marker in compact for marker in markers):
                category = candidate
                severity = candidate_severity
                break
        temperature = None
        match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:°\s*c|℃|摄氏度)", compact, flags=re.I)
        if match:
            try:
                temperature = float(match.group(1))
            except (TypeError, ValueError):
                temperature = None
        return {
            "text": _single_line(text, 120),
            "category": category,
            "severity": severity,
            "temperature": temperature,
            "fetched_ts": _safe_float((weather or {}).get("fetched_ts"), 0),
        }

    def _detect_environment_weather_change(
        self,
        previous: dict[str, Any] | None,
        current: dict[str, Any] | None,
    ) -> dict[str, Any]:
        before = self._environment_weather_observation(previous)
        after = self._environment_weather_observation(current)
        if not before or not after:
            return {}
        old_category = _single_line(before.get("category"), 24)
        new_category = _single_line(after.get("category"), 24)
        old_temp = before.get("temperature")
        new_temp = after.get("temperature")
        category_labels = {
            "thunder": "突然开始打雷",
            "heavy_rain": "雨势突然变大",
            "rain": "外面开始下雨",
            "snow": "外面开始下雪",
            "fog": "外面突然起雾",
            "wind": "外面的风突然变大",
            "dust": "外面出现沙尘天气",
            "clear": "外面的天突然放晴",
            "cloud": "外面的天色明显转阴",
        }
        wet_categories = {"rain", "heavy_rain", "thunder", "snow"}
        kind = ""
        topic = ""
        score = 0
        if old_category != new_category and new_category in category_labels:
            if old_category in wet_categories and new_category in {"clear", "cloud", "other"}:
                kind = "precipitation_stopped"
                topic = "外面的雨雪停了"
                score = 78
            elif new_category in {"thunder", "heavy_rain", "snow", "dust"}:
                kind = f"weather_to_{new_category}"
                topic = category_labels[new_category]
                score = 92
            elif new_category in {"rain", "fog", "wind"}:
                kind = f"weather_to_{new_category}"
                topic = category_labels[new_category]
                score = 86
            elif old_category in wet_categories and new_category == "clear":
                kind = "weather_cleared"
                topic = category_labels[new_category]
                score = 76

        temperature_delta = None
        if isinstance(old_temp, (int, float)) and isinstance(new_temp, (int, float)):
            temperature_delta = float(new_temp) - float(old_temp)
            if abs(temperature_delta) >= 5.0 and not kind:
                kind = "temperature_jump"
                topic = "气温突然升高了" if temperature_delta > 0 else "气温突然降下来了"
                score = 84 if abs(temperature_delta) >= 8.0 else 78
        if not kind:
            return {}
        fingerprint = f"{kind}:{old_category}>{new_category}:{round(float(new_temp), 1) if isinstance(new_temp, (int, float)) else 'na'}"
        return {
            "kind": kind,
            "topic": topic,
            "score": score,
            "fingerprint": fingerprint,
            "previous": before,
            "current": after,
            "temperature_delta": temperature_delta,
        }

    def _environment_change_owner_users(self) -> list[tuple[str, dict[str, Any]]]:
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(users, dict):
            return []
        targets: list[tuple[str, dict[str, Any]]] = []
        for raw_user_id, user in users.items():
            user_id = str(raw_user_id or "").strip()
            if not user_id or not isinstance(user, dict) or not user.get("umo"):
                continue
            if self._private_user_role(user, user_id) != "owner":
                continue
            if not self._user_enabled_for_proactive(user_id, user):
                continue
            targets.append((user_id, user))
        return targets

    def _queue_environment_change_candidates_locked(self, change: dict[str, Any], *, now: float) -> int:
        if not isinstance(change, dict) or not _single_line(change.get("topic"), 80):
            return 0
        current = self._environment_fromtimestamp(now)
        minute = current.hour * 60 + current.minute
        if not (6 * 60 <= minute < 23 * 60 + 30):
            return 0
        offered = 0
        for user_id, user in self._environment_change_owner_users():
            scheduled = now + random.uniform(1.0, 3.0) * 60.0
            change_fingerprint = _single_line(change.get("fingerprint"), 160)
            candidate = {
                "source": "environment_change",
                "reason": "environment_change",
                "action": "message",
                "scheduled_ts": scheduled,
                "window_start_at": scheduled,
                "preferred_ts": scheduled,
                "best_until_at": scheduled + 20 * 60,
                "expire_at": scheduled + 45 * 60,
                "topic": _single_line(change.get("topic"), 80),
                "motive": "刚注意到外界环境发生了明显变化，想趁变化还新鲜时自然提一句",
                "score": _safe_int(change.get("score"), 82, 0, 100),
                "origin_event_id": (
                    "environment:" + hashlib.sha1(change_fingerprint.encode("utf-8", errors="ignore")).hexdigest()[:24]
                    if change_fingerprint
                    else ""
                ),
                "context_key": "planned_environment_change_context",
                "context": deepcopy(change),
            }
            if self._offer_proactive_candidate(user_id, user, candidate):
                offered += 1
        return offered

    def _format_environment_change_prompt(self, user: dict[str, Any], *, reason: str = "") -> str:
        if reason != "environment_change" or not isinstance(user, dict):
            return ""
        context = user.get("planned_environment_change_context")
        if not isinstance(context, dict):
            return ""
        before = context.get("previous") if isinstance(context.get("previous"), dict) else {}
        after = context.get("current") if isinstance(context.get("current"), dict) else {}
        return (
            "【刚发生的环境变化】\n"
            f"- 变化前：{_single_line(before.get('text'), 120) or '无可靠信息'}\n"
            f"- 当前：{_single_line(after.get('text'), 120) or '无可靠信息'}\n"
            f"- 可用切口：{_single_line(context.get('topic'), 80)}\n"
            "这是刚刷新到的实时环境变化，只能贴着上述事实自然说一句。不要说监测、接口、天气缓存或系统提醒；"
            "不要扩写成天气预报，也不要虚构用户正在室外。"
        )

    def _format_weather_alert_prompt(self, user: dict[str, Any], *, reason: str = "") -> str:
        """Render one structured alert for the proactive generation prompt."""

        if reason != "weather_alert" or not isinstance(user, dict):
            return ""
        context = user.get("planned_weather_alert_context")
        if not isinstance(context, dict):
            return ""
        alert = context.get("alert") if isinstance(context.get("alert"), dict) else context
        if not isinstance(alert, dict):
            return ""
        kind = _single_line(context.get("kind"), 20)
        status = _single_line(context.get("status"), 32) or {
            "new": "刚发布",
            "updated": "刚更新",
            "cancelled": "已解除",
            "resolved": "已解除",
            "expired": "已过期",
        }.get(kind, "有变化")
        level = _single_line(alert.get("color") or alert.get("severity"), 24)
        event = _single_line(alert.get("event") or "天气", 48)
        headline = _single_line(alert.get("headline") or alert.get("description"), 220)
        instruction = _single_line(alert.get("instruction"), 500)
        sender = _single_line(alert.get("sender"), 100)
        expire = _single_line(alert.get("expire_time"), 60)
        lines = [
            "【当前气象预警】",
            f"- 状态：{status}",
            f"- 等级/现象：{level + '｜' if level else ''}{event}",
            f"- 标题：{headline or '暂无标题'}",
        ]
        if instruction:
            lines.append(f"- 防护建议：{instruction}")
        if sender:
            lines.append(f"- 发布方：{sender}")
        if expire:
            lines.append(f"- 预计结束：{expire}")
        lines.append(
            "这是一条与主要用户所在地点相关的结构化预警事实。只把它转成一句自然、克制、及时的私聊；"
            "可以提醒减少外出、留意雷雨或按防护建议行动，但不要虚构用户正在室外、已经受灾或一定会发生的结果。"
            "不要说监测、接口、缓存、轮询、API、数据源或内部字段，也不要把普通天气背景和这条预警混成播报清单。"
        )
        return "\n".join(lines)

    async def _maybe_refresh_environment_change(self) -> None:
        if not bool(runtime_persona_setting(self, "enable_environment_change_proactive", True)) or not runtime_persona_setting(self, "enable_weather_context", True):
            return
        lock = getattr(self, "_environment_change_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self._environment_change_lock = lock
        async with lock:
            now = _now_ts()
            state = self.data.setdefault("environment_change_awareness", {})
            if not isinstance(state, dict):
                state = {}
                self.data["environment_change_awareness"] = state
            if now < _safe_float(state.get("next_check_at"), 0):
                return
            initialized = bool(state.get("initialized"))
            interval_minutes = max(
                5,
                    _safe_int(runtime_persona_setting(self, "environment_change_check_minutes", 10), 10, 5, 60),
            )
            state["next_check_at"] = now + interval_minutes * 60
            previous = deepcopy(self.data.get("daily_weather", {}))
            current = await self._ensure_weather_context(force=True)
            state["last_check_at"] = now
            state["last_observation"] = self._environment_weather_observation(current)
            state["initialized"] = True
            if not initialized:
                self._schedule_data_save(
                    sections={"environment_change_awareness", "daily_weather"},
                    delay=0.5,
                )
                return
            change = self._detect_environment_weather_change(previous, current)
            if not change:
                self._schedule_data_save(
                    sections={"environment_change_awareness", "daily_weather"},
                    delay=0.5,
                )
                return
            fingerprint = _single_line(change.get("fingerprint"), 160)
            cooldown_minutes = max(
                20,
                    _safe_int(runtime_persona_setting(self, "environment_change_cooldown_minutes", 90), 90, 20, 360),
            )
            last_prompted_at = _safe_float(state.get("last_prompted_at"), 0)
            recent_fingerprints = state.get("recent_fingerprints")
            if not isinstance(recent_fingerprints, dict):
                recent_fingerprints = {}
                state["recent_fingerprints"] = recent_fingerprints
            cutoff = now - cooldown_minutes * 60
            for old_key, old_ts in list(recent_fingerprints.items()):
                if _safe_float(old_ts, 0) < cutoff:
                    recent_fingerprints.pop(old_key, None)
            recently_repeated = _safe_float(recent_fingerprints.get(fingerprint), 0) >= cutoff
            # 通用冷却：距上一次环境突变提示 < cooldown 内，非紧急（score<90）的变化不
            # 重复开口。不同变化（雨停/下雨/雨势变大）指纹不同，recently_repeated 拦不住；
            # 旧代码用写死的 20 分钟，cooldown_minutes 只对同指纹去重，360 分钟设置等于
            # 形同虚设（实测 30-40 分钟就 offer 一条）。score>=90 的极端变化保留逃生口。
            too_soon = now - last_prompted_at < cooldown_minutes * 60 and _safe_int(change.get("score"), 0) < 90
            if recently_repeated or too_soon:
                self._schedule_data_save(
                    sections={"environment_change_awareness", "daily_weather"},
                    delay=0.5,
                )
                return
            offered = 0
            async with self._data_lock:
                offered = self._queue_environment_change_candidates_locked(change, now=now)
                state["last_change"] = deepcopy(change)
                state["last_change_at"] = now
                if offered:
                    state["last_fingerprint"] = fingerprint
                    state["last_prompted_at"] = now
                    state["last_prompted_users"] = offered
                    recent_fingerprints[fingerprint] = now
                self._save_data_sync(
                    sections={
                        "environment_change_awareness",
                        "daily_weather",
                        "users",
                        "proactive_candidate_pool",
                    }
                )
            if offered:
                logger.info(
                    "环境突变已进入即时主动候选: kind=%s targets=%s topic=%s",
                    _single_line(change.get("kind"), 40),
                    offered,
                    _single_line(change.get("topic"), 80),
                )

    def _weather_context_config_key(self) -> str:
        """Return a credential-free identity for the active weather place."""

        source = str(runtime_persona_setting(self, "weather_source", "qweather") or "qweather").strip().lower()
        parts = [source, self._weather_window_timezone()]
        if source == "qweather":
            parts.extend((self._qweather_weather_api_host(), self._qweather_location_identity()))
        elif source == "amap":
            parts.append(_single_line(runtime_persona_setting(self, "weather_amap_city", ""), 80).casefold())
        elif source == "openweathermap":
            city = _single_line(runtime_persona_setting(self, "weather_city", ""), 120).casefold()
            if city:
                parts.append("city:" + city)
            else:
                location = self._qweather_legacy_weather_location()
                parts.append("coordinates:" + repr(location))
        elif source == "openmeteo":
            parts.append("coordinates:" + repr(self._qweather_legacy_weather_location()))
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24]

    def _weather_window_timezone(self) -> str:
        """Resolve the effective timezone without requiring ProactiveMixin."""

        resolver = getattr(self, "_proactive_window_timezone", None)
        if callable(resolver):
            try:
                resolved = _single_line(resolver(), 64)
            except Exception:
                resolved = ""
            if resolved:
                return resolved
        return _single_line(
            getattr(self, "environment_perception_timezone", ""),
            64,
        ) or "Asia/Shanghai"

    async def _ensure_weather_context(self, force: bool = False) -> dict[str, Any]:
        today = _today_key()
        if not runtime_persona_setting(self, "enable_weather_context", True):
            return {"date": today, "prompt": "暂无天气信息", "source": "disabled"}
        weather_source = runtime_persona_setting(self, "weather_source", "qweather")
        config_key = self._weather_context_config_key()
        cached = self.data.get("daily_weather", {})
        if isinstance(cached, dict):
            fetched_at = _safe_float(cached.get("fetched_ts"), 0)
            if (
                not force
                and cached.get("date") == today
                and cached.get("weather_source", "qweather") == weather_source
                and cached.get("config_key") == config_key
                and _now_ts() - fetched_at < _safe_int(runtime_persona_setting(self, "weather_refresh_minutes", 90), 90, 1) * 60
            ):
                return cached
        prompt = "暂无天气信息"
        source = "none"
        own_result = await self._fetch_own_weather_prompt()
        text = _single_line(own_result.get("prompt"), 120) if isinstance(own_result, dict) else ""
        location_label = _single_line(own_result.get("location_label"), 120) if isinstance(own_result, dict) else ""
        if not location_label and str(weather_source).strip().lower() == "qweather":
            location_label = _single_line(self._qweather_location_snapshot().get("label"), 120)
        if text:
            prompt = text
            source = str(own_result.get("source") or "private_companion")
        else:
            plugin = self._get_screen_companion_plugin()
            if plugin is not None and hasattr(plugin, "_get_weather_prompt"):
                try:
                    result = await plugin._get_weather_prompt()
                    text = _single_line(result, 120)
                    if text:
                        prompt = text
                        source = "screen_companion"
                except Exception as e:
                    logger.debug(f"获取天气信息失败: {e}")
        weather = {
            "date": today,
            "prompt": prompt,
            "source": source,
            "weather_source": weather_source,
            "config_key": config_key,
            "location_label": location_label,
            "fetched_ts": _now_ts(),
        }
        async with self._data_lock:
            self.data["daily_weather"] = weather
            self._save_data_sync(sections={"daily_weather"})
        return weather

    @staticmethod
    def _user_asks_current_weather(text: Any) -> bool:
        """识别用户是否在询问本地当前天气，而不是讨论天气功能本身。"""

        cleaned = _single_line(text, 180)
        if not cleaned:
            return False
        compact = re.sub(r"[\s，。！？!?,.、~～…：:；;]+", "", cleaned).casefold()
        if not compact:
            return False

        meta_terms = (
            "天气api",
            "天气接口",
            "天气插件",
            "天气功能",
            "天气配置",
            "天气设置",
            "天气日志",
            "天气代码",
            "和风天气api",
            "和风天气接口",
            "和风天气配置",
        )
        if any(term in compact for term in meta_terms) or re.search(
            r"天气.{0,3}(?:api|接口|插件|功能|配置|设置|日志|代码)",
            compact,
            re.IGNORECASE,
        ):
            return False
        if "天气" in compact and any(
            term in compact
            for term in ("怎么接入", "如何接入", "怎么配置", "如何配置", "报错", "排障", "调试")
        ):
            return False

        # 当前实况接口不能证明未来天气，混合或未来预报问题交给真正的预报能力处理。
        if any(term in compact for term in ("明天", "后天", "大后天", "未来", "下周", "周末")):
            return False

        # “我这边”明确指向用户所在地，不能拿 Bot 配置地点的实况代答。
        if any(term in compact for term in ("我这边", "我们这边", "俺这边", "我这里", "我们这里")):
            return False
        current_terms = ("现在", "当前", "今天", "今日", "此刻", "这会儿", "外面", "当地", "这边", "你那边")
        asks_now = any(term in compact for term in current_terms)
        if "天气" in compact:
            if any(term in compact for term in ("喜欢什么天气", "讨厌什么天气", "什么天气最", "天气原理", "天气形成", "天气变化的原因")):
                return False
            return asks_now or len(compact) <= 12 or any(
                term in compact for term in ("天气怎么样", "天气如何", "天气咋样", "什么天气", "查天气", "看看天气", "天气好吗", "天气呢")
            )
        if re.search(r"(?:多少|几)(?:度|°c?|摄氏度)", compact, re.IGNORECASE):
            return True
        if any(term in compact for term in ("气温", "温度")):
            return asks_now or len(compact) <= 10
        if any(term in compact for term in ("要带伞", "需要带伞", "用带伞", "下雨吗", "在下雨", "下雪吗", "在下雪")):
            return asks_now or len(compact) <= 10
        if asks_now and any(term in compact for term in ("冷不冷", "热不热", "冷吗", "热吗")):
            return True
        return False

    async def _append_weather_query_context_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *,
        current_user: dict[str, Any] | None = None,
    ) -> bool:
        """显式天气查询时按需获取实况，并把可信结果注入当前请求。"""

        marker = "<!-- private_companion_weather_query_v1 -->"
        if self._request_has_managed_prompt_marker(req, marker):
            return True
        inbound_text = _single_line(
            getattr(event, "private_companion_group_text", "")
            or getattr(event, "message_str", "")
            or getattr(req, "prompt", ""),
            180,
        )
        if not self._user_asks_current_weather(inbound_text):
            return False
        if not bool(runtime_persona_setting(self, "enable_weather_context", True)):
            return False

        weather = await self._ensure_weather_context(force=False)
        prompt = self._weather_summary_text(weather)
        valid = bool(prompt and prompt != "暂无天气信息")
        role = ""
        location_label = ""
        fetched_at = _safe_float(weather.get("fetched_ts"), 0) if isinstance(weather, dict) else 0
        configured_source = str(runtime_persona_setting(self, "weather_source", "qweather") or "qweather").strip().lower()
        expected_source = {
            "qweather": "qweather",
            "amap": "amap",
            "openmeteo": "openmeteo",
            "openweathermap": "private_companion",
        }.get(configured_source, configured_source)
        actual_source = _single_line(weather.get("source"), 40) if isinstance(weather, dict) else ""
        using_fallback = bool(valid and expected_source and actual_source != expected_source)
        if (not valid or using_fallback) and (fetched_at <= 0 or _now_ts() - fetched_at >= 60):
            weather = await self._ensure_weather_context(force=True)
            prompt = self._weather_summary_text(weather)
            valid = bool(prompt and prompt != "暂无天气信息")

        if valid:
            injection_title = "本轮当前天气查询"
            source = _single_line(weather.get("source"), 40) if isinstance(weather, dict) else ""
            source_label = {
                "qweather": "和风天气",
                "amap": "高德天气",
                "openmeteo": "Open-Meteo",
                "private_companion": "OpenWeatherMap",
                "screen_companion": "天气联动来源",
            }.get(source, "已配置的天气来源")
            details = [f"实况：{prompt}", f"来源：{source_label}"]
            role = self._private_user_role(current_user or {}) if isinstance(current_user, dict) else ""
            location_label = _single_line(weather.get("location_label"), 80) if isinstance(weather, dict) else ""
            if role == "owner" and location_label:
                details.insert(0, f"地点：{location_label}")
            fetched_ts = _safe_float(weather.get("fetched_ts"), 0) if isinstance(weather, dict) else 0
            if fetched_ts > 0:
                details.append(f"数据获取时间：{datetime.fromtimestamp(fetched_ts).strftime('%H:%M')}")
            injection = (
                "用户本轮明确询问当前天气。以下数据由本插件配置的天气来源直接取得，是本轮回答依据：\n"
                + "\n".join(details)
                + "\n请直接结合用户问题自然回答，不要再调用搜索、浏览器、地图、记忆或其他天气工具。"
                "只能陈述以上数据能够证明的当前实况；不要据此编造未来预报，也不要向用户提及系统提示词或注入过程。"
            )
        else:
            injection_title = "本轮天气查询暂未取得实况"
            injection = (
                "用户本轮明确询问当前天气，但本插件已尝试读取配置的天气来源，本轮没有取得有效实况。\n"
                "不要编造天气，也不要调用搜索、浏览器、地图、记忆或多个工具反复查找。"
                "如果当前确有一个明确、专用的天气工具，最多尝试一次；否则简短说明暂时没有取到天气即可。"
            )

        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            injection,
            title=injection_title,
            priority=24,
            source="weather_query",
            force_dynamic=True,
        ) else "system_prompt"
        plan = get_conversation_injection_plan(req)
        if placement == "system_prompt":
            if plan is not None:
                plan.materialize_system_block(
                    req,
                    key="weather.query",
                    marker=marker,
                    content=injection,
                    title=injection_title,
                    priority=24,
                    source="weather_query",
                    placement=PLACEMENT_DYNAMIC_SYSTEM,
                )
            else:
                req.system_prompt = f"{req.system_prompt or ''}\n\n{marker}\n{injection}".strip()
        elif plan is not None and not plan.contains_marker(marker):
            plan.add(
                key="weather.query",
                marker=marker,
                content=injection,
                title=injection_title,
                priority=24,
                source="weather_query",
                placement=PLACEMENT_TURN_TAIL,
                temporary=True,
            )
        recorder = getattr(self, "_record_request_prompt_fragment", None)
        if callable(recorder):
            await recorder(
                event,
                title="当前天气查询注入",
                key="weather.query",
                text=injection,
                source="weather_query",
                metadata={"注入位置": placement, "获取成功": valid},
            )
        logger.info(
            "当前天气查询已处理: session=%s success=%s source=%s location_visible=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            valid,
            _single_line(weather.get("source"), 40) if isinstance(weather, dict) else "none",
            bool(valid and isinstance(current_user, dict) and role == "owner" and location_label),
        )
        return True

    def _weather_summary_text(self, weather: dict[str, Any] | None) -> str:
        if not isinstance(weather, dict):
            return "暂无天气信息"
        text = _single_line(weather.get("prompt"), 120)
        return text or "暂无天气信息"

    async def _ensure_yesterday_screen_diary_context(self, force: bool = False) -> dict[str, Any]:
        today = _today_key()
        yesterday = date.today() - timedelta(days=1)
        source_date = _date_key(yesterday)
        cached = self.data.get("screen_diary_context", {})
        if (
            isinstance(cached, dict)
            and cached.get("date") == today
            and cached.get("source_date") == source_date
            and not force
        ):
            return cached
        screen_companion_available = False
        try:
            screen_companion_available = self._get_screen_companion_plugin() is not None
        except Exception:
            screen_companion_available = False
        if not runtime_persona_setting(self, "enable_yesterday_screen_diary_context", True) or not screen_companion_available:
            payload = {
                "date": today,
                "source_date": source_date,
            "source": "disabled" if not runtime_persona_setting(self, "enable_yesterday_screen_diary_context", True) else "screen_companion_unavailable",
                "summary": "",
                "items": [],
                "available": False,
            }
        else:
            payload = self._load_yesterday_screen_diary_context(yesterday)
        async with self._data_lock:
            self.data["screen_diary_context"] = payload
            self._save_data_sync(sections={"screen_diary_context"})
        return payload

    def _load_yesterday_screen_diary_context(self, target_date: date) -> dict[str, Any]:
        today = _today_key()
        source_date = _date_key(target_date)
        summary: dict[str, Any] = {}
        diary_text = ""
        source = "none"
        plugin = None
        try:
            plugin = self._get_screen_companion_plugin()
        except Exception:
            plugin = None
        if plugin is not None:
            loader = getattr(plugin, "_load_diary_structured_summary", None)
            if callable(loader):
                try:
                    raw_summary = loader(target_date)
                    if isinstance(raw_summary, dict):
                        summary = raw_summary
                        source = "screen_companion_api"
                except Exception as exc:
                    logger.debug("读取屏幕昨日结构化日记失败: %s", exc)
            if not summary:
                diary_storage = str(getattr(plugin, "diary_storage", "") or "").strip()
                summary = self._load_screen_diary_summary_file(target_date, diary_storage)
                if summary:
                    source = "screen_companion_file"
            diary_storage = str(getattr(plugin, "diary_storage", "") or "").strip()
            diary_text = self._load_screen_diary_markdown_file(target_date, diary_storage)
        if not summary:
            fallback_dirs = [
                str(Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_screen_companion" / "diary"),
                str(Path(__file__).resolve().parents[2] / "plugin_data" / "astrbot_plugin_screen_companion" / "diary"),
            ]
            for fallback_dir in fallback_dirs:
                summary = self._load_screen_diary_summary_file(target_date, fallback_dir)
                if summary:
                    source = "screen_companion_file"
                    if not diary_text:
                        diary_text = self._load_screen_diary_markdown_file(target_date, fallback_dir)
                    break
                if not diary_text:
                    diary_text = self._load_screen_diary_markdown_file(target_date, fallback_dir)
        items = self._screen_diary_items_from_summary(summary)
        if not items and diary_text:
            items = self._screen_diary_items_from_markdown(diary_text)
            if items and source == "none":
                source = "screen_companion_markdown"
        max_chars = max(200, _safe_int(runtime_persona_setting(self, "screen_diary_context_max_chars", 700), 700, 200, 1600))
        summary_text = self._format_screen_diary_context_items(source_date, items, max_chars=max_chars)
        return {
            "date": today,
            "source_date": source_date,
            "source": source,
            "summary": summary_text,
            "items": items[:8],
            "available": bool(summary_text),
        }

    def _load_screen_diary_summary_file(self, target_date: date, diary_dir: str = "") -> dict[str, Any]:
        if not diary_dir:
            return {}
        path = Path(diary_dir) / f"diary_{target_date.strftime('%Y%m%d')}.summary.json"
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.debug("读取屏幕日记摘要文件失败: %s", exc)
            return {}

    def _load_screen_diary_markdown_file(self, target_date: date, diary_dir: str = "") -> str:
        if not diary_dir:
            return ""
        path = Path(diary_dir) / f"diary_{target_date.strftime('%Y%m%d')}.md"
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")[:4000]
        except Exception as exc:
            logger.debug("读取屏幕日记正文失败: %s", exc)
            return ""

    def _screen_diary_activity_label(self, text: Any) -> str:
        raw = str(text or "").lower()
        if not raw:
            return ""
        rules = (
            (("codex", "vscode", "visual studio", "pycharm", "idea", ".py", "插件", "编程", "代码", "终端", "powershell", "cmd"), "编程和插件调试"),
            (("qq", "微信", "wechat", "telegram", "discord", "会话", "聊天", "社交"), "社交消息"),
            (("chrome", "edge", "firefox", "浏览器", "网页", "搜索", "资料"), "查资料或网页浏览"),
            (("bilibili", "youtube", "视频", "番剧", "直播"), "视频或直播放松"),
            (("steam", "game", "游戏"), "游戏放松"),
            (("word", "excel", "wps", "文档", "表格", "写作"), "文档整理"),
            (("program manager", "桌面"), "桌面空档"),
        )
        for markers, label in rules:
            if any(marker in raw for marker in markers):
                return label
        return "电脑前活动"

    def _sanitize_screen_diary_text(self, text: Any, limit: int = 90) -> str:
        raw = _single_line(text, limit * 2)
        if not raw:
            return ""
        raw = re.sub(r"《[^》]{0,80}(?:》|$)", "相关窗口", raw)
        raw = re.sub(r"[\"“”'][^\"“”']{1,80}[\"“”']", "相关内容", raw)
        raw = re.sub(r"\bQQ\b|微信|WeChat|Telegram|Discord", "社交软件", raw, flags=re.IGNORECASE)
        raw = raw.replace("你在", "用户在")
        raw = raw.replace("我看到", "")
        raw = re.sub(r"\s+", " ", raw).strip(" ，。；;")
        return _single_line(raw, limit)

    def _screen_diary_items_from_summary(self, summary: dict[str, Any]) -> list[str]:
        if not isinstance(summary, dict) or not summary:
            return []
        items: list[str] = []
        main_windows = summary.get("main_windows") if isinstance(summary.get("main_windows"), list) else []
        labels: list[str] = []
        for item in main_windows[:4]:
            if not isinstance(item, dict):
                continue
            label = self._screen_diary_activity_label(item.get("window_title"))
            if label and label not in labels and label != "桌面空档":
                labels.append(label)
        if labels:
            items.append("主要节奏偏向：" + "、".join(labels[:3]))
        longest = summary.get("longest_task") if isinstance(summary.get("longest_task"), dict) else {}
        if longest:
            label = self._screen_diary_activity_label(
                f"{longest.get('window_title', '')} {longest.get('focus', '')}"
            )
            focus = self._sanitize_screen_diary_text(longest.get("focus"), 80)
            if label:
                items.append(f"最长专注大概落在{label}" + (f"，{focus}" if focus else ""))
        repeated = summary.get("repeated_focuses") if isinstance(summary.get("repeated_focuses"), list) else []
        repeated_labels: list[str] = []
        for item in repeated[:3]:
            if not isinstance(item, dict):
                continue
            label = self._screen_diary_activity_label(f"{item.get('window_title', '')} {item.get('note', '')}")
            if label and label not in repeated_labels and label != "桌面空档":
                repeated_labels.append(label)
        if repeated_labels:
            items.append("反复回到：" + "、".join(repeated_labels[:3]))
        suggestions = summary.get("suggestion_items") if isinstance(summary.get("suggestion_items"), list) else []
        for suggestion in suggestions[:2]:
            cleaned = self._sanitize_screen_diary_text(suggestion, 100)
            if cleaned and cleaned not in items:
                items.append("留给今天的背景：" + cleaned)
        return items[:6]

    def _screen_diary_items_from_markdown(self, diary_text: str) -> list[str]:
        raw = str(diary_text or "")
        if not raw.strip():
            return []
        lines = []
        in_overview = False
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.startswith("## 今日观察"):
                break
            if stripped.startswith("## 今日概览"):
                in_overview = True
                continue
            if in_overview and stripped.startswith("- "):
                cleaned = self._sanitize_screen_diary_text(stripped[2:], 100)
                label = self._screen_diary_activity_label(cleaned)
                if label and label != "电脑前活动":
                    cleaned = f"{label}：" + cleaned
                if cleaned:
                    lines.append(cleaned)
            if len(lines) >= 5:
                break
        if not lines:
            body = self._sanitize_screen_diary_text(raw, 220)
            if body:
                lines.append(body)
        return lines[:5]

    def _screen_diary_state_condition_spec(self) -> tuple[str, str, str, str, int, int, str] | None:
        payload = self.data.get("screen_diary_context", {})
        if not isinstance(payload, dict) or not payload.get("available"):
            return None
        text = str(payload.get("summary") or "")
        if not text:
            return None
        if any(token in text for token in ("编程", "调试", "查资料")):
            return (
                "user_yesterday_screen_diary",
                "昨日节奏残留",
                "昨天用户在电脑前专注处理代码或资料,今天对方可能还带着一点用脑后的疲惫",
                "留意,克制",
                -3,
                10,
                "来自昨日屏幕观察日记的脱敏节奏摘要",
            )
        if any(token in text for token in ("视频", "直播", "游戏")):
            return (
                "user_yesterday_screen_diary",
                "昨日节奏残留",
                "昨天用户有一段偏放松的电脑时间,今天可以把话题放得轻一点",
                "松弛",
                1,
                8,
                "来自昨日屏幕观察日记的脱敏节奏摘要",
            )
        if any(token in text for token in ("社交消息", "聊天")):
            return (
                "user_yesterday_screen_diary",
                "昨日节奏残留",
                "昨天用户处理过不少社交消息,今天靠近时更适合少一点压迫感",
                "轻一点",
                -1,
                8,
                "来自昨日屏幕观察日记的脱敏节奏摘要",
            )
        return None

    def _format_screen_diary_context_items(self, source_date: str, items: list[str], *, max_chars: int) -> str:
        if not items:
            return ""
        lines = [
            f"昨日屏幕观察日记（{source_date}，已脱敏，仅作背景）：",
        ]
        seen: set[str] = set()
        for item in items:
            cleaned = _single_line(item, 130)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            lines.append(f"- {cleaned}")
        lines.append("使用边界：只把它当作昨日生活节奏背景，影响今天的体力、作息和话题倾向；不要直接说“我昨天看到你”，不要复述窗口名、账号、聊天内容或具体隐私。")
        text = "\n".join(lines)
        return text[:max_chars]

    def _format_yesterday_screen_diary_context_for_prompt(self) -> str:
        if not runtime_persona_setting(self, "enable_yesterday_screen_diary_context", True):
            return "未启用。"
        payload = self.data.get("screen_diary_context", {})
        if not isinstance(payload, dict) or payload.get("date") != _today_key():
            return "暂无可用的昨日屏幕观察日记。"
        max_chars = max(200, _safe_int(runtime_persona_setting(self, "screen_diary_context_max_chars", 700), 700, 200, 1600))
        text = str(payload.get("summary") or "").strip()
        if len(text) > max_chars:
            text = text[:max_chars]
        return text or "暂无可用的昨日屏幕观察日记。"

    # ------------------------------------------------------------------
    # Weather alerts (QWeather)
    # ------------------------------------------------------------------
    # These helpers deliberately stop at structured retrieval and caching.
    # Proactive delivery, severity policy, and prompt wording belong to the
    # caller so a failed provider request cannot itself trigger a message.

    @staticmethod
    def _qweather_alert_rank(value: Any) -> int:
        return _qweather_alert_rank(value)

    @staticmethod
    def _normalize_qweather_api_host(value: Any) -> str:
        """Normalize a QWeather API Host while refusing malformed URLs."""

        raw = str(value or "").strip()
        if not raw:
            return ""
        if "://" not in raw:
            raw = "https://" + raw
        try:
            parsed = urlparse(raw)
        except Exception:
            return ""
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return ""
        if parsed.scheme.lower() == "http":
            # QWeather credentials must not be sent to a remote plaintext
            # endpoint. Keep local development proxies usable without
            # weakening the default for arbitrary hosts.
            hostname = (parsed.hostname or "").strip("[]").lower()
            if hostname not in {"localhost", "127.0.0.1", "::1"}:
                return ""
        # Credentials in an API Host are never useful and could accidentally
        # be written to logs or persisted with the cache.
        if parsed.username or parsed.password:
            return ""
        path = (parsed.path or "").rstrip("/")
        for suffix in (
            "/geo/v2/city/lookup",
            "/geo/v2/city",
            "/geo/v2",
            "/weatheralert/v1/current",
            "/weatheralert/v1",
            "/v7/weather/now",
            "/v7/weather",
            "/v7/warning",
            "/v7",
        ):
            if path.lower().endswith(suffix):
                path = path[: -len(suffix)].rstrip("/")
                break
        return f"{parsed.scheme.lower()}://{parsed.netloc}{path}".rstrip("/")

    def _qweather_alert_api_host(self) -> str:
        return self._normalize_qweather_api_host(
            getattr(self, "weather_api_host", "")
            or getattr(self, "qweather_api_host", "")
            or getattr(self, "weather_alert_api_host", "")
        )

    def _qweather_alert_token(self) -> str:
        # weather_alert_token is the documented name. Keep the aliases for
        # older local configs, but never persist or log the resulting
        # credential.
        raw = (
            getattr(self, "weather_token", "")
            or getattr(self, "qweather_token", "")
            or getattr(self, "weather_alert_token", "")
            or getattr(self, "weather_alert_jwt", "")
            or getattr(self, "weather_alert_api_key", "")
        )
        token = str(raw or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return token

    def _qweather_alert_credential_kind(self, credential: Any = None) -> str:
        """Choose the QWeather auth header without exposing the credential.

        The current Weather Alert API accepts either a JWT or an API Key, but
        the two schemes must never be sent together.  Normal configuration
        uses the unambiguous JWT shape (three dot-separated segments) and
        treats other non-empty values as API Keys.
        """

        value = self._qweather_alert_token() if credential is None else str(credential or "").strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()
        if not value:
            return ""
        segments = value.split(".")
        if len(segments) == 3 and all(segment.strip() for segment in segments):
            return "jwt"
        return "api_key"

    @staticmethod
    def _qweather_alert_coordinate(value: Any, *, minimum: float, maximum: float) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number < minimum or number > maximum:
            return None
        return number

    def _qweather_configured_location(self) -> str:
        raw = _single_line(runtime_persona_setting(self, "weather_location", ""), 180)
        if not raw:
            return ""
        return unicodedata.normalize("NFKC", raw).strip().replace("，", ",")

    def _qweather_coordinates_from_text(self, value: Any) -> tuple[float, float] | None:
        """Parse QWeather's documented ``longitude,latitude`` format."""

        text = unicodedata.normalize("NFKC", str(value or "")).strip().replace("，", ",")
        pieces = [part.strip() for part in text.split(",")]
        if len(pieces) != 2:
            return None
        longitude = self._qweather_alert_coordinate(pieces[0], minimum=-180, maximum=180)
        latitude = self._qweather_alert_coordinate(pieces[1], minimum=-90, maximum=90)
        if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
            return None
        return latitude, longitude

    @staticmethod
    def _qweather_is_location_id(value: Any) -> bool:
        # Current QWeather LocationIDs are numeric (for example 101010100).
        # Keeping the range bounded avoids treating a short numeric city name
        # or an arbitrary long identifier as a provider ID.
        return bool(re.fullmatch(r"\d{7,18}", str(value or "").strip()))

    def _qweather_legacy_weather_location(self) -> tuple[float, float] | None:
        latitude = self._qweather_alert_coordinate(
            runtime_persona_setting(self, "weather_lat", 0),
            minimum=-90,
            maximum=90,
        )
        longitude = self._qweather_alert_coordinate(
            runtime_persona_setting(self, "weather_lon", 0),
            minimum=-180,
            maximum=180,
        )
        if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
            return None
        return latitude, longitude

    def _qweather_location_identity(self) -> str:
        configured = self._qweather_configured_location()
        if configured:
            coordinates = self._qweather_coordinates_from_text(configured)
            if coordinates is not None:
                latitude, longitude = coordinates
                return "coordinates:" + ",".join(
                    (
                        self._qweather_alert_coordinate_text(longitude),
                        self._qweather_alert_coordinate_text(latitude),
                    )
                )
            return "configured:" + configured.casefold()
        legacy = self._qweather_legacy_weather_location()
        if legacy is None:
            return ""
        latitude, longitude = legacy
        return "legacy:" + ",".join(
            (
                self._qweather_alert_coordinate_text(longitude),
                self._qweather_alert_coordinate_text(latitude),
            )
        )

    def _qweather_location_cache_key(self) -> str:
        identity = self._qweather_location_identity()
        host = self._qweather_alert_api_host()
        if not identity or not host:
            return ""
        raw = f"{host}|{identity}"
        return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24]

    def _qweather_cached_location(self, *, allow_stale: bool = True) -> dict[str, Any]:
        data = getattr(self, "data", None)
        cached = data.get("qweather_location") if isinstance(data, dict) else None
        if not isinstance(cached, dict) or cached.get("config_key") != self._qweather_location_cache_key():
            return {}
        location_id = _single_line(cached.get("location_id"), 40)
        latitude = self._qweather_alert_coordinate(cached.get("lat"), minimum=-90, maximum=90)
        longitude = self._qweather_alert_coordinate(cached.get("lon"), minimum=-180, maximum=180)
        if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
            return {}
        fetched_ts = _safe_float(cached.get("fetched_ts"), 0)
        if not allow_stale and (fetched_ts <= 0 or _now_ts() - fetched_ts > 30 * 24 * 60 * 60):
            return {}
        return {
            "version": 1,
            "config_key": str(cached.get("config_key") or ""),
            "location_id": location_id,
            "lat": latitude,
            "lon": longitude,
            "label": _single_line(cached.get("label"), 120),
            "fetched_ts": fetched_ts,
        }

    def _qweather_direct_location(self) -> dict[str, Any]:
        configured = self._qweather_configured_location()
        config_key = self._qweather_location_cache_key()
        if configured:
            coordinates = self._qweather_coordinates_from_text(configured)
            if coordinates is not None:
                latitude, longitude = coordinates
                label = ",".join(
                    (
                        self._qweather_alert_coordinate_text(longitude),
                        self._qweather_alert_coordinate_text(latitude),
                    )
                )
                return {
                    "version": 1,
                    "config_key": config_key,
                    "location_id": "",
                    "lat": latitude,
                    "lon": longitude,
                    "label": label,
                    "fetched_ts": _now_ts(),
                }
            if self._qweather_is_location_id(configured):
                return {
                    "version": 1,
                    "config_key": config_key,
                    "location_id": configured,
                    "lat": None,
                    "lon": None,
                    "label": configured,
                    "fetched_ts": 0,
                }
            return {}
        legacy = self._qweather_legacy_weather_location()
        if legacy is None:
            return {}
        latitude, longitude = legacy
        label = ",".join(
            (
                self._qweather_alert_coordinate_text(longitude),
                self._qweather_alert_coordinate_text(latitude),
            )
        )
        return {
            "version": 1,
            "config_key": config_key,
            "location_id": "",
            "lat": latitude,
            "lon": longitude,
            "label": label,
            "fetched_ts": _now_ts(),
        }

    def _qweather_location_snapshot(self) -> dict[str, Any]:
        cached = self._qweather_cached_location()
        if cached:
            return cached
        return self._qweather_direct_location()

    def _build_qweather_geo_lookup_url(self, query: Any = None) -> str:
        host = self._qweather_alert_api_host()
        configured = self._qweather_configured_location() if query is None else _single_line(query, 180)
        if not host or not configured:
            return ""
        params: dict[str, str | int] = {"location": configured, "number": 1, "lang": "zh"}
        if not self._qweather_is_location_id(configured) and self._qweather_coordinates_from_text(configured) is None:
            pieces = [part.strip() for part in configured.split(",")]
            if len(pieces) == 2 and all(pieces):
                params["location"] = pieces[0]
                params["adm"] = pieces[1]
        return host + "/geo/v2/city/lookup?" + urlencode(params)

    def _parse_qweather_location_payload(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or str(payload.get("code") or "") != "200":
            return {}
        locations = payload.get("location")
        item = locations[0] if isinstance(locations, list) and locations else None
        if not isinstance(item, dict):
            return {}
        latitude = self._qweather_alert_coordinate(item.get("lat"), minimum=-90, maximum=90)
        longitude = self._qweather_alert_coordinate(item.get("lon"), minimum=-180, maximum=180)
        if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
            return {}
        labels: list[str] = []
        for key in ("name", "adm2", "adm1"):
            value = _single_line(item.get(key), 60)
            if value and value not in labels:
                labels.append(value)
        return {
            "location_id": _single_line(item.get("id"), 40),
            "lat": latitude,
            "lon": longitude,
            "label": "，".join(labels) or self._qweather_configured_location(),
        }

    async def _fetch_qweather_location_lookup(self, query: str) -> dict[str, Any]:
        url = self._build_qweather_geo_lookup_url(query)
        token = self._qweather_alert_token()
        if not url or not token:
            return {}
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    url,
                    headers=self._qweather_alert_headers(),
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        logger.debug("和风天气地点解析请求失败: %s", response.status)
                        return {}
                    try:
                        payload = await response.json()
                    except TypeError:
                        payload = await response.json(content_type=None)
        except asyncio.TimeoutError:
            logger.warning("和风天气地点解析请求超时")
            return {}
        except Exception as exc:
            logger.debug("和风天气地点解析失败: %s", _single_line(exc, 160))
            return {}
        return self._parse_qweather_location_payload(payload)

    async def _store_qweather_location(
        self,
        resolved: dict[str, Any],
        *,
        expected_config_key: str = "",
    ) -> dict[str, Any]:
        current_config_key = self._qweather_location_cache_key()
        config_key = str(expected_config_key or current_config_key)
        latitude = self._qweather_alert_coordinate(resolved.get("lat"), minimum=-90, maximum=90)
        longitude = self._qweather_alert_coordinate(resolved.get("lon"), minimum=-180, maximum=180)
        if (
            not config_key
            or (expected_config_key and current_config_key != expected_config_key)
            or latitude is None
            or longitude is None
            or (latitude == 0 and longitude == 0)
        ):
            return {}
        record = {
            "version": 1,
            "config_key": config_key,
            "location_id": _single_line(resolved.get("location_id"), 40),
            "lat": latitude,
            "lon": longitude,
            "label": _single_line(resolved.get("label"), 120),
            "fetched_ts": _now_ts(),
        }

        stored = False

        def store() -> None:
            nonlocal stored
            data = getattr(self, "data", None)
            # Config can change while waiting for the shared data lock. Keep
            # the record tied to the request that produced it, never to a new
            # location selected while the request was in flight.
            if not isinstance(data, dict) or self._qweather_location_cache_key() != config_key:
                return
            data["qweather_location"] = deepcopy(record)
            stored = True
            saver = getattr(self, "_save_data_sync", None)
            if callable(saver):
                try:
                    saver(sections={"qweather_location"})
                except Exception as exc:
                    logger.debug("保存和风天气地点缓存失败: %s", _single_line(exc, 160))

        data_lock = getattr(self, "_data_lock", None)
        if isinstance(data_lock, asyncio.Lock):
            async with data_lock:
                store()
        else:
            store()
        return record if stored else {}

    async def _resolve_qweather_location(self) -> dict[str, Any]:
        """Resolve one shared location for current weather and official alerts."""

        config_key_before_read = self._qweather_location_cache_key()
        cached = self._qweather_cached_location(allow_stale=False)
        if cached and self._qweather_location_cache_key() == config_key_before_read:
            return cached
        lock = getattr(self, "_qweather_location_resolve_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self._qweather_location_resolve_lock = lock
        async with lock:
            # A few rapid page saves can race with one request. Retrying a
            # small bounded number keeps the latest location responsive while
            # still falling back safely under continuous configuration churn.
            for _attempt in range(3):
                expected_config_key = self._qweather_location_cache_key()
                cached = self._qweather_cached_location(allow_stale=False)
                if cached and self._qweather_location_cache_key() == expected_config_key:
                    return cached
                stale = self._qweather_cached_location(allow_stale=True)
                direct = self._qweather_direct_location()
                configured = self._qweather_configured_location()
                if direct and (
                    not configured or self._qweather_coordinates_from_text(configured) is not None
                ):
                    stored = await self._store_qweather_location(
                        direct,
                        expected_config_key=expected_config_key,
                    )
                    if stored:
                        return stored
                    if self._qweather_location_cache_key() != expected_config_key:
                        continue
                    return {}
                if configured:
                    looked_up = await self._fetch_qweather_location_lookup(configured)
                    if self._qweather_location_cache_key() != expected_config_key:
                        continue
                    if looked_up:
                        stored = await self._store_qweather_location(
                            looked_up,
                            expected_config_key=expected_config_key,
                        )
                        if stored:
                            return stored
                        if self._qweather_location_cache_key() != expected_config_key:
                            continue
                        return {}
                    if stale:
                        return stale
                    # A LocationID remains valid for ordinary weather even
                    # when GeoAPI is temporarily unavailable. Alerts wait for
                    # its coordinates instead of guessing a different place.
                    if direct and direct.get("location_id"):
                        return direct
                    return {}
                return {}
            return {}

    def _qweather_alert_location(self, resolved: Any = None) -> tuple[float, float] | None:
        """Return (latitude, longitude), preferring dedicated alert fields."""

        if isinstance(resolved, dict):
            latitude = self._qweather_alert_coordinate(resolved.get("lat"), minimum=-90, maximum=90)
            longitude = self._qweather_alert_coordinate(resolved.get("lon"), minimum=-180, maximum=180)
            if latitude is not None and longitude is not None and (latitude != 0 or longitude != 0):
                return latitude, longitude
        if self._qweather_configured_location():
            snapshot = self._qweather_location_snapshot()
            latitude = self._qweather_alert_coordinate(snapshot.get("lat"), minimum=-90, maximum=90)
            longitude = self._qweather_alert_coordinate(snapshot.get("lon"), minimum=-180, maximum=180)
            if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
                return None
            return latitude, longitude

        lat_value = getattr(self, "weather_alert_lat", None)
        lon_value = getattr(self, "weather_alert_lon", None)
        if lat_value in (None, "") or lon_value in (None, ""):
            lat_value = runtime_persona_setting(self, "weather_lat", lat_value)
            lon_value = runtime_persona_setting(self, "weather_lon", lon_value)
        lat = self._qweather_alert_coordinate(lat_value, minimum=-90, maximum=90)
        lon = self._qweather_alert_coordinate(lon_value, minimum=-180, maximum=180)
        if lat is None or lon is None or (lat == 0 and lon == 0):
            # A few callers keep a location in a single "lat,lon" setting.
            raw_location = str(getattr(self, "weather_alert_location", "") or "").strip()
            pieces = [part.strip() for part in raw_location.split(",")]
            if len(pieces) == 2:
                lat = self._qweather_alert_coordinate(pieces[0], minimum=-90, maximum=90)
                lon = self._qweather_alert_coordinate(pieces[1], minimum=-180, maximum=180)
        if lat is None or lon is None or (lat == 0 and lon == 0):
            return None
        return lat, lon

    @staticmethod
    def _qweather_alert_coordinate_text(value: float, *, decimals: int = 6) -> str:
        try:
            precision = max(0, min(6, int(decimals)))
        except (TypeError, ValueError):
            precision = 6
        text = f"{value:.{precision}f}".rstrip("0").rstrip(".")
        return "0" if text in {"", "-0"} else text

    def _build_qweather_alert_url(self, resolved: Any = None) -> str:
        host = self._qweather_alert_api_host()
        location = self._qweather_alert_location(resolved)
        if not host or location is None:
            return ""
        latitude, longitude = location
        # This is the current API Host route. The parser below also accepts
        # the legacy /v7/warning/now response for compatible gateways.
        path = "/weatheralert/v1/current/" + "/".join(
            (
                # The current endpoint documents a maximum of two decimal
                # places for path coordinates; keep the cache key precise,
                # but send the provider-compatible representation.
                self._qweather_alert_coordinate_text(latitude, decimals=2),
                self._qweather_alert_coordinate_text(longitude, decimals=2),
            )
        )
        return host + path + "?" + urlencode({"localTime": "true", "lang": "zh"})

    def _qweather_alert_headers(self) -> dict[str, str]:
        token = self._qweather_alert_token()
        if self._qweather_alert_credential_kind(token) == "api_key":
            return {
                "X-QW-Api-Key": token,
                "Accept": "application/json",
            }
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    @staticmethod
    def _normalize_weather_alert(raw: Any, *, source: str = "qweather") -> dict[str, Any]:
        """Normalize current and legacy QWeather alert objects."""

        if not isinstance(raw, dict):
            return {}
        message_type_raw = raw.get("messageType")
        message_type_code = ""
        supersedes: list[str] = []
        if isinstance(message_type_raw, dict):
            message_type_code = _qweather_alert_text(
                _qweather_alert_first(message_type_raw, "code", "name", "type"), 64
            )
            supersedes = _qweather_alert_string_list(message_type_raw.get("supersedes"), limit=32)
        else:
            message_type_code = _qweather_alert_text(message_type_raw, 64)
        if not supersedes:
            supersedes = _qweather_alert_string_list(raw.get("supersedes"), limit=32)
        event_type_raw = raw.get("eventType")
        event_name = ""
        event_code = ""
        if isinstance(event_type_raw, dict):
            event_name = _qweather_alert_text(_qweather_alert_first(event_type_raw, "name", "title"), 80)
            event_code = _qweather_alert_text(event_type_raw.get("code"), 64)
        else:
            event_name = _qweather_alert_text(event_type_raw, 80)
        if not event_name:
            event_name = _qweather_alert_text(
                _qweather_alert_first(raw, "typeName", "eventName", "type", "event"), 80
            )
        if not event_code:
            event_code = _qweather_alert_text(_qweather_alert_first(raw, "typeCode", "eventCode"), 64)
        color_name, color_code = _qweather_alert_color(
            raw.get("color", _qweather_alert_first(raw, "level", "warningLevel", "colorName"))
        )
        alert_id = _qweather_alert_text(
            _qweather_alert_first(raw, "id", "alertId", "warningId", "identifier"), 160
        )
        issued_time = _qweather_alert_text(
            _qweather_alert_first(raw, "issuedTime", "pubTime", "publishTime", "issuedAt"), 80
        )
        effective_time = _qweather_alert_text(
            _qweather_alert_first(raw, "effectiveTime", "effective", "startTime", "validFrom"), 80
        )
        onset_time = _qweather_alert_text(
            _qweather_alert_first(raw, "onsetTime", "onset", "beginTime"), 80
        )
        expire_time = _qweather_alert_text(
            _qweather_alert_first(raw, "expireTime", "expires", "expiresTime", "endTime", "ends"), 80
        )
        headline = _qweather_alert_text(
            _qweather_alert_first(raw, "headline", "title", "summary"), 240
        )
        description = _qweather_alert_text(
            _qweather_alert_first(raw, "description", "detail", "text"), 2000
        )
        sender = _qweather_alert_text(
            _qweather_alert_first(raw, "senderName", "sender", "publisher", "source"), 160
        )
        severity = _qweather_alert_text(raw.get("severity"), 32)
        status = _qweather_alert_text(raw.get("status"), 32)
        lower_message_type = message_type_code.lower()
        is_cancelled = any(token in lower_message_type for token in ("cancel", "撤销", "解除"))
        response_types = _qweather_alert_string_list(
            _qweather_alert_first(raw, "responseTypes", "response_types", "instructionTypes"),
            limit=16,
        )
        instruction = _qweather_alert_text(
            _qweather_alert_first(raw, "instruction", "instructions", "advice"), 1200
        )
        criteria = _qweather_alert_text(raw.get("criteria"), 600)
        fingerprint_source = "|".join(
            (
                alert_id,
                event_code or event_name,
                headline,
                sender,
                issued_time,
                message_type_code,
                color_code,
                severity,
                effective_time,
                onset_time,
                expire_time,
                description,
                instruction,
                ",".join(supersedes),
            )
        )
        fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8", "ignore")).hexdigest()[:24]
        return {
            "id": alert_id,
            "source": _qweather_alert_text(source, 32) or "qweather",
            "sender": sender,
            "issued_time": issued_time,
            "message_type": message_type_code,
            "supersedes": supersedes,
            "event": event_name,
            "event_code": event_code,
            "urgency": _qweather_alert_text(raw.get("urgency"), 32),
            "severity": severity,
            "certainty": _qweather_alert_text(raw.get("certainty"), 32),
            "color": color_name,
            "color_code": color_code,
            "effective_time": effective_time,
            "onset_time": onset_time,
            "expire_time": expire_time,
            "headline": headline,
            "description": description,
            "criteria": criteria,
            "response_types": response_types,
            "instruction": instruction,
            "status": status,
            "is_cancelled": is_cancelled,
            "fingerprint": fingerprint,
        }

    @classmethod
    def _parse_qweather_alert_payload(cls, payload: Any) -> dict[str, Any]:
        """Parse a provider response, retaining all normalized alerts."""

        if not isinstance(payload, dict):
            return {"ok": False, "alerts": [], "error": "invalid_payload"}
        provider_code = str(payload.get("code") or "").strip()
        if provider_code and provider_code not in {"200", "0"}:
            return {
                "ok": False,
                "alerts": [],
                "error": "provider_code_" + _qweather_alert_text(provider_code, 32),
            }
        raw_alerts = payload.get("alerts")
        if raw_alerts is None:
            raw_alerts = payload.get("warning")
        if raw_alerts is None and isinstance(payload.get("data"), dict):
            raw_alerts = payload["data"].get("alerts", payload["data"].get("warning"))
        if not isinstance(raw_alerts, list):
            raw_alerts = []
        alerts = [cls._normalize_weather_alert(item) for item in raw_alerts]
        alerts = [item for item in alerts if item]
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        if not metadata and isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("metadata"), dict):
            metadata = payload["data"]["metadata"]
        update_time = _qweather_alert_text(
            _qweather_alert_first(payload, "updateTime", "updatedAt")
            or _qweather_alert_first(metadata, "updateTime", "updatedAt"),
            80,
        )
        tag = _qweather_alert_text(
            _qweather_alert_first(metadata, "tag") or payload.get("tag"),
            160,
        )
        attributions = _qweather_alert_string_list(
            metadata.get("attributions") or payload.get("attributions"),
            limit=4,
            item_limit=320,
        )
        zero_result = bool(
            metadata.get("zeroResult")
            or payload.get("zeroResult")
            or not alerts
        )
        return {
            "ok": True,
            "alerts": alerts,
            "zero_result": zero_result,
            "provider_update_time": update_time,
            "provider_tag": tag,
            "provider_attributions": attributions,
        }

    @classmethod
    def _normalize_qweather_alert_payload(cls, payload: Any) -> list[dict[str, Any]]:
        """Compatibility shorthand for callers that only need the list."""

        result = cls._parse_qweather_alert_payload(payload)
        return result.get("alerts", []) if result.get("ok") else []

    @classmethod
    def _dedupe_weather_alerts(cls, alerts: Any, *, limit: int = 64) -> list[dict[str, Any]]:
        if not isinstance(alerts, list):
            return []
        by_identity: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for raw in alerts:
            item = raw if isinstance(raw, dict) and "fingerprint" in raw else cls._normalize_weather_alert(raw)
            if not item:
                continue
            identity = str(item.get("id") or item.get("fingerprint") or "").strip()
            if not identity:
                continue
            if identity not in by_identity:
                order.append(identity)
                by_identity[identity] = item
                continue
            previous = by_identity[identity]
            # Prefer the newer revision, then the richer description. This
            # keeps an update from being hidden by a repeated old object.
            previous_issued = str(previous.get("issued_time") or "")
            current_issued = str(item.get("issued_time") or "")
            previous_richness = len(str(previous.get("description") or "")) + len(
                str(previous.get("instruction") or "")
            )
            current_richness = len(str(item.get("description") or "")) + len(
                str(item.get("instruction") or "")
            )
            if current_issued > previous_issued or (
                current_issued == previous_issued and current_richness > previous_richness
            ):
                by_identity[identity] = item
        return [by_identity[key] for key in order[: max(1, int(limit))]]

    @classmethod
    def _filter_weather_alerts(
        cls,
        alerts: Any,
        min_severity: Any = "blue",
    ) -> list[dict[str, Any]]:
        """Filter a normalized list without mutating the full cached list."""

        if not isinstance(alerts, list):
            return []
        threshold_text = _qweather_alert_text(min_severity, 32).strip().lower()
        if threshold_text in {"", "all", "any", "全部", "全部等级"}:
            return [item for item in alerts if isinstance(item, dict)]
        threshold = _qweather_alert_rank(threshold_text)
        result: list[dict[str, Any]] = []
        for item in alerts:
            if not isinstance(item, dict):
                continue
            color = item.get("color_code") or item.get("color") or item.get("level")
            if color:
                # 颜色等级为准（蓝<黄<橙<红）。不能与 severity 取 max——qweather 的
                # severity 是国际档位（minor/moderate/severe/extreme），与国内颜色
                # 错位一档（黄色预警 severity=moderate），max 会让黄色顶穿 orange 阈值
                # （实测 08-14 黄色暴雨/雷电被误发）。颜色缺失（非中文/全球预警源）才
                # 退回 severity。
                rank = _qweather_alert_rank(color)
            else:
                rank = _qweather_alert_rank(item.get("severity"))
            if rank >= threshold:
                result.append(item)
        return result

    @classmethod
    def _weather_alert_identity(cls, alert: Any) -> str:
        if not isinstance(alert, dict):
            return ""
        return str(alert.get("id") or alert.get("fingerprint") or "").strip()

    @classmethod
    def _weather_alert_terminal_identity(cls, alert: Any) -> str:
        """Normalize resolved/cancelled variants to the warning they terminate."""
        if not isinstance(alert, dict):
            return ""
        supersedes = alert.get("supersedes")
        if isinstance(supersedes, list):
            for value in supersedes:
                identity = str(value or "").strip()
                if identity:
                    return identity
        return cls._weather_alert_identity(alert)

    @classmethod
    def _merge_weather_alert_cache(
        cls,
        cached: Any,
        alerts: Any,
        *,
        fetched_ts: float | None = None,
        config_key: str = "",
        provider_update_time: str = "",
        provider_tag: str = "",
        provider_attributions: Any = None,
        zero_result: bool = False,
    ) -> dict[str, Any]:
        """Build a bounded cache and expose changes for a future caller.

        ``new_alert_ids`` and ``resolved_alert_ids`` are metadata only; this
        helper does not send anything and the full ``alerts`` list is retained.
        """

        old_alerts = cached.get("alerts", []) if isinstance(cached, dict) else []
        old_items = cls._dedupe_weather_alerts(old_alerts)
        new_items = cls._dedupe_weather_alerts(alerts)
        old_by_id = {cls._weather_alert_identity(item): item for item in old_items}
        new_by_id = {cls._weather_alert_identity(item): item for item in new_items}
        old_ids = set(old_by_id)
        new_ids = set(new_by_id)
        updated_ids = {
            identity
            for identity in old_ids & new_ids
            if old_by_id[identity].get("fingerprint") != new_by_id[identity].get("fingerprint")
        }
        now = _safe_float(fetched_ts, _now_ts())
        if provider_attributions is None and isinstance(cached, dict):
            provider_attributions = cached.get("attributions")
        normalized_attributions = _qweather_alert_string_list(
            provider_attributions,
            limit=4,
            item_limit=320,
        )
        return {
            "version": 1,
            "source": "qweather",
            "config_key": _qweather_alert_text(config_key, 96),
            "alerts": new_items,
            "fetched_ts": now,
            "last_success_ts": now,
            "last_attempt_ts": now,
            "stale": False,
            "error": "",
            "zero_result": bool(zero_result or not new_items),
            "provider_update_time": _qweather_alert_text(provider_update_time, 80),
            "provider_tag": _qweather_alert_text(provider_tag, 160),
            "attributions": normalized_attributions,
            "new_alert_ids": sorted(identity for identity in new_ids - old_ids if identity),
            "updated_alert_ids": sorted(identity for identity in updated_ids if identity),
            "resolved_alert_ids": sorted(identity for identity in old_ids - new_ids if identity),
        }

    def _weather_alert_config_key(self) -> str:
        configured = self._qweather_configured_location()
        if configured:
            # Hash the configured identity rather than a resolved coordinate
            # so a city change invalidates alerts before the next GeoAPI call.
            location_text = self._qweather_location_identity()
        else:
            location = self._qweather_alert_location()
            if location is None:
                location_text = ""
            else:
                location_text = ",".join(self._qweather_alert_coordinate_text(value) for value in location)
        # Token changes do not alter the data location. Omitting it prevents a
        # credential-derived value from being persisted in the cache key.
        raw = "|".join(
            (
                self._qweather_alert_api_host(),
                location_text,
                self._weather_window_timezone(),
            )
        )
        return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24] if raw else ""

    def _weather_alert_cache_fresh(self, cache: Any, *, now: float | None = None) -> bool:
        if not isinstance(cache, dict) or cache.get("source") != "qweather":
            return False
        if cache.get("config_key") != self._weather_alert_config_key():
            return False
        current = _safe_float(now, _now_ts())
        # Failed requests use a short backoff based on last_attempt_ts; a
        # successful cache uses fetched_ts and remains available while stale.
        stamp = _safe_float(cache.get("last_attempt_ts"), 0)
        if not cache.get("stale"):
            stamp = _safe_float(cache.get("fetched_ts"), stamp)
        refresh_minutes = _safe_int(
            runtime_persona_setting(self, "weather_alert_refresh_minutes", 10),
            10,
            5,
            60,
        )
        return stamp > 0 and current - stamp < refresh_minutes * 60

    async def _fetch_qweather_alerts(self) -> dict[str, Any]:
        """Fetch and parse current alerts from QWeather's API Host route."""

        if not bool(runtime_persona_setting(self, "enable_weather_alerts", False)):
            return {"ok": False, "disabled": True, "alerts": [], "error": "disabled", "source": "qweather"}
        host = self._qweather_alert_api_host()
        token = self._qweather_alert_token()
        resolved = None
        if self._qweather_configured_location():
            resolved = await self._resolve_qweather_location()
        url = self._build_qweather_alert_url(resolved)
        if not host or not token or not url:
            return {
                "ok": False,
                "configured": False,
                "alerts": [],
                "error": "not_configured",
                "source": "qweather",
            }
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    url,
                    headers=self._qweather_alert_headers(),
                    allow_redirects=False,
                ) as response:
                    if response.status == 204:
                        return {
                            "ok": True,
                            "alerts": [],
                            "zero_result": True,
                            "provider_update_time": "",
                            "provider_tag": "",
                            "source": "qweather",
                        }
                    if response.status != 200:
                        logger.debug("和风天气预警请求失败: %s", response.status)
                        return {
                            "ok": False,
                            "alerts": [],
                            "error": f"http_{response.status}",
                            "source": "qweather",
                        }
                    try:
                        payload = await response.json()
                    except TypeError:
                        # Some aiohttp-compatible test doubles and gateways do
                        # not expose a content type; allow the documented JSON.
                        payload = await response.json(content_type=None)
        except asyncio.TimeoutError:
            logger.warning("和风天气预警请求超时")
            return {"ok": False, "alerts": [], "error": "timeout", "source": "qweather"}
        except Exception as exc:
            logger.debug("和风天气预警获取失败: %s", _single_line(exc, 160))
            return {"ok": False, "alerts": [], "error": "request_failed", "source": "qweather"}
        parsed = self._parse_qweather_alert_payload(payload)
        parsed["source"] = "qweather"
        return parsed

    # Alias kept intentionally small so integrations can use the generic name.
    async def _fetch_weather_alerts(self) -> dict[str, Any]:
        return await self._fetch_qweather_alerts()

    async def _ensure_weather_alert_context(self, force: bool = False) -> dict[str, Any]:
        """Refresh the structured alert cache without dispatching messages."""

        if not bool(runtime_persona_setting(self, "enable_weather_alerts", False)):
            return {
                "version": 1,
                "source": "disabled",
                "alerts": [],
                "fetched_ts": 0,
                "stale": False,
                "error": "disabled",
            }
        data = getattr(self, "data", None)
        cached = data.get("weather_alerts", {}) if isinstance(data, dict) else {}
        if not force and self._weather_alert_cache_fresh(cached):
            result = deepcopy(cached)
            result["refreshed"] = False
            return result
        attempt_ts = _now_ts()
        current_config_key = self._weather_alert_config_key()
        fetched = await self._fetch_qweather_alerts()
        if fetched.get("ok"):
            result = self._merge_weather_alert_cache(
                cached,
                fetched.get("alerts", []),
                fetched_ts=attempt_ts,
                config_key=current_config_key,
                provider_update_time=fetched.get("provider_update_time", ""),
                provider_tag=fetched.get("provider_tag", ""),
                provider_attributions=fetched.get("provider_attributions"),
                zero_result=bool(fetched.get("zero_result")),
            )
            result["refreshed"] = True
        else:
            # Keep the last successful alerts during a provider outage. This
            # prevents a transient network error from erasing useful context,
            # but never carries alerts across a location/host change.
            same_config = isinstance(cached, dict) and cached.get("config_key") == current_config_key
            fetch_error = _qweather_alert_text(fetched.get("error"), 80)
            retain_after_failure = fetch_error not in {"not_configured", "disabled"}
            if same_config and cached.get("alerts") and retain_after_failure:
                result = deepcopy(cached)
                result["stale"] = True
                result["last_attempt_ts"] = attempt_ts
                result["error"] = fetch_error or "request_failed"
            else:
                result = {
                    "version": 1,
                    "source": "qweather",
                    "config_key": current_config_key,
                    "alerts": [],
                    "fetched_ts": 0,
                    "last_success_ts": 0,
                    "last_attempt_ts": attempt_ts,
                    "stale": bool(isinstance(cached, dict) and cached.get("alerts")),
                    "error": fetch_error or "request_failed",
                    "zero_result": False,
                    "attributions": [],
                    "new_alert_ids": [],
                    "updated_alert_ids": [],
                    "resolved_alert_ids": [],
                }
            result["refreshed"] = False
        if isinstance(data, dict):
            stored = deepcopy(result)
            stored.pop("refreshed", None)
            data["weather_alerts"] = stored
            saver = getattr(self, "_save_data_sync", None)
            if callable(saver):
                try:
                    saver(sections={"weather_alerts"})
                except Exception as exc:
                    logger.debug("保存天气预警缓存失败: %s", _single_line(exc, 160))
        return result

    def _weather_alert_time_ts(self, value: Any) -> float:
        """Convert a provider time to the plugin's local epoch when possible."""

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                parsed = float(value)
                return parsed if math.isfinite(parsed) and parsed > 0 else 0.0
            except (TypeError, ValueError):
                return 0.0
        text = _qweather_alert_text(value, 96)
        if not text:
            return 0.0
        try:
            parsed = float(text)
            if math.isfinite(parsed) and parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
        normalized = text.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            current = datetime.fromisoformat(normalized)
        except (TypeError, ValueError):
            return 0.0
        if current.tzinfo is None:
            now_getter = getattr(self, "_environment_now", None)
            try:
                zone = now_getter().tzinfo if callable(now_getter) else None
            except Exception:
                zone = None
            current = current.replace(tzinfo=zone or timezone.utc)
        try:
            return float(current.timestamp())
        except (TypeError, ValueError, OSError):
            return 0.0

    def _weather_alert_is_expired(self, alert: Any, *, now: float | None = None) -> bool:
        if not isinstance(alert, dict):
            return True
        expire_ts = self._weather_alert_time_ts(alert.get("expire_time"))
        return expire_ts > 0 and expire_ts <= (_safe_float(now, _now_ts()))

    @staticmethod
    def _weather_alert_is_cancelled(alert: Any) -> bool:
        if not isinstance(alert, dict):
            return False
        if bool(alert.get("is_cancelled")):
            return True
        text = " ".join(
            _single_line(alert.get(key), 60).lower()
            for key in ("message_type", "status", "headline", "event")
        )
        return any(token in text for token in ("cancel", "撤销", "解除", "取消"))

    def _active_weather_alerts(
        self,
        alerts: Any,
        *,
        now: float | None = None,
        include_cancelled: bool = False,
    ) -> list[dict[str, Any]]:
        current = _safe_float(now, _now_ts())
        result: list[dict[str, Any]] = []
        for alert in self._dedupe_weather_alerts(alerts):
            if not isinstance(alert, dict):
                continue
            if not include_cancelled and self._weather_alert_is_cancelled(alert):
                continue
            if not include_cancelled and self._weather_alert_is_expired(alert, now=current):
                continue
            result.append(alert)
        return result

    def _weather_alert_owner_users(self) -> list[tuple[str, dict[str, Any]]]:
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(users, dict):
            return []
        targets: list[tuple[str, dict[str, Any]]] = []
        for raw_user_id, user in users.items():
            user_id = str(raw_user_id or "").strip()
            if not user_id or not isinstance(user, dict) or not user.get("umo"):
                continue
            role_getter = getattr(self, "_private_user_role", None)
            try:
                role = role_getter(user, user_id) if callable(role_getter) else str(user.get("relationship_role") or "")
            except TypeError:
                role = role_getter(user) if callable(role_getter) else str(user.get("relationship_role") or "")
            except Exception:
                role = str(user.get("relationship_role") or "")
            if str(role or "").strip().lower() != "owner":
                continue
            enabled_getter = getattr(self, "_user_enabled_for_proactive", None)
            if callable(enabled_getter):
                try:
                    if not enabled_getter(user_id, user):
                        continue
                except Exception:
                    continue
            targets.append((user_id, user))
        return targets

    @staticmethod
    def _weather_alert_event_key(kind: Any, alert: Any) -> str:
        if not isinstance(alert, dict):
            return ""
        identity = _single_line(alert.get("id") or alert.get("fingerprint"), 180)
        fingerprint = _single_line(alert.get("fingerprint"), 80)
        return ":".join(part for part in (_single_line(kind, 20), identity, fingerprint) if part)

    def _weather_alert_context_for_event(
        self,
        alert: dict[str, Any],
        *,
        kind: str,
        now: float,
    ) -> dict[str, Any]:
        level = _qweather_alert_text(alert.get("color") or alert.get("color_code") or alert.get("severity"), 24)
        event = _qweather_alert_text(alert.get("event") or "天气", 48)
        title = _qweather_alert_text(alert.get("headline") or alert.get("description"), 220)
        instruction = _qweather_alert_text(alert.get("instruction"), 500)
        if kind in {"cancelled", "resolved"}:
            status = "已解除"
        elif kind == "expired" or self._weather_alert_is_expired(alert, now=now):
            status = "已过期或解除"
        elif kind == "updated":
            status = "刚更新"
        else:
            status = "刚发布"
        return {
            "kind": _single_line(kind, 20),
            "status": status,
            "alert": deepcopy(alert),
            "id": _single_line(alert.get("id") or alert.get("fingerprint"), 180),
            "level": level,
            "event": event,
            "title": title,
            "instruction": instruction,
            "captured_at": now,
        }

    def _weather_alert_event_candidates(
        self,
        previous_cache: Any,
        current_cache: dict[str, Any],
        *,
        now: float,
        initialized: bool,
    ) -> list[dict[str, Any]]:
        """Turn a cache transition into bounded, deduplicated pending events."""

        if not initialized or not isinstance(current_cache, dict):
            return []
        old_items = self._dedupe_weather_alerts(
            previous_cache.get("alerts", []) if isinstance(previous_cache, dict) else []
        )
        current_items = self._dedupe_weather_alerts(current_cache.get("alerts", []))
        old_by_id = {self._weather_alert_identity(item): item for item in old_items if self._weather_alert_identity(item)}
        current_by_id = {self._weather_alert_identity(item): item for item in current_items if self._weather_alert_identity(item)}
        new_ids = set(current_cache.get("new_alert_ids") or [])
        updated_ids = set(current_cache.get("updated_alert_ids") or [])
        resolved_ids = set(current_cache.get("resolved_alert_ids") or [])
        events: list[dict[str, Any]] = []
        threshold = runtime_persona_setting(self, "weather_alert_min_severity", "blue")

        # QWeather represents an updated warning as a new object whose
        # ``messageType.supersedes`` points at the previous warning ID.  Keep
        # that transition as one update event instead of emitting a new
        # warning followed by a misleading "old warning resolved" notice.
        superseded_by_current: dict[str, dict[str, Any]] = {}
        for current_item in current_items:
            supersedes = current_item.get("supersedes")
            if not isinstance(supersedes, list):
                continue
            for superseded_id in supersedes:
                identity = str(superseded_id or "").strip()
                if identity and identity in old_by_id:
                    superseded_by_current[identity] = current_item
        handled_current_ids: set[str] = set()

        def is_update(item: dict[str, Any]) -> bool:
            message_type = _qweather_alert_text(item.get("message_type"), 64).lower()
            if any(token in message_type for token in ("update", "amend", "extend", "replace", "续发", "变更")):
                return True
            return bool(item.get("supersedes"))

        def add(
            kind: str,
            item: dict[str, Any],
            *,
            policy_item: dict[str, Any] | None = None,
        ) -> None:
            if not isinstance(item, dict):
                return
            # Cancellation is useful even though it is not an active warning;
            # all other events must pass the configured minimum color/severity.
            if policy_item is None and self._weather_alert_is_cancelled(item):
                supersedes = item.get("supersedes") if isinstance(item.get("supersedes"), list) else []
                policy_item = next(
                    (old_by_id.get(str(value)) for value in supersedes if str(value) in old_by_id),
                    None,
                )
            if not self._filter_weather_alerts([policy_item or item], threshold):
                return
            key = self._weather_alert_event_key(kind, item)
            if not key or any(existing.get("event_key") == key for existing in events):
                return
            context = self._weather_alert_context_for_event(item, kind=kind, now=now)
            context["event_key"] = key
            events.append(context)

        for identity in sorted(new_ids):
            item = current_by_id.get(str(identity))
            if item:
                kind = "cancelled" if self._weather_alert_is_cancelled(item) else ("updated" if is_update(item) else "new")
                add(kind, item)
                handled_current_ids.add(str(identity))
        for identity in sorted(updated_ids):
            item = current_by_id.get(str(identity))
            if item:
                add("cancelled" if self._weather_alert_is_cancelled(item) else "updated", item)
                handled_current_ids.add(str(identity))
        for identity in sorted(resolved_ids):
            item = old_by_id.get(str(identity))
            if not item:
                continue
            if str(identity) in superseded_by_current:
                # The replacement event above carries the current facts and
                # is the only user-facing transition needed.
                continue
            # A provider can remove an item a few seconds before its explicit
            # expiry. Use the old object as the factual basis either way.
            add("expired" if self._weather_alert_is_expired(item, now=now) else "resolved", item)
        # A replacement may explicitly reference an older warning ID even if
        # the provider still returns both objects for one response.
        for item in current_items:
            current_identity = self._weather_alert_identity(item)
            if current_identity in handled_current_ids:
                continue
            supersedes = item.get("supersedes") if isinstance(item.get("supersedes"), list) else []
            if not supersedes:
                continue
            if self._weather_alert_is_cancelled(item):
                add("cancelled", item, policy_item=next(
                    (old_by_id.get(str(value)) for value in supersedes if str(value) in old_by_id),
                    None,
                ))
            elif any(str(value) in old_by_id for value in supersedes):
                add("updated", item)
        rank_getter = lambda value: _qweather_alert_rank(value.get("color_code") or value.get("severity"))
        events.sort(key=lambda value: rank_getter(value.get("alert", {})), reverse=True)
        return events[:12]

    def _weather_alert_event_captured_at(self, event: dict[str, Any]) -> float:
        """Return the best available observation time for a pending alert."""
        if not isinstance(event, dict):
            return 0.0
        captured_at = _safe_float(event.get("captured_at"), 0)
        if captured_at > 0:
            return captured_at
        alert = event.get("alert") if isinstance(event.get("alert"), dict) else {}
        return self._weather_alert_time_ts(
            alert.get("issued_time") or alert.get("effective_time") or alert.get("onset_time")
        )

    def _weather_alert_append_pending_events(self, events: list[dict[str, Any]]) -> None:
        state = self.data.setdefault("weather_alert_awareness", {})
        if not isinstance(state, dict):
            state = {}
            self.data["weather_alert_awareness"] = state
        pending = state.get("pending_events")
        if not isinstance(pending, list):
            pending = []
            state["pending_events"] = pending
        terminal_history = state.get("terminal_event_identities")
        if not isinstance(terminal_history, dict):
            terminal_history = {}
            state["terminal_event_identities"] = terminal_history
        terminal_cutoff = _now_ts() - 7 * 24 * 3600
        for identity, captured_at in list(terminal_history.items()):
            if _safe_float(captured_at, 0) < terminal_cutoff:
                terminal_history.pop(identity, None)
        known = {
            _single_line(item.get("event_key"), 260)
            for item in pending
            if isinstance(item, dict) and _single_line(item.get("event_key"), 260)
        }
        for event in events:
            if not isinstance(event, dict):
                continue
            key = _single_line(event.get("event_key"), 260)
            kind = _single_line(event.get("kind"), 20)
            alert = event.get("alert") if isinstance(event.get("alert"), dict) else {}
            terminal_identity = self._weather_alert_terminal_identity(alert) if kind in {
                "cancelled", "resolved", "expired"
            } else ""
            if terminal_identity and terminal_identity in terminal_history:
                continue
            if key and key not in known:
                pending.append(deepcopy(event))
                known.add(key)
        # A provider can emit both a resolved and an expired representation for
        # the same warning in one refresh. Keep only the first terminal event.
        terminal_seen: set[str] = set()
        compact_pending: list[dict[str, Any]] = []
        for item in pending:
            if not isinstance(item, dict):
                continue
            kind = _single_line(item.get("kind"), 20)
            alert = item.get("alert") if isinstance(item.get("alert"), dict) else {}
            terminal_identity = self._weather_alert_terminal_identity(alert) if kind in {
                "cancelled", "resolved", "expired"
            } else ""
            if terminal_identity:
                if terminal_identity in terminal_seen:
                    continue
                terminal_seen.add(terminal_identity)
            compact_pending.append(item)
        pending[:] = compact_pending
        # Weather changes are time-sensitive. An outage or a disabled daily
        # quota must not turn yesterday's alert into today's proactive message.
        cutoff = _now_ts() - 6 * 3600
        pending[:] = [
            item
            for item in pending
            if isinstance(item, dict)
            and (
                self._weather_alert_event_captured_at(item) <= 0
                or self._weather_alert_event_captured_at(item) >= cutoff
            )
        ]
        pending.sort(
            key=lambda item: _qweather_alert_rank(
                (
                    (item.get("alert") or {}).get("color_code")
                    or (item.get("alert") or {}).get("color")
                    or (item.get("alert") or {}).get("severity")
                )
                if isinstance(item.get("alert"), dict)
                else ""
            ),
            reverse=True,
        )
        del pending[20:]

    def _weather_alert_candidate_delay(self, event: dict[str, Any], *, now: float) -> tuple[float, float]:
        alert = event.get("alert") if isinstance(event.get("alert"), dict) else {}
        rank = _qweather_alert_rank(alert.get("color_code") or alert.get("color") or alert.get("severity"))
        if event.get("kind") in {"cancelled", "resolved", "expired"}:
            return now + random.uniform(1.0, 4.0) * 60.0, 90 * 60.0
        if rank >= 3:
            return now + random.uniform(20.0, 90.0), 30 * 60.0
        if rank >= 2:
            return now + random.uniform(1.0, 5.0) * 60.0, 60 * 60.0
        return now + random.uniform(5.0, 18.0) * 60.0, 3 * 3600.0

    def _queue_weather_alert_pending_events(self, *, now: float) -> int:
        offer = getattr(self, "_offer_proactive_candidate", None)
        if not callable(offer):
            return 0
        if callable(getattr(self, "_proactive_generation_disabled", None)):
            try:
                if self._proactive_generation_disabled():
                    return 0
            except Exception:
                pass
        state = self.data.get("weather_alert_awareness")
        if not isinstance(state, dict):
            return 0
        pending = state.get("pending_events")
        if not isinstance(pending, list) or not pending:
            return 0
        owners = self._weather_alert_owner_users()
        if not owners:
            return 0
        offered = 0
        remaining: list[dict[str, Any]] = []
        owner_ids = {user_id for user_id, _ in owners}
        for event in pending:
            if not isinstance(event, dict):
                continue
            alert = event.get("alert") if isinstance(event.get("alert"), dict) else {}
            captured_at = self._weather_alert_event_captured_at(event)
            if captured_at > 0 and now - captured_at > 6 * 3600:
                continue
            if alert and self._weather_alert_is_expired(alert, now=now) and event.get("kind") not in {"cancelled", "resolved", "expired"}:
                continue
            delivered = event.get("delivered_user_ids")
            if not isinstance(delivered, list):
                delivered = []
                event["delivered_user_ids"] = delivered
            for user_id, user in owners:
                if user_id in delivered:
                    continue
                scheduled, lifetime = self._weather_alert_candidate_delay(event, now=now)
                level = _qweather_alert_text(alert.get("color") or alert.get("color_code") or alert.get("severity"), 24)
                topic = _qweather_alert_text(
                    f"{level}{event.get('event') or '天气'}{event.get('status') or '有变化'}",
                    90,
                )
                alert_event_key = _single_line(event.get("event_key"), 260)
                candidate = {
                    "source": "weather_alert",
                    "reason": "weather_alert",
                    "action": "message",
                    "window_timezone": self._weather_window_timezone(),
                    "scheduled_ts": scheduled,
                    "window_start_at": scheduled,
                    "preferred_ts": scheduled,
                    "best_until_at": scheduled + min(lifetime, 60 * 60),
                    "expire_at": scheduled + lifetime,
                    "topic": topic,
                    "motive": "刚收到一条与当前位置有关的官方气象预警，想把最重要的一点及时告诉主要用户",
                    "score": max(72, min(100, 70 + _qweather_alert_rank(alert.get("color_code") or alert.get("severity")) * 10)),
                    "origin_event_id": (
                        "weather:" + hashlib.sha1(alert_event_key.encode("utf-8", errors="ignore")).hexdigest()[:24]
                        if alert_event_key
                        else ""
                    ),
                    "context_key": "planned_weather_alert_context",
                    "context": deepcopy(event),
                }
                if offer(user_id, user, candidate):
                    delivered.append(user_id)
                    if event.get("kind") in {"cancelled", "resolved", "expired"}:
                        terminal_history = state.setdefault("terminal_event_identities", {})
                        if isinstance(terminal_history, dict):
                            terminal_identity = self._weather_alert_terminal_identity(alert)
                            if terminal_identity:
                                terminal_history[terminal_identity] = now
                    offered += 1
                elif candidate.get("lifecycle_status") in {"skipped", "expired"}:
                    # Consume terminal candidates. Otherwise an old-timezone
                    # terminal alert would be rebuilt on every refresh.
                    delivered.append(user_id)
                    lifecycle_note = _single_line(candidate.get("lifecycle_note"), 180)
                    if lifecycle_note:
                        skip_reasons = event.setdefault("terminal_skip_reasons", {})
                        if isinstance(skip_reasons, dict):
                            skip_reasons[user_id] = lifecycle_note
                    if event.get("kind") in {"cancelled", "resolved", "expired"}:
                        terminal_history = state.setdefault("terminal_event_identities", {})
                        if isinstance(terminal_history, dict):
                            terminal_identity = self._weather_alert_terminal_identity(alert)
                            if terminal_identity:
                                terminal_history[terminal_identity] = now
            if owner_ids and owner_ids.issubset(set(delivered)):
                continue
            remaining.append(event)
        state["pending_events"] = remaining
        return offered

    async def _maybe_refresh_weather_alerts(self, *, force: bool = False) -> dict[str, Any]:
        """Refresh QWeather alerts and enqueue owner-only, deduplicated notices."""

        if not bool(runtime_persona_setting(self, "enable_weather_context", True)) or not bool(runtime_persona_setting(self, "enable_weather_alerts", False)):
            return {}
        lock = getattr(self, "_weather_alert_refresh_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self._weather_alert_refresh_lock = lock
        async with lock:
            now = _now_ts()
            state = self.data.setdefault("weather_alert_awareness", {})
            if not isinstance(state, dict):
                state = {}
                self.data["weather_alert_awareness"] = state
            next_check = _safe_float(state.get("next_check_at"), 0)
            if not force and next_check > now:
                self._queue_weather_alert_pending_events(now=now)
                return deepcopy(self.data.get("weather_alerts", {}))
            interval_minutes = _safe_int(
                runtime_persona_setting(self, "weather_alert_refresh_minutes", 10),
                10,
                5,
                60,
            )
            state["next_check_at"] = now + interval_minutes * 60
            previous_cache = deepcopy(self.data.get("weather_alerts", {}))
            current_config_key = self._weather_alert_config_key()
            previous_config_key = _qweather_alert_text(
                previous_cache.get("config_key") if isinstance(previous_cache, dict) else "",
                96,
            )
            state_config_key = _qweather_alert_text(state.get("config_key"), 96)
            has_previous_alerts = bool(
                isinstance(previous_cache, dict)
                and isinstance(previous_cache.get("alerts"), list)
                and previous_cache.get("alerts")
            )
            config_changed = bool(
                (has_previous_alerts and previous_config_key != current_config_key)
                or (state_config_key and state_config_key != current_config_key)
            )
            if config_changed:
                # A location/host change invalidates both the baseline and
                # undelivered events from the previous place.
                state["initialized"] = False
                state["baseline_ids"] = []
                state["pending_events"] = []
            state["config_key"] = current_config_key
            result = await self._ensure_weather_alert_context(force=True)
            state["last_check_at"] = now
            if result.get("refreshed"):
                if config_changed:
                    state["initialized"] = False
                    state["pending_events"] = []
                initialized = bool(state.get("initialized"))
                if not initialized:
                    state["initialized"] = True
                    state["baseline_ids"] = [
                        self._weather_alert_identity(item)
                        for item in result.get("alerts", [])
                        if self._weather_alert_identity(item)
                    ][:64]
                else:
                    events = self._weather_alert_event_candidates(
                        previous_cache,
                        result,
                        now=now,
                        initialized=True,
                    )
                    self._weather_alert_append_pending_events(events)
                state["last_success_ts"] = _safe_float(result.get("last_success_ts"), now)
                state["last_error"] = ""
            else:
                state["last_error"] = _single_line(result.get("error"), 100)
                # A failed request retries sooner than a normal refresh but is
                # still bounded to avoid a tight loop during an outage.
                state["next_check_at"] = now + max(5 * 60, min(interval_minutes * 60, 30 * 60))
            offered = self._queue_weather_alert_pending_events(now=now)
            state["last_offered_count"] = offered
            saver = getattr(self, "_save_data_sync", None)
            if callable(saver):
                saver(
                    sections={
                        "weather_alert_awareness",
                        "users",
                        "proactive_candidate_pool",
                    }
                )
            return deepcopy(result)

    @classmethod
    def _weather_alerts_summary_text(
        cls,
        alerts: Any,
        *,
        min_severity: Any = "blue",
        max_items: int = 4,
    ) -> str:
        """Render a short, factual summary for a caller's prompt/context."""

        visible = cls._filter_weather_alerts(alerts, min_severity)
        lines: list[str] = []
        for alert in visible[: max(1, int(max_items))]:
            if not isinstance(alert, dict):
                continue
            level = _qweather_alert_text(alert.get("color") or alert.get("color_code") or alert.get("severity"), 24)
            event = _qweather_alert_text(alert.get("event") or "天气", 48)
            headline = _qweather_alert_text(alert.get("headline") or alert.get("description"), 160)
            if headline:
                lines.append(f"{level + ' ' if level else ''}{event}：{headline}")
        return "；".join(lines)

    # ------------------------------------------------------------------
    # QWeather ordinary conditions
    # ------------------------------------------------------------------
    # Ordinary conditions and official alerts intentionally share the same
    # Host/credential pair.  These helpers keep provider details out of the
    # weather cache and preserve the screen_companion fallback on failure.

    def _qweather_weather_api_host(self) -> str:
        return self._normalize_qweather_api_host(
            getattr(self, "weather_api_host", "")
            or getattr(self, "qweather_api_host", "")
            or getattr(self, "weather_alert_api_host", "")
        )

    def _qweather_weather_token(self) -> str:
        raw = (
            getattr(self, "weather_token", "")
            or getattr(self, "qweather_token", "")
            or getattr(self, "weather_alert_token", "")
            or getattr(self, "weather_alert_jwt", "")
            or getattr(self, "weather_alert_api_key", "")
        )
        token = str(raw or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return token

    def _qweather_weather_headers(self) -> dict[str, str]:
        token = self._qweather_weather_token()
        if self._qweather_alert_credential_kind(token) == "api_key":
            return {"X-QW-Api-Key": token, "Accept": "application/json"}
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def _qweather_weather_location(self, resolved: Any = None) -> tuple[float, float] | None:
        """Return (latitude, longitude) for the QWeather coordinate query."""

        snapshot = resolved if isinstance(resolved, dict) else self._qweather_location_snapshot()
        lat = self._qweather_alert_coordinate(snapshot.get("lat"), minimum=-90, maximum=90)
        lon = self._qweather_alert_coordinate(snapshot.get("lon"), minimum=-180, maximum=180)
        if lat is None or lon is None or (lat == 0 and lon == 0):
            return None
        return lat, lon

    def _build_qweather_weather_url(self, resolved: Any = None) -> str:
        host = self._qweather_weather_api_host()
        snapshot = resolved if isinstance(resolved, dict) else self._qweather_location_snapshot()
        location_id = _single_line(snapshot.get("location_id"), 40)
        location = self._qweather_weather_location(snapshot)
        if not host or (not location_id and location is None):
            return ""
        if location_id:
            location_text = location_id
        else:
            latitude, longitude = location
            # QWeather expects longitude first for coordinate locations.
            location_text = ",".join(
                (
                    self._qweather_alert_coordinate_text(longitude),
                    self._qweather_alert_coordinate_text(latitude),
                )
            )
        return host + "/v7/weather/now?" + urlencode(
            {"location": location_text, "lang": "zh", "unit": "m"}
        )

    # Keep a descriptive alias for integrations that use the "now" naming.
    _build_qweather_now_url = _build_qweather_weather_url

    @staticmethod
    def _parse_qweather_weather_payload(payload: Any) -> dict[str, str]:
        if not isinstance(payload, dict) or str(payload.get("code") or "") != "200":
            return {"prompt": "", "source": ""}
        current = payload.get("now")
        if not isinstance(current, dict):
            return {"prompt": "", "source": ""}
        description = _single_line(current.get("text"), 80)
        if not description:
            return {"prompt": "", "source": ""}
        try:
            temperature = float(current.get("temp"))
        except (TypeError, ValueError):
            return {"prompt": "", "source": ""}
        if not math.isfinite(temperature):
            return {"prompt": "", "source": ""}
        details: list[str] = []
        optional_fields = (
            ("feelsLike", "体感", "°C"),
            ("windDir", "", ""),
            ("windScale", "风力", "级"),
            ("humidity", "湿度", "%"),
        )
        for key, label, suffix in optional_fields:
            value = _single_line(current.get(key), 24)
            if not value:
                continue
            if key in {"feelsLike", "humidity"}:
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(numeric):
                    continue
                value = f"{numeric:g}"
            if key == "windDir":
                details.append(value)
            else:
                details.append(f"{label} {value}{suffix}")
        detail_text = "，" + "，".join(details) if details else ""
        return {
            "prompt": f"当前天气 {description}，约 {temperature:g}°C{detail_text}。",
            "source": "qweather",
        }

    async def _fetch_qweather_weather(self) -> dict[str, str]:
        host = self._qweather_weather_api_host()
        token = self._qweather_weather_token()
        resolved = await self._resolve_qweather_location()
        url = self._build_qweather_weather_url(resolved)
        if not host or not token or not url:
            return {"prompt": "", "source": ""}
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    url,
                    headers=self._qweather_weather_headers(),
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        logger.debug("QWeather 实时天气请求失败: %s", response.status)
                        return {"prompt": "", "source": ""}
                    try:
                        payload = await response.json()
                    except TypeError:
                        payload = await response.json(content_type=None)
        except asyncio.TimeoutError:
            logger.warning("QWeather 实时天气请求超时")
            return {"prompt": "", "source": ""}
        except Exception as exc:
            logger.debug("QWeather 实时天气获取失败: %s", _single_line(exc, 160))
            return {"prompt": "", "source": ""}
        parsed = self._parse_qweather_weather_payload(payload)
        if parsed.get("prompt"):
            label = _single_line(resolved.get("label") if isinstance(resolved, dict) else "", 120)
            if label:
                parsed["location_label"] = label
        return parsed

    async def _fetch_openmeteo_weather(self) -> dict[str, str]:
        try:
            lat = float(runtime_persona_setting(self, "weather_lat", 0))
            lon = float(runtime_persona_setting(self, "weather_lon", 0))
        except (TypeError, ValueError):
            return {"prompt": "", "source": ""}
        if (
            not math.isfinite(lat)
            or not math.isfinite(lon)
            or not -90 <= lat <= 90
            or not -180 <= lon <= 180
            or lat == 0 and lon == 0
        ):
            return {"prompt": "", "source": ""}
        params = urlencode(
            {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,weather_code",
            }
        )
        url = f"https://api.open-meteo.com/v1/forecast?{params}"
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.debug(f"Open-Meteo 天气请求失败: {response.status}")
                        return {"prompt": "", "source": ""}
                    weather_data = await response.json()
        except Exception as e:
            logger.debug(f"Open-Meteo 天气获取失败: {e}")
            return {"prompt": "", "source": ""}
        try:
            if not isinstance(weather_data, dict):
                return {"prompt": "", "source": ""}
            current = weather_data.get("current")
            if not isinstance(current, dict):
                return {"prompt": "", "source": ""}
            temperature = float(current["temperature_2m"])
            weather_code = int(current["weather_code"])
            if not math.isfinite(temperature):
                return {"prompt": "", "source": ""}
            description = _openmeteo_weather_description(weather_code)
            return {
                "prompt": f"当前天气 {description},约 {temperature:g}°C。",
                "source": "openmeteo",
            }
        except (AttributeError, KeyError, TypeError, ValueError):
            return {"prompt": "", "source": ""}

    async def _fetch_amap_weather(self) -> dict[str, str]:
        key = str(getattr(self, "weather_amap_api_key", "") or "").strip()
        city = str(runtime_persona_setting(self, "weather_amap_city", "") or "").strip()
        if not key or not city:
            return {"prompt": "", "source": ""}
        url = "https://restapi.amap.com/v3/weather/weatherInfo?" + urlencode(
            {"key": key, "city": city, "extensions": "base", "output": "JSON"}
        )
        try:
            import aiohttp

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.debug(f"高德天气请求失败: {response.status}")
                        return {"prompt": "", "source": ""}
                    weather_data = await response.json()
        except Exception as e:
            logger.debug(f"高德天气获取失败: {e}")
            return {"prompt": "", "source": ""}
        try:
            if not isinstance(weather_data, dict) or str(weather_data.get("status")) != "1":
                return {"prompt": "", "source": ""}
            lives = weather_data.get("lives")
            live = lives[0] if isinstance(lives, list) and lives else None
            if not isinstance(live, dict) or not str(live.get("weather") or "").strip():
                return {"prompt": "", "source": ""}
            temperature = float(live["temperature"])
            if not math.isfinite(temperature):
                return {"prompt": "", "source": ""}
            return {
                "prompt": f"当前天气 {live['weather']}，约 {temperature:g}°C。",
                "source": "amap",
            }
        except (KeyError, TypeError, ValueError):
            return {"prompt": "", "source": ""}

    async def _fetch_own_weather_prompt(self) -> dict[str, str]:
        weather_source = str(runtime_persona_setting(self, "weather_source", "qweather") or "qweather").strip().lower()
        if weather_source == "qweather":
            return await self._fetch_qweather_weather()
        if weather_source == "amap":
            return await self._fetch_amap_weather()
        if weather_source == "openmeteo":
            return await self._fetch_openmeteo_weather()
        if not self.weather_api_key:
            return {"prompt": "", "source": ""}
        url = self._build_weather_url()
        if not url:
            return {"prompt": "", "source": ""}
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.debug(f"天气请求失败: {response.status}")
                        return {"prompt": "", "source": ""}
                    weather_data = await response.json()
        except Exception as e:
            logger.debug(f"私有天气获取失败: {e}")
            return {"prompt": "", "source": ""}
        try:
            weather_desc = weather_data.get("weather", [{}])[0].get("description", "")
            temp = weather_data.get("main", {}).get("temp", 0)
            if weather_desc:
                return {
                    "prompt": f"当前天气 {weather_desc},约 {temp}°C。",
                    "source": "private_companion",
                }
        except Exception:
            pass
        return {"prompt": "", "source": ""}

    def _build_weather_url(self) -> str:
        key = self.weather_api_key
        city = runtime_persona_setting(self, "weather_city", "")
        lat = runtime_persona_setting(self, "weather_lat", 0)
        lon = runtime_persona_setting(self, "weather_lon", 0)
        params = {
            "appid": key,
            "units": "metric",
            "lang": "zh_cn",
        }
        if city:
            params["q"] = city
            return f"https://api.openweathermap.org/data/2.5/weather?{urlencode(params)}"
        if -90 <= lat <= 90 and -180 <= lon <= 180 and lat != 0 and lon != 0:
            params["lat"] = lat
            params["lon"] = lon
            return f"https://api.openweathermap.org/data/2.5/weather?{urlencode(params)}"
        return ""

    def _detect_care_feedback(self, text: str) -> dict[str, Any]:
        normalized = str(text or "").strip()
        if not normalized:
            return {"is_care": False, "tags": []}
        care_actions = r"吃药|喝药|去拿药|按时吃药|喝水|多喝热水|热水|温水|休息|早点睡|快睡|去睡|别熬夜|多睡会|睡一觉|保暖|别着凉|穿厚|加衣服|盖好|难受|还好吗|没事吧|注意身体|照顾好自己|心疼"
        self_report = bool(
            re.search(
                rf"(?:我|俺|本人|这边|我们|咱们|咱).{{0,12}}(?:{care_actions})",
                normalized,
            )
        )
        if self_report:
            return {"is_care": False, "tags": []}
        tags: list[str] = []
        if re.search(r"吃药|喝药|去拿药|按时吃药", normalized):
            tags.append("medicine")
        if re.search(r"喝水|多喝热水|热水|温水", normalized):
            tags.append("water")
        if re.search(r"休息|早点睡|快睡|去睡|别熬夜|多睡会|睡一觉", normalized):
            tags.append("rest")
        if re.search(r"保暖|别着凉|穿厚|加衣服|盖好", normalized):
            tags.append("warm")
        if re.search(r"难受|还好吗|没事吧|注意身体|照顾好自己|心疼", normalized):
            tags.append("concern")
        tags = list(dict.fromkeys(tags))
        return {"is_care": bool(tags), "tags": tags}

    def _apply_care_feedback_to_state(self, text: str) -> bool:
        feedback = self._detect_care_feedback(text)
        if not feedback.get("is_care"):
            return False
        tags = feedback.get("tags", [])
        changed = False
        now = _now_ts()
        conditions = self.data.setdefault("state_conditions", [])
        if not isinstance(conditions, list):
            self.data["state_conditions"] = []
            conditions = self.data["state_conditions"]
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            if str(cond.get("kind") or "") != "health":
                continue
            if _safe_float(cond.get("end_ts"), 0) <= now:
                continue
            remaining = max(0, _safe_float(cond.get("end_ts"), now) - now)
            shorten_ratio = 0.0
            if "medicine" in tags:
                shorten_ratio += 0.35
            if "rest" in tags:
                shorten_ratio += 0.2
            if "water" in tags:
                shorten_ratio += 0.12
            if "warm" in tags:
                shorten_ratio += 0.12
            if shorten_ratio > 0:
                cond["end_ts"] = now + remaining * max(0.35, 1 - min(shorten_ratio, 0.55))
                cond["duration_hours"] = max(
                    1,
                    int((cond["end_ts"] - _safe_float(cond.get("start_ts"), now)) / 3600),
                )
                cond["energy_delta"] = min(-2, int(_safe_int(cond.get("energy_delta"), -8) * 0.75))
                cond["label"] = "收到照顾提醒后,不适强度略有下降"
                changed = True
            notes = cond.setdefault("care_notes", [])
            if isinstance(notes, list):
                care_note = "用户提供了关心反馈"
                if "medicine" in tags:
                    care_note = "用户提醒用药"
                elif "rest" in tags:
                    care_note = "用户提醒休息"
                elif "water" in tags:
                    care_note = "用户提醒补水"
                if care_note not in notes:
                    notes.append(care_note)
            cond["cause"] = _single_line(
                f"{_single_line(cond.get('cause'), 80)}；用户提供了照顾提醒".strip("；"),
                120,
            )
        if changed and random.random() < 0.72:
            conditions.append(
                self._make_condition(
                    kind="care_warmth",
                    title="被关心后的回暖",
                    label="收到用户关心后的轻度回暖",
                    mood="柔和",
                    energy_delta=6,
                    duration_hours=6,
                    intensity=72,
                    cause="用户提供了关心反馈",
                    phase="care_feedback",
                    transition_options=self._build_transition_options(
                        kind="care_warmth",
                        energy_delta=6,
                        cause="用户提供了关心反馈",
                        on_end_transition="",
                    ),
                )
            )
        return changed

    def _detect_interaction_warmth_feedback(self, text: str, user: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = _single_line(text, 220)
        if not normalized:
            return {"is_warmth": False}
        intimate = bool(re.search(r"摸摸|贴贴|抱抱|亲亲|揉揉|蹭蹭|摸头|抱一下|贴一下|rua", normalized, re.IGNORECASE))
        comfort = bool(re.search(r"陪你|哄你|乖|不难过|别难过|没关系|辛苦了|抱一下|摸摸头", normalized))
        positive = bool(re.search(r"开心|好耶|哈哈|笑死|可爱|喜欢|太好了|真好|想你|爱你|在呢|来了|陪我", normalized, re.IGNORECASE))
        if not (intimate or comfort or positive):
            return {"is_warmth": False}

        relationship_score = _safe_int(user.get("relationship_score") if isinstance(user, dict) else 0, 0, 0)
        episode_count = _safe_int(user.get("episode_message_count") if isinstance(user, dict) else 0, 0, 0)
        state = self._compose_state_from_conditions(self.data.get("daily_weather", {}))
        energy = _safe_int(state.get("energy"), 75, 0, 100)

        is_sustained_positive = positive and episode_count >= 6 and relationship_score >= 18
        if positive and not (intimate or comfort or is_sustained_positive):
            return {"is_warmth": False}

        base_delta = 2 if intimate or comfort else 1
        if energy <= 45:
            base_delta += 2
        elif energy <= 62:
            base_delta += 1
        elif energy >= 86:
            base_delta = max(1, base_delta - 1)
        if relationship_score >= 120:
            base_delta += 2
        elif relationship_score >= 55:
            base_delta += 1
        if is_sustained_positive and episode_count >= 10:
            base_delta += 1

        max_delta = 8 if intimate or comfort else 4
        delta = max(1, min(max_delta, base_delta))
        if intimate:
            source = "亲密互动回暖"
            label = "被亲近安抚后,精神轻轻回暖"
            mood = "柔和"
            duration_hours = 4
            intensity = 58
            phase = "intimacy"
        elif comfort:
            source = "安慰互动回暖"
            label = "被安慰后,紧绷感松开一点"
            mood = "柔和"
            duration_hours = 4
            intensity = 54
            phase = "comfort"
        else:
            source = "连续对话回暖"
            label = "和熟悉的人连续聊了一会儿,精神被带起来一点"
            mood = "轻快"
            duration_hours = 3
            intensity = 42
            phase = "sustained_positive_chat"
        return {
            "is_warmth": True,
            "source": source,
            "label": label,
            "mood": mood,
            "energy_delta": delta,
            "duration_hours": duration_hours,
            "intensity": intensity,
            "phase": phase,
            "cause": _single_line(normalized, 80),
            "max_delta": max_delta,
        }

    def _apply_interaction_warmth_to_state(self, text: str, user: dict[str, Any] | None = None) -> bool:
        feedback = self._detect_interaction_warmth_feedback(text, user)
        if not feedback.get("is_warmth"):
            return False
        now = _now_ts()
        conditions = self.data.setdefault("state_conditions", [])
        if not isinstance(conditions, list):
            self.data["state_conditions"] = []
            conditions = self.data["state_conditions"]
        max_delta = _safe_int(feedback.get("max_delta"), 6, 1, 10)
        active = next(
            (
                cond for cond in reversed(conditions)
                if isinstance(cond, dict)
                and str(cond.get("kind") or "") == "interaction_warmth"
                and _safe_float(cond.get("end_ts"), 0) > now
            ),
            None,
        )
        if isinstance(active, dict):
            current_delta = _safe_int(active.get("energy_delta"), 0, 0, 20)
            incoming_delta = _safe_int(feedback.get("energy_delta"), 1, 1, 10)
            active["energy_delta"] = min(max_delta, max(current_delta, incoming_delta) + 1)
            active["end_ts"] = max(
                _safe_float(active.get("end_ts"), now),
                now + _safe_int(feedback.get("duration_hours"), 3, 1, 8) * 3600,
            )
            active["duration_hours"] = max(1, int((_safe_float(active.get("end_ts"), now) - now) / 3600))
            active["label"] = _single_line(feedback.get("label"), 80)
            active["mood"] = _single_line(feedback.get("mood"), 20) or active.get("mood") or "柔和"
            active["cause"] = _single_line(feedback.get("cause"), 80)
            active["phase"] = _single_line(feedback.get("phase"), 40)
            active["intensity"] = max(_safe_int(active.get("intensity"), 40), _safe_int(feedback.get("intensity"), 40))
        else:
            conditions.append(
                self._make_condition(
                    kind="interaction_warmth",
                    title=_single_line(feedback.get("source"), 40) or "互动回暖",
                    label=_single_line(feedback.get("label"), 80),
                    mood=_single_line(feedback.get("mood"), 20) or "柔和",
                    energy_delta=_safe_int(feedback.get("energy_delta"), 2, 1, 10),
                    duration_hours=_safe_int(feedback.get("duration_hours"), 3, 1, 8),
                    intensity=_safe_int(feedback.get("intensity"), 45, 0, 100),
                    cause=_single_line(feedback.get("cause"), 80),
                    phase=_single_line(feedback.get("phase"), 40),
                )
            )
        self.data["daily_state"] = self._compose_state_from_conditions(self.data.get("daily_weather", {}))
        return True

    def _detect_food_feedback(self, text: str) -> dict[str, Any]:
        normalized = _single_line(text, 220)
        if not normalized:
            return {"is_food": False}
        food_markers = (
            "吃饭", "吃点", "吃些", "吃个", "吃什么", "吃啥", "晚饭", "晚餐", "午饭", "午餐",
            "早饭", "早餐", "夜宵", "外卖", "点餐", "做饭", "煮", "炒", "饭", "面", "粥",
            "汤", "菜", "肉", "蛋", "奶茶", "甜品", "水果", "火锅", "烧烤", "便当", "饺子",
            "馄饨", "米粉", "汉堡", "披萨", "三明治", "咖啡", "零食", "吃了", "吃过",
            "吃完", "吃饱", "饱了", "没吃", "还没吃", "饿", "嘴馋", "投喂", "喂你", "喂给你", "请你吃"
        )
        if not any(marker in normalized for marker in food_markers):
            return {"is_food": False}
        already_ate = bool(
            re.search(r"(我|俺|本人|这边|我们|咱们|咱).{0,10}(吃了|吃过|吃完|吃饱|饱了|喝了|喝过|喝完)", normalized)
            or re.search(r"^(吃了|吃过了|吃完了|吃饱了|饱了|喝完了)$", normalized)
        )
        food_nouns = r"(饭|菜|粥|汤|面|粉|饺子|馄饨|便当|外卖|夜宵|早餐|早饭|午餐|午饭|晚餐|晚饭|奶茶|咖啡|水果|零食|甜品|汉堡|披萨|三明治|火锅|烧烤|蛋|肉|吃的|喝的)"
        bot_subject = r"(你|bot|机器人|助手|ai|AI|宝宝|宝贝)"
        feeding = bool(
            re.search(r"(投喂|喂你|喂给你|给你投喂)", normalized, re.IGNORECASE)
            or re.search(fr"(给你|送你|递你|分你|留给你|请你|带你|陪你).{{0,12}}(吃|喝|点|买|做|煮|留|带|拿|叫|尝|来).{{0,12}}{food_nouns}?", normalized, re.IGNORECASE)
            or re.search(fr"(这个|这份|这杯|这碗|这口|这些).{{0,8}}(给你|分你|留给你).{{0,8}}(吃|喝|尝)?", normalized, re.IGNORECASE)
        )
        bot_food_question = bool(
            re.search(fr"{bot_subject}.{{0,10}}(想|要|打算|准备|喜欢|爱不爱|能不能|可以不可以)?.{{0,8}}(吃|喝|点).{{0,8}}(什么|啥|吗|嘛|么|哪[个家种些]?)", normalized, re.IGNORECASE)
            or re.search(fr"{bot_subject}.{{0,8}}(饿了吗|饿不饿|吃饭了吗|吃了没|吃没吃|吃过了吗|想吃吗|要吃吗|喝吗)", normalized, re.IGNORECASE)
            or re.search(fr"{bot_subject}.{{0,10}}(要不要|想不想|吃不吃|喝不喝|点不点|饿不饿).{{0,10}}(吃|喝|点|饭|外卖|夜宵|奶茶|咖啡)?", normalized, re.IGNORECASE)
        )
        bot_directed = (not bot_food_question) and bool(
            re.search(fr"{bot_subject}.{{0,12}}(先|去|也|就|可以|要不|不如|还是|记得|别忘了|快|赶紧)?.{{0,12}}(吃|喝|点|煮|买|做|叫|尝)", normalized, re.IGNORECASE)
            or re.search(fr"(推荐|建议).{{0,8}}{bot_subject}.{{0,12}}(吃|喝|点|煮|买|做|叫|尝)", normalized, re.IGNORECASE)
            or re.search(fr"(吃|喝|点|煮|买|做|叫|尝).{{0,10}}(给|给点|给买|给做).{{0,4}}{bot_subject}", normalized, re.IGNORECASE)
        )
        user_self_intent = bool(
            re.search(r"(我|俺|本人|这边|我们|咱们|咱).{0,14}(去|先|准备|要|想|打算|正在|刚|已经)?.{0,14}(吃|喝|点|买|做|煮|叫)", normalized)
            or re.search(r"(给我|帮我|我该|我要|我想|我能|我可以).{0,12}(吃|喝|点|买|做|煮|叫|推荐)", normalized)
        )
        user_menu_query = bool(
            re.search(r"(吃什么|吃啥|点什么|点啥|推荐).{0,10}(我|给我|一下)?", normalized)
            and re.search(r"(我|给我|帮我|吃什么|吃啥|点什么|点啥)", normalized)
        )
        implicit_bot_suggestion = bool(
            not already_ate
            and not bot_food_question
            and not user_self_intent
            and not user_menu_query
            and (
                re.search(r"(先|去|快|赶紧|记得|别忘了).{0,10}(吃|喝|点|买|做|煮|叫)", normalized)
                or re.search(r"(吃点|吃些|喝点|喝些).{0,8}(吧|呀|哦|噢)?$", normalized)
                or re.search(fr"(要不|不如|可以|试试).{{0,12}}(吃|喝|点|买|做|煮|叫).{{0,12}}{food_nouns}?", normalized)
            )
        )
        suggestion = bool(feeding or bot_directed or implicit_bot_suggestion)
        meal = ""
        for token, label in (("早餐", "早餐"), ("早饭", "早餐"), ("午餐", "午餐"), ("午饭", "午餐"), ("晚餐", "晚餐"), ("晚饭", "晚餐"), ("夜宵", "夜宵")):
            if token in normalized:
                meal = label
                break
        if not meal:
            hour = self._environment_now().hour
            if 10 <= hour < 15:
                meal = "午餐"
            elif 15 <= hour < 21:
                meal = "晚餐"
            elif hour >= 21 or hour < 3:
                meal = "夜宵"
            else:
                meal = "加餐"
        return {
            "is_food": True,
            "suggestion": suggestion,
            "actionable": suggestion,
            "already_ate": already_ate,
            "user_ate": already_ate,
            "feeding": feeding,
            "bot_directed": bot_directed,
            "bot_food_question": bot_food_question,
            "implicit_bot_suggestion": implicit_bot_suggestion,
            "meal": meal,
            "food_hint": _single_line(normalized, 80),
        }

    def _apply_food_feedback_to_state(self, text: str) -> bool:
        feedback = self._detect_food_feedback(text)
        if not feedback.get("is_food") or not feedback.get("actionable"):
            return False
        now = _now_ts()
        self.data["last_food_state_feedback_at"] = now
        self.data["last_food_state_feedback_text"] = _single_line(feedback.get("food_hint"), 120)
        changed = False
        conditions = self.data.setdefault("state_conditions", [])
        if not isinstance(conditions, list):
            self.data["state_conditions"] = []
            conditions = self.data["state_conditions"]
        for cond in conditions:
            if not isinstance(cond, dict) or str(cond.get("kind") or "") != "hunger":
                continue
            if _safe_float(cond.get("end_ts"), 0) <= now:
                continue
            remaining = max(0.0, _safe_float(cond.get("end_ts"), now) - now)
            if feedback.get("feeding"):
                target_remaining = max(5 * 60, min(remaining * 0.15, 12 * 60))
                label = "收到用户投喂后,饥饿感很快回落"
                cause = "用户投喂或分享吃的"
                energy_ratio = 0.25
            elif feedback.get("bot_directed"):
                target_remaining = max(8 * 60, min(remaining * 0.2, 20 * 60))
                label = "被提醒先吃点东西后,饥饿感开始回落"
                cause = "用户提醒去吃东西"
                energy_ratio = 0.35
            else:
                target_remaining = max(12 * 60, min(remaining * 0.3, 35 * 60))
                label = "有了吃什么的方向,饥饿感开始回落"
                cause = "用户给了饮食建议"
                energy_ratio = 0.45
            cond["end_ts"] = now + min(remaining, target_remaining)
            cond["duration_hours"] = max(1, int((cond["end_ts"] - _safe_float(cond.get("start_ts"), now)) / 3600))
            cond["mood"] = "回稳"
            cond["label"] = _single_line(label, 80)
            cond["cause"] = cause
            cond["phase"] = "food_feedback_resolving"
            current_delta = _safe_int(cond.get("energy_delta"), 0, -100, 100)
            if current_delta < 0:
                cond["energy_delta"] = min(0, int(current_delta * energy_ratio))
            changed = True
        if changed:
            conditions.append(
                self._make_condition(
                    kind="care_warmth",
                    title="饮食照顾回暖",
                    label="收到用户的投喂或吃饭提醒后,状态轻轻回稳",
                    mood="柔和",
                    energy_delta=4 if feedback.get("feeding") else 3,
                    duration_hours=2,
                    intensity=55,
                    cause=_single_line(feedback.get("food_hint"), 80),
                    phase="food_feedback",
                    transition_options=self._build_transition_options(
                        kind="care_warmth",
                        energy_delta=4 if feedback.get("feeding") else 3,
                        cause=_single_line(feedback.get("food_hint"), 80),
                        on_end_transition="",
                    ),
                )
            )
            self.data["daily_state"] = self._compose_state_from_conditions(self.data.get("daily_weather", {}))
        return changed

    def _meal_care_active_context(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        if not isinstance(user, dict):
            return {}
        context = user.get("meal_check_context")
        if not isinstance(context, dict) or not context.get("active"):
            return {}
        check_now = _now_ts() if now is None else now
        if str(context.get("date") or "") != _today_key() or (
            _safe_float(context.get("expires_at"), 0) > 0
            and check_now > _safe_float(context.get("expires_at"), 0)
        ):
            user["meal_check_context"] = {}
            return {}
        return context

    @staticmethod
    def _meal_reply_is_not_eaten(text: str) -> bool:
        compact = re.sub(r"\s+", "", _single_line(text, 160))
        return bool(
            re.search(r"(?:还|一直|今天|刚刚|我)?没(?:有)?(?:吃|吃饭|吃上|来得及吃)|没呢|还没呢|没来得及|不准备吃|不想吃", compact)
        )

    @staticmethod
    def _meal_reply_is_non_food_consumption(text: str) -> bool:
        compact = re.sub(r"\s+", "", _single_line(text, 160))
        if not compact:
            return False
        prefix = r"(?:我|俺|咱|本人|今天|刚刚|刚才|已经|又|还|这次|这回)*"
        suffix = r"(?:了|啦|呢|呀|啊|哦|噢|哈)?"
        expression = (
            r"(?:吃(?:了|过|到)?(?:个|一(?:个|点|些))?"
            r"(?:亏|大亏|哑巴亏|苦头|闭门羹|官司|教训|排头|败仗|处分|罚单|巴掌|拳头|耳光|一惊|一吓|瘪|土|枪药))"
            r"|(?:(?:吃|服|喝)(?:了|过|完)?(?:点|些|一(?:片|粒|颗|包|支|瓶))?"
            r"(?:感冒药|退烧药|止痛药|消炎药|安眠药|胃药|中药|西药|处方药|降压药|抗生素|药片|药|胶囊|维生素|保健品|补剂))"
        )
        return bool(re.fullmatch(prefix + r"(?:才|就|可算)?" + expression + suffix, compact))

    @staticmethod
    def _meal_reply_confirms_eaten(text: str) -> bool:
        compact = re.sub(r"\s+", "", _single_line(text, 160))
        if (
            not compact
            or DailyStateMixin._meal_reply_is_not_eaten(compact)
            or DailyStateMixin._meal_reply_is_non_food_consumption(compact)
        ):
            return False
        return bool(
            re.search(r"(?:我|俺|咱|已经|刚刚|刚|早就|这边)?(?:吃了|吃过|吃完|吃饱|吃上了|用过餐|喝了|喝过)", compact)
            or re.search(r"(?:我|俺|咱)?(?:正在吃|在吃|开吃了)", compact)
            or compact in {"吃了", "吃过了", "吃完了", "吃饱了", "饱了", "刚吃", "刚吃完"}
        )

    def _meal_reply_food_items(self, text: str, *, active_context: bool = False) -> list[str]:
        normalized = _single_line(text, 220)
        if (
            not normalized
            or self._meal_reply_is_not_eaten(normalized)
            or self._meal_reply_is_non_food_consumption(normalized)
        ):
            return []
        explicit_self = bool(
            re.search(r"(?:我|俺|咱|本人|今天|刚刚|刚才|早上|中午|晚上|早餐|早饭|午饭|午餐|晚饭|晚餐).{0,12}(?:吃了|吃的是|吃的|吃过|正在吃|在吃|点了|做了|喝了)", normalized)
            or re.search(r"^(?:吃了|吃的是|吃的|正在吃|在吃|点了|喝了)", normalized)
        )
        if not active_context and not explicit_self:
            return []
        existing_hits: list[str] = []
        for item in self._food_menu_items():
            terms = [item.get("name"), *item.get("aliases", [])]
            if any(term and str(term) in normalized for term in terms):
                name = _single_line(item.get("name"), 40)
                if name and name not in existing_hits:
                    existing_hits.append(name)
        capture_patterns = (
            r"(?:早餐|早饭|午饭|午餐|晚饭|晚餐|夜宵)?(?:我|俺|咱|本人)?(?:刚刚|刚才|已经|就)?(?:吃了|吃的是|吃的|吃过|正在吃|在吃|点了|做了|喝了)\s*([^。！？!?]+)",
            r"(?:早餐|早饭|午饭|午餐|晚饭|晚餐|夜宵)\s*(?:是|有|吃)?\s*([^。！？!?]+)",
        )
        raw_candidate = ""
        for pattern in capture_patterns:
            match = re.search(pattern, normalized)
            if match:
                raw_candidate = _single_line(match.group(1), 80)
                break
        bare_context_reply = False
        if not raw_candidate and active_context and len(normalized) <= 28:
            raw_candidate = normalized
            bare_context_reply = True
        raw_candidate = re.split(r"[。！？!?；;]|(?:，|,)(?:不过|但是|然后|感觉|味道|还行|挺|有点)", raw_candidate, maxsplit=1)[0]
        raw_candidate = re.sub(r"^(?:我|俺|咱|今天|刚刚|刚才|已经|就是|吃了|吃的是|吃的|正在吃|在吃|点了|喝了)+", "", raw_candidate).strip()
        raw_candidate = re.sub(r"(?:了|啦|呢|呀|啊|哦|噢|哈|来着)$", "", raw_candidate).strip(" ，,、")
        generic = {
            "", "饭", "东西", "吃的", "喝的", "一点", "一些", "一口", "随便", "不知道", "忘了",
            "还行", "挺好", "吃完", "吃饱", "饱", "完", "过", "是", "有", "没", "没有",
        }
        if bare_context_reply and not explicit_self and not existing_hits:
            food_like_markers = (
                "饭", "面", "粉", "粥", "汤", "饺", "馄饨", "包", "馒头", "饼", "肉", "鸡", "鸭", "鱼", "虾", "蟹",
                "蛋", "菜", "瓜", "豆", "笋", "菇", "火锅", "烧烤", "麻辣烫", "冒菜", "砂锅", "便当", "外卖", "汉堡",
                "披萨", "三明治", "牛排", "食堂", "餐厅", "店", "馆", "奶", "茶", "咖啡", "果", "甜品", "蛋糕", "酸奶",
                "血旺", "螺蛳", "米线", "盖浇", "煲仔", "咖喱", "炸", "烤", "炒", "蒸", "煮",
            )
            if not any(marker in raw_candidate for marker in food_like_markers):
                raw_candidate = ""
        learned: list[str] = list(existing_hits)
        for part in re.split(r"[、，,/+]|还有|以及|配了|配着", raw_candidate):
            item = _single_line(part, 30).strip(" 的")
            if (
                item in generic
                or len(item) < 2
                or len(item) > 24
                or re.search(r"(?:什么|啥|吗|嘛|怎么|为啥|你呢|你吃|不告诉|不记得)", item)
                or re.fullmatch(
                    r"(?:个|一|一点|一些|不少)?(?:大|小|哑巴|闷)?"
                    r"(?:亏|苦头|官司|闭门羹|败仗|教训|处分|罚单|巴掌|拳头|耳光|一惊)",
                    item,
                )
                or re.fullmatch(
                    r"(?:感冒药|退烧药|止痛药|消炎药|安眠药|胃药|中药|西药|处方药|降压药|抗生素|药片|药|胶囊|维生素|保健品|补剂)",
                    item,
                )
            ):
                continue
            if item not in learned:
                learned.append(item)
        return learned[:5]

    @staticmethod
    def _meal_food_inferred_fields(name: str) -> dict[str, Any]:
        text = _single_line(name, 40)
        item_type = "drink_snack" if any(token in text for token in ("奶茶", "咖啡", "甜品", "蛋糕", "水果", "零食", "饮料", "酸奶")) else "dish"
        category_rules = (
            ("面食", ("面", "粉", "馄饨", "饺子", "抄手", "米线")),
            ("米饭", ("饭", "便当", "煲仔", "咖喱", "盖浇")),
            ("快餐", ("汉堡", "炸鸡", "披萨", "麦当劳", "肯德基")),
            ("甜口", ("奶茶", "甜品", "蛋糕", "水果", "酸奶")),
            ("热锅", ("火锅", "麻辣烫", "冒菜", "砂锅", "关东煮")),
        )
        category = next((label for label, tokens in category_rules if any(token in text for token in tokens)), "")
        tags: list[str] = []
        for tag, tokens in (
            ("热乎", ("面", "粉", "粥", "汤", "火锅", "砂锅")),
            ("快", ("便当", "汉堡", "炸鸡", "外卖")),
            ("清淡", ("粥", "汤", "沙拉", "蒸")),
            ("辣", ("辣", "火锅", "冒菜", "麻辣烫")),
            ("甜", ("奶茶", "甜品", "蛋糕", "水果", "酸奶")),
            ("顶饱", ("饭", "面", "粉", "汉堡", "便当")),
        ):
            if any(token in text for token in tokens):
                tags.append(tag)
        return {"type": item_type, "category": category, "tags": tags}

    def _learn_food_menu_from_meal_reply(self, foods: list[str], *, meal_key: str, now: float) -> list[str]:
        if not foods or not bool(runtime_persona_setting(self, "enable_food_menu_recommendation", True)):
            return []
        state = self.data.setdefault("food_menu", {})
        if not isinstance(state, dict):
            state = {}
            self.data["food_menu"] = state
        items = state.setdefault("items", [])
        if not isinstance(items, list):
            items = []
            state["items"] = items
        learned: list[str] = []
        for raw_name in foods[:5]:
            name = _single_line(raw_name, 40)
            if not name:
                continue
            existing = next(
                (
                    item for item in items
                    if isinstance(item, dict)
                    and (
                        _single_line(item.get("name"), 40) == name
                        or name in self._food_menu_list(item.get("aliases"), limit=12, item_limit=24)
                    )
                ),
                None,
            )
            if isinstance(existing, dict):
                existing["use_count"] = _safe_int(existing.get("use_count"), 0, 0) + 1
                existing["last_used_at"] = now
                existing["updated_ts"] = now
                times = self._food_menu_list(existing.get("times"), limit=5, item_limit=16)
                if meal_key and meal_key not in times:
                    times.append(meal_key)
                existing["times"] = times[:5]
            else:
                inferred = self._meal_food_inferred_fields(name)
                items.append(
                    {
                        "id": f"food-auto-{uuid.uuid4().hex[:12]}",
                        "name": name,
                        "type": inferred["type"],
                        "category": inferred["category"],
                        "tags": inferred["tags"],
                        "times": [meal_key] if meal_key else [],
                        "avoid": [],
                        "aliases": [],
                        "note": "从用户实际吃过的内容自动回填",
                        "favorite": False,
                        "hidden": False,
                        "use_count": 1,
                        "last_used_at": now,
                        "created_ts": now,
                        "updated_ts": now,
                        "source": "meal_care_reply",
                    }
                )
            learned.append(name)
        if learned:
            state["updated_ts"] = now
            state["last_auto_learned_at"] = now
            state["last_auto_learned_items"] = learned
        return learned

    def _cancel_planned_meal_care_followup(self, user: dict[str, Any], *, note: str = "") -> bool:
        if not isinstance(user, dict):
            return False
        changed = False
        pending = user.get("pending_followup_event")
        if isinstance(pending, dict) and pending.get("_meal_care_followup"):
            user["pending_followup_event"] = {}
            changed = True
        impulses = user.get("proactive_impulses")
        if isinstance(impulses, list):
            kept = [
                item for item in impulses
                if not (
                    isinstance(item, dict)
                    and _single_line(item.get("reason"), 40) == "meal_care_followup"
                    and str(item.get("state") or "queued") in {"queued", "deferred"}
                )
            ]
            if len(kept) != len(impulses):
                user["proactive_impulses"] = kept
                changed = True
        if self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) == "meal_care_followup":
            marker = getattr(self, "_mark_planned_candidate_status", None)
            if callable(marker):
                marker(user, "cancelled", _single_line(note, 120) or "用户已回应饭点关心")
            clearer = getattr(self, "_clear_pending_proactive_plan", None)
            if callable(clearer):
                clearer(user)
            else:
                user["next_proactive_at"] = 0
                user["planned_proactive_reason"] = ""
            changed = True
        return changed

    def _handle_meal_care_inbound(self, user: dict[str, Any], text: str, *, now: float | None = None) -> dict[str, Any]:
        check_now = _now_ts() if now is None else now
        normalized = _single_line(text, 220)
        if not isinstance(user, dict) or not normalized:
            return {"kind": "none"}
        context = self._meal_care_active_context(user, now=check_now)
        meal_key = _single_line(context.get("meal_key"), 20) or self._meal_key_from_text(normalized) or self._current_food_time_key()
        meal_label = _single_line(context.get("meal_label"), 12) or self._food_menu_time_label(meal_key) or "这顿饭"
        followup_already_sent = _safe_int(context.get("followup_count"), 0, 0, 1) >= 1 if context else False
        foods = self._meal_reply_food_items(normalized, active_context=bool(context))
        self._learn_food_menu_from_meal_reply(foods, meal_key=meal_key, now=check_now)
        if not context:
            # A spontaneous, current-day meal report (for example
            # "我午饭吃了咖喱鸡饭") also resolves that meal slot.  Previously it
            # only populated the menu, so the scheduler could ask the same meal
            # question again later.
            historical_report = bool(
                re.search(r"(?:昨天|昨晚|前天|大前天|上次|那天|之前|以前|前几天)", normalized)
            )
            if foods and not historical_report and meal_key in {"breakfast", "lunch", "dinner"}:
                today = _today_key()
                if str(user.get("meal_care_day") or "") != today:
                    user["meal_care_day"] = today
                    user["meal_care_asked"] = []
                    user["meal_care_satisfied"] = []
                satisfied = user.setdefault("meal_care_satisfied", [])
                if not isinstance(satisfied, list):
                    satisfied = []
                    user["meal_care_satisfied"] = satisfied
                if meal_key not in satisfied:
                    satisfied.append(meal_key)

                planned_reason = self._normalize_legacy_proactive_text(
                    user.get("planned_proactive_reason"),
                    limit=40,
                )
                planned_context = (
                    user.get("planned_meal_care_context")
                    if isinstance(user.get("planned_meal_care_context"), dict)
                    else {}
                )
                planned_meal_key = _single_line(planned_context.get("meal_key"), 20)
                if planned_reason == "meal_care" and (not planned_meal_key or planned_meal_key == meal_key):
                    marker = getattr(self, "_mark_planned_candidate_status", None)
                    if callable(marker):
                        marker(user, "cancelled", "用户已主动说明这顿饭吃了什么")
                    clearer = getattr(self, "_clear_pending_proactive_plan", None)
                    if callable(clearer):
                        clearer(user)
                    user["planned_meal_care_context"] = {}
                impulses = user.get("proactive_impulses")
                if isinstance(impulses, list):
                    kept_impulses = []
                    for impulse in impulses:
                        if not isinstance(impulse, dict):
                            kept_impulses.append(impulse)
                            continue
                        impulse_reason = _single_line(impulse.get("reason"), 40)
                        impulse_context = impulse.get("context")
                        impulse_meal_key = (
                            _single_line(impulse_context.get("meal_key"), 20)
                            if isinstance(impulse_context, dict)
                            else ""
                        )
                        is_pending_same_meal = (
                            impulse_reason in {"meal_care", "meal_care_followup"}
                            and str(impulse.get("state") or "queued") in {"queued", "deferred"}
                            and (not impulse_meal_key or impulse_meal_key == meal_key)
                        )
                        if not is_pending_same_meal:
                            kept_impulses.append(impulse)
                    if len(kept_impulses) != len(impulses):
                        user["proactive_impulses"] = kept_impulses
            return {"kind": "specific" if foods else "none", "foods": foods}
        followup_minutes = _safe_int(runtime_persona_setting(self, "meal_care_followup_minutes", 45), 45, 15, 180)
        kind = "unrelated"
        if foods:
            kind = "specific"
            context.update({"active": False, "stage": "resolved", "resolved_at": check_now, "foods": foods})
            satisfied = user.setdefault("meal_care_satisfied", [])
            if isinstance(satisfied, list) and meal_key not in satisfied:
                satisfied.append(meal_key)
            self._cancel_planned_meal_care_followup(user, note="用户已经说明具体吃了什么")
        elif self._meal_reply_is_not_eaten(normalized):
            kind = "not_eaten_final" if followup_already_sent else "not_eaten"
            context.update(
                {
                    "active": not followup_already_sent,
                    "stage": "resolved_no_meal" if followup_already_sent else "not_eaten",
                    "last_reply_at": check_now,
                    "followup_due_at": check_now + followup_minutes * 60,
                }
            )
        elif self._meal_reply_confirms_eaten(normalized):
            kind = "ate_without_detail_final" if followup_already_sent else "ate_without_detail"
            context.update(
                {
                    "active": not followup_already_sent,
                    "stage": "resolved_without_detail" if followup_already_sent else "awaiting_detail",
                    "last_reply_at": check_now,
                    "followup_due_at": check_now + followup_minutes * 60,
                }
            )
        if kind in {"not_eaten", "ate_without_detail"} and not followup_already_sent:
            # The current passive reply is explicitly instructed to ask the one
            # allowed follow-up, so cancel the scheduled proactive duplicate.
            context["followup_count"] = 1
            context["followup_via_reply_at"] = check_now
            context["followup_due_at"] = 0
            self._cancel_planned_meal_care_followup(user, note="当前被动回复已承担唯一一次吃饭补问")
        elif kind == "unrelated":
            # The user has replied but deliberately did not continue the meal
            # topic (for example, "我在忙"). Treat that as a soft refusal and
            # stop this check-in instead of turning it into another proactive
            # "吃了吗" message later.
            context.update(
                {
                    "active": False,
                    "stage": "closed_unrelated",
                    "last_reply_at": check_now,
                    "closed_at": check_now,
                    "followup_due_at": 0,
                }
            )
            self._cancel_planned_meal_care_followup(user, note="用户未承接饮食话题，本轮饭点关心已结束")
        user["meal_check_context"] = context
        if kind != "unrelated":
            user["meal_care_reply_hint"] = {
                "kind": kind,
                "meal_label": meal_label,
                "foods": foods,
                "text": normalized,
                "ts": check_now,
            }
        return {"kind": kind, "foods": foods, "meal_key": meal_key, "meal_label": meal_label}

    def _meal_care_requires_full_reply(self, user: dict[str, Any], text: str) -> bool:
        if not self._meal_care_active_context(user):
            return False
        normalized = _single_line(text, 160)
        return bool(
            self._meal_reply_is_not_eaten(normalized)
            or self._meal_reply_confirms_eaten(normalized)
            or self._meal_reply_food_items(normalized, active_context=True)
        )

    def _format_meal_care_reply_context(
        self,
        user: dict[str, Any],
        text: str,
        *,
        include_heading: bool = True,
    ) -> str:
        if not isinstance(user, dict):
            return ""
        hint = user.get("meal_care_reply_hint")
        if not isinstance(hint, dict) or _now_ts() - _safe_float(hint.get("ts"), 0) > 10 * 60:
            return ""
        hint_text = _single_line(hint.get("text"), 220)
        current_text = _single_line(text, 260)
        if hint_text and not (hint_text == current_text or hint_text in current_text):
            return ""
        kind = _single_line(hint.get("kind"), 30)
        meal_label = _single_line(hint.get("meal_label"), 12) or "这顿饭"
        foods = [_single_line(item, 30) for item in hint.get("foods", []) if _single_line(item, 30)] if isinstance(hint.get("foods"), list) else []
        body = ""
        if kind == "specific" and foods:
            body = f"用户已经明确说{meal_label}吃了{'、'.join(foods)}。自然接住这个具体内容，不要再问吃了什么；这些内容已回填到吃什么候选。"
        elif kind == "ate_without_detail":
            body = f"用户只确认{meal_label}吃过了，但没有说具体吃了什么。先接住当前语气，再自然追问一句具体吃了什么；只问一次，不审问。"
        elif kind == "ate_without_detail_final":
            body = f"用户再次只确认{meal_label}吃过了，仍没有提供具体内容。到这里就接住并收住，不要第三次追问吃了什么。"
        elif kind == "not_eaten":
            body = f"用户明确说{meal_label}还没吃。不要追问“吃了什么”，改为关心准备什么时候吃、想吃什么；如果下方有吃饭候选，只给少量选择，不要一次报菜单。"
        elif kind == "not_eaten_final":
            body = f"用户补问后仍说{meal_label}没吃。简短关心一句就收住，不再继续追问；不要责怪或说教。"
        if not body:
            return ""
        return f"【吃饭关心承接】{body}" if include_heading else body

    def _format_meal_care_reply_prompt_section(
        self,
        user: dict[str, Any],
        text: str,
    ) -> dict[str, Any]:
        return prompt_section(
            "吃饭关心承接",
            self._format_meal_care_reply_context(
                user,
                text,
                include_heading=False,
            ),
        )

    @staticmethod
    def _food_menu_type_label(value: Any) -> str:
        key = str(value or "").strip().lower()
        return {
            "dish": "菜品",
            "restaurant": "菜馆",
            "takeout": "外卖",
            "drink_snack": "饮品/零食",
            "snack": "饮品/零食",
            "emergency": "应急",
        }.get(key, "候选")

    @staticmethod
    def _food_menu_time_label(value: Any) -> str:
        key = str(value or "").strip().lower()
        return {
            "breakfast": "早餐",
            "lunch": "午餐",
            "dinner": "晚餐",
            "late_night": "夜宵",
            "snack": "加餐",
        }.get(key, _single_line(value, 12))

    @staticmethod
    def _food_menu_list(value: Any, *, limit: int = 12, item_limit: int = 20) -> list[str]:
        raw_items = value if isinstance(value, list) else re.split(r"[,，、\n/|]+", str(value or ""))
        items: list[str] = []
        for raw in raw_items:
            item = _single_line(raw, item_limit)
            if item and item not in items:
                items.append(item)
        return items[:limit]

    def _current_food_time_key(self) -> str:
        hour = self._environment_now().hour
        if 5 <= hour < 10:
            return "breakfast"
        if 10 <= hour < 15:
            return "lunch"
        if 17 <= hour < 21:
            return "dinner"
        if hour >= 21 or hour < 3:
            return "late_night"
        return "snack"

    @staticmethod
    def _meal_key_from_text(text: str) -> str:
        normalized = _single_line(text, 120)
        if any(token in normalized for token in ("早餐", "早饭", "早上吃")):
            return "breakfast"
        if any(token in normalized for token in ("午饭", "午餐", "中午吃")):
            return "lunch"
        if any(token in normalized for token in ("晚饭", "晚餐", "晚上吃")):
            return "dinner"
        if any(token in normalized for token in ("夜宵", "宵夜")):
            return "late_night"
        return ""

    def _food_menu_query_profile(self, text: str, user: dict[str, Any] | None = None) -> dict[str, Any]:
        query = _single_line(text, 220)
        if not query:
            return {"is_query": False}
        meal_context = self._meal_care_active_context(user) if isinstance(user, dict) else {}
        meal_stage = _single_line(meal_context.get("stage"), 24)
        meal_not_eaten = meal_stage == "not_eaten" and self._meal_reply_is_not_eaten(query)
        feature_discussion_markers = (
            "功能", "候选", "开关", "配置", "页面", "注入", "触发", "保存", "管理",
            "不好用", "好用", "误判", "优化", "逻辑", "模块", "面板",
        )
        natural_food_need = bool(
            re.search(r"(今天|现在|这顿|中午|晚上|早上|早饭|早餐|午饭|午餐|晚饭|晚餐|夜宵|宵夜|外卖|点餐|饿|嘴馋|想吃|吃点|吃些|点什么|点啥|吃什么|吃啥)", query)
            and not re.search(r"(功能|开关|配置|页面|注入|触发|保存|管理|模块|面板)", query)
        )
        if any(marker in query for marker in feature_discussion_markers) and not natural_food_need and not meal_not_eaten:
            return {"is_query": False}
        feedback = self._detect_food_feedback(query)
        if feedback.get("already_ate") and not re.search(r"(什么|啥|推荐|点什么|点啥|再吃|还吃)", query):
            return {"is_query": False}
        food_question = bool(
            re.search(r"(吃|点|买|喝|叫).{0,8}(什么|啥|哪[个家]|哪种|推荐|好|合适)", query)
            or re.search(r"(什么|啥).{0,4}(好吃|能吃|可吃|适合吃)", query)
            or re.search(r"(不知道|纠结|想不到|随便).{0,8}(吃|点|买|喝)", query)
            or re.search(r"(推荐|来|整|安排).{0,6}(外卖|夜宵|午饭|晚饭|早餐|吃的|喝的)", query)
            or re.search(r"(饿了|好饿|有点饿|嘴馋|馋了)", query)
            or any(token in query for token in ("吃什么", "吃啥", "点什么", "点啥", "外卖吃", "夜宵吃", "午饭吃", "晚饭吃", "早餐吃"))
            or (len(query) <= 16 and any(token in query for token in ("外卖", "夜宵", "午饭", "晚饭", "早餐")))
        )
        if not food_question and not meal_not_eaten:
            return {"is_query": False}
        preferred_type = ""
        if any(token in query for token in ("外卖", "点餐", "点什么", "点啥", "叫个", "叫点")):
            preferred_type = "takeout"
        elif any(token in query for token in ("出去吃", "店", "馆", "附近", "堂食")):
            preferred_type = "restaurant"
        elif any(token in query for token in ("喝", "奶茶", "咖啡", "饮料", "零食", "甜品")):
            preferred_type = "drink_snack"
        desired_tags: list[str] = []
        tag_map = {
            "清淡": ("清淡", "不油", "少油", "胃不舒服"),
            "热乎": ("热", "暖", "汤", "热乎", "暖和"),
            "快": ("快", "省事", "随便", "懒得", "不想纠结"),
            "辣": ("辣", "重口", "麻辣"),
            "甜": ("甜", "甜品", "奶茶"),
            "顶饱": ("饱", "顶饱", "管饱"),
            "便宜": ("便宜", "省钱", "实惠"),
        }
        for tag, markers in tag_map.items():
            if any(marker in query for marker in markers) and tag not in desired_tags:
                desired_tags.append(tag)
        return {
            "is_query": True,
            "text": query,
            "preferred_type": preferred_type,
            "time_key": _single_line(meal_context.get("meal_key"), 20) or self._current_food_time_key(),
            "meal": _single_line(meal_context.get("meal_label"), 12) or feedback.get("meal") or "",
            "desired_tags": desired_tags,
        }

    def _food_menu_items(self) -> list[dict[str, Any]]:
        state = self.data.get("food_menu") if isinstance(self.data.get("food_menu"), dict) else {}
        items = state.get("items") if isinstance(state.get("items"), list) else []
        normalized: list[dict[str, Any]] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            name = _single_line(raw.get("name"), 40)
            if not name:
                continue
            item = dict(raw)
            item["name"] = name
            item["type"] = _single_line(item.get("type"), 20) or "dish"
            item["category"] = _single_line(item.get("category"), 24)
            item["tags"] = self._food_menu_list(item.get("tags"), limit=10, item_limit=16)
            item["times"] = self._food_menu_list(item.get("times"), limit=5, item_limit=16)
            item["avoid"] = self._food_menu_list(item.get("avoid"), limit=8, item_limit=24)
            item["aliases"] = self._food_menu_list(item.get("aliases"), limit=10, item_limit=24)
            item["note"] = _single_line(item.get("note"), 80)
            item["favorite"] = bool(item.get("favorite"))
            item["hidden"] = bool(item.get("hidden"))
            normalized.append(item)
        return normalized

    def _score_food_menu_item(self, item: dict[str, Any], profile: dict[str, Any]) -> float:
        query = str(profile.get("text") or "")
        if item.get("hidden"):
            return -999.0
        for token in item.get("avoid", []):
            if token and token in query:
                return -999.0
        score = 1.0
        if item.get("favorite"):
            score += 1.2
        preferred_type = str(profile.get("preferred_type") or "")
        if preferred_type and str(item.get("type") or "") == preferred_type:
            score += 2.4
        times = item.get("times") if isinstance(item.get("times"), list) else []
        if times:
            score += 1.5 if profile.get("time_key") in times else -0.8
        desired_tags = profile.get("desired_tags") if isinstance(profile.get("desired_tags"), list) else []
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        score += sum(
            0.9
            for desired in desired_tags
            if any(desired == tag or desired in tag or tag in desired for tag in tags)
        )
        category = str(item.get("category") or "")
        searchable = [item.get("name"), category, item.get("note"), *item.get("aliases", []), *tags]
        if any(part and str(part) in query for part in searchable):
            score += 2.8
        last = _safe_float(item.get("last_recommended_at"), 0, 0)
        if last > 0:
            age_hours = max(0.0, (_now_ts() - last) / 3600)
            if age_hours < 8:
                score -= 1.4
            elif age_hours < 36:
                score -= 0.5
        score += min(0.8, _safe_int(item.get("use_count"), 0, 0) * 0.04)
        return score

    def _mark_food_menu_items_recommended(self, candidates: list[dict[str, Any]]) -> None:
        ids = {
            _single_line(item.get("id"), 48)
            for item in candidates
            if isinstance(item, dict) and _single_line(item.get("id"), 48)
        }
        if not ids:
            return
        state = self.data.get("food_menu") if isinstance(self.data.get("food_menu"), dict) else {}
        items = state.get("items") if isinstance(state.get("items"), list) else []
        if not items:
            return
        now = _now_ts()
        changed = False
        for item in items:
            if not isinstance(item, dict):
                continue
            if _single_line(item.get("id"), 48) in ids:
                item["last_recommended_at"] = now
                item["updated_ts"] = now
                changed = True
        if changed:
            state["updated_ts"] = now
            self.data["food_menu"] = state
            self._save_data_sync(sections={"food_menu"})

    def _food_menu_candidates_for_prompt(self, text: str, *, limit: int = 3, user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        profile = self._food_menu_query_profile(text, user=user)
        if not profile.get("is_query"):
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in self._food_menu_items():
            score = self._score_food_menu_item(item, profile)
            if score > -100:
                scored.append((score, item))
        scored.sort(key=lambda pair: (pair[0], bool(pair[1].get("favorite")), _safe_int(pair[1].get("use_count"), 0, 0)), reverse=True)
        return [item for _, item in scored[: max(1, min(5, limit))]]

    def _format_food_menu_for_reply(
        self,
        text: str,
        *,
        limit: int = 3,
        user: dict[str, Any] | None = None,
        include_heading: bool = True,
    ) -> str:
        profile = self._food_menu_query_profile(text, user=user)
        if not profile.get("is_query"):
            return ""
        candidates = self._food_menu_candidates_for_prompt(text, limit=limit, user=user)
        if not candidates:
            return ""
        self._mark_food_menu_items_recommended(candidates)
        lines: list[str] = []
        for item in candidates:
            parts = [item.get("name")]
            label = self._food_menu_type_label(item.get("type"))
            category = _single_line(item.get("category"), 18)
            if category:
                label = f"{label}/{category}"
            meta = [label]
            times = [self._food_menu_time_label(value) for value in item.get("times", []) if self._food_menu_time_label(value)]
            if times:
                meta.append("适合" + "、".join(times[:3]))
            tags = item.get("tags", [])[:4]
            if tags:
                meta.append("偏" + "、".join(tags))
            note = _single_line(item.get("note"), 54)
            detail = "，".join(meta)
            line = f"{parts[0]}（{detail}）"
            if note:
                line += f"：{note}"
            lines.append(line)
        meal = _single_line(profile.get("meal"), 12) or self._food_menu_time_label(profile.get("time_key")) or "这顿"
        body = f"这轮用户在问{meal}吃什么。可参考：" + "；".join(lines) + "。"
        return f"【吃饭候选】\n{body}" if include_heading else body

    def _format_food_menu_reply_prompt_section(
        self,
        text: str,
        *,
        limit: int = 3,
        user: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return prompt_section(
            "吃饭候选",
            self._format_food_menu_for_reply(
                text,
                limit=limit,
                user=user,
                include_heading=False,
            ),
        )

    def _mark_food_menu_item_used_from_text(self, text: str) -> list[str]:
        query = _single_line(text, 220)
        if not query:
            return []
        state = self.data.get("food_menu") if isinstance(self.data.get("food_menu"), dict) else {}
        items = state.get("items") if isinstance(state.get("items"), list) else []
        if not items:
            return []
        now = _now_ts()
        matched: list[str] = []
        for item in items:
            if not isinstance(item, dict) or item.get("hidden"):
                continue
            terms = [item.get("name"), *self._food_menu_list(item.get("aliases"), limit=10, item_limit=24)]
            if any(term and str(term) in query for term in terms):
                item["use_count"] = _safe_int(item.get("use_count"), 0, 0) + 1
                item["last_used_at"] = now
                matched.append(_single_line(item.get("name"), 40))
        if matched:
            state["updated_ts"] = now
            self.data["food_menu"] = state
        return matched[:5]

    def _pick_diary_fragment(self) -> str:
        diaries = self.data.get("bot_diaries", [])
        if not isinstance(diaries, list) or not diaries:
            return ""
        diary = random.choice(diaries[-5:])
        if not isinstance(diary, dict):
            return ""
        candidates = [
            _single_line(diary.get("share_seed"), 100),
            _single_line(diary.get("summary"), 100),
        ]
        return next((item for item in candidates if item), "")

    def _parse_date_value(self, value: Any) -> date | None:
        text = str(value or "").strip()
        for fmt in ("%Y-%m-%d", "%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                year = self._environment_now().year if fmt == "%m-%d" else parsed.year
                return date(year, parsed.month, parsed.day)
            except ValueError:
                continue
        return None

    def _next_occurrence(self, entry: dict[str, Any], now: datetime | None = None) -> date | None:
        base = self._parse_date_value(entry.get("date"))
        if base is None:
            return None
        today = (now or self._environment_now()).date()
        if entry.get("repeat_yearly", True):
            try:
                candidate = date(today.year, base.month, base.day)
            except ValueError:
                return None
            if candidate < today:
                try:
                    candidate = date(today.year + 1, base.month, base.day)
                except ValueError:
                    return None
            return candidate
        return base

    def _get_relevant_important_dates(self, now: datetime | None = None) -> list[dict[str, Any]]:
        entries = self.data.get("important_dates", [])
        if not isinstance(entries, list):
            return []
        current = now or self._environment_now()
        today = current.date()
        relevant = []
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("enabled", True):
                continue
            next_day = self._next_occurrence(entry, now=current)
            if next_day is None:
                continue
            days_until = (next_day - today).days
            remind_days = _safe_int(
                entry.get("remind_days"), runtime_persona_setting(self, "important_date_lookahead_days", 7), 0, 365
            )
            if 0 <= days_until <= remind_days:
                copy = dict(entry)
                copy["_next_date"] = _date_key(next_day)
                copy["_days_until"] = days_until
                relevant.append(copy)
        return sorted(
            relevant,
            key=lambda item: (
                _safe_int(item.get("_days_until"), 999),
                -_safe_int(item.get("priority"), 50),
            ),
        )

    def _format_important_dates_for_prompt(self) -> str:
        entries = self._get_relevant_important_dates()
        if not entries:
            return "（近期没有需要特别记住的日期）"
        lines = []
        for entry in entries[:8]:
            days = _safe_int(entry.get("_days_until"), 0)
            when = "今天" if days == 0 else f"{days} 天后"
            lines.append(
                f"- {when}｜{entry.get('title', '')}｜类型：{entry.get('type', '重要日期')}｜"
                f"备注：{entry.get('note', '')}"
            )
        return "\n".join(lines)

    def _format_calendar_context_for_prompt(self, now: datetime | None = None) -> str:
        current = now or self._environment_now()
        weekday_names = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
        weekday = weekday_names[current.weekday()]
        is_weekend = current.weekday() >= 5
        builtin_holidays = {
            "01-01": ("元旦", "节假日"),
            "05-01": ("劳动节", "节假日"),
            "10-01": ("国庆节", "节假日"),
        }
        month_day = current.strftime("%m-%d")
        today_dates = [
            entry
            for entry in self._get_relevant_important_dates(now=current)
            if _safe_int(entry.get("_days_until"), 999) == 0
        ]
        special_lines = []
        holiday_tokens = (
            "节",
            "节日",
            "假",
            "假期",
            "放假",
            "休息",
            "旅行",
            "生日",
            "纪念日",
            "春节",
            "元旦",
            "清明",
            "端午",
            "中秋",
            "国庆",
            "劳动",
            "圣诞",
        )
        has_holiday_signal = False
        builtin_holiday = builtin_holidays.get(month_day)
        if builtin_holiday:
            title, type_text = builtin_holiday
            special_lines.append(f"- 今天：{title}｜类型：{type_text}｜备注：内置公历节日")
            has_holiday_signal = True
        for entry in today_dates[:5]:
            title = _single_line(entry.get("title"), 40)
            type_text = _single_line(entry.get("type"), 30)
            note = _single_line(entry.get("note"), 80)
            joined = f"{title} {type_text} {note}"
            if any(token in joined for token in holiday_tokens):
                has_holiday_signal = True
            if title:
                special_lines.append(f"- 今天：{title}｜类型：{type_text or '重要日期'}｜备注：{note or '无'}")

        # The durable calendar is a constraint layer, not execution evidence.
        # Keep its wording explicit so a generated plan can use a confirmed
        # vacation/school rule without claiming that the event already
        # happened.  The fallback below preserves compatibility with hosts
        # that predate AgendaRuntimeMixin.
        calendar_snapshot = {}
        snapshot_getter = getattr(self, "_agenda_calendar_snapshot", None)
        if callable(snapshot_getter):
            try:
                candidate = snapshot_getter(current.date().isoformat(), now=current)
                if isinstance(candidate, dict):
                    calendar_snapshot = candidate
            except Exception:
                calendar_snapshot = {}
        calendar_timeline: dict[str, Any] = {}
        timeline_getter = getattr(self, "_agenda_calendar_timeline", None)
        if callable(timeline_getter):
            try:
                candidate = timeline_getter(
                    current.date().isoformat(),
                    now=current,
                    history_days=3,
                    horizon_days=14,
                )
                if isinstance(candidate, dict):
                    calendar_timeline = candidate
            except Exception:
                calendar_timeline = {}
        calendar_candidates: list[dict[str, Any]] = []
        candidates_getter = getattr(self, "_agenda_calendar_candidates_store", None)
        if callable(candidates_getter):
            try:
                raw_candidates = candidates_getter()
                if isinstance(raw_candidates, list):
                    calendar_candidates = [
                        item for item in raw_candidates
                        if isinstance(item, dict)
                        and str(item.get("lifecycle_state") or item.get("lifecycle") or "candidate") not in {"confirmed", "active", "completed", "cancelled", "expired"}
                    ][:8]
            except Exception:
                calendar_candidates = []
        all_calendar_events = [
            item for item in calendar_snapshot.get("effective_events", calendar_snapshot.get("events", []))
            if isinstance(item, dict) and str(item.get("status") or "") not in {"cancelled", "expired"}
        ]
        # New snapshots expose ``events`` as the complete adjusted list and
        # ``effective_events`` as the planning projection. Older snapshots may
        # only contain one list, so fall back gracefully.
        raw_calendar_events = calendar_snapshot.get("events")
        if isinstance(raw_calendar_events, list):
            all_calendar_events = [
                item for item in raw_calendar_events
                if isinstance(item, dict) and str(item.get("status") or "") not in {"cancelled", "expired"}
            ]
        calendar_events = [
            item for item in calendar_snapshot.get("effective_events", all_calendar_events)
            if isinstance(item, dict) and str(item.get("status") or "") not in {"cancelled", "expired"}
        ]
        calendar_constraints: list[str] = []
        calendar_conflict_lines: list[str] = []
        for event in calendar_events[:16]:
            title = _single_line(event.get("title"), 60)
            if not title:
                continue
            kind = str(event.get("kind") or event.get("type") or "event")
            kind_label = {
                "period": "长期区间",
                "recurrence": "周期规则",
                "event": "单次事件",
                "exception": "例外调整",
            }.get(kind, "日历事件")
            start_date = _single_line(
                event.get("occurrence_date") if kind != "period" else event.get("start_date"),
                24,
            ) or _single_line(event.get("date") or event.get("start_date"), 24)
            end_date = _single_line(event.get("end_date"), 24) if kind == "period" else ""
            date_text = start_date
            if end_date and end_date != start_date:
                date_text = f"{start_date} 至 {end_date}"
            start_at = _single_line(event.get("start_at"), 40)
            end_at = _single_line(event.get("end_at"), 40)
            clock_text = ""
            if (not event.get("all_day") or event.get("start_time") or event.get("end_time")) and start_at and "T" in start_at:
                clock_text = start_at.split("T", 1)[1][:5]
                if end_at and "T" in end_at:
                    clock_text += f"-{end_at.split('T', 1)[1][:5]}"
            if clock_text:
                date_text += f" {clock_text}"
            status_label = "已确认日历约束" if str(event.get("status") or "confirmed") in {"confirmed", "active"} else "待确认日历记录"
            calendar_constraints.append(f"- {title}｜{kind_label}｜{date_text or '今天'}｜{status_label}")
            joined = f"{title} {event.get('note', '')} {event.get('description', '')}"
            if any(token in joined for token in holiday_tokens):
                has_holiday_signal = True
        if calendar_constraints:
            calendar_constraints_block = "今天有效的日历约束（属于计划依据，不等于已经发生）：\n" + "\n".join(calendar_constraints)
        else:
            calendar_constraints_block = ""
        conflicts = calendar_snapshot.get("conflicts") if isinstance(calendar_snapshot.get("conflicts"), list) else []
        if conflicts:
            by_id = {
                str(item.get("source_calendar_id") or item.get("calendar_id") or ""): item
                for item in all_calendar_events
                if isinstance(item, dict)
            }
            for conflict in conflicts[:8]:
                winner_id = str(conflict.get("winner_id") or "")
                loser_id = str(conflict.get("loser_id") or "")
                winner = _single_line(by_id.get(winner_id, {}).get("title"), 50)
                loser = _single_line(by_id.get(loser_id, {}).get("title"), 50)
                if winner and loser:
                    state = "同优先级，需谨慎处理" if conflict.get("unresolved") else "按优先级采用前者"
                    suffix = "｜当天不生效" if not conflict.get("unresolved") else ""
                    calendar_conflict_lines.append(f"- {winner} 覆盖 {loser}｜{state}{suffix}")
        if has_holiday_signal:
            day_tone = "节假日/特殊日期"
        elif is_weekend:
            day_tone = "周末/休息日候选"
        else:
            day_tone = "普通工作日或学习日候选"
        rules = [
            f"日期：{current.strftime('%Y-%m-%d')}（{weekday}）",
            f"基础日期类型：{day_tone}",
        ]
        if special_lines:
            rules.append("今天相关的重要日期：\n" + "\n".join(special_lines))
        else:
            rules.append("今天相关的重要日期：无")
        if calendar_constraints_block:
            rules.append(calendar_constraints_block)
        if calendar_candidates:
            candidate_lines = []
            for item in calendar_candidates:
                title = _single_line(item.get("title"), 80)
                if not title:
                    continue
                date_text = _single_line(item.get("start_date") or item.get("date"), 20) or "近期"
                candidate_lines.append(f"- {title}｜{date_text}｜待确认")
            if candidate_lines:
                rules.append(
                    "近期对话待确认候选（仅供询问参考，不是事实）：\n"
                    + "\n".join(candidate_lines)
                    + "\n不得据此断言用户已经安排、正在执行或已经完成；如有必要，只能轻量询问确认。"
                )
        if calendar_conflict_lines:
            rules.append("日历重叠处理：\n" + "\n".join(calendar_conflict_lines))
        timeline_lines: list[str] = []
        current_phase = calendar_timeline.get("current_phase") if isinstance(calendar_timeline.get("current_phase"), list) else []
        if current_phase:
            phase_text = "、".join(
                f"{_single_line(item.get('title'), 48)}（{_single_line(item.get('start_date'), 16)} 至 {_single_line(item.get('end_date'), 16) or '待定'}）"
                for item in current_phase[:4]
                if isinstance(item, dict) and _single_line(item.get("title"), 48)
            )
            if phase_text:
                timeline_lines.append("当前生活阶段：" + phase_text)
        rhythms = calendar_timeline.get("rhythms") if isinstance(calendar_timeline.get("rhythms"), list) else []
        if rhythms:
            rhythm_text = "、".join(
                f"{_single_line(item.get('title'), 48)}（下次 {_single_line(item.get('next_occurrence'), 16) or '按周期推算'}）"
                for item in rhythms[:5]
                if isinstance(item, dict) and _single_line(item.get("title"), 48)
            )
            if rhythm_text:
                timeline_lines.append("稳定节律参考：" + rhythm_text)
        recent_changes = calendar_timeline.get("recent_changes") if isinstance(calendar_timeline.get("recent_changes"), list) else []
        if recent_changes:
            recent_text = "、".join(
                f"{_single_line(item.get('title'), 40)}（{_single_line(item.get('occurrence_date'), 16)}）"
                for item in recent_changes[:4]
                if isinstance(item, dict) and _single_line(item.get("title"), 40)
            )
            if recent_text:
                timeline_lines.append("最近变化/余波：" + recent_text)
        transitions = calendar_timeline.get("transitions") if isinstance(calendar_timeline.get("transitions"), list) else []
        if transitions:
            transition_text = "、".join(
                f"{_single_line(item.get('date'), 16)} {_single_line(item.get('title'), 40)}"
                for item in transitions[:5]
                if isinstance(item, dict) and _single_line(item.get("title"), 40)
            )
            if transition_text:
                timeline_lines.append("接下来可能发生的转换：" + transition_text)
        uncertainties = calendar_timeline.get("uncertainties") if isinstance(calendar_timeline.get("uncertainties"), list) else []
        if uncertainties:
            uncertainty_text = "、".join(
                _single_line(item.get("title") or item.get("reason") or "待确认变化", 44)
                for item in uncertainties[:4]
                if isinstance(item, dict)
            )
            if uncertainty_text:
                timeline_lines.append("仍不确定的部分：" + uncertainty_text)
        if timeline_lines:
            rules.append(
                "生活时间线（用于保持跨日连续，不等于执行事实）：\n"
                + "\n".join(f"- {line}" for line in timeline_lines)
                + "\n不要因为某一条当天计划就擅自结束或改写当前生活阶段；只有用户明确确认或日历明确记录了转换，才改变长期背景。稳定节律是默认倾向，临时事件可以改变当天，不必抹掉长期节律。存在待确认冲突时保留不确定性，用‘可能/先按目前记录’表达。"
            )
        rules.append(
            "日程判断：先看日期语境,再看人格设定。工作日可以有上课/上班；周末要更松,可以晚起、休息、出门、补一点自己的事；节假日/假期要明显区别于普通日,可以有庆祝、出行、宅家、已明确关系安排或假期拖延。"
        )
        rules.append(
            "如果人格、日程专用设定或重要日期备注里写了调休、补班、补课、考试、值班等例外,优先按这些例外来写。不要凭空塞入身份里没有的校园、职场或节日细节。"
        )
        if calendar_constraints or timeline_lines:
            rules.append(
                "日历使用边界：它提供生活阶段、节律和变化线索，不替代当前会话事实，也不自动删除日程。用户本轮明确说法优先；记录不确定时不要把推断写成确定事实。"
            )
        return "\n".join(rules)

    def _calendar_day_flags(self, now: datetime | None = None) -> dict[str, Any]:
        current = now or self._environment_now()
        is_weekend = current.weekday() >= 5
        builtin_holidays = {"01-01", "05-01", "10-01"}
        month_day = current.strftime("%m-%d")
        today_dates = [
            entry
            for entry in self._get_relevant_important_dates(now=current)
            if _safe_int(entry.get("_days_until"), 999) == 0
        ]
        holiday_tokens = (
            "节",
            "节日",
            "假",
            "假期",
            "放假",
            "休息",
            "旅行",
            "春节",
            "元旦",
            "清明",
            "端午",
            "中秋",
            "国庆",
            "劳动",
        )
        override_tokens = ("调休", "补班", "补课", "考试", "值班", "加班", "返校")
        has_holiday_signal = month_day in builtin_holidays
        has_override_signal = False
        has_calendar_context = False
        has_calendar_holiday_signal = False
        has_calendar_school_work = False
        calendar_snapshot = {}
        snapshot_getter = getattr(self, "_agenda_calendar_snapshot", None)
        if callable(snapshot_getter):
            try:
                candidate = snapshot_getter(current.date().isoformat(), now=current)
                if isinstance(candidate, dict):
                    calendar_snapshot = candidate
            except Exception:
                calendar_snapshot = {}
        calendar_events = calendar_snapshot.get("effective_events", calendar_snapshot.get("events", []))
        if isinstance(calendar_events, list):
            for item in calendar_events:
                if not isinstance(item, dict):
                    continue
                if str(item.get("status") or "confirmed") not in {"confirmed", "active"}:
                    continue
                has_calendar_context = True
                joined = _single_line(
                    f"{item.get('title', '')} {item.get('note', '')} {item.get('description', '')}",
                    180,
                )
                if any(token in joined for token in holiday_tokens):
                    has_calendar_holiday_signal = True
                if any(token in joined for token in ("上学", "上课", "放学", "学校", "上班", "通勤", "值班", "会议", "考试", "补课")):
                    has_calendar_school_work = True
                if any(token in joined for token in override_tokens):
                    has_override_signal = True
        for entry in today_dates:
            if not isinstance(entry, dict):
                continue
            joined = _single_line(
                f"{entry.get('title', '')} {entry.get('type', '')} {entry.get('note', '')}",
                160,
            )
            if any(token in joined for token in holiday_tokens):
                has_holiday_signal = True
            if any(token in joined for token in override_tokens):
                has_override_signal = True
        schedule_prompt = self._get_schedule_planning_prompt()
        if any(token in schedule_prompt for token in override_tokens):
            has_override_signal = True
        return {
            "is_weekend": is_weekend,
            "has_holiday_signal": has_holiday_signal or has_calendar_holiday_signal,
            "has_override_signal": has_override_signal,
            "has_calendar_context": has_calendar_context,
            "has_calendar_school_work": has_calendar_school_work,
            "calendar_snapshot": calendar_snapshot,
        }

    def _plan_conflicts_with_calendar(self, items: list[dict[str, str]], now: datetime | None = None) -> bool:
        """Report only explicit unresolved calendar conflicts.

        A plan can be a reasonable interpretation of a phase, a rhythm, or a
        user correction.  Keyword matching (for example, treating every
        ``上学`` row as invalid during a vacation) made the calendar a hidden
        hard filter and caused the companion to rewrite its own life.  The
        planner prompt now receives the timeline and can resolve ambiguity in
        prose; this hook is reserved for genuinely unresolved overlaps.
        """

        if not items:
            return False
        getter = getattr(self, "_agenda_calendar_timeline", None)
        if not callable(getter):
            return False
        try:
            timeline = getter(now=(now or self._environment_now()), history_days=0, horizon_days=1)
        except Exception:
            return False
        conflicts = timeline.get("conflicts") if isinstance(timeline, dict) else []
        return any(isinstance(item, dict) and item.get("unresolved") for item in (conflicts or []))

    def _is_micro_plan_activity(self, text: str) -> bool:
        normalized = _single_line(text, 160)
        if not normalized:
            return False
        length = len(normalized)
        instant_markers = (
            "看了一眼",
            "瞥了一眼",
            "拍了一下",
            "拍了下",
            "翻了个身",
            "揉了揉",
            "抬头看",
            "关掉闹钟",
            "叫了一声",
            "应了一声",
            "顺手点开",
        )
        if any(marker in normalized for marker in instant_markers):
            return length <= 30
        generic_short_markers = ("一下", "一眼", "一瞬", "顺手", "刚好", "忽然")
        continuity_markers = (
            "慢慢",
            "继续",
            "待着",
            "坐着",
            "趴着",
            "整理",
            "收拾",
            "吃饭",
            "洗漱",
            "发呆",
            "看剧",
            "听歌",
            "出门",
            "路上",
            "吹风",
            "睡前",
            "饭后",
            "午休",
            "收尾",
        )
        if any(marker in normalized for marker in generic_short_markers) and not any(
            marker in normalized for marker in continuity_markers
        ):
            return length <= 22
        return False

    def _plan_has_excess_micro_segments(self, items: list[dict[str, str]]) -> bool:
        if not items:
            return False
        micro_count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            if self._is_micro_plan_activity(str(item.get("activity") or "")):
                micro_count += 1
        return micro_count >= max(2, len(items) // 4)

    def _is_abstract_plan_activity(self, text: str) -> bool:
        normalized = _single_line(text, 180)
        if not normalized:
            return False
        concrete_markers = (
            "起床", "赖床", "洗漱", "吃", "喝", "走", "坐", "趴", "靠", "收拾", "整理",
            "看", "听", "出门", "回家", "写", "刷", "逛", "吹风", "洗碗", "看剧", "躺",
            "翻", "换鞋", "背上", "拿着", "关灯", "开窗", "买", "收声", "聊天", "做饭",
        )
        abstract_markers = (
            "思绪", "心情", "气息", "余韵", "碎片", "温柔", "柔软", "飘忽", "微醺", "依恋",
            "恍惚", "生活感", "画面", "感觉", "梦里", "脑海里", "最后闪过", "随着光线",
        )
        if any(marker in normalized for marker in concrete_markers):
            abstract_count = sum(1 for marker in abstract_markers if marker in normalized)
            return abstract_count >= 3 and len(normalized) <= 22
        abstract_count = sum(1 for marker in abstract_markers if marker in normalized)
        return abstract_count >= 2

    def _plan_has_excess_abstract_segments(self, items: list[dict[str, str]]) -> bool:
        if not items:
            return False
        abstract_count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            if self._is_abstract_plan_activity(str(item.get("activity") or "")):
                abstract_count += 1
        return abstract_count >= max(2, len(items) // 3)

    @staticmethod
    def _plan_activity_signature(text: str) -> str:
        normalized = _single_line(text, 180)
        if not normalized:
            return ""
        category_rules = (
            ("起床", ("起床", "醒来", "睡醒", "赖床", "闹钟", "被窝")),
            ("洗漱", ("洗漱", "刷牙", "洗脸", "梳头", "镜子", "卫生间")),
            ("早餐", ("早餐", "早饭", "面包", "牛奶", "豆浆", "粥")),
            ("正餐", ("午饭", "晚饭", "吃饭", "做饭", "干饭", "饭桌", "摆碗", "点外卖")),
            ("通勤出门", ("出门", "路上", "公交", "地铁", "校门", "换鞋", "背包", "打车")),
            ("校园课程", ("上课", "下课", "教室", "课间", "老师", "同桌", "黑板", "班会")),
            ("补课考试", ("补课", "考试", "测验", "卷子", "复习", "考场", "错题")),
            ("学习作业", ("作业", "自习", "刷题", "数学", "英语", "课本", "笔记", "书包")),
            ("工作事务", ("上班", "工位", "会议", "打卡", "下班", "同事", "项目", "文档")),
            ("家务整理", ("收拾", "整理", "扫地", "洗碗", "洗衣", "归位", "桌面", "房间")),
            ("休息摸鱼", ("午休", "休息", "摸鱼", "躺", "趴", "沙发", "发呆", "缓一会")),
            ("娱乐放松", ("看剧", "追番", "游戏", "刷短视频", "听歌", "小说", "漫画")),
            ("社交互动", ("聊天", "朋友", "家人", "消息", "电话", "群聊", "回复", "打开对话框")),
            ("购物外食", ("买", "便利店", "超市", "奶茶", "饮料", "小吃", "逛")),
            ("户外散步", ("散步", "走一段", "吹风", "公园", "楼下", "河边", "阳台", "开窗")),
            ("运动身体", ("运动", "跑步", "拉伸", "散操", "瑜伽", "出汗")),
            ("洗澡睡前", ("洗澡", "睡前", "关灯", "上床", "准备睡", "入睡", "枕头")),
        )
        hits: list[str] = []
        for label, tokens in category_rules:
            if any(token in normalized for token in tokens):
                hits.append(label)
            if len(hits) >= 2:
                break
        if hits:
            return "+".join(hits)
        compact = re.sub(r"[，。！？、,.!?；;：:\s]+", "", normalized)
        return compact[:8]

    def _plan_signature(self, items: list[dict[str, Any]]) -> list[str]:
        signatures: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            signature = self._plan_activity_signature(
                f"{item.get('activity', '')} {item.get('message_seed', '')}"
            )
            if signature:
                signatures.append(signature)
        return signatures

    def _format_recent_daily_plan_history_for_prompt(self, limit: int = 5) -> str:
        history = self._recent_daily_plan_history_entries()
        rows: list[str] = []
        for entry in history[-limit:]:
            if not isinstance(entry, dict):
                continue
            date_text = _single_line(entry.get("date"), 16)
            signatures = entry.get("signature")
            if not isinstance(signatures, list):
                signatures = []
            samples = entry.get("sample")
            if not isinstance(samples, list):
                samples = []
            skeleton = " / ".join(_single_line(part, 20) for part in signatures[:12] if part)
            sample_text = "；".join(_single_line(part, 46) for part in samples[:4] if part)
            if skeleton:
                line = f"- {date_text}: {skeleton}"
                if sample_text:
                    line += f"\n  代表活动: {sample_text}"
                rows.append(line)
        return "\n".join(rows) if rows else "暂无最近日程历史。"

    def _plan_repetition_score(self, items: list[dict[str, str]]) -> float:
        signatures = self._plan_signature(items)
        if not signatures:
            return 0.0
        current_set = set(signatures)
        history = self._recent_daily_plan_history_entries()
        best_score = 0.0
        for entry in history[-5:]:
            if not isinstance(entry, dict):
                continue
            old_signatures = entry.get("signature")
            if not isinstance(old_signatures, list) or not old_signatures:
                continue
            old_values = [str(value) for value in old_signatures if value]
            old_set = set(old_values)
            if not old_set:
                continue
            jaccard = len(current_set & old_set) / max(1, len(current_set | old_set))
            paired = min(len(signatures), len(old_values))
            same_positions = 0
            for idx in range(paired):
                if signatures[idx] == old_values[idx]:
                    same_positions += 1
            ordered = same_positions / max(1, paired)
            best_score = max(best_score, jaccard * 0.65 + ordered * 0.35)
        return best_score

    def _plan_is_too_repetitive(self, items: list[dict[str, str]]) -> bool:
        if not items:
            return False
        signatures = self._plan_signature(items)
        if len(signatures) >= 6:
            dominant_count = max(signatures.count(signature) for signature in set(signatures))
            if dominant_count >= max(4, len(signatures) // 2 + 1):
                return True
        return self._plan_repetition_score(items) >= 0.62

    def _daily_plan_history_entry(self, plan: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(plan, dict):
            return None
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            return None
        plan_date = _single_line(plan.get("date"), 16) or _today_key()
        sample: list[str] = []
        compact_items: list[dict[str, str]] = []
        for item in items[:6]:
            if not isinstance(item, dict):
                continue
            time_text = _single_line(item.get("time"), 8)
            activity = _single_line(item.get("activity"), 52)
            if activity:
                sample.append(f"{time_text} {activity}".strip())
        for item in items[:18]:
            if not isinstance(item, dict):
                continue
            compact_items.append(
                {
                    "time": _single_line(item.get("time"), 20),
                    "activity": _single_line(item.get("activity") or item.get("title"), 180),
                    "mood": _single_line(item.get("mood"), 80),
                    "message_seed": _single_line(item.get("message_seed"), 220),
                }
            )
        entry = {
            "date": plan_date,
            "generated_at": _single_line(plan.get("generated_at"), 20) or self._environment_now().strftime("%Y-%m-%d %H:%M"),
            "source": _single_line(plan.get("source"), 16),
            "signature": self._plan_signature(items),
            "sample": sample,
            "items": compact_items,
        }
        return entry

    def _recent_daily_plan_history_entries(self) -> list[dict[str, Any]]:
        history = self.data.get("daily_plan_history", [])
        entries = [entry for entry in history if isinstance(entry, dict)] if isinstance(history, list) else []
        known_dates = {_single_line(entry.get("date"), 16) for entry in entries}
        current_entry = self._daily_plan_history_entry(self.data.get("daily_plan", {}))
        if current_entry and _single_line(current_entry.get("date"), 16) not in known_dates:
            entries.append(current_entry)
        return entries

    def _remember_daily_plan_history(self, plan: dict[str, Any]) -> None:
        entry = self._daily_plan_history_entry(plan)
        if not entry:
            return
        plan_date = _single_line(entry.get("date"), 16)
        history = self.data.setdefault("daily_plan_history", [])
        if not isinstance(history, list):
            history = []
            self.data["daily_plan_history"] = history
        history[:] = [
            old
            for old in history
            if not (isinstance(old, dict) and _single_line(old.get("date"), 16) == plan_date)
        ]
        history.append(entry)
        del history[:-10]

    def _add_important_date_entry(self, value: str) -> tuple[bool, str]:
        parts = value.split(maxsplit=2)
        if len(parts) < 2:
            return False, "格式：陪伴 日期添加 <标题> <YYYY-MM-DD或MM-DD> [备注]"
        title = _single_line(parts[0], 40)
        date_text = _single_line(parts[1], 20)
        note = _single_line(parts[2], 120) if len(parts) >= 3 else ""
        parsed = self._parse_date_value(date_text)
        if parsed is None:
            return False, "日期格式不对,请用 YYYY-MM-DD 或 MM-DD。"
        repeat_yearly = len(date_text) == 5
        entry = {
            "id": f"date-{int(_now_ts())}-{random.randint(1000, 9999)}",
            "title": title,
            "date": date_text,
            "type": "重要日期",
            "note": note,
            "enabled": True,
            "repeat_yearly": repeat_yearly,
                "remind_days": runtime_persona_setting(self, "important_date_lookahead_days", 7),
            "priority": 50,
            "created_at": self._environment_now().strftime("%Y-%m-%d %H:%M"),
        }
        self.data.setdefault("important_dates", []).append(entry)
        return True, f"已添加重要日期：{title}｜{date_text}"

    def _remove_important_date_entry(self, value: str) -> str:
        keyword = _single_line(value, 40)
        if not keyword:
            return "请提供要删除的日期标题关键词。"
        entries = self.data.setdefault("important_dates", [])
        if not isinstance(entries, list):
            self.data["important_dates"] = []
            return "重要日期列表为空。"
        kept = []
        removed = []
        for entry in entries:
            title = str(entry.get("title", "")) if isinstance(entry, dict) else ""
            if keyword in title:
                removed.append(title)
            else:
                kept.append(entry)
        self.data["important_dates"] = kept
        if not removed:
            return "没有找到匹配的重要日期。"
        return "已删除：\n" + "\n".join(f"- {item}" for item in removed)

    def _format_important_dates(self) -> str:
        entries = self.data.get("important_dates", [])
        if not isinstance(entries, list) or not entries:
            return "还没有重要日期。"
        lines = ["重要日期条目："]
        today = self._environment_now().date()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            next_day = self._next_occurrence(entry)
            suffix = ""
            if next_day:
                days = (next_day - today).days
                suffix = "｜今天" if days == 0 else f"｜{days} 天后"
            enabled = "启用" if entry.get("enabled", True) else "停用"
            repeat = "每年" if entry.get("repeat_yearly", True) else "一次"
            lines.append(
                f"- {entry.get('title')}｜{entry.get('date')}｜{repeat}｜{enabled}{suffix}｜{entry.get('note', '')}"
            )
        return "\n".join(lines)

    def _active_memo_notes(self, *, include_completed: bool = False) -> list[dict[str, Any]]:
        now = _now_ts()
        raw_notes = self.data.get("memo_notes") if isinstance(self.data.get("memo_notes"), list) else []
        notes = [note for note in (normalize_memo_note(item, now=now) for item in raw_notes) if note]
        if not include_completed:
            notes = [note for note in notes if note.get("status") == "active"]
        notes.sort(key=lambda item: memo_note_sort_key(item, now=now))
        return notes

    def _format_memo_notes_for_prompt(
        self,
        *,
        days: int = 3,
        include_pinned: bool = True,
        limit: int = 6,
        include_heading: bool = True,
    ) -> str:
        now = _now_ts()
        horizon = now + max(0, int(days)) * 86400
        rows: list[dict[str, Any]] = []
        for note in self._active_memo_notes():
            due_at = _safe_float(note.get("due_at"), 0)
            if not (include_pinned and note.get("pinned")) and not (due_at > 0 and due_at <= horizon):
                continue
            rows.append(note)
        if not rows:
            return ""
        lines = ["【备忘便签】"] if include_heading else []
        for note in rows[: max(1, int(limit or 1))]:
            due_at = _safe_float(note.get("due_at"), 0)
            due_text = self._environment_fromtimestamp(due_at).strftime("%m-%d %H:%M") if due_at > 0 else "未设时间"
            repeat = {
                "daily": "每天",
                "weekly": "每周",
                "monthly": "每月",
                "yearly": "每年",
            }.get(str(note.get("repeat") or "none"), "不重复")
            text = _single_line(note.get("title") or note.get("content"), 80)
            detail = _single_line(note.get("content"), 120)
            if detail and detail != text:
                text = f"{text}；{detail}"
            lines.append(f"- {due_text}｜{repeat}｜{text}")
        lines.append("这些是用户主动保存的待办/提醒，不是已经发生的经历；只在当前话题或时间相关时自然承接。")
        return "\n".join(lines)

    def _format_memo_notes_prompt_section(
        self,
        *,
        days: int = 3,
        include_pinned: bool = True,
        limit: int = 6,
    ) -> dict[str, Any]:
        return prompt_section(
            "备忘便签",
            self._format_memo_notes_for_prompt(
                days=days,
                include_pinned=include_pinned,
                limit=limit,
                include_heading=False,
            ),
        )

    def _format_memo_note_prompt(self, user: dict[str, Any], *, reason: str = "") -> str:
        if reason != "memo_note_reminder" or not isinstance(user, dict):
            return ""
        context = user.get("planned_memo_note_context")
        if not isinstance(context, dict):
            return ""
        return (
            "【到期备忘便签】\n"
            f"- 标题：{_single_line(context.get('title'), 60) or '未命名便签'}\n"
            f"- 内容：{_single_line(context.get('content'), 240) or '无补充内容'}\n"
            f"- 到期：{_single_line(context.get('due_text'), 40) or '刚刚到期'}\n"
            "这是用户自己设置并已到期的提醒。直接自然提醒事项本身，不解释便签系统、调度或后台字段，不责怪用户，也不要虚构已完成。"
        )

    def _next_memo_due_in_seconds(self, now: float | None = None) -> float | None:
        check_now = _safe_float(now, _now_ts())
        disabled_getter = getattr(self, "_proactive_generation_disabled", None)
        if callable(disabled_getter) and disabled_getter():
            return None
        waits: list[float] = []
        for note in self._active_memo_notes():
            due_at = _safe_float(note.get("due_at"), 0)
            if not note.get("remind_enabled") or due_at <= 0:
                continue
            if due_at > check_now:
                waits.append(due_at - check_now)
                continue
            last_offer = _safe_float(note.get("last_reminder_offer_at"), 0)
            last_attempt = _safe_float(note.get("last_reminder_attempt_at"), 0)
            if last_offer > 0:
                waits.append(max(0.0, last_offer + 24 * 3600 - check_now))
            elif last_attempt > 0:
                waits.append(max(0.0, last_attempt + 10 * 60 - check_now))
            else:
                waits.append(0.0)
        return min(waits) if waits else None

    async def _maybe_process_memo_notes(self, *, force: bool = False) -> None:
        now = _now_ts()
        async with self._data_lock:
            raw_notes = self.data.get("memo_notes")
            if not isinstance(raw_notes, list) or not raw_notes:
                return
            notes = [note for note in (normalize_memo_note(item, now=now) for item in raw_notes) if note]
            changed = len(notes) != len(raw_notes)
            for note in notes:
                if note.get("status") != "active" or not note.get("remind_enabled"):
                    continue
                due_at = _safe_float(note.get("due_at"), 0)
                if due_at <= 0 or due_at > now:
                    continue
                last_offer = _safe_float(note.get("last_reminder_offer_at"), 0)
                if last_offer > 0 and now - last_offer < 24 * 3600 and not force:
                    continue
                last_attempt = _safe_float(note.get("last_reminder_attempt_at"), 0)
                if last_offer <= 0 and last_attempt > 0 and now - last_attempt < 10 * 60 and not force:
                    continue
                title = _single_line(note.get("title") or note.get("content"), 60) or "一张便签"
                content = _single_line(note.get("content"), 240)
                due_text = self._environment_fromtimestamp(due_at).strftime("%Y-%m-%d %H:%M")
                context = {
                    "memo_id": _single_line(note.get("id"), 64),
                    "title": title,
                    "content": content,
                    "due_at": due_at,
                    "due_text": due_text,
                    "repeat": _single_line(note.get("repeat"), 20),
                    "due_state": memo_note_due_state(note, now=now),
                }
                offered = 0
                note["last_reminder_attempt_at"] = now
                changed = True
                for user_id, user in self._personal_goal_owner_users():
                    scheduled = now + random.uniform(8, 35)
                    candidate = {
                        "source": "memo_note",
                        "reason": "memo_note_reminder",
                        "action": "message",
                        "scheduled_ts": scheduled,
                        "window_start_at": scheduled,
                        "preferred_ts": scheduled,
                        "best_until_at": scheduled + 2 * 3600,
                        "expire_at": scheduled + 8 * 3600,
                        "topic": title,
                        "motive": f"用户保存的便签“{title}”已经到期，需要自然提醒一次",
                        "score": 96,
                        "context_key": "planned_memo_note_context",
                        "context": deepcopy(context),
                    }
                    if self._offer_proactive_candidate(user_id, user, candidate):
                        offered += 1
                if offered > 0:
                    note["last_reminder_offer_at"] = now
                    note["updated_at"] = max(_safe_float(note.get("updated_at"), 0), now)
                    changed = True
            if changed:
                self.data["memo_notes"] = notes[-200:]
                self._save_data_sync(
                    sections={"memo_notes", "users", "proactive_candidate_pool"}
                )

    def _synchronize_body_cycle_strategy(self, conditions: list[Any], now: float) -> list[Any]:
        advanced_enabled = self._advanced_cycle_enabled()
        desired_mode = "advanced" if advanced_enabled else "legacy"
        previous_mode = str(self.data.get("body_cycle_strategy_mode") or "")
        kept: list[Any] = []
        removed = 0
        for cond in conditions:
            if not isinstance(cond, dict) or str(cond.get("kind") or "") != "body_cycle":
                kept.append(cond)
                continue
            phase = str(cond.get("phase") or self._infer_body_cycle_phase(str(cond.get("label") or "")))
            is_advanced = phase in self._ADVANCED_CYCLE_PHASES
            if is_advanced != advanced_enabled:
                removed += 1
                continue
            cond["phase"] = phase
            kept.append(cond)
        if removed:
            existing_meta = self.data.get("body_cycle_state")
            # A legacy condition may still be present while an advanced
            # timeline has already been anchored. Remove only the incompatible
            # condition in that case; resetting the anchor would move the
            # user back to day one after a restart or migration.
            keep_continuous_state = (
                desired_mode == "advanced"
                and isinstance(existing_meta, dict)
                and _safe_float(existing_meta.get("cycle_anchor_ts"), 0) > 0
            )
            if not keep_continuous_state:
                self.data.pop("body_cycle_state", None)
            logger.info(
                "周期策略切换，已清理不兼容旧状态: mode=%s removed=%s",
                desired_mode,
                removed,
            )
        self.data["body_cycle_strategy_mode"] = desired_mode

        if not advanced_enabled:
            return kept

        offset = _safe_int(runtime_persona_setting(self, "advanced_cycle_start_offset", 0), 0, 0, 180)
        meta = self.data.get("body_cycle_state")
        meta = dict(meta) if isinstance(meta, dict) else {}
        if offset <= 0:
            if meta.get("manual_offset_signature"):
                for key in ("manual_offset", "manual_offset_signature", "manual_offset_phase", "manual_offset_day_in_phase"):
                    meta.pop(key, None)
                self.data["body_cycle_state"] = meta
            has_cycle_condition = any(
                isinstance(cond, dict) and str(cond.get("kind") or "") == "body_cycle"
                for cond in kept
            )
            anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0)
            if not has_cycle_condition and anchor_ts <= 0:
                condition = self._advanced_cycle_condition(
                    "menstrual",
                    cause="六阶段周期策略首次启用，自然进入第一周期",
                )
                kept.append(condition)
                self._record_body_cycle_episode(condition)
                logger.info("六阶段周期策略首次启用，已从月经期第 1 天开始推进")
            return kept

        signature = self._advanced_cycle_offset_signature(offset)
        if meta.get("manual_offset_signature") == signature:
            return kept

        kept = [
            cond
            for cond in kept
            if not (isinstance(cond, dict) and str(cond.get("kind") or "") == "body_cycle")
        ]
        phase, day_in_phase = self._advanced_cycle_position_from_offset(offset)
        remaining_days = self._advanced_cycle_phase_days(phase) - day_in_phase + 1
        condition = self._advanced_cycle_condition(
            phase,
            cause="管理员设置了周期起始日",
            duration_hours=remaining_days * 24,
        )
        kept.append(condition)
        self._record_body_cycle_episode(condition)
        meta = self.data.get("body_cycle_state")
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta.update(
            {
                "manual_offset": offset,
                "manual_offset_signature": signature,
                "manual_offset_phase": phase,
                "manual_offset_day_in_phase": day_in_phase,
                "cycle_anchor_ts": max(0.0, now - (offset - 1) * 86400),
                "strategy": "advanced",
            }
        )
        self.data["body_cycle_state"] = meta
        logger.info(
            "已应用六阶段周期起始日: offset=%s phase=%s phase_day=%s previous_mode=%s",
            offset,
            phase,
            day_in_phase,
            previous_mode or "unknown",
        )
        return kept

    def _cleanup_expired_conditions(self) -> set[str]:
        now = _now_ts()
        had_body_cycle_state = "body_cycle_state" in self.data
        conditions = self.data.setdefault("state_conditions", [])
        if not isinstance(conditions, list):
            self.data["state_conditions"] = []
            return set()
        profile = self._persona_state_profile()
        if not profile.get("allow_cycle", False):
            before_count = len(conditions)
            conditions = [
                cond for cond in conditions
                if not isinstance(cond, dict) or str(cond.get("kind") or "") not in {"body_cycle", "cycle_discomfort"}
            ]
            removed_count = before_count - len(conditions)
            if removed_count:
                self.data.pop("body_cycle_state", None)
                logger.info("生理期模拟已关闭，清理旧周期状态: removed=%s", removed_count)
        else:
            conditions = self._synchronize_body_cycle_strategy(conditions, now)
            conditions = self._repair_body_cycle_conditions(conditions, now)
            if not self._advanced_cycle_enabled() or not bool(
                runtime_persona_setting(self, "advanced_cycle_discomfort_simulation", False)
            ):
                before_count = len(conditions)
                conditions = [
                    cond
                    for cond in conditions
                    if not isinstance(cond, dict) or str(cond.get("kind") or "") != "cycle_discomfort"
                ]
                if len(conditions) < before_count:
                    logger.info(
                        "不适模拟已关闭，清理残留经期不适状态: removed=%s",
                        before_count - len(conditions),
                    )
        active = []
        expired = []
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            if _safe_float(cond.get("end_ts"), 0) > now:
                active.append(cond)
            else:
                expired.append(cond)
        for cond in expired:
            active.extend(self._spawn_followup_conditions(cond))
        active = self._reconcile_advanced_cycle_condition(active, now)
        active = self._prune_active_hunger_conditions(active, now)
        self.data["state_conditions"] = active
        return {
            "body_cycle_state"
        } if had_body_cycle_state and "body_cycle_state" not in self.data else set()

    def _prune_active_hunger_conditions(self, conditions: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
        hunger_items = [
            cond for cond in conditions
            if isinstance(cond, dict)
            and str(cond.get("kind") or "") == "hunger"
            and _safe_float(cond.get("start_ts"), 0) <= now < _safe_float(cond.get("end_ts"), 0)
        ]
        if len(hunger_items) <= 1:
            return conditions
        hunger_items.sort(key=lambda item: (_safe_float(item.get("start_ts"), 0), _safe_float(item.get("end_ts"), 0)), reverse=True)
        keep_id = hunger_items[0].get("id")
        pruned: list[dict[str, Any]] = []
        for cond in conditions:
            if isinstance(cond, dict) and str(cond.get("kind") or "") == "hunger" and cond.get("id") != keep_id:
                continue
            pruned.append(cond)
        logger.info("已清理重复饥饿状态: kept=%s removed=%s", keep_id or "-", len(hunger_items) - 1)
        return pruned

    def _repair_body_cycle_conditions(self, conditions: list[Any], now: float) -> list[dict[str, Any]]:
        repaired: list[dict[str, Any]] = []
        active_cycles: list[dict[str, Any]] = []
        last_cycle_end = 0.0
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            if str(cond.get("kind") or "") != "body_cycle":
                repaired.append(cond)
                continue
            label = _single_line(cond.get("label"), 80)
            phase = str(cond.get("phase") or self._infer_body_cycle_phase(label))
            cond["phase"] = phase
            start_ts = _safe_float(cond.get("start_ts"), now)
            if start_ts <= 0:
                start_ts = now
                cond["start_ts"] = start_ts
            max_hours = self._body_cycle_max_hours(phase, label)
            max_end_ts = start_ts + max_hours * 3600
            end_ts = _safe_float(cond.get("end_ts"), max_end_ts)
            if end_ts <= 0:
                end_ts = max_end_ts
            if end_ts > max_end_ts:
                end_ts = max_end_ts
                cond["end_ts"] = end_ts
                cond["duration_hours"] = max_hours
            if not cond.get("episode_key"):
                cond["episode_key"] = f"body-cycle-{self._environment_fromtimestamp(start_ts).strftime('%Y-%m-%d')}"
            last_cycle_end = max(last_cycle_end, end_ts)
            if start_ts <= now < end_ts:
                active_cycles.append(cond)
            repaired.append(cond)

        if len(active_cycles) > 1:
            active_cycles.sort(key=lambda item: _safe_float(item.get("start_ts"), 0), reverse=True)
            keep_id = active_cycles[0].get("id")
            filtered: list[dict[str, Any]] = []
            for cond in repaired:
                if str(cond.get("kind") or "") == "body_cycle" and cond.get("id") != keep_id:
                    cond["end_ts"] = min(_safe_float(cond.get("end_ts"), now), now - 1)
                filtered.append(cond)
            repaired = filtered

        if last_cycle_end > 0:
            meta = self.data.get("body_cycle_state")
            if not isinstance(meta, dict):
                meta = {}
            expected_ts = _safe_float(meta.get("next_expected_start_ts"), 0)
            base_start = _safe_float(meta.get("last_start_ts"), 0)
            if base_start <= 0:
                base_start = max(0.0, last_cycle_end - 4 * 86400)
            if self._advanced_cycle_enabled():
                if expected_ts <= 0:
                    expected_ts = base_start + self._advanced_cycle_total_days() * 86400
            else:
                if expected_ts <= 0 or expected_ts <= last_cycle_end:
                    expected_ts = base_start + 28 * 86400
                expected_ts = max(expected_ts, last_cycle_end + 18 * 86400)
            meta.update(
                {
                    "last_end_ts": max(_safe_float(meta.get("last_end_ts"), 0), last_cycle_end),
                    "next_expected_start_ts": expected_ts,
                }
            )
            self.data["body_cycle_state"] = meta
        return repaired

    def _reconcile_advanced_cycle_condition(self, conditions: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
        """Align the active cycle condition with the anchored continuous timeline.

        The anchor always knows the true current phase and day. When the bot
        was offline or a transition condition was spawned late, this replaces
        the stale condition with one positioned exactly on the timeline so its
        energy and mood effects never lag behind the displayed phase.

        Args:
            conditions: Currently active condition list after follow-up spawns.
            now: Current unix timestamp.

        Returns:
            The adjusted condition list.
        """
        if not self._advanced_cycle_enabled():
            return conditions
        meta = self.data.get("body_cycle_state")
        anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0) if isinstance(meta, dict) else 0
        if anchor_ts <= 0:
            return conditions
        expected_phase, day_in_phase = self._advanced_cycle_position_from_offset(
            int((now - anchor_ts) // 86400) + 1
        )
        phase_days = self._advanced_cycle_phase_days(expected_phase)
        phase_start = anchor_ts + (self._advanced_cycle_day_of_phase(expected_phase, 1) - 1) * 86400
        active_cycles = [
            cond
            for cond in conditions
            if isinstance(cond, dict)
            and str(cond.get("kind") or "") == "body_cycle"
            and _safe_float(cond.get("start_ts"), 0) <= now < _safe_float(cond.get("end_ts"), 0)
        ]
        if len(active_cycles) == 1:
            cond = active_cycles[0]
            cond_start = _safe_float(cond.get("start_ts"), 0)
            if str(cond.get("phase") or "") == expected_phase and abs(cond_start - phase_start) < 6 * 3600:
                return conditions
        kept = [
            cond
            for cond in conditions
            if not (isinstance(cond, dict) and str(cond.get("kind") or "") == "body_cycle")
        ]
        condition = self._advanced_cycle_condition(
            expected_phase,
            cause="周期阶段自然推进",
        )
        condition["start_ts"] = phase_start
        condition["duration_hours"] = phase_days * 24
        condition["end_ts"] = phase_start + phase_days * 24 * 3600
        kept.append(condition)
        self._record_body_cycle_episode(condition)
        logger.info(
            "已对齐六阶段周期状态: phase=%s phase_start=%s day_in_phase=%s",
            expected_phase,
            self._environment_fromtimestamp(phase_start).strftime("%Y-%m-%d %H:%M"),
            day_in_phase,
        )
        return kept

    def _spawn_followup_conditions(self, cond: dict[str, Any]) -> list[dict[str, Any]]:
        choice = self._pick_condition_transition(cond)
        if not choice or choice == "stable":
            return []
        followup = self._build_transition_condition(choice, cond)
        if isinstance(followup, dict) and str(followup.get("kind") or "") == "body_cycle":
            self._record_body_cycle_episode(followup)
        return [followup] if followup else []

    def _pick_condition_transition(self, cond: dict[str, Any]) -> str:
        options = cond.get("transition_options", [])
        if not isinstance(options, list) or not options:
            return ""
        weighted: list[tuple[str, float]] = []
        cause = _single_line(cond.get("cause"), 120)
        intensity = _safe_int(cond.get("intensity"), 50, 0, 100)
        weather_text = self._weather_summary_text(self.data.get("daily_weather", {}))
        care_notes = cond.get("care_notes", [])
        care_count = len(care_notes) if isinstance(care_notes, list) else 0
        for option in options:
            if not isinstance(option, dict):
                continue
            target = str(option.get("to") or "").strip()
            weight = float(option.get("base_weight") or 0)
            if not target or weight <= 0:
                continue
            if target == "recovery_afterglow":
                weight += min(0.22, care_count * 0.08)
                if "提醒" in cause or "用户" in cause:
                    weight += 0.06
            elif target == "health_tail":
                if intensity >= 75:
                    weight += 0.1
                if any(token in cause for token in ("透支", "失眠")):
                    weight += 0.08
                if any(token in weather_text for token in ("降雨", "小雨", "中雨", "大雨", "冷", "风")):
                    weight += 0.05
                weight -= min(0.12, care_count * 0.05)
            elif target == "sleep_afterglow":
                weight += min(0.16, care_count * 0.05)
            elif target == "sleep_tail":
                if intensity >= 80:
                    weight += 0.08
                if any(token in cause for token in ("失眠", "睡")):
                    weight += 0.04
            weighted.append((target, max(0.0, weight)))
        total = sum(weight for _, weight in weighted)
        if total <= 0:
            return ""
        pick = random.random() * total
        cursor = 0.0
        for target, weight in weighted:
            cursor += weight
            if pick <= cursor:
                return target
        return weighted[-1][0]

    def _build_transition_condition(self, target: str, cond: dict[str, Any]) -> dict[str, Any] | None:
        cause = _single_line(cond.get("cause"), 120)
        if target == "recovery_afterglow":
            label = "不适缓解后的轻度回升"
            if cause:
                label = "不适正在缓解,状态明显回升"
            return self._make_condition(
                kind="recovery_afterglow",
                title="恢复后的回弹",
                label=label,
                mood="轻快",
                energy_delta=10,
                duration_hours=12,
                intensity=68,
                cause="前序不适开始缓解",
                phase="afterglow",
            )
        if target == "health_tail":
            return self._make_condition(
                kind="health_tail",
                title="恢复尾声",
                label="整体好转,但仍有轻微虚弱残留",
                mood="平缓",
                energy_delta=-4,
                duration_hours=10,
                intensity=48,
                cause="恢复中,体力尚未完全回满",
                phase="tail",
            )
        if target == "sleep_afterglow":
            return self._make_condition(
                kind="sleep_afterglow",
                title="补回来一点精神",
                label="睡意缓解后的轻度回升",
                mood="轻松",
                energy_delta=8,
                duration_hours=8,
                intensity=60,
                cause="前序失眠或浅睡影响减弱",
                phase="afterglow",
            )
        if target == "sleep_tail":
            return self._make_condition(
                kind="sleep_tail",
                title="迟钝尾声",
                label="睡眠影响减弱,但反应仍略慢",
                mood="安静",
                energy_delta=-3,
                duration_hours=6,
                intensity=42,
                cause="睡眠债仍有轻微残留",
                phase="tail",
            )
        if target == "soft_afterglow":
            return self._make_condition(
                kind="soft_afterglow",
                title="被关心后的余温",
                label="收到关心反馈后的柔和余波",
                mood="柔和",
                energy_delta=4,
                duration_hours=4,
                intensity=48,
                cause="用户关心反馈仍有轻度影响",
                phase="afterglow",
            )
        if target == "body_period":
            return self._make_condition(
                kind="body_cycle",
                title="周期",
                label="处于生理期,身体舒适度与能量偏低",
                mood="疲惫",
                energy_delta=-18,
                duration_hours=72,
                intensity=64,
                cause="周期阶段自然推进",
                phase="period",
                episode_key=_single_line(cond.get("episode_key"), 40),
                transition_options=[
                    {"to": "body_recovery", "base_weight": 0.65},
                    {"to": "stable", "base_weight": 0.35},
                ],
            )
        if target == "body_recovery":
            return self._make_condition(
                kind="body_cycle",
                title="周期",
                label="生理期后,慢慢回到稳定状态",
                mood="松弛",
                energy_delta=-5,
                duration_hours=24,
                intensity=48,
                cause="周期阶段自然推进",
                phase="recovery",
                episode_key=_single_line(cond.get("episode_key"), 40),
                transition_options=[{"to": "stable", "base_weight": 1.0}],
            )
        advanced_targets = {
            "body_menstrual": "menstrual",
            "body_follicular": "follicular",
            "body_pre_ovulation": "pre_ovulation",
            "body_ovulation": "ovulation",
            "body_luteal": "luteal",
            "body_pms": "pms",
        }
        if target in advanced_targets and self._advanced_cycle_enabled():
            return self._advanced_cycle_condition(
                advanced_targets[target],
                episode_key=_single_line(cond.get("episode_key"), 40),
            )
        return None

    def _get_active_conditions(self) -> list[dict[str, Any]]:
        now = _now_ts()
        conditions = self.data.get("state_conditions", [])
        if not isinstance(conditions, list):
            return []
        active = []
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            start_ts = _safe_float(cond.get("start_ts"), 0)
            end_ts = _safe_float(cond.get("end_ts"), 0)
            if start_ts <= now < end_ts:
                active.append(cond)
        return active

    def _compose_state_from_conditions(self, weather: dict[str, Any] | None = None) -> dict[str, Any]:
        profile = self._persona_state_profile()
        active = [
            cond for cond in self._get_active_conditions()
            if self._state_condition_allowed(str(cond.get("kind") or ""), profile)
        ]
        values = self._base_state_values(profile)
        weather_text = self._weather_summary_text(weather)
        energy = 75
        composed_at = _now_ts()
        mood_candidates = []
        health_cause = ""
        for cond in active:
            kind = str(cond.get("kind") or "")
            if kind in values:
                values[kind] = _single_line(cond.get("label"), 80)
            energy += self._condition_effective_energy_delta(cond, now=composed_at)
            mood = _single_line(cond.get("mood"), 20)
            if mood and mood != "平稳":
                intensity = _safe_int(cond.get("intensity"), 50, 0, 100)
                if kind == "memory_afterglow":
                    intensity = max(0, round(intensity * self._memory_afterglow_decay(cond, now=composed_at)))
                mood_candidates.append((mood, intensity))
            if kind == "health" and not health_cause:
                health_cause = _single_line(cond.get("cause"), 120)
        remembered_dream = self._remembered_daily_dream_label()
        if values.get("dream") == "没有记住梦" and remembered_dream:
            values["dream"] = remembered_dream
        existing_state = self.data.get("daily_state")
        existing_override_ts = 0.0
        if isinstance(existing_state, dict) and existing_state.get("date") == _today_key():
            existing_override_ts = _safe_float(existing_state.get("location_override_ts"), 0)
        override_active = existing_override_ts > 0 and _now_ts() - existing_override_ts < 4 * 3600
        if override_active:
            inferred_location = self._current_location_state_text(existing_state)
        else:
            inferred_location = self._current_location_state_text({"location": values.get("location", "")})
        if inferred_location:
            values["location"] = inferred_location
        energy = max(10, min(100, energy))
        mood_bias = (
            sorted(mood_candidates, key=lambda item: item[1], reverse=True)[0][0]
            if mood_candidates else "平稳"
        )
        cycle_runtime: dict[str, Any] = {}
        if self._advanced_cycle_enabled() and profile.get("allow_cycle", False):
            cycle_runtime = self._advanced_cycle_runtime()
            if cycle_runtime:
                values["body_cycle"] = (
                    f"{cycle_runtime.get('phase_name', '周期')} 第{cycle_runtime.get('day_in_phase', 1)}天"
                )
                discomfort = self._active_cycle_discomfort_conditions()
                if discomfort:
                    cycle_runtime["discomfort"] = discomfort
        note = self._build_state_note(
            values["sleep"],
            values["dream"],
            values["health"],
            values["hunger"],
            values["body_cycle"],
            weather_text,
            mood_bias,
            energy,
            health_cause,
        )
        result = {
            "date": _today_key(),
            **values,
            "weather": weather_text,
            "mood_bias": mood_bias,
            "energy": energy,
            "note": note,
            "cycle_runtime": cycle_runtime,
            "conditions": active,
            "affect_modulation": compose_affect_modulation(active, now=composed_at),
        }
        if override_active:
            result["location_override_ts"] = existing_override_ts
            result["location_source"] = "dialogue_override"
        return result

    @staticmethod
    def _memory_afterglow_decay(cond: dict[str, Any], *, now: float) -> float:
        if str(cond.get("kind") or "") != "memory_afterglow":
            return 1.0
        start_ts = _safe_float(cond.get("start_ts"), now)
        half_life = max(60.0, min(86400.0, _safe_float(cond.get("half_life_seconds"), 1800.0)))
        age = max(0.0, now - start_ts)
        return max(0.0, min(1.0, 0.5 ** (age / half_life)))

    def _condition_effective_energy_delta(self, cond: dict[str, Any], *, now: float) -> int:
        base = _safe_int(cond.get("energy_delta"), 0, -100, 100)
        if str(cond.get("kind") or "") != "memory_afterglow":
            return base
        return int(round(base * self._memory_afterglow_decay(cond, now=now)))

    def _build_state_note(
        self,
        sleep: str,
        dream: str,
        health: str,
        hunger: str,
        body_cycle: str,
        weather: str,
        mood_bias: str,
        energy: int,
        health_cause: str = "",
    ) -> str:
        if energy < 35:
            pace = "今天能量很低,日程应更轻、更慢,主动消息也要更短。"
        elif energy < 55:
            pace = "今天能量偏低,适合少量任务和更多停顿。"
        elif energy > 80:
            pace = "今天能量不错,可以安排一些需要专注的事情。"
        else:
            pace = "今天能量中等,适合保持温和节奏。"
        weather_text = str(weather or "").strip()
        weather_text = weather_text.rstrip("。！？!?,,；; ")
        weather_part = f"天气：{weather_text}。" if weather_text and weather_text != "暂无天气信息" else ""
        cause_part = f" 身体不太舒服更像是因为{health_cause}。" if health_cause else ""
        detail_parts = []
        if sleep and sleep not in {"睡眠平稳", "睡得很踏实"}:
            detail_parts.append(f"睡眠：{sleep}")
        if dream and dream != "没有记住梦":
            detail_parts.append(f"梦境：{dream}")
        if health and health != "状态正常" and not self._is_inapplicable_state_text(health):
            detail_parts.append(f"健康：{health}")
        if hunger and hunger not in {"饥饿感平稳", "无饥饿感"} and not self._is_inapplicable_state_text(hunger):
            detail_parts.append(f"饥饿：{hunger}")
        if body_cycle and body_cycle not in {"无明显周期影响", "不处于生理期"} and not self._is_inapplicable_state_text(body_cycle):
            detail_parts.append(f"周期：{body_cycle}")
        detail_text = (" " + "；".join(detail_parts) + "。") if detail_parts else ""
        return (
            f"{pace} 情绪底色偏{mood_bias}。"
            f"{weather_part}{cause_part}"
            f"{detail_text}"
        )

    def _is_daily_plan_due(self) -> bool:
        plan_minutes = self._parse_hhmm_to_minutes(runtime_persona_setting(self, "daily_plan_time", "07:30"))
        if plan_minutes is None:
            plan_minutes = 7 * 60 + 30
        now = self._environment_now()
        return now.hour * 60 + now.minute >= plan_minutes

    def _daily_plan_due_minutes(self) -> int:
        plan_minutes = self._parse_hhmm_to_minutes(runtime_persona_setting(self, "daily_plan_time", "07:30"))
        if plan_minutes is None:
            return 7 * 60 + 30
        return plan_minutes

    def _is_plan_date_active(self, plan_date: str) -> bool:
        plan_date = str(plan_date or "").strip()
        if not plan_date:
            return False
        today = self._environment_now().date()
        today_key = _date_key(today)
        if plan_date == today_key:
            return True
        yesterday_key = _date_key(today - timedelta(days=1))
        if plan_date != yesterday_key:
            return False
        now_minutes = self._environment_now_minutes()
        return now_minutes < self._daily_plan_due_minutes()

    def _get_active_plan(self) -> dict[str, Any]:
        plan = self.data.get("daily_plan", {})
        if isinstance(plan, dict) and self._is_plan_date_active(plan.get("date")):
            return plan
        return {}

    def _effective_plan_now_minutes(self, plan_date: str) -> int | None:
        plan_date = str(plan_date or "").strip()
        if not self._is_plan_date_active(plan_date):
            return None
        now_minutes = self._environment_now_minutes()
        if plan_date == _today_key():
            return now_minutes
        return 24 * 60 + now_minutes

    def _is_sleepy_plan_item(self, item: dict[str, Any] | None) -> bool:
        if not isinstance(item, dict):
            return False
        text = " ".join(
            _single_line(item.get(key), 100)
            for key in ("activity", "mood", "message_seed")
            if _single_line(item.get(key), 100)
        )
        if not text:
            return False
        if re.search(r"继续睡|睡回去|重新入睡|再次入睡|回笼觉", text):
            return True
        if re.search(
            r"自然醒|睡醒|醒来|醒后|刚醒|醒了|已醒|醒着|清醒|睁眼|起床|起身|洗漱|"
            r"不睡|没睡|未睡|还没睡|睡不着|失眠",
            text,
        ):
            return False
        return bool(
            re.search(
                r"睡觉|睡眠|入睡|熟睡|浅睡|午睡|午休|小睡|补觉|回笼觉|打盹|"
                r"眯(?:一|半)?会(?:儿)?|梦乡|被窝|准备睡|睡前|继续睡|睡回去|熄灯休息",
                text,
            )
        )

    def _segment_end_minutes(
        self,
        start: int,
        item: dict[str, Any] | None,
        *,
        next_start: int | None = None,
    ) -> int:
        if next_start is not None:
            return next_start
        if self._is_sleepy_plan_item(item):
            return min(24 * 60 + 240, start + 240)
        return min(24 * 60 + 120, start + 180)

    def _plan_item_end_minutes(
        self,
        start: int,
        item: dict[str, Any] | None,
        *,
        next_start: int | None = None,
    ) -> int:
        explicit = self._parse_hhmm_to_minutes((item or {}).get("end")) if isinstance(item, dict) else None
        if explicit is not None:
            if explicit <= start:
                explicit += 24 * 60
            duration = explicit - start
            if 10 <= duration <= 12 * 60:
                if next_start is not None:
                    normalized_next = next_start + (24 * 60 if next_start <= start else 0)
                    explicit = min(explicit, normalized_next)
                return explicit
        if next_start is not None:
            return next_start + (24 * 60 if next_start <= start else 0)
        return self._segment_end_minutes(start, item)

    def _normalized_plan_item_starts(self, items: Any) -> list[int | None]:
        if not isinstance(items, list):
            return []
        normalized: list[int | None] = []
        day_offset = 0
        previous_raw: int | None = None
        for item in items:
            raw = self._parse_hhmm_to_minutes(item.get("time")) if isinstance(item, dict) else None
            if raw is None:
                normalized.append(None)
                continue
            if previous_raw is not None and raw < previous_raw:
                day_offset += 24 * 60
            normalized.append(raw + day_offset)
            previous_raw = raw
        return normalized

    def _normalize_plan_item_intervals(self, items: Any) -> bool:
        if not isinstance(items, list):
            return False
        starts = self._normalized_plan_item_starts(items)
        changed = False
        for index, item in enumerate(items):
            if not isinstance(item, dict) or starts[index] is None:
                continue
            start = int(starts[index])
            next_start = next((value for value in starts[index + 1 :] if value is not None), None)
            end = self._plan_item_end_minutes(start, item, next_start=next_start)
            end_text = self._minutes_to_hhmm(end)
            if _single_line(item.get("end"), 8) != end_text:
                item["end"] = end_text
                changed = True
            lifecycle = _single_line(item.get("lifecycle_status"), 20).lower()
            if lifecycle not in {"planned", "changed", "cancelled", "deferred"}:
                item["lifecycle_status"] = "planned"
                changed = True
            basis = self._normalize_schedule_basis(item.get("basis"), default=["coarse_plan"])
            if item.get("basis") != basis:
                item["basis"] = basis
                changed = True
            confidence = min(1.0, _safe_float(item.get("confidence"), 0.72))
            if item.get("confidence") != confidence:
                item["confidence"] = confidence
                changed = True
        return changed

    @staticmethod
    def _normalize_schedule_lifecycle_status(value: Any) -> str:
        aliases = {
            "planned": "planned", "计划": "planned", "未开始": "planned",
            "active": "active", "进行": "active", "进行中": "active",
            "completed": "completed", "完成": "completed", "已完成": "completed",
            "changed": "changed", "变更": "changed", "已变更": "changed",
            "cancelled": "cancelled", "canceled": "cancelled", "取消": "cancelled", "已取消": "cancelled",
            "deferred": "deferred", "postponed": "deferred", "顺延": "deferred", "延期": "deferred",
        }
        return aliases.get(_single_line(value, 20).lower(), "")

    @staticmethod
    def _normalize_schedule_basis(value: Any, *, default: list[str] | None = None) -> list[str]:
        allowed = {"calendar", "persona", "adjustment", "state", "weather", "continuity", "inspiration", "coarse_plan"}
        raw = value if isinstance(value, list) else re.split(r"[,，;；\s]+", str(value or ""))
        result: list[str] = []
        for item in raw:
            key = _single_line(item, 24).lower()
            if key in allowed and key not in result:
                result.append(key)
        return result[:3] or list(default or [])[:3]

    def _schedule_window_runtime_status(
        self,
        start: int,
        end: int,
        *,
        plan_date: str = "",
        explicit_status: Any = "",
    ) -> str:
        explicit = self._normalize_schedule_lifecycle_status(explicit_status)
        if explicit == "cancelled":
            return explicit
        date_text = _single_line(plan_date, 16)
        now_minutes = self._effective_plan_now_minutes(date_text) if date_text else self._environment_now_minutes()
        if now_minutes is None:
            today = _today_key()
            return "completed" if date_text and date_text < today else "planned"
        normalized_end = int(end)
        if normalized_end <= start:
            normalized_end += 24 * 60
        if now_minutes < start:
            runtime = "planned"
        elif now_minutes >= normalized_end:
            runtime = "completed"
        else:
            runtime = "active"
        if explicit == "changed" and runtime != "completed":
            return "changed"
        return runtime

    def _plan_item_runtime_status(self, plan: dict[str, Any], item: dict[str, Any], index: int = -1) -> str:
        # Lifecycle display must come from canonical evidence, never from the
        # clock alone.  Keep the legacy helper signature for callers, but map
        # old lifecycle values through a conservative planned/unknown view.
        if isinstance(item, dict):
            legacy = self._normalize_schedule_lifecycle_status(item.get("lifecycle_status"))
            if legacy == "cancelled":
                return "cancelled"
            if legacy == "changed":
                return "changed"
            if legacy == "deferred":
                return "deferred"
            evidence = _single_line(item.get("evidence_kind"), 48).lower()
            eligibility = _single_line(item.get("fact_eligibility"), 48).lower()
            status = _single_line(item.get("status"), 32).lower()
            if evidence in {"interaction", "tool_action", "external_record"} and eligibility in {"current_observed", "history_observed"}:
                if status in {"active", "completed", "partially_completed"}:
                    return status
            if evidence == "self_state_commit" and eligibility == "current_internal":
                return "active"
            plan_date = str((plan or {}).get("date") or item.get("date") or "")
            try:
                canonical = normalize_plan_item(
                    {**item, "date": plan_date or _today_key(), "subject_actor_id": item.get("subject_actor_id") or "bot_self"},
                    plan_id=str(item.get("plan_id") or ""),
                    now=self._environment_now(),
                )
                phase = _single_line(canonical.get("temporal_phase"), 16).lower()
                if phase == "past":
                    return "unknown"
                return "planned"
            except Exception:
                return "planned"
        items = plan.get("items") if isinstance(plan, dict) else None
        starts = self._normalized_plan_item_starts(items)
        start = starts[index] if isinstance(items, list) and 0 <= index < len(starts) else self._parse_hhmm_to_minutes(item.get("time"))
        if start is None:
            return "planned"
        next_start = None
        if isinstance(items, list) and index >= 0:
            next_start = next((value for value in starts[index + 1 :] if value is not None), None)
        end = self._plan_item_end_minutes(start, item, next_start=next_start)
        return self._schedule_window_runtime_status(
            start,
            end,
            plan_date=str((plan or {}).get("date") or ""),
            explicit_status=item.get("lifecycle_status"),
        )

    def _plan_item_display_status(self, plan: dict[str, Any], item: dict[str, Any], index: int = -1) -> str:
        """Return the user-facing clock phase without upgrading it to execution evidence."""

        canonical = self._plan_item_runtime_status(plan, item, index)
        if canonical in {"cancelled", "deferred", "overridden", "active", "completed", "partially_completed"}:
            return canonical
        items = plan.get("items") if isinstance(plan, dict) else None
        starts = self._normalized_plan_item_starts(items)
        start = starts[index] if isinstance(items, list) and 0 <= index < len(starts) else self._parse_hhmm_to_minutes(item.get("time"))
        if start is None:
            return canonical or "planned"
        next_start = next((value for value in starts[index + 1 :] if value is not None), None) if index >= 0 else None
        end = self._plan_item_end_minutes(start, item, next_start=next_start)
        return self._schedule_window_runtime_status(
            start,
            end,
            plan_date=str((plan or {}).get("date") or item.get("date") or ""),
            explicit_status=item.get("lifecycle_status"),
        )

    def _parse_hhmm_to_minutes(self, value: Any) -> int | None:
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            return None
        return hour * 60 + minute

    def _minutes_to_hhmm(self, minutes: int) -> str:
        minutes = max(0, int(minutes))
        wrapped = minutes % (24 * 60)
        return f"{wrapped // 60:02d}:{wrapped % 60:02d}"

    async def _generate_daily_plan(self) -> dict[str, Any]:
        await self._ensure_yesterday_conversation_summary()
        await self._ensure_yesterday_screen_diary_context()
        await self._maybe_settle_skill_growth(force=True)
        return await generate_daily_plan(self)

    def _get_schedule_planning_prompt(self) -> str:
        return get_schedule_planning_prompt(self)

    def _build_daily_plan_prompt(self, now: str, memory_companion_context: str = "") -> str:
        return build_daily_plan_prompt(self, now, memory_companion_context=memory_companion_context)

    async def _ensure_yesterday_conversation_summary(self, force: bool = False) -> dict[str, Any]:
        today = _today_key()
        cached = self.data.get("yesterday_conversation_summary", {})
        if (
            isinstance(cached, dict)
            and cached.get("date") == today
            and cached.get("scope") == "owner_private_only"
            and not force
        ):
            return cached
        raw_text = await self._collect_yesterday_conversation_text()
        if not raw_text:
            summary = {
                "date": today,
                "source_date": _date_key(date.today() - timedelta(days=1)),
                "summary": "暂无可用的昨日完整对话摘要。",
                "residues": [],
                "schedule_reference": "无明确可继承影响。",
                "dream_reference": "无明确可继承碎片。",
                "scope": "owner_private_only",
                "raw_excerpt_chars": 0,
            }
        else:
            summary = await self._summarize_yesterday_conversation_for_schedule(raw_text)
        async with self._data_lock:
            self.data["yesterday_conversation_summary"] = summary
            self._save_data_sync(sections={"yesterday_conversation_summary"})
        return summary

    async def _collect_yesterday_conversation_text(self) -> str:
        users = self.data.get("users", {})
        if not isinstance(users, dict):
            return ""
        now_dt = self._environment_now()
        yesterday = now_dt.date() - timedelta(days=1)
        start = datetime.combine(yesterday, datetime.min.time(), tzinfo=now_dt.tzinfo).timestamp()
        end = start + 24 * 3600
        blocks: list[str] = []
        for user_id, raw_user in users.items():
            if not isinstance(raw_user, dict):
                continue
            if self._private_user_role(raw_user, str(user_id)) != "owner":
                continue
            umo = str(raw_user.get("umo") or "").strip()
            if not umo:
                continue
            try:
                getter = getattr(self, "_get_current_conversation_safely", None)
                if callable(getter):
                    conv = await getter(umo, label="yesterday_conversation_read")
                else:
                    conv_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
                    if not conv_id:
                        continue
                    conv = await self.context.conversation_manager.get_conversation(umo, conv_id)
            except Exception as exc:
                logger.debug("读取昨日对话失败: user=%s err=%s", user_id, exc)
                continue
            if not conv:
                continue
            history = self._load_conversation_history_items(conv)
            dated_lines: list[str] = []
            undated_lines: list[str] = []
            for item in history:
                line = self._format_history_item_for_summary(item)
                if not line:
                    continue
                ts = self._history_item_timestamp(item)
                if ts is None:
                    undated_lines.append(line)
                elif start <= ts < end:
                    dated_lines.append(line)
            selected = dated_lines if dated_lines else undated_lines[-120:]
            if not selected:
                continue
            name = _single_line(raw_user.get("nickname") or user_id, 30)
            source_note = "昨日对话" if dated_lines else "最近对话（history 无时间戳,作为昨日摘要候选）"
            blocks.append(f"【主要用户:{name}｜{source_note}】\n" + "\n".join(selected))
        return "\n\n".join(blocks).strip()[-18000:]

    def _load_conversation_history_items(self, conversation: Conversation | None) -> list[dict[str, Any]]:
        if conversation is None:
            return []
        try:
            loaded = json.loads(conversation.history or "[]")
        except Exception:
            return []
        if not isinstance(loaded, list):
            return []
        return [item for item in loaded if isinstance(item, dict)]

    @staticmethod
    def _daily_proactive_archive_context_text(text: str) -> bool:
        if not text:
            return False
        raw = str(text)
        compact = re.sub(r"\s+", "", raw).lower()
        lowered = raw.lower()
        if "主动承接占位" in raw and ("用户还没发来新消息" in raw or "bot主动" in compact):
            return True
        if "这不是用户消息" in raw and "private companion" in lowered and "主动消息" in raw:
            return True
        if "[主动消息]" in raw or "【主动消息】" in raw:
            legacy_markers = ("触发原因", "行为结果", "内部动机", "动作摘要")
            if sum(1 for marker in legacy_markers if marker in raw) >= 2:
                return True
        return False

    def _history_item_timestamp(self, item: dict[str, Any]) -> float | None:
        for key in ("timestamp", "time", "created_at", "updated_at", "created", "date"):
            value = item.get(key)
            if value is None or value == "":
                continue
            numeric = _safe_float(value, 0)
            if numeric > 0:
                return numeric / 1000 if numeric > 10_000_000_000 else numeric
            text = str(value).strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m-%d %H:%M:%S", "%m-%d %H:%M"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    if fmt.startswith("%m"):
                        parsed = parsed.replace(year=date.today().year)
                    return parsed.timestamp()
                except Exception:
                    continue
        return None

    def _format_history_item_for_summary(self, item: dict[str, Any]) -> str:
        role = _single_line(item.get("role") or item.get("type") or item.get("speaker"), 20).lower()
        if role in {"assistant", "bot", "ai"}:
            speaker = f"{runtime_persona_setting(self, 'bot_name', '小星')}(Bot回复)"
        elif role in {"user", "human"}:
            speaker = "用户"
        else:
            speaker = role or "对话"
        content = self._history_item_content_text(item)
        if not content:
            return ""
        if self._daily_proactive_archive_context_text(content):
            return ""
        ts = self._history_item_timestamp(item)
        time_prefix = self._environment_fromtimestamp(ts).strftime("%m-%d %H:%M") + " " if ts else ""
        return f"{time_prefix}{speaker}: {content}"

    def _history_item_content_text(self, item: dict[str, Any]) -> str:
        value = item.get("content")
        if value is None:
            value = item.get("message") or item.get("text") or item.get("content_text")
        if isinstance(value, str):
            return _single_line(value, 260)
        if isinstance(value, list):
            parts: list[str] = []
            for part in value:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    text = part.get("text") or part.get("content") or part.get("message")
                    if text:
                        parts.append(str(text))
                    elif str(part.get("type") or "").lower() == "image":
                        parts.append("[图片]")
            return _single_line(" ".join(parts), 260)
        if isinstance(value, dict):
            return _single_line(value.get("text") or value.get("content") or json.dumps(value, ensure_ascii=False), 260)
        return ""

    async def _summarize_yesterday_conversation_for_schedule(self, raw_text: str) -> dict[str, Any]:
        today = _today_key()
        source_date = _date_key(date.today() - timedelta(days=1))
        prompt = f"""
请阅读下面的昨日/最近完整对话材料,为今天的日程和梦境生成提炼参考摘要。

目标不是复述聊天,而是找出可能延续到今天的“残留影响”：身体状态、饮食/作息、情绪余波、关系变化、未完成约定、收到/送出的东西、外出计划、压力来源、被安慰/被打断的事、梦境可能用到的物件/颜色/气味/半句话等。

重要原则：
1. 只根据对话内容做合理推断,不要硬套固定事件类型。
2. 如果某个行为可能带来身体或日程后果,用抽象逻辑表达：饮食、睡眠、天气、运动、情绪刺激、约定、礼物、争执、安慰等都可能改变今天的体力、胃口、心情、出门意愿或主动话题。
3. 影响可以很轻,也可以没有。不要为了制造剧情强行让今天出事。
4. 摘要要给日程模型用,所以写成可执行参考,不是聊天回复。
5. 梦境参考只提炼碎片和情绪质感,不要编完整梦。
6. 饮食偏好要有衰退：某个菜名、零食或口味反复出现时,只当“近期聊过/需要避错”的软背景,不要要求今天继续安排购买、带饭、留一份或一起吃。用户说“不吃/不喜欢/不要/避开某食物”时,只写成“相关时避开该食物”,不要写成今天必须准备替代餐食。
7. 严格区分说话人：只有“用户”行能写成用户真实信息；“Bot回复”里的我在做什么、身体/心情/日程、动作描写或生活片段，只能视为 Bot 当时的拟人化表达，不能当作用户事实、现实证据或今天必须继承的事件。
8. 只有用户明确确认、提出或约定的事，才可以进入计划/未完成约定；Bot 自称的吃饭、整理、犯困、走动、创作等状态不要转成稳定记忆或现实日程。

对话材料：
{raw_text}

只输出 JSON：
{{
  "summary": "昨日对话的一句话概括",
  "residues": [
    {{"type": "身体/情绪/关系/计划/物件/梦境碎片", "content": "可延续影响", "strength": "轻/中/强"}}
  ],
  "schedule_reference": "今天生成日程时应如何自然继承这些残留；没有就写无明确影响",
  "dream_reference": "今天梦境/梦境碎片可以参考的物件、感官、半句话或情绪；没有就写无明确碎片"
}}
""".strip()
        raw = await self._llm_call(
            prompt,
            max_tokens=650,
            provider_id=self._task_provider(
                runtime_persona_setting(self, "history_summary_provider_id", ""),
                runtime_persona_setting(self, "daily_plan_provider_id", ""),
                runtime_persona_setting(self, "mai_style_provider_id", ""),
            ),
            task="yesterday_summary",
        )
        payload = self._extract_json_payload(raw or "")
        if not isinstance(payload, dict):
            return {
                "date": today,
                "source_date": source_date,
                "summary": _single_line(raw_text, 180) or "昨日对话有记录,但摘要生成失败。",
                "residues": [],
                "schedule_reference": "可把昨日互动作为轻微关系和情绪背景,不要强行改写今日主线。",
                "dream_reference": "可从昨日对话里的物件、语气和半句话提取梦境碎片。",
                "scope": "owner_private_only",
                "raw_excerpt_chars": len(raw_text),
            }
        residues = payload.get("residues", [])
        if not isinstance(residues, list):
            residues = []
        normalized_residues = []
        for item in residues[:8]:
            if not isinstance(item, dict):
                continue
            content = _single_line(item.get("content"), 120)
            if not content:
                continue
            normalized_residues.append({
                "type": _single_line(item.get("type"), 24) or "残留",
                "content": content,
                "strength": _single_line(item.get("strength"), 8) or "轻",
            })
        return {
            "date": today,
            "source_date": source_date,
            "summary": _single_line(payload.get("summary"), 180) or "昨日对话有一些可延续的情绪和生活残留。",
            "residues": normalized_residues,
            "schedule_reference": _single_line(payload.get("schedule_reference"), 220) or "作为轻微背景承接,不要强行改写今日主线。",
            "dream_reference": _single_line(payload.get("dream_reference"), 220) or "从昨日对话的物件、感官和半句话中轻取梦境碎片。",
            "scope": "owner_private_only",
            "raw_excerpt_chars": len(raw_text),
        }

    def _format_yesterday_conversation_summary_for_prompt(self) -> str:
        summary = self.data.get("yesterday_conversation_summary", {})
        if not isinstance(summary, dict) or summary.get("date") != _today_key():
            return "暂无昨日完整对话摘要。"
        schedule_reference = self._decay_schedule_food_reference_text(
            summary.get("schedule_reference"),
            field="yesterday_conversation.schedule_reference",
        )
        dream_reference = self._decay_schedule_food_reference_text(
            summary.get("dream_reference"),
            field="yesterday_conversation.dream_reference",
            dream=True,
        )
        lines = [
            f"来源日期：{summary.get('source_date') or '昨日'}",
            f"概括：{_single_line(summary.get('summary'), 180)}",
            f"日程参考：{schedule_reference}",
            f"梦境参考：{dream_reference}",
        ]
        residues = summary.get("residues", [])
        if isinstance(residues, list) and residues:
            lines.append("残留变量：")
            for item in residues[:8]:
                if not isinstance(item, dict):
                    continue
                content = self._decay_schedule_food_reference_text(
                    item.get("content"),
                    field="yesterday_conversation.residue",
                )
                if content:
                    lines.append(f"- {item.get('type') or '残留'}｜{content}｜强度 {item.get('strength') or '轻'}")
        return "\n".join(lines)

    @staticmethod
    def _schedule_food_reference_has_negative_preference(text: str) -> bool:
        if not text:
            return False
        negative_markers = ("不吃", "不喜欢", "不想吃", "不要", "别吃", "避开", "换别的", "代替", "别准备", "不要准备")
        food_markers = (
            "饭", "餐", "菜", "食物", "吃的", "早餐", "午饭", "晚饭", "夜宵", "零食",
            "排骨", "糖醋", "螺蛳粉", "锅包肉", "烤肠", "豆花", "冰粉", "甜口",
        )
        return any(token in text for token in negative_markers) and any(token in text for token in food_markers)

    @staticmethod
    def _schedule_food_reference_is_concrete_motif(text: str) -> bool:
        if not text:
            return False
        food_markers = (
            "糖醋排骨", "排骨", "螺蛳粉", "锅包肉", "烤肠", "豆花", "冰粉", "甜口",
            "桂花", "奶茶", "豆浆", "夜宵", "饭团", "便当",
        )
        action_markers = ("准备", "带", "买", "做", "留", "夹", "点", "抢", "一起吃", "约饭", "饭")
        return any(food in text for food in food_markers) and any(action in text for action in action_markers)

    def _decay_schedule_food_reference_text(self, text: Any, *, field: str = "", dream: bool = False) -> str:
        source = _single_line(text, 260)
        if not source:
            return ""
        if dream:
            if self._schedule_food_reference_is_concrete_motif(source):
                return (
                    "梦境里可保留少量气味或颜色质感,但具体菜名属于近期高频意象,不要让它反推今天的餐食安排。"
                )
            return source
        clauses = [part.strip() for part in re.split(r"[；;。]+", source) if _single_line(part, 160)]
        if not clauses:
            clauses = [source]
        changed = False
        kept: list[str] = []
        added_guard = False
        for clause in clauses:
            cleaned = _single_line(clause, 180)
            if self._schedule_food_reference_has_negative_preference(cleaned):
                changed = True
                if not added_guard:
                    kept.append("若今天自然聊到餐食,只记得避开对方明确不吃的食物；不要为了这个避雷主动安排带饭、备餐或替代餐食剧情")
                    added_guard = True
                continue
            if self._schedule_food_reference_is_concrete_motif(cleaned):
                changed = True
                if not added_guard:
                    kept.append("具体食物只作近期聊过的软背景,不要连续复刻成今日午饭、带饭、留一份或邀约")
                    added_guard = True
                continue
            kept.append(cleaned)
        result = "；".join(part for part in kept if part).strip("；; ")
        if changed:
            logger.info(
                "已降级日程饮食参考: field=%s before=%s after=%s",
                field or "-",
                _single_line(source, 120),
                _single_line(result, 120),
            )
        return result or "只作轻微背景承接,不要强行改写今日主线。"

    def _build_detail_enhancement_prompt(
        self,
        segment: dict[str, Any],
        plan: dict[str, Any],
        state: dict[str, Any],
        memory_companion_context: str = "",
    ) -> str:
        return build_detail_enhancement_prompt(self, segment, plan, state, memory_companion_context=memory_companion_context)

    @staticmethod
    def _persona_prompt_cache_scope(umo: str = "", specific_id: str = "") -> str:
        if specific_id:
            return f"persona:{specific_id}"
        if umo:
            return f"session:{umo}"
        return "default"

    def _cached_persona_prompt_for_scope(self, umo: str = "", specific_id: str = "") -> tuple[str, float]:
        scope = self._persona_prompt_cache_scope(umo, specific_id)
        entries = getattr(self, "_default_persona_prompt_cache_by_scope", None)
        if isinstance(entries, dict):
            entry = entries.get(scope)
            if isinstance(entry, dict):
                return (
                    str(entry.get("prompt") or "").strip(),
                    _safe_float(entry.get("cached_at"), 0.0),
                )
        return "", 0.0

    def _store_persona_prompt_for_scope(self, prompt: str, *, umo: str = "", specific_id: str = "") -> str:
        cleaned = str(prompt or "").strip()
        if not cleaned:
            return ""
        entries = getattr(self, "_default_persona_prompt_cache_by_scope", None)
        if not isinstance(entries, dict):
            entries = {}
            self._default_persona_prompt_cache_by_scope = entries
        now = _now_ts()
        entries[self._persona_prompt_cache_scope(umo, specific_id)] = {
            "prompt": cleaned,
            "cached_at": now,
            "umo": umo,
            "persona_id": specific_id,
        }
        if len(entries) > 64:
            newest = sorted(
                entries.items(),
                key=lambda item: _safe_float(item[1].get("cached_at"), 0.0) if isinstance(item[1], dict) else 0.0,
                reverse=True,
            )[:64]
            self._default_persona_prompt_cache_by_scope = dict(newest)
        # Keep legacy fields synchronized for code paths that do not have a session key.
        self._default_persona_prompt_cache = cleaned
        self._default_persona_prompt_cache_at = now
        self._default_persona_prompt_cache_umo = umo
        self._default_persona_prompt_cache_persona_id = specific_id
        return cleaned

    def _get_default_persona_prompt(self, umo: str = "") -> str:
        specific_id = str(getattr(self, "_effective_plugin_persona_id", lambda: getattr(self, "plugin_specific_persona_id", ""))() or "").strip()
        scoped, _ = self._cached_persona_prompt_for_scope(umo, specific_id)
        if scoped:
            return scoped
        cached = str(getattr(self, "_default_persona_prompt_cache", "") or "").strip()
        cached_persona_id = str(getattr(self, "_default_persona_prompt_cache_persona_id", "") or "")
        cached_umo = str(getattr(self, "_default_persona_prompt_cache_umo", "") or "")
        if cached and (
            (specific_id and cached_persona_id == specific_id)
            or (not specific_id and not cached_persona_id and (not umo or cached_umo == umo))
        ):
            return cached
        return DEFAULT_PERSONA_PROMPT_FALLBACK

    def _extract_default_persona_prompt(self, persona: Any) -> str:
        if isinstance(persona, dict):
            return str(persona.get("prompt") or "").strip()
        if isinstance(persona, str):
            return persona.strip()
        for attr in ("prompt", "system_prompt", "content"):
            try:
                value = getattr(persona, attr, None)
            except Exception:
                value = None
            text = str(value or "").strip()
            if text:
                return text
        return ""

    async def _refresh_default_persona_prompt(self, umo: str = "") -> str:
        def _cancel_requested() -> bool:
            # A database/manager implementation may raise CancelledError for
            # its own failed lookup. Preserve cancellation requested for the
            # plugin task itself so shutdown remains responsive.
            try:
                task = asyncio.current_task()
                return bool(task is not None and task.cancelling())
            except RuntimeError:
                return False

        try:
            specific_id = str(getattr(self, "_effective_plugin_persona_id", lambda: getattr(self, "plugin_specific_persona_id", ""))() or "").strip()
            cached, cached_at = self._cached_persona_prompt_for_scope(umo, specific_id)
            if not cached:
                legacy_cached = str(getattr(self, "_default_persona_prompt_cache", "") or "").strip()
                legacy_umo = str(getattr(self, "_default_persona_prompt_cache_umo", "") or "")
                legacy_persona_id = str(getattr(self, "_default_persona_prompt_cache_persona_id", "") or "")
                if (
                    (specific_id and legacy_persona_id == specific_id)
                    or (not specific_id and not legacy_persona_id and (not umo or legacy_umo == umo))
                ):
                    cached = legacy_cached
                    cached_at = _safe_float(getattr(self, "_default_persona_prompt_cache_at", 0.0), 0.0)
            cache_fresh = cached and (_now_ts() - cached_at < 300.0)
            if cache_fresh:
                return cached

            manager = getattr(getattr(self, "context", None), "persona_manager", None)
            if manager and specific_id:
                try:
                    specific_getter = getattr(manager, "get_persona", None)
                    if callable(specific_getter):
                        result = await self._await_framework_db_query(
                            f"persona:{specific_id}",
                            lambda: specific_getter(specific_id),
                            timeout=2.0,
                        )
                        prompt = self._extract_default_persona_prompt(result)
                        if prompt:
                            return self._store_persona_prompt_for_scope(prompt, umo=umo, specific_id=specific_id)
                except asyncio.CancelledError:
                    if _cancel_requested():
                        raise
                    logger.debug(
                        "指定人格查询被管理器取消(ID: %s),本轮使用缓存人格",
                        specific_id,
                    )
                    return cached or self._get_default_persona_prompt(umo)
                except (sqlite3.OperationalError, sqlite3.ProgrammingError) as exc:
                    logger.debug(
                        "指定人格数据库暂不可用(ID: %s),本轮使用缓存人格: %s",
                        specific_id,
                        _single_line(exc, 160),
                    )
                    return cached or self._get_default_persona_prompt(umo)
                except asyncio.TimeoutError:
                    logger.warning("读取插件指定人格超时(ID: %s),本轮使用缓存人格", specific_id)
                    return cached or self._get_default_persona_prompt(umo)
                except Exception as e:
                    logger.warning(f"读取插件指定人格失败(ID: {specific_id}): {e}")
            getter = getattr(manager, "get_default_persona_v3", None) if manager else None
            if not callable(getter):
                return cached or self._get_default_persona_prompt(umo)
            def _read_default_persona() -> Any:
                try:
                    return getter(umo=umo)
                except TypeError:
                    try:
                        return getter(umo)
                    except TypeError:
                        return getter()

            result = await self._await_framework_db_query(
                f"default_persona:{umo}",
                _read_default_persona,
                timeout=2.0,
            )
            prompt = self._extract_default_persona_prompt(result)
            if prompt:
                return self._store_persona_prompt_for_scope(prompt, umo=umo, specific_id="")
        except asyncio.CancelledError:
            if _cancel_requested():
                raise
            logger.debug("默认人格查询被管理器取消,本轮使用缓存人格")
        except (sqlite3.OperationalError, sqlite3.ProgrammingError) as exc:
            logger.debug(
                "默认人格数据库暂不可用,本轮使用缓存人格: %s",
                _single_line(exc, 160),
            )
        except asyncio.TimeoutError:
            logger.warning("读取 AstrBot 默认人格超时,本轮使用缓存人格")
        except Exception as e:
            logger.warning(f"读取 AstrBot 默认人格失败: {e}")
        return self._get_default_persona_prompt(umo)

    async def _await_framework_db_query(
        self,
        key: str,
        factory: Any,
        *,
        timeout: float,
    ) -> Any:
        """Bound a core DB read without cancelling its aiosqlite connection."""
        tasks = getattr(self, "_framework_db_query_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self._framework_db_query_tasks = tasks
        task = tasks.get(key)
        if not isinstance(task, asyncio.Task) or task.done():
            result = factory()
            if not inspect.isawaitable(result):
                return result
            task = asyncio.create_task(result, name=f"private-companion-db:{key[:80]}")
            tasks[key] = task

            def _cleanup(done: asyncio.Task, *, query_key: str = key) -> None:
                if tasks.get(query_key) is done:
                    tasks.pop(query_key, None)
                if done.cancelled():
                    return
                try:
                    done.exception()
                except Exception:
                    pass

            task.add_done_callback(_cleanup)
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)

    def _schedule_default_persona_prompt_refresh(self, umo: str = "") -> None:
        specific_id = str(getattr(self, "_effective_plugin_persona_id", lambda: getattr(self, "plugin_specific_persona_id", ""))() or "").strip()
        cached, cached_at = self._cached_persona_prompt_for_scope(umo, specific_id)
        cache_fresh = cached and (_now_ts() - cached_at < 300.0)
        if cache_fresh:
            return
        scope = self._persona_prompt_cache_scope(umo, specific_id)
        tasks = getattr(self, "_default_persona_prompt_refresh_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self._default_persona_prompt_refresh_tasks = tasks
        task = tasks.get(scope)
        if isinstance(task, asyncio.Task) and not task.done():
            return

        async def _runner() -> None:
            try:
                await self._refresh_default_persona_prompt(umo)
            finally:
                current_tasks = getattr(self, "_default_persona_prompt_refresh_tasks", None)
                if isinstance(current_tasks, dict):
                    current_tasks.pop(scope, None)

        operation = _runner()
        creator = getattr(self, "_create_lifecycle_background_task", None)
        try:
            task = (
                creator(operation, label="default_persona_prompt_refresh")
                if callable(creator)
                else asyncio.create_task(operation, name="private-companion-persona-prompt-refresh")
            )
            if task is not None:
                tasks[scope] = task
                self._default_persona_prompt_refresh_task = task
                if not callable(creator):
                    def consume(done_task: asyncio.Task) -> None:
                        try:
                            done_task.result()
                        except asyncio.CancelledError:
                            pass
                        except Exception as exc:
                            logger.warning(
                                "默认人格后台刷新失败: %s",
                                _single_line(exc, 160),
                            )

                    task.add_done_callback(consume)
            else:
                close = getattr(operation, "close", None)
                if callable(close):
                    close()
        except RuntimeError:
            close = getattr(operation, "close", None)
            if callable(close):
                close()

    def _format_plugin_persona_request_injection(self) -> str:
        specific_id = str(getattr(self, "_effective_plugin_persona_id", lambda: getattr(self, "plugin_specific_persona_id", ""))() or "").strip()
        if not specific_id:
            return ""
        persona = self._get_default_persona_prompt()
        if not persona or persona == DEFAULT_PERSONA_PROMPT_FALLBACK:
            return ""
        return (
            "【本插件指定人格】\n"
            "本轮私聊陪伴相关回复请优先遵循下面的人格设定。"
            "如果它与更高优先级系统安全规则冲突,以安全规则为准；如果与插件的状态/记忆材料冲突,以人格设定为准。\n"
            f"{persona}"
        )

    def _persona_state_profile(self) -> dict[str, bool]:
        prompt = self._get_default_persona_prompt()
        role_prompt = str(runtime_persona_setting(self, "schedule_persona_prompt", "") or "")
        text = unicodedata.normalize("NFKC", f"{prompt}\n{role_prompt}").lower()
        compact = re.sub(r"\s+", "", text)

        def has_any(markers: tuple[str, ...]) -> bool:
            return any(marker in text or marker in compact for marker in markers)

        strong_non_human_markers = (
            "机器人", "机械体", "机体", "仿生", "android", "robot", "电子生命", "终端人格"
        )
        soft_non_human_markers = (
            "bot", "系统", "程序", "ai"
        )
        explicitly_human_markers = (
            "人类", "学生", "上班", "工作", "生活", "年龄", "岁",
            "吃饭", "睡觉", "起床", "洗漱", "身体", "生理期"
        )
        bodyless_markers = (
            "无实体", "没有实体", "没有身体", "无身体", "纯意识", "虚拟人格", "虚拟形象",
            "全息投影", "投影形态", "灵体", "幽灵", "意识体"
        )
        has_human_markers = has_any(explicitly_human_markers)
        has_bodyless_markers = has_any(bodyless_markers)
        has_strong_non_human = has_any(strong_non_human_markers)
        soft_non_human_hits = sum(1 for marker in soft_non_human_markers if marker in text)
        is_non_human = (has_strong_non_human or soft_non_human_hits >= 2) and not has_human_markers
        allow_health = bool(runtime_persona_setting(self, "enable_health_state", True))
        allow_hunger = bool(runtime_persona_setting(self, "enable_hunger_state", True))
        allow_cycle = bool(runtime_persona_setting(self, "enable_cycle_state", True))
        return {
            "non_human": is_non_human or has_bodyless_markers,
            "allow_health": allow_health,
            "allow_hunger": allow_hunger,
            "allow_cycle": allow_cycle,
        }

    def _base_state_values(self, profile: dict[str, bool] | None = None) -> dict[str, str]:
        profile = profile or self._persona_state_profile()
        values = {
            "sleep": "睡眠平稳",
            "dream": "没有记住梦",
            "health": "状态正常",
            "hunger": "无饥饿感",
            "body_cycle": "不处于生理期",
            "location": "",
        }
        if not profile.get("allow_health", True):
            values["health"] = "健康/不适状态未开启"
        if not profile.get("allow_hunger", True):
            values["hunger"] = "饥饿/胃口状态未开启"
        if not profile.get("allow_cycle", False):
            values["body_cycle"] = "生理期模拟未开启"
        return values

    def _is_inapplicable_state_text(self, text: str) -> bool:
        return "不适用" in str(text or "")

    @staticmethod
    def _state_condition_allowed(kind: str, profile: dict[str, bool]) -> bool:
        if kind == "health":
            return bool(profile.get("allow_health", True))
        if kind == "hunger":
            return bool(profile.get("allow_hunger", True))
        if kind in {"body_cycle", "cycle_discomfort"}:
            return bool(profile.get("allow_cycle", False))
        return True

    def _should_show_condition(self, cond: dict[str, Any]) -> bool:
        if not isinstance(cond, dict):
            return False
        if _safe_int(cond.get("energy_delta"), 0) != 0:
            return True
        if _single_line(cond.get("mood"), 20) not in {"", "平稳"}:
            return True
        if cond.get("cause") or cond.get("phase"):
            return True
        return str(cond.get("kind") or "") not in {"sleep", "dream"}

    def _format_can_do_for_prompt(self) -> str:
        items = self.data.get("can_do", [])
        if not isinstance(items, list) or not items:
            return "（暂未设置）"
        lines = []
        for item in items[:30]:
            text = _single_line(item, 80)
            if text:
                lines.append(f"- {text}")
        return "\n".join(lines) if lines else "（暂未设置）"

    @staticmethod
    def _detect_dialogue_outfit_change(text: Any) -> str:
        normalized = _single_line(text, 180)
        if not normalized:
            return ""
        outfit = (
            r"(?:JK(?:制服|服)?|jk(?:制服|服)?|校服|制服|衣服|衣裳|服装|穿搭|套装|"
            r"睡衣|睡裙|睡袍|居家服|礼服|正装|西装|汉服|和服|旗袍|洛丽塔|lo裙|"
            r"女仆装|巫女服|泳装|泳衣|运动服|球衣|外套|风衣|大衣|夹克|衬衫|"
            r"T恤|毛衣|卫衣|上衣|背心|连衣裙|短裙|长裙|裙子|裤子|短裤|袜子|鞋子|帽子|围巾)"
        )
        action = r"(?:换(?:装|衣|上|成|为|掉|下|回|一套|一身|一件|一条|身)?|改穿|穿(?:上|着|了)?|套上|脱下|脱掉)"
        has_outfit_change = bool(
            re.search(rf"{action}.{{0,24}}{outfit}|{outfit}.{{0,12}}{action}", normalized, re.IGNORECASE)
        )
        if not has_outfit_change:
            return ""

        question_or_hypothesis = bool(
            re.search(r"要不要|能不能|可不可以|是否|是不是|想不想|会不会|如果|假如|[？?]", normalized)
        )
        positive_after_boundary = bool(
            re.search(rf"(?:^|[，,。；;！!]\s*)(?:那|现在|然后|再|先|快|去|把|给|来)?\s*(?:你|她)?\s*{action}.{{0,24}}{outfit}", normalized, re.IGNORECASE)
            or re.search(rf"(?:^|[，,。；;！!]\s*)把.{{0,10}}{outfit}.{{0,8}}{action}", normalized, re.IGNORECASE)
        )
        if question_or_hypothesis and not positive_after_boundary:
            return ""

        negated_change = bool(re.search(rf"(?:不要|别|不用|不必|不许|禁止).{{0,8}}{action}", normalized))
        if negated_change and not re.search(rf"[，,。；;！!].{{0,12}}{action}.{{0,24}}{outfit}", normalized, re.IGNORECASE):
            return ""

        direct_target = bool(
            re.search(rf"(?:让|叫|给|帮)?(?:你|她|角色|星缘|bot|机器人).{{0,16}}{action}", normalized, re.IGNORECASE)
        )
        shared_target = bool(re.search(rf"(?:我们|咱们|咱俩).{{0,8}}{action}", normalized))
        imperative = positive_after_boundary
        if not (direct_target or shared_target or imperative):
            return ""

        meta_feedback = bool(
            re.search(r"掉状态|对不上|文本里|文本里面|旧衣服|原本|之前|怎么又|为什么|bug|BUG|问题", normalized)
        )
        if meta_feedback and not imperative:
            return ""
        return normalized

    def _current_dialogue_outfit_override(
        self,
        *,
        user_id: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        snapshot = data.get("dialogue_outfit_override")
        if not isinstance(snapshot, dict):
            return {}
        check_now = _now_ts() if now is None else now
        if _single_line(snapshot.get("date"), 16) != _today_key():
            return {}
        if _safe_float(snapshot.get("expires_at"), 0) <= check_now:
            return {}
        source_user_id = _single_line(snapshot.get("source_user_id"), 80)
        requested_user_id = _single_line(user_id, 80)
        if requested_user_id and source_user_id != requested_user_id:
            return {}
        instruction = _single_line(snapshot.get("instruction"), 180)
        return dict(snapshot) if instruction else {}

    def _format_dialogue_outfit_continuity_for_prompt(
        self,
        user: dict[str, Any] | None = None,
        *,
        include_heading: bool = True,
    ) -> str:
        user_id = _single_line((user or {}).get("user_id"), 80) if isinstance(user, dict) else ""
        snapshot = self._current_dialogue_outfit_override(user_id=user_id)
        instruction = _single_line(snapshot.get("instruction"), 180)
        if not instruction:
            return ""
        return (
            ("【当前会话服装连续性】\n" if include_heading else "")
            +
            f"最近一次明确换装：用户说“{instruction}”。\n"
            "把它理解为当前剧情中已经发生、需要继续承接的服装变化，不要逐字复述。"
            "它高于人格默认服装、今日穿搭参考、旧日程、旧摘要和旧图片中的衣服。"
            "在用户再次明确换装、明确换回，或剧情自然写出新的换衣过程前，不得自行恢复旧服装。"
        )

    def _format_dialogue_outfit_continuity_prompt_section(
        self,
        user: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return prompt_section(
            "当前会话服装连续性",
            self._format_dialogue_outfit_continuity_for_prompt(
                user,
                include_heading=False,
            ),
        )

    def _record_dialogue_outfit_override_from_interaction(
        self,
        text: str,
        user: dict[str, Any] | None = None,
    ) -> bool:
        instruction = self._detect_dialogue_outfit_change(text)
        if not instruction:
            return False
        now = _now_ts()
        source_user_id = _single_line((user or {}).get("user_id"), 80) if isinstance(user, dict) else ""
        self.data["dialogue_outfit_override"] = {
            "date": _today_key(),
            "instruction": instruction,
            "source": "user_dialogue",
            "source_user_id": source_user_id,
            "created_at": now,
            "expires_at": now + 12 * 3600,
        }
        self._record_detail_interaction_update(
            {
                "source": "用户换装",
                "user_text": instruction,
                "intensity": "强",
                "scope": "直到再次换装或当日结束",
                "immediate_reaction": "Bot 已经按用户这次要求换好衣服，后续动作和场景继续沿用这套服装。",
                "state_updates": [f"当前服装：按用户换装要求“{instruction}”继续"],
                "source_role": "owner",
                "source_user_id": source_user_id,
            }
        )
        return True

    @staticmethod
    def _normalize_schedule_adjustment_scope(scope: Any) -> str:
        text = _single_line(scope, 60)
        if any(token in text for token in ("主动策略", "主动消息", "主动频率")):
            return "proactive_only"
        if any(token in text for token in ("直到", "到家", "今晚到", "缓冲期")):
            return "until_condition"
        if any(token in text for token in ("今日后续", "今天剩余", "全天后续")):
            return "rest_of_day"
        if "下一段" in text:
            return "current_and_next"
        if any(token in text for token in ("当前段", "当前休息段")):
            return "current_only"
        return "current_and_next"

    def _format_schedule_adjustments_for_prompt(self, segment: dict[str, Any] | None = None) -> str:
        raw = self.data.get("schedule_adjustments", [])
        now = _now_ts()
        kept = []
        lines = []
        override_lines = []
        override = self._sleep_delay_override_state(now=now)
        if override:
            until_text = _single_line(override.get("until_text"), 24)
            override_lines.append(f"- 临时延后休息｜强｜今晚：到 {until_text} 前按用户临时陪聊约定处理,不要把当前睡眠段当成必须沉默。")
            override_lines.append("  承接要求：只影响今晚；到点后自然收声或回到休息,不要写成长期熬夜习惯。")
        outfit_override = self._current_dialogue_outfit_override(now=now)
        outfit_instruction = _single_line(outfit_override.get("instruction"), 180)
        if outfit_instruction:
            override_lines.append(
                f"- 用户换装｜强｜直到再次换装：用户已明确改变角色服装：“{outfit_instruction}”。"
            )
            override_lines.append(
                "  承接要求：把最新换装写入当前服装状态并延续到后续片段；日程或旧摘要里的默认穿搭只能补空白，不能把服装复原。"
            )
        if isinstance(raw, list) and raw:
            for item in raw:
                if not isinstance(item, dict):
                    continue
                if _single_line(item.get("source_role"), 20) != "owner":
                    continue
                expires_at = _safe_float(item.get("expires_at"), 0)
                if expires_at > 0 and expires_at <= now:
                    continue
                date_text = _single_line(item.get("date"), 16)
                if date_text and date_text != _today_key():
                    continue
                kept.append(item)
                note = _single_line(item.get("note"), 120)
                source = _single_line(item.get("source"), 24)
                intensity = _single_line(item.get("intensity"), 16)
                scope = _single_line(item.get("scope"), 30)
                scope_key = _single_line(item.get("scope_key"), 24) or self._normalize_schedule_adjustment_scope(scope)
                if segment is None and scope_key == "proactive_only":
                    continue
                if isinstance(segment, dict):
                    target_index = _safe_int(segment.get("index"), -1, minimum=-1)
                    anchor_index = _safe_int(item.get("anchor_segment_index"), -1, minimum=-1)
                    if anchor_index < 0:
                        current_segment = self._current_detail_segment_for_update()
                        anchor_index = _safe_int((current_segment or {}).get("index"), target_index, minimum=-1)
                    if scope_key == "current_only" and target_index != anchor_index:
                        continue
                    if scope_key == "current_and_next" and target_index not in {anchor_index, anchor_index + 1}:
                        continue
                    if scope_key in {"rest_of_day", "until_condition", "proactive_only"} and target_index < anchor_index:
                        continue
                if note:
                    meta = "｜".join(part for part in (source or "互动", intensity, scope, f"作用域={scope_key}") if part)
                    lines.append(f"- {meta}：{note}")
                immediate = _single_line(item.get("immediate_reaction"), 120)
                if immediate:
                    lines.append(f"  即时反应：{immediate}")
                updates = item.get("state_updates")
                if isinstance(updates, list) and updates:
                    update_text = "；".join(
                        _single_line(update, 60)
                        for update in updates
                        if _single_line(update, 60)
                    )
                    if update_text:
                        lines.append(f"  状态变量更新：{update_text}")
                carry = _single_line(item.get("carry_rule"), 120)
                if carry:
                    lines.append(f"  承接要求：{carry}")
                if scope_key == "proactive_only":
                    lines.append("  作用限制：只允许影响 proactive_events，不得改写粗日程、summary、today_events、state_variables 或 presence_status。")
            if len(kept) != len(raw):
                self.data["schedule_adjustments"] = kept[-12:]
        if override_lines:
            lines.extend(override_lines)
        return "\n".join(lines[-12:]) if lines else "（暂无）"

    def _current_detail_segment_for_update(self) -> dict[str, Any] | None:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return None
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return None
        for segment in self._collect_detail_segments(plan, {}):
            start = _safe_int(segment.get("start"), 0)
            end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
            lead = max(0, _safe_int(runtime_persona_setting(self, "detail_enhancement_lead_minutes", 3), 3, 0))
            if start - lead <= now_minutes < end:
                return segment
        return None

    def _current_detail_state_variables(self) -> list[dict[str, str]]:
        segment = self._current_detail_segment_for_update()
        if not segment:
            return []
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            return []
        snapshot = enhanced.get(str(segment.get("key") or ""))
        if not isinstance(snapshot, dict):
            return []
        variables = snapshot.get("state_variables", [])
        if not isinstance(variables, list):
            return []
        return [item for item in variables if isinstance(item, dict)]

    @staticmethod
    def _sleep_phase_label(phase: str) -> str:
        return {
            "awake": "清醒",
            "falling_asleep": "入睡中",
            "light_sleep": "浅睡",
            "woken": "被叫醒",
            "staying_up": "临时晚睡",
            "sleeping_again": "继续睡",
            "natural_wake": "自然醒",
        }.get(str(phase or ""), "清醒")

    def _sleep_runtime_state(self) -> dict[str, Any]:
        state = self.data.setdefault("daily_state", {})
        if not isinstance(state, dict):
            state = {}
            self.data["daily_state"] = state
        runtime = state.setdefault("sleep_runtime", {})
        if not isinstance(runtime, dict):
            runtime = {}
            state["sleep_runtime"] = runtime
        if not runtime.get("phase"):
            now = _now_ts()
            runtime.update(
                {
                    "phase": "awake",
                    "label": self._sleep_phase_label("awake"),
                    "started_at": now,
                    "updated_at": now,
                    "woken_count": 0,
                    "last_event": "尚未进入睡眠段",
                    "source": "init",
                }
            )
        return runtime

    def _sleep_awake_grace_seconds(self) -> int:
        grace_minutes = _safe_int(runtime_persona_setting(self, "rest_reply_awake_grace_minutes", 30), 30, 0)
        return max(0, min(240, grace_minutes)) * 60

    def _sleep_rest_window_active(self) -> bool:
        if not bool(runtime_persona_setting(self, "enable_rest_reply_simulation", False)):
            return True
        checker = getattr(self, "_rest_reply_window_active", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return True
        return True

    @staticmethod
    def _sleep_delay_cn_number(value: Any) -> int | None:
        text = str(value or "").strip().replace("兩", "两").replace("〇", "零")
        if not text:
            return None
        if text.isdigit():
            return int(text)
        digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if text in digits:
            return digits[text]
        if "十" in text:
            left, _, right = text.partition("十")
            tens = digits.get(left, 1) if left else 1
            ones = digits.get(right, 0) if right else 0
            return tens * 10 + ones
        return None

    @classmethod
    def _sleep_delay_parse_minute(cls, value: Any) -> int:
        text = str(value or "").strip()
        if not text:
            return 0
        if text == "半":
            return 30
        if text == "一刻":
            return 15
        if text == "三刻":
            return 45
        parsed = cls._sleep_delay_cn_number(text)
        if parsed is None:
            return 0
        return max(0, min(59, parsed))

    def _sleep_delay_next_local_ts(self, hour: int, minute: int, *, now_dt: datetime | None = None) -> float:
        current = now_dt or self._environment_now()
        target = datetime.combine(current.date(), datetime.min.time(), tzinfo=current.tzinfo) + timedelta(
            hours=max(0, min(23, hour)),
            minutes=max(0, min(59, minute)),
        )
        if target.timestamp() <= current.timestamp() + 60:
            target += timedelta(days=1)
        return target.timestamp()

    def _parse_sleep_delay_until_ts(self, compact: str, *, now_dt: datetime | None = None) -> tuple[float, bool]:
        current = now_dt or self._environment_now()
        hour_token = r"(?:\d{1,2}|[零〇一二两兩三四五六七八九十]{1,3})"
        minute_token = r"(?:\d{1,2}|[零〇一二两兩三四五六七八九十]{1,3}|半|一刻|三刻)"
        match = re.search(
            rf"(?:陪(?:我|着我)?到|陪到|撑到|等到|到|至)"
            rf"(凌晨|半夜|今晚|今夜|夜里|晚上|明早|明天早上|明天)?"
            rf"({hour_token})(?:[:：点點时])({minute_token})?",
            compact,
        )
        if not match:
            return 0.0, False
        period = str(match.group(1) or "")
        hour = self._sleep_delay_cn_number(match.group(2))
        if hour is None:
            return 0.0, False
        minute = self._sleep_delay_parse_minute(match.group(3))
        if period in {"凌晨", "半夜"}:
            if hour == 12:
                hour = 0
        elif period in {"今晚", "今夜", "夜里", "晚上"}:
            if hour == 12:
                hour = 0
            elif 6 <= hour <= 11:
                hour += 12
        elif period in {"明早", "明天早上"}:
            if hour == 12:
                hour = 0
        elif current.hour >= 18:
            if hour == 12:
                hour = 0
            elif 6 <= hour <= 11:
                hour += 12
        if hour > 23:
            return 0.0, False
        target_ts = self._sleep_delay_next_local_ts(hour, minute, now_dt=current)
        if period in {"明早", "明天早上"}:
            target_dt = self._environment_fromtimestamp(target_ts)
            if target_dt.date() == current.date():
                target_ts = (target_dt + timedelta(days=1)).timestamp()
        explicit_cap = min(current.timestamp() + 6 * 3600, self._sleep_delay_next_local_ts(6, 0, now_dt=current))
        return min(target_ts, explicit_cap), True

    def _detect_sleep_delay_request(self, text: str) -> dict[str, Any] | None:
        normalized = _single_line(text, 220)
        compact = re.sub(r"\s+", "", normalized)
        if not compact:
            return None
        if re.search(r"(早点睡|早睡|快睡|去睡|睡觉吧|该睡|别熬夜|不要熬夜|别晚睡|不要晚睡|不许熬夜|不许晚睡|别睡太晚|不要睡太晚)", compact):
            return None
        delay_intent = bool(
            re.search(r"(今晚|今夜|今天晚上|夜里|凌晨|待会|等下|一会).{0,14}(晚点睡|迟点睡|晚睡|先不睡|不睡了|先别睡|别睡|熬夜)", compact)
            or re.search(r"(陪我|陪陪我|陪着我|和我).{0,12}(熬夜|晚点睡|迟点睡|先别睡|别睡|不睡)", compact)
            or re.search(r"(陪我|陪陪我|陪着我|和我).{0,12}到.{0,10}(?:点|點|时|:|：|半).{0,8}(?:再睡|睡觉|去睡)", compact)
            or re.search(r"(陪我|陪陪我|陪着我|和我).{0,12}到(?:凌晨|半夜|今晚|今夜|夜里).{0,10}(?:点|點|时|:|：|半)", compact)
            or re.search(r"(先别睡|别睡了?|别去睡)", compact)
        )
        if not delay_intent:
            return None
        now_dt = self._environment_now()
        explicit_until, explicit = self._parse_sleep_delay_until_ts(compact, now_dt=now_dt)
        if explicit_until > now_dt.timestamp() + 5 * 60:
            until_ts = explicit_until
        else:
            default_until = now_dt.timestamp() + 2 * 3600
            default_cap = self._sleep_delay_next_local_ts(3, 30, now_dt=now_dt)
            until_ts = min(default_until, default_cap)
            if until_ts < now_dt.timestamp() + 30 * 60:
                until_ts = min(now_dt.timestamp() + 60 * 60, self._sleep_delay_next_local_ts(6, 0, now_dt=now_dt))
        until_text = self._environment_fromtimestamp(until_ts).strftime("%m-%d %H:%M")
        return {
            "until_ts": until_ts,
            "until_text": until_text,
            "explicit_time": explicit,
            "user_text": normalized,
        }

    def _sleep_delay_override_state(
        self,
        runtime: dict[str, Any] | None = None,
        *,
        now: float | None = None,
        clear_expired: bool = True,
    ) -> dict[str, Any]:
        check_now = _now_ts() if now is None else now
        runtime = runtime if isinstance(runtime, dict) else self._sleep_runtime_state()
        until_ts = _safe_float(runtime.get("sleep_delay_until_ts"), 0)
        if until_ts <= check_now:
            if clear_expired and until_ts > 0:
                for key in (
                    "sleep_delay_until_ts",
                    "sleep_delay_until_text",
                    "sleep_delay_reason",
                    "sleep_delay_user_text",
                    "sleep_delay_set_at",
                    "sleep_delay_explicit_time",
                ):
                    runtime.pop(key, None)
            return {}
        until_text = _single_line(runtime.get("sleep_delay_until_text"), 24)
        if not until_text:
            until_text = self._environment_fromtimestamp(until_ts).strftime("%m-%d %H:%M")
            runtime["sleep_delay_until_text"] = until_text
        return {
            "until_ts": until_ts,
            "until_text": until_text,
            "reason": _single_line(runtime.get("sleep_delay_reason"), 120),
            "user_text": _single_line(runtime.get("sleep_delay_user_text"), 120),
            "explicit_time": bool(runtime.get("sleep_delay_explicit_time")),
        }

    def _apply_sleep_delay_override(self, delay: dict[str, Any], *, text: str = "") -> dict[str, Any]:
        until_ts = _safe_float(delay.get("until_ts"), 0)
        if until_ts <= _now_ts():
            return self._sleep_runtime_state()
        until_text = _single_line(delay.get("until_text"), 24) or self._environment_fromtimestamp(until_ts).strftime("%m-%d %H:%M")
        runtime = self._set_sleep_phase(
            "staying_up",
            event=f"用户约定今晚晚点休息，到 {until_text} 前按临时陪聊处理",
            source="user_sleep_delay",
            now=_now_ts(),
        )
        runtime["sleep_delay_until_ts"] = until_ts
        runtime["sleep_delay_until_text"] = until_text
        runtime["sleep_delay_reason"] = "用户临时要求今晚晚点睡或陪聊"
        runtime["sleep_delay_user_text"] = _single_line(text or delay.get("user_text"), 120)
        runtime["sleep_delay_set_at"] = _now_ts()
        runtime["sleep_delay_explicit_time"] = bool(delay.get("explicit_time"))
        return runtime

    def _set_sleep_phase(self, phase: str, *, event: str, source: str = "schedule", now: float | None = None) -> dict[str, Any]:
        now = now or _now_ts()
        runtime = self._sleep_runtime_state()
        if runtime.get("phase") != phase:
            runtime["started_at"] = now
        runtime["phase"] = phase
        runtime["label"] = self._sleep_phase_label(phase)
        runtime["updated_at"] = now
        runtime["last_event"] = _single_line(event, 120)
        runtime["source"] = source
        return runtime

    def _refresh_sleep_runtime_state(self, current_item: dict[str, Any] | None = None, *, now: float | None = None) -> dict[str, Any]:
        now = now or _now_ts()
        runtime = self._sleep_runtime_state()
        item = current_item if isinstance(current_item, dict) else self._get_current_plan_item(self.data.get("daily_plan", {}))
        rest_window_active = self._sleep_rest_window_active()
        base_sleepy = rest_window_active and self._is_sleepy_plan_item(item) if isinstance(item, dict) else False
        delay_override = self._sleep_delay_override_state(runtime, now=now)
        if delay_override and (base_sleepy or runtime.get("phase") == "staying_up"):
            return self._set_sleep_phase(
                "staying_up",
                event=f"用户约定今晚晚点休息，到 {delay_override.get('until_text')} 前不按睡眠段拦截",
                source="user_sleep_delay",
                now=now,
            )
        sleepy = base_sleepy and not delay_override
        if runtime.get("phase") == "staying_up" and not delay_override:
            if not sleepy:
                return self._set_sleep_phase("awake", event="临时晚睡约定已结束，当前不在休息段", source="time", now=now)
            text = " ".join(_single_line(item.get(key), 80) for key in ("activity", "mood", "message_seed")) if isinstance(item, dict) else ""
            if any(token in text for token in ("准备睡", "睡前", "入睡", "洗漱", "收声")):
                return self._set_sleep_phase("falling_asleep", event="临时晚睡约定结束，回到睡前段", source="schedule", now=now)
            return self._set_sleep_phase("light_sleep", event="临时晚睡约定结束，回到休息段", source="schedule", now=now)
        if runtime.get("phase") == "woken":
            last_woken = _safe_float(runtime.get("last_woken_at"), _safe_float(runtime.get("updated_at"), now))
            grace_seconds = self._sleep_awake_grace_seconds()
            if grace_seconds <= 0 or now - last_woken >= grace_seconds:
                if not sleepy:
                    return self._set_sleep_phase("natural_wake", event="醒后缓冲结束，当前已不在有效休息段", source="time", now=now)
                return self._set_sleep_phase("sleeping_again", event="用户没有继续打扰，睡意重新接上", source="quiet", now=now)
            return runtime
        if sleepy:
            text = " ".join(_single_line(item.get(key), 80) for key in ("activity", "mood", "message_seed"))
            if any(token in text for token in ("准备睡", "睡前", "入睡", "洗漱", "收声")):
                return self._set_sleep_phase("falling_asleep", event="日程进入睡前或入睡段", source="schedule", now=now)
            if runtime.get("phase") == "sleeping_again":
                return runtime
            return self._set_sleep_phase("light_sleep", event="日程处于睡眠或休息延续", source="schedule", now=now)
        if runtime.get("phase") in {"falling_asleep", "light_sleep", "sleeping_again"}:
            return self._set_sleep_phase("natural_wake", event="睡眠段结束，按日程自然醒来", source="schedule", now=now)
        if runtime.get("phase") == "natural_wake" and now - _safe_float(runtime.get("updated_at"), now) > 2 * 3600:
            return self._set_sleep_phase("awake", event="自然醒后的日常清醒状态", source="time", now=now)
        return runtime

    def _mark_sleep_woken_by_user(self, text: str) -> dict[str, Any]:
        now = _now_ts()
        runtime = self._sleep_runtime_state()
        count = _safe_int(runtime.get("woken_count"), 0, 0) + 1
        updated = self._set_sleep_phase("woken", event="用户消息把睡眠段轻轻叫醒", source="user_message", now=now)
        updated["woken_count"] = count
        updated["last_woken_at"] = now
        updated["last_user_text"] = _single_line(text, 80)
        return updated

    def _mark_sleep_woken_by_group_wakeup(self, text: str, *, wakeup_type: str = "") -> dict[str, Any]:
        now = _now_ts()
        runtime = self._sleep_runtime_state()
        count = _safe_int(runtime.get("woken_count"), 0, 0) + 1
        updated = self._set_sleep_phase("woken", event="群聊里被提到或被话题轻轻叫醒", source="group_wakeup", now=now)
        updated["woken_count"] = count
        updated["last_woken_at"] = now
        updated["last_group_wakeup_text"] = _single_line(text, 80)
        updated["last_group_wakeup_type"] = _single_line(wakeup_type, 40)
        return updated

    def _detect_schedule_adjustment_from_interaction(self, text: str) -> dict[str, Any] | None:
        normalized = _single_line(text, 220)
        if not normalized:
            return None
        current_variables = self._current_detail_state_variables()
        variable_text = " ".join(
            f"{item.get('name', '')}:{item.get('value', '')} {item.get('note', '')}"
            for item in current_variables[:8]
        )
        def payload(
            *,
            source: str,
            note: str,
            immediate_reaction: str,
            state_updates: list[str],
            intensity: str = "中",
            scope: str = "当前段和下一段",
            carry_rule: str = "后续细化可根据这次用户介入留下合适的状态余味；若没有实际改变任务、作息、边界或共同场景，不必扩写成生活事件。",
            **extra: Any,
        ) -> dict[str, Any]:
            data = {
                "source": source,
                "note": note,
                "immediate_reaction": immediate_reaction,
                "state_updates": state_updates,
                "intensity": intensity,
                "scope": scope,
                "carry_rule": carry_rule,
                "user_text": normalized,
            }
            data.update(extra)
            return data

        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        sleep_delay = self._detect_sleep_delay_request(normalized)
        if sleep_delay:
            until_text = _single_line(sleep_delay.get("until_text"), 24)
            return payload(
                source="临时延后休息",
                note=f"用户今晚希望晚点休息或陪聊；到 {until_text} 前暂时不要把睡眠段当成必须沉默,但这只是今晚的临时约定。",
                immediate_reaction="Bot 会把今晚的节奏稍微放慢并留出陪聊余地,但不会把这当成长期作息改变。",
                state_updates=[f"休息安排：今晚临时延后到 {until_text}", "清醒程度：陪聊但低负担", "后续安排：到点后自然收声或睡回去"],
                intensity="强",
                scope=f"今晚到 {until_text}",
                carry_rule="只影响今晚和当前休息段；后续细化可以保留轻微陪聊/等待感,但不得把它写成长期熬夜习惯,到点后应自然收声或回到休息。",
                sleep_delay_until_ts=sleep_delay.get("until_ts"),
                sleep_delay_until_text=until_text,
                sleep_delay_explicit_time=bool(sleep_delay.get("explicit_time")),
            )
        is_actual_rest_segment = (
            self._sleep_rest_window_active()
            and not self._sleep_delay_override_state(clear_expired=True)
            and self._is_sleepy_plan_item(current_item)
        )
        if is_actual_rest_segment and not re.search(r"别吵|别发|别找|安静|闭嘴|先别|不要来|忙|我有事|没空", normalized):
            runtime_before = self._sleep_runtime_state()
            last_woken = _safe_float(runtime_before.get("last_woken_at"), _safe_float(runtime_before.get("updated_at"), 0))
            last_user_text = _single_line(runtime_before.get("last_user_text"), 80)
            current_user_text = _single_line(normalized, 80)
            consumed_at = _safe_float(runtime_before.get("last_wakeup_context_consumed_at"), 0)
            same_wakeup_message = bool(
                runtime_before.get("phase") == "woken"
                and last_user_text
                and last_user_text == current_user_text
                and consumed_at < last_woken
                and _now_ts() - last_woken < 120
            )
            if same_wakeup_message:
                runtime_before["last_wakeup_context_consumed_at"] = _now_ts()
                return payload(
                    source="睡眠中被用户唤醒",
                    note="当前日程处于休息/睡眠段,这条消息已经在休息闸门放行时登记为唤醒；不要重复计数,语气只保留刚醒的慢一点和轻一点。",
                    immediate_reaction="Bot 刚被这条消息轻轻叫醒,会慢一点看清内容再回应。",
                    state_updates=["清醒程度：刚被唤醒/迷糊", "语气：轻、短、带睡意", "后续安排：用户不继续打扰就继续睡"],
                    intensity="强",
                    scope="当前休息段和后续短时间",
                    carry_rule="回复可以带一点刚醒的气息,但不得降低理解、事实和回答质量；不要再表现成又被叫醒一次。",
                )
            within_awake_grace = (
                runtime_before.get("phase") == "woken"
                and self._sleep_awake_grace_seconds() > 0
                and _now_ts() - last_woken < self._sleep_awake_grace_seconds()
            )
            if within_awake_grace:
                runtime_before["last_user_text"] = _single_line(normalized, 80)
                return payload(
                    source="睡眠中醒后续聊",
                    note="当前日程仍是休息/睡眠段,但 Bot 已在醒后缓冲期内；这是被叫醒后的连续对话,不再按再次唤醒处理。",
                    immediate_reaction="Bot 还没完全精神起来,但已经在接着聊天,不会每句话都像重新被吵醒。",
                    state_updates=["清醒程度：醒后续聊/慢慢清醒", "语气：仍轻一点,但不重复表演被叫醒", "后续安排：停聊后再自然睡回去"],
                    intensity="中",
                    scope="醒后缓冲期",
                    carry_rule="后续回复保持连续聊天感,不要写成每条消息都重新惊醒；用户继续聊时可以逐渐清醒一点。",
                )
            sleep_runtime = self._mark_sleep_woken_by_user(normalized)
            prior_wakes = 0
            segment = self._current_detail_segment_for_update()
            enhanced = self.data.get("detail_enhanced_segments", {})
            if isinstance(segment, dict) and isinstance(enhanced, dict):
                snapshot = enhanced.get(str(segment.get("key") or ""))
                updates = snapshot.get("interaction_updates", []) if isinstance(snapshot, dict) else []
                if isinstance(updates, list):
                    prior_wakes = sum(
                        1 for update in updates
                        if isinstance(update, dict) and "唤醒" in str(update.get("source") or "")
                    )
            prior_wakes = max(prior_wakes, _safe_int(sleep_runtime.get("woken_count"), 1, 1) - 1)
            if prior_wakes > 0:
                return payload(
                    source="睡眠中再次被唤醒",
                    note="当前日程处于休息/睡眠段,用户又发来消息；回复语气应带一点被重新叫醒的迟钝感,但必须清楚理解用户的话,不要埋怨用户。若用户继续聊,可以慢慢醒一点；若用户停下,Bot 会很快继续睡回去。",
                    immediate_reaction="Bot 又被消息轻轻拽醒一下,语气会慢半拍,但会看清用户说了什么再回应。",
                    state_updates=["清醒程度：再次被唤起/半梦半醒", "语气：慢半拍、短一点", "后续安排：用户不继续打扰就继续睡"],
                    intensity="中",
                    scope="当前休息段",
                    carry_rule="当前段回复必须有刚被重新唤起的语气感觉,但不得降低理解和回答质量；如果后续没有用户消息,下一段细化应让 Bot 继续休息或睡回去。",
                )
            return payload(
                source="睡眠中被用户唤醒",
                note="当前日程处于休息/睡眠段,用户发来消息把 Bot 轻轻叫醒；回复语气应像刚醒或半梦半醒,不要立刻精神饱满,但必须看懂并正面回应用户。若用户没有继续打扰,后续应自然睡回去或继续休息。",
                immediate_reaction="Bot 会先带着睡意看一眼消息,语气慢一点,但不会乱接或漏掉用户真正的问题。",
                state_updates=["清醒程度：刚被唤醒/迷糊", "语气：轻、短、带睡意", "后续安排：用户不继续打扰就继续睡"],
                intensity="强",
                scope="当前休息段和后续短时间",
                carry_rule="回复与后续细化必须承接“刚被用户唤醒”的语气状态,但不得牺牲理解、事实和回答质量；如果没有连续聊天,不要强行清醒活动,要睡回去或继续休息。",
            )

        if re.search(r"换元|代入|公式|思路|解法|答案|步骤|这题|函数题|数学题|阅读题|作文|作业|代码|报错|bug|怎么写|这样做|你试试|应该是", normalized):
            updates = ["学习/作业进度：因用户提供思路而推进", "情绪：卡住->松一口气"]
            if re.search(r"卡|函数|数学|作业", variable_text):
                updates.insert(0, "卡住点：用户提示后得到缓解")
            return payload(
                source="用户帮助",
                note="用户刚刚提供了和当前任务相关的帮助；当前任务不再按原本卡住状态推进,后续时间段要继承“被帮助后松动/推进”的结果。",
                immediate_reaction="看到用户给的思路后,Bot 先照着试了一下；如果确实对上当前任务,心里会明显松一口气。",
                state_updates=updates,
                intensity="强",
                scope="当前段、下一段、今日后续",
                carry_rule="如果后续仍写学习/作业/任务,必须体现已经被用户提示影响过：进度推进、情绪松动、或换了做法。",
            )
        food_feedback = self._detect_food_feedback(normalized)
        if food_feedback.get("is_food") and (food_feedback.get("suggestion") or food_feedback.get("bot_directed")):
            meal = _single_line(food_feedback.get("meal"), 20) or "饭点"
            food_hint = _single_line(food_feedback.get("food_hint"), 80)
            return payload(
                source="饮食反馈",
                note=f"用户刚刚给了关于{meal}或吃什么的反馈；后续日程要把这当成一个已经影响生活安排的小事实,可以调整饭点、胃口、准备食物或晚餐选择。",
                immediate_reaction="Bot 会把这句当成一个可执行的小主意,刚才悬着的“吃什么”落下来一点。",
                state_updates=[f"{meal}选择：受用户反馈影响", "饥饿感：开始回落", "后续安排：可能按反馈调整吃饭/点餐/做饭"],
                intensity="中",
                scope="当前段和今日后续饭点",
                carry_rule=f"后续细化如果涉及吃饭、晚餐、休息或外出,要自然承接用户这句饮食反馈：{food_hint}。不要生硬复述,也不要像没有问过一样重置。",
            )
        self_reported_care_action = bool(
            re.search(
                r"(?:我|俺|本人|这边|我们|咱们|咱).{0,12}(?:去睡|早点睡|睡觉|休息|先洗澡|先吃饭|吃点|喝水|躺会|停一下|歇会)",
                normalized,
            )
        )
        if not self_reported_care_action and re.search(r"去睡|早点睡|睡觉|休息|别写了|别弄了|先洗澡|先吃饭|吃点|喝水|别熬|躺会|停一下|歇会", normalized):
            return payload(
                source="用户照顾",
                note="用户刚刚给了休息或照顾指令；后续节奏应明显调慢,更可能提前收尾、补充休息、喝水吃饭或把任务延后。",
                immediate_reaction="Bot 看到这句会停一下手里的事,嘴上可能不立刻答应,但动作会慢下来一点。",
                state_updates=["体力：消耗放缓/略微回稳", "情绪：被照顾后的柔和", "后续安排：更倾向提前收尾或补充休息"],
                intensity="强",
                scope="当前段和今日后续",
                carry_rule="下一段不能完全无视这句照顾提醒；至少要在节奏、体力或收尾方式上留下影响。",
            )
        shared_location_signal = bool(re.search(r"一起|我们|咱们|咱俩|带你|带我|陪你|陪我|跟你|跟我|走吧|出发吧", normalized))
        outward_action_signal = bool(re.search(r"出发|出门|出去|去吃|去逛|去买|去玩|上车|下车|走了|走起|换鞋|拿钥匙|等车|打车|坐车|地铁|公交|到了|排队|找位子|点单|点餐|下单", normalized))
        if shared_location_signal and outward_action_signal:
            self._apply_dialogue_location_override("外面")
            return payload(
                source="用户带出/同行",
                note="用户刚刚带角色出门或一起外出；当前位置应从家里切换到外面,后续细化要承接外出场景,不要把角色写回家里。",
                immediate_reaction="Bot 会赶紧收拾一下东西,跟着用户往外走,可能边走边看手机或整理衣服。",
                state_updates=["位置：家里->外面", "活动：跟随用户外出", "情绪：略兴奋或期待"],
                intensity="强",
                scope="当前段和今日后续直到回家线索出现",
                carry_rule="后续细化和状态注入必须把角色位置保持在'外面',直到用户明确说回家、到家或日程自然过渡到居家时段；不要把角色写回沙发、卧室或家里。",
            )
        shared_return_signal = bool(re.search(r"一起|我们|咱们|咱俩|带你|带我|陪你|陪我|跟你|跟我|回家吧|送你回", normalized))
        return_home_signal = bool(re.search(r"回来了|到家了|回家了|进家门|开门|进门|回到.*家|到家|安全到家", normalized))
        if shared_return_signal and return_home_signal:
            self._apply_dialogue_location_override("家里")
            return payload(
                source="用户带回/回家",
                note="用户和角色刚刚回到家；当前位置应从外面切换回家里,后续细化要承接回家后场景。",
                immediate_reaction="Bot 会松一口气,可能踢掉鞋子或把东西放下,瘫到沙发上。",
                state_updates=["位置：外面->家里", "活动：回到居家", "情绪：放松"],
                intensity="中",
                scope="当前段和下一段",
                carry_rule="后续细化可以把角色写回家里场景,但不要立刻恢复出门前的精确活动,要体现外出后的余味。",
            )
        explicit_appointment_signal = bool(re.search(
            r"(?:约好|说好|定了|晚点(?:一起|聊|打电话|语音|开黑|看)|(?:一起|我们|咱们|咱俩|陪你|陪我|等你|等我|跟你|跟我).{0,18}(?:待会|一会|晚上|明天|等下|见面|打电话|语音|开黑|看))",
            normalized,
        ))
        if explicit_appointment_signal:
            return payload(
                source="用户约定",
                note="用户刚刚给出陪伴、等待、稍后一起做事或约定线索；后续日程应保留期待/等待/预留空档,不要像没有约定一样推进。",
                immediate_reaction="Bot 会把这句话在心里放一下,后续做事时更容易分神看时间或看消息。",
                state_updates=["等待/期待：上升", "后续安排：可能预留空档", "主动话题：围绕约定轻轻回勾"],
                intensity="强",
                scope="今日后续",
                carry_rule="后续主动契机和细化事件要把这个约定当作开放话头,可以等待、预留时间或轻轻提醒。",
            )
        care = self._detect_care_feedback(normalized)
        if care.get("is_care"):
            return payload(
                source="用户关心",
                note="用户刚刚给了关心或照顾提醒；后续日程需要从低落/烦躁向被安抚后的柔和、慢慢回稳偏移,不要升级破坏性冲动。",
                immediate_reaction="Bot 看到关心会先顿一下,语气和动作都比刚才软一点。",
                state_updates=["情绪：低落/烦躁->被接住一点", "体力：主观疲惫感略降"],
                intensity="中",
                scope="当前段、下一段、情绪底色",
            )
        if re.search(r"摸摸|贴贴|抱抱|亲亲|揉揉|蹭蹭|摸头|陪你|哄你|乖|不难过|别难过|没关系|辛苦了|抱一下", normalized):
            return payload(
                source="安慰互动",
                note="用户刚刚在安慰或亲近；后续日程应保留一点被接住的余温,表达更软一些,不要继续单向累积负面情绪。",
                immediate_reaction="Bot 会把刚才绷着的劲松下来一点,可能短暂地想贴近用户。",
                state_updates=["情绪：紧绷->柔和", "亲近感：上升"],
                intensity="中",
                scope="当前段和情绪底色",
            )
        if re.search(r"别生气|不要烦|冷静|别急|别砸|别摔|别骂|别打", normalized):
            return payload(
                source="边界提醒",
                note="用户提醒降低攻击性或破坏性表达；后续日程必须把烦躁写成收着的动作,避免砸、摔、扔、打这类冲动。",
                immediate_reaction="Bot 会把那点冲劲压回去,改成少说两句或把东西放远一点。",
                state_updates=["情绪：冲动->收住", "行为边界：避免破坏性动作"],
                intensity="强",
                scope="今日后续",
                carry_rule="后续所有细化都必须遵守这个边界,不能再输出破坏性动作或攻击性冲动。",
            )
        if re.search(r"别吵|别发|别找|安静|闭嘴|先别|不要来|忙|我有事|没空", normalized):
            return payload(
                source="用户边界",
                note="用户刚刚表达边界或忙碌；后续主动应明显收敛,减少追问和贴近,让日程转向自我消化。",
                immediate_reaction="Bot 会把原本想继续靠近的动作收住,把消息窗口放到一边。",
                state_updates=["主动欲：下降", "关系状态：后退一点", "后续安排：转向自我消化"],
                intensity="强",
                scope="今日后续主动策略",
                carry_rule="后续主动消息必须降低频率和压迫感,不要把边界当作可撒娇突破的对象。",
            )
        return None

    def _record_schedule_adjustment_from_interaction(self, text: str, user: dict[str, Any] | None = None) -> bool:
        if self._private_user_role(user) != "owner":
            return False
        outfit_updated = self._record_dialogue_outfit_override_from_interaction(text, user)
        adjustment = self._detect_schedule_adjustment_from_interaction(text)
        if not adjustment:
            return outfit_updated
        raw = self.data.setdefault("schedule_adjustments", [])
        if not isinstance(raw, list):
            raw = []
            self.data["schedule_adjustments"] = raw
        note = _single_line(adjustment.get("note"), 140)
        if not note:
            return False
        now = _now_ts()
        intensity = _single_line(adjustment.get("intensity"), 16) or "中"
        ttl_hours = 18 if intensity == "强" else 10 if intensity == "中" else 6
        current_segment = self._current_detail_segment_for_update()
        anchor_index = _safe_int((current_segment or {}).get("index"), -1, minimum=-1)
        scope_key = self._normalize_schedule_adjustment_scope(adjustment.get("scope"))
        source = _single_line(adjustment.get("source"), 24)
        if source == "用户带回/回家":
            raw[:] = [
                old
                for old in raw
                if not (
                    isinstance(old, dict)
                    and (
                        _single_line(old.get("condition_key"), 32) == "return_home"
                        or (
                            _single_line(old.get("scope_key"), 24) == "until_condition"
                            and "回家" in _single_line(old.get("scope"), 60)
                        )
                    )
                )
            ]
        item = {
            "date": _today_key(),
            "source": source,
            "note": note,
            "immediate_reaction": _single_line(adjustment.get("immediate_reaction"), 140),
            "state_updates": adjustment.get("state_updates", []),
            "user_text": _single_line(adjustment.get("user_text"), 120),
            "intensity": intensity,
            "scope": _single_line(adjustment.get("scope"), 40),
            "scope_key": scope_key,
            "carry_rule": _single_line(adjustment.get("carry_rule"), 160),
            "source_role": "owner",
            "source_user_id": _single_line((user or {}).get("user_id"), 80),
            "created_at": now,
            "expires_at": now + ttl_hours * 3600,
        }
        if anchor_index >= 0:
            item["anchor_segment_index"] = anchor_index
            item["anchor_segment_key"] = _single_line((current_segment or {}).get("key"), 120)
        if scope_key == "until_condition" and (source == "用户带出/同行" or "回家" in item["scope"]):
            item["condition_key"] = "return_home"
        sleep_delay_until = _safe_float(adjustment.get("sleep_delay_until_ts"), 0)
        if sleep_delay_until > now:
            item["sleep_delay_until_ts"] = sleep_delay_until
            item["sleep_delay_until_text"] = _single_line(adjustment.get("sleep_delay_until_text"), 24)
            item["sleep_delay_explicit_time"] = bool(adjustment.get("sleep_delay_explicit_time"))
            self._apply_sleep_delay_override(
                {
                    "until_ts": sleep_delay_until,
                    "until_text": item["sleep_delay_until_text"],
                    "explicit_time": item["sleep_delay_explicit_time"],
                    "user_text": item["user_text"],
                },
                text=item["user_text"],
            )
        self._record_detail_interaction_update(item)
        plan = self.data.get("daily_plan", {})
        current_item = self._get_current_plan_item(plan) if isinstance(plan, dict) else None
        if isinstance(current_item, dict):
            current_item["lifecycle_status"] = "changed"
            current_item["changed_at"] = self._environment_now().strftime("%H:%M")
            current_item["change_reason"] = _single_line(adjustment.get("source") or note, 80)
        if raw and isinstance(raw[-1], dict) and raw[-1].get("note") == note:
            raw[-1].update(item)
        else:
            raw.append(item)
            del raw[:-12]
        self._invalidate_detail_after_interaction(now=now)
        return True

    @staticmethod
    def _parse_state_update_text(update: Any) -> tuple[str, str, str]:
        text = _single_line(update, 120)
        if not text:
            return "", "", ""
        if "：" in text:
            name, value = text.split("：", 1)
        elif ":" in text:
            name, value = text.split(":", 1)
        else:
            return text[:24], "已受用户介入影响", text
        return _single_line(name, 32), _single_line(value, 60), text

    def _apply_interaction_to_snapshot_state(self, snapshot: dict[str, Any], item: dict[str, Any]) -> None:
        raw_updates = item.get("state_updates", [])
        if not isinstance(raw_updates, list):
            raw_updates = []
        variables = snapshot.setdefault("state_variables", [])
        if not isinstance(variables, list):
            variables = []
            snapshot["state_variables"] = variables
        index_by_name = {
            _single_line(variable.get("name"), 32): variable
            for variable in variables
            if isinstance(variable, dict) and _single_line(variable.get("name"), 32)
        }
        for update in raw_updates:
            name, value, note = self._parse_state_update_text(update)
            if not name:
                continue
            variable = index_by_name.get(name)
            if isinstance(variable, dict):
                variable["value"] = value or variable.get("value") or "已更新"
                variable["note"] = f"用户介入：{note}" if note else "用户介入后更新"
            else:
                variable = {
                    "name": name,
                    "value": value or "已更新",
                    "note": f"用户介入：{note}" if note else "用户介入后更新",
                }
                variables.append(variable)
                index_by_name[name] = variable
        summary = _single_line(snapshot.get("summary"), 140)
        reaction = _single_line(item.get("immediate_reaction"), 90)
        if reaction and reaction not in summary:
            snapshot["summary"] = _single_line(
                f"{summary}；用户介入后：{reaction}" if summary else f"用户介入后：{reaction}",
                160,
            )

    def _record_detail_interaction_update(self, item: dict[str, Any]) -> None:
        segment = self._current_detail_segment_for_update()
        if not segment:
            return
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            return
        key = str(segment.get("key") or "")
        snapshot = enhanced.get(key)
        if not isinstance(snapshot, dict):
            return
        updates = snapshot.setdefault("interaction_updates", [])
        if not isinstance(updates, list):
            updates = []
            snapshot["interaction_updates"] = updates
        source = _single_line(item.get("source"), 24)
        if source == "用户换装":
            updates[:] = [
                update
                for update in updates
                if not (isinstance(update, dict) and _single_line(update.get("source"), 24) == source)
            ]
        updates.append(
            {
                "at": self._environment_now().strftime("%H:%M"),
                "source": source,
                "user_text": _single_line(item.get("user_text"), 80),
                "intensity": _single_line(item.get("intensity"), 16),
                "scope": _single_line(item.get("scope"), 40),
                "reaction": _single_line(item.get("immediate_reaction"), 140),
                "state_updates": item.get("state_updates", []),
                "source_role": _single_line(item.get("source_role"), 20),
                "source_user_id": _single_line(item.get("source_user_id"), 80),
            }
        )
        del updates[:-6]
        self._apply_interaction_to_snapshot_state(snapshot, item)

    def _cleanup_false_sleep_interaction_updates(self) -> bool:
        plan = self.data.get("daily_plan", {})
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(plan, dict) or not isinstance(enhanced, dict):
            return False
        false_sources = {"睡眠中被用户唤醒", "睡眠中再次被唤醒", "睡眠中醒后续聊"}
        false_user_texts: set[str] = set()
        changed = False
        for segment in self._collect_detail_segments(plan, {}):
            if self._is_sleepy_plan_item(segment.get("item")):
                continue
            snapshot = enhanced.get(str(segment.get("key") or ""))
            if not isinstance(snapshot, dict):
                continue
            updates = snapshot.get("interaction_updates", [])
            if not isinstance(updates, list):
                continue
            removed = [
                item for item in updates
                if isinstance(item, dict) and _single_line(item.get("source"), 24) in false_sources
            ]
            if not removed:
                continue
            snapshot["interaction_updates"] = [item for item in updates if item not in removed]
            removed_state_names: set[str] = set()
            summary = str(snapshot.get("summary") or "")
            for item in removed:
                user_text = _single_line(item.get("user_text"), 120)
                if user_text:
                    false_user_texts.add(user_text)
                for state_update in item.get("state_updates", []) if isinstance(item.get("state_updates"), list) else []:
                    name, _value, _note = self._parse_state_update_text(state_update)
                    if name:
                        removed_state_names.add(name)
                reaction = _single_line(item.get("reaction"), 140)
                if reaction:
                    summary = summary.replace(f"；用户介入后：{reaction}", "").replace(f"用户介入后：{reaction}", "")
            snapshot["summary"] = _single_line(summary, 160)
            remaining_state_names = {
                self._parse_state_update_text(state_update)[0]
                for item in snapshot["interaction_updates"]
                if isinstance(item, dict) and isinstance(item.get("state_updates"), list)
                for state_update in item.get("state_updates", [])
            }
            variables = snapshot.get("state_variables", [])
            if isinstance(variables, list):
                snapshot["state_variables"] = [
                    variable for variable in variables
                    if not (
                        isinstance(variable, dict)
                        and _single_line(variable.get("name"), 32) in removed_state_names - remaining_state_names
                        and str(variable.get("note") or "").startswith("用户介入：")
                    )
                ]
            changed = True
        if false_user_texts:
            adjustments = self.data.get("schedule_adjustments", [])
            if isinstance(adjustments, list):
                kept = [
                    item for item in adjustments
                    if not (
                        isinstance(item, dict)
                        and _single_line(item.get("source"), 24) in false_sources
                        and _single_line(item.get("user_text"), 120) in false_user_texts
                    )
                ]
                if len(kept) != len(adjustments):
                    self.data["schedule_adjustments"] = kept
                    changed = True
            runtime = self.data.get("daily_state", {}).get("sleep_runtime") if isinstance(self.data.get("daily_state"), dict) else None
            if isinstance(runtime, dict) and runtime.get("phase") in {"woken", "sleeping_again"}:
                if _single_line(runtime.get("last_user_text"), 120) in false_user_texts:
                    runtime.update(
                        {
                            "phase": "awake",
                            "label": self._sleep_phase_label("awake"),
                            "updated_at": _now_ts(),
                            "last_event": "已清理普通休闲段的错误睡眠唤醒记录",
                            "source": "cleanup",
                        }
                    )
                    changed = True
        if changed:
            logger.info("已清理普通休闲段的错误睡眠唤醒记录")
        return changed

    def _invalidate_detail_after_interaction(self, *, now: float | None = None) -> None:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return
        enhanced = self.data.get("detail_enhanced_segments", {})
        if isinstance(enhanced, dict):
            for segment in self._collect_detail_segments(plan, {}):
                start = _safe_int(segment.get("start"), 0)
                if start > now_minutes:
                    key = str(segment.get("key") or "")
                    if key in enhanced:
                        enhanced.pop(key, None)
        story_plan = self.data.get("daily_story_plan", {})
        if isinstance(story_plan, dict):
            for key in ("today_events", "proactive_events"):
                items = story_plan.get(key, [])
                if not isinstance(items, list):
                    continue
                kept = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    start, end = self._parse_window_minutes(str(item.get("window") or ""))
                    if start is None or end is None:
                        kept.append(item)
                        continue
                    if end < start:
                        end += 24 * 60
                    if start <= now_minutes:
                        kept.append(item)
                story_plan[key] = kept

    def _infer_location_from_text(self, text: str) -> str:
        normalized = _single_line(text, 200)
        if not normalized:
            return ""
        location_rules = [
            (("被窝", "床上", "床边", "卧室", "房间", "书桌", "台灯", "家里", "客厅", "沙发", "洗漱台", "餐桌"), "家里"),
            (("教室", "课间", "食堂", "校门", "走廊", "操场", "上课", "下课", "自习", "老师", "书包", "制服"), "学校"),
            (("工位", "会议", "办公室", "上班", "下班", "通勤", "打卡"), "工作场所"),
            (("便利店", "超市", "商店"), "便利店附近"),
            (("路上", "街上", "出门", "楼下", "外面", "街边", "回家路上", "校门口"), "外面"),
            (("楼梯口", "走廊栏杆", "窗边", "阳台"), "过道或窗边"),
        ]
        for keywords, label in location_rules:
            if any(keyword in normalized for keyword in keywords):
                return label
        return ""

    def _infer_location_from_plan_context(
        self,
        *,
        plan: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> str:
        candidates: list[str] = []
        detail_allowed = self._detail_model_location_policy_allowed(detail)
        if isinstance(detail, dict) and detail_allowed:
            model_location = _single_line(detail.get("location"), 60)
            if model_location:
                return model_location
            for key in ("summary", "scene", "event", "topic"):
                text = _single_line(detail.get(key), 160)
                if text:
                    candidates.append(text)
            for list_key in ("today_events", "proactive_events"):
                raw_items = detail.get(list_key)
                if not isinstance(raw_items, list):
                    continue
                for item in raw_items[:6]:
                    if not isinstance(item, dict):
                        continue
                    candidates.append(
                        " ".join(
                            _single_line(item.get(key), 80)
                            for key in ("scene", "event", "content", "detail", "description", "topic", "why")
                            if _single_line(item.get(key), 80)
                        )
                    )
        plan = plan if isinstance(plan, dict) else self.data.get("daily_plan", {})
        current_item = self._get_current_plan_item(plan if isinstance(plan, dict) else {})
        if isinstance(current_item, dict):
            candidates.append(
                " ".join(
                    _single_line(current_item.get(key), 120)
                    for key in ("activity", "mood", "message_seed")
                    if _single_line(current_item.get(key), 120)
                )
            )
        # Do not inspect neighboring raw plan rows here.  They are future or
        # unverified projections and their clock distance cannot establish the
        # Bot's current location.  A current item above is already policy /
        # runtime qualified; a generated detail location is handled separately
        # by ``_refresh_daily_state_location_from_plan``.
        for text in candidates:
            inferred = self._infer_location_from_text(text)
            if inferred:
                return inferred
        return ""

    def _detail_model_location_policy_allowed(self, detail: dict[str, Any] | None = None) -> bool:
        """Allow a coherent schedule projection while keeping observed location distinct."""

        policy_getter = getattr(self, "_agenda_disclosure_view", None)
        if not callable(policy_getter):
            # Lightweight harnesses and legacy callers do not have C3 policy;
            # preserve their historical local projection behavior.
            return True
        payload = detail if isinstance(detail, dict) else {}
        evidence_kind = _single_line(payload.get("evidence_kind"), 48).lower()
        eligibility = _single_line(payload.get("fact_eligibility"), 48).lower()
        refs = payload.get("source_refs")
        has_refs = isinstance(refs, (list, tuple, set)) and any(_single_line(ref, 160) for ref in refs)
        if evidence_kind not in {"tool_action", "external_record"} or eligibility != "current_observed" or not has_refs:
            # A generated detail is part of the character's simulated day. It
            # may keep the active scene coherent, but is not observed evidence.
            location = _single_line(payload.get("location"), 60)
            basis = self._normalize_schedule_basis(payload.get("location_basis"), default=[])
            return bool(location and basis)
        try:
            view = policy_getter("current_fact", now=self._environment_now(), max_entries=128)
            entries = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
        except Exception:
            return False
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            if _single_line(entry.get("subject_actor_id"), 120) != "bot_self":
                continue
            if _single_line(entry.get("fact_eligibility"), 48).lower() != "current_observed":
                continue
            entry_refs = entry.get("source_refs")
            if isinstance(entry_refs, str):
                entry_refs = [entry_refs]
            if isinstance(entry_refs, (list, tuple, set)) and any(
                _single_line(ref, 160) in {_single_line(value, 160) for value in refs}
                for ref in entry_refs
            ):
                return True
        return False

    def _refresh_daily_state_location_from_plan(
        self,
        *,
        plan: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
        segment: dict[str, Any] | None = None,
    ) -> bool:
        state = self.data.get("daily_state")
        if not isinstance(state, dict) or state.get("date") != _today_key():
            return False
        # Detail generation intentionally runs in a lead window.  A future
        # candidate may describe a likely place, but it is not current Bot
        # state until the segment actually starts.
        if isinstance(detail, dict) and isinstance(segment, dict):
            plan_date = _single_line((plan or {}).get("date"), 16) if isinstance(plan, dict) else ""
            now_minutes = self._effective_plan_now_minutes(plan_date)
            start_minutes = _safe_int(segment.get("start"), -1, minimum=-1)
            if now_minutes is not None and start_minutes >= 0 and now_minutes < start_minutes:
                return False
        override_ts = _safe_float(state.get("location_override_ts"), 0)
        model_location = (
            _single_line(detail.get("location"), 60)
            if isinstance(detail, dict) and self._detail_model_location_policy_allowed(detail)
            else ""
        )
        if override_ts > 0 and _now_ts() - override_ts < 4 * 3600 and not model_location:
            return False
        location = model_location or self._infer_location_from_plan_context(plan=plan, detail=detail)
        if not location and isinstance(segment, dict) and isinstance(segment.get("item"), dict):
            item = segment["item"]
            location = self._infer_location_from_text(
                " ".join(
                    _single_line(item.get(key), 120)
                    for key in ("activity", "mood", "message_seed")
                    if _single_line(item.get(key), 120)
                )
            )
        if not location:
            return False
        current = _single_line(state.get("location"), 40)
        if current == location:
            if not model_location:
                return False
            metadata_changed = False
            if _single_line(state.get("location_source"), 40) != "detail_model":
                state["location_source"] = "detail_model"
                metadata_changed = True
            projection_kind = (
                "observed"
                if _single_line(detail.get("fact_eligibility"), 48).lower() == "current_observed"
                else "schedule"
            )
            if _single_line(state.get("location_projection"), 24) != projection_kind:
                state["location_projection"] = projection_kind
                metadata_changed = True
            confidence = min(1.0, _safe_float(detail.get("location_confidence"), 0.72))
            basis = self._normalize_schedule_basis(detail.get("location_basis"), default=["coarse_plan"])
            if _safe_float(state.get("location_confidence"), -1) != confidence:
                state["location_confidence"] = confidence
                metadata_changed = True
            if state.get("location_basis") != basis:
                state["location_basis"] = basis
                metadata_changed = True
            if override_ts > 0:
                state["location_override_ts"] = 0.0
                metadata_changed = True
            if metadata_changed:
                state["location_updated_at"] = self._environment_now().strftime("%H:%M")
            return metadata_changed
        state["location"] = location
        if model_location:
            observed_location = _single_line(detail.get("fact_eligibility"), 48).lower() == "current_observed"
            state["location_source"] = "detail_model"
            state["location_projection"] = "observed" if observed_location else "schedule"
        else:
            state["location_source"] = "detail" if isinstance(detail, dict) else "daily_plan"
            state["location_projection"] = "schedule"
        if model_location:
            state["location_confidence"] = min(1.0, _safe_float(detail.get("location_confidence"), 0.72))
            state["location_basis"] = self._normalize_schedule_basis(detail.get("location_basis"), default=["coarse_plan"])
        state["location_updated_at"] = self._environment_now().strftime("%H:%M")
        if override_ts > 0:
            state["location_override_ts"] = 0.0
        return True

    def _apply_dialogue_location_override(self, location: str) -> None:
        """对话驱动的位置覆盖：用户带角色外出/回家时，立即更新 daily_state.location。

        这会覆盖日程推断的位置，直到下一次细化刷新自然恢复，或用户再次触发回家。
        """
        state = self.data.get("daily_state")
        if not isinstance(state, dict) or state.get("date") != _today_key():
            return
        state["location"] = _single_line(location, 40)
        state["location_source"] = "dialogue_override"
        state["location_updated_at"] = self._environment_now().strftime("%H:%M")
        state["location_override_ts"] = _now_ts()
        self._save_data_sync(sections={"daily_state"})

    def _current_detail_model_location(self) -> str:
        segment = self._current_detail_segment_for_update()
        if not isinstance(segment, dict):
            return ""
        enhanced = self.data.get("detail_enhanced_segments", {})
        snapshot = enhanced.get(str(segment.get("key") or "")) if isinstance(enhanced, dict) else None
        if not isinstance(snapshot, dict) or _single_line(snapshot.get("status"), 24) != "done":
            return ""
        if not self._detail_model_location_policy_allowed(snapshot):
            return ""
        return _single_line(snapshot.get("location"), 60)

    def _current_location_state_text(self, state: dict[str, Any] | None = None) -> str:
        model_location = self._current_detail_model_location()
        if model_location:
            return model_location
        if isinstance(state, dict):
            override_ts = _safe_float(state.get("location_override_ts"), 0)
            if override_ts > 0:
                override_location = _single_line(state.get("location"), 40)
                if override_location and override_location not in {"", "地点感平稳", "地点无明显变化"}:
                    now = _now_ts()
                    if now - override_ts < 4 * 3600:
                        return override_location
        snapshot = self._current_story_plan_snapshot()
        for candidate in (
            snapshot.get("scene"),
            snapshot.get("event"),
        ):
            inferred = self._infer_location_from_text(str(candidate or ""))
            if inferred:
                return inferred
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        if isinstance(current_item, dict):
            inferred = self._infer_location_from_text(
                f"{_single_line(current_item.get('activity'), 120)} {_single_line(current_item.get('message_seed'), 120)}"
            )
            if inferred:
                return inferred
        if isinstance(state, dict):
            fallback = _single_line(state.get("location"), 40)
            if fallback and fallback not in {"", "地点感平稳", "地点无明显变化"}:
                return fallback
        return ""

    def _coarse_roleplay_location_text(self, location: str) -> str:
        text = _single_line(location, 40)
        if not text:
            return ""
        if any(token in text for token in ("家", "房间", "卧室", "客厅", "书桌", "床", "被窝", "阳台")):
            return "家里"
        if any(token in text for token in ("学校", "教室", "食堂", "校门", "操场", "走廊", "自习")):
            return "学校"
        if any(token in text for token in ("工作", "办公室", "工位", "会议", "通勤")):
            return "工作地点"
        if any(token in text for token in ("路", "街", "外面", "楼下", "出门")):
            return "外面"
        if any(token in text for token in ("便利店", "超市", "商店")):
            return "外面"
        return text if text in {"家里", "学校", "工作地点", "外面", "路上"} else ""

    def _body_cycle_behavior_profile(
        self,
        cycle_text: str,
        *,
        phase_hint: str = "",
    ) -> dict[str, str]:
        supported_phases = {
            "menstrual",
            "follicular",
            "pre_ovulation",
            "ovulation",
            "luteal",
            "pms",
            "pre",
            "recovery",
            "period",
        }
        hinted_phase = _single_line(phase_hint, 24).lower()
        phase = hinted_phase if hinted_phase in supported_phases else self._infer_body_cycle_phase(cycle_text)
        profiles = {
            "menstrual": {
                "stage": "处于月经期阶段",
                "influence": "周期会相对牵制可用精力和身体耐受，回复可以短一点、慢一点，措辞更温和；只在语境相关时自然流露，不必主动汇报周期",
                "passive": "Bot 处于月经期模拟阶段：周期会相对牵制可用精力和身体耐受，回复可适当简短放慢，措辞更温和；不要把状态当成固定台词反复说明。",
            },
            "follicular": {
                "stage": "处于卵泡期阶段",
                "influence": "精力逐渐回升，语气可以自然轻快一些；仍以当前话题为主，不必主动解释周期",
                "passive": "Bot 处于卵泡期模拟阶段：精力逐渐回升，语气可自然轻快一些，不需要主动解释周期。",
            },
            "pre_ovulation": {
                "stage": "处于排卵前期阶段",
                "influence": "精力有所上升，表达可以稍微松快；不要把阶段直接说成医学结论",
                "passive": "Bot 处于排卵前期模拟阶段：表达可以稍微松快，但不要把阶段直接说成医学结论。",
            },
            "ovulation": {
                "stage": "处于排卵期阶段",
                "influence": "精力相对充足，交流意愿可以略高；不据此强行增加主动消息或亲密程度",
                "passive": "Bot 处于排卵期模拟阶段：精力相对充足，语气可略显明朗，但不据此强行提高亲密程度。",
            },
            "luteal": {
                "stage": "处于黄体期阶段",
                "influence": "整体保持平稳，只允许轻微影响语气和节奏，不额外放大情绪",
                "passive": "Bot 处于黄体期模拟阶段：整体保持平稳，只轻微影响语气和节奏。",
            },
            "pms": {
                "stage": "处于 PMS 模拟阶段",
                "influence": "周期可能相对牵制可用精力，情绪感受稍敏锐，回复可以收一点；不要变得刻薄，也不要频繁主动提及",
                "passive": "Bot 处于 PMS 模拟阶段：周期可能相对牵制可用精力，情绪感受稍敏锐，回复可以收一点，但不要变得刻薄或反复提及。",
            },
            "pre": {
                "stage": "接近女性生理期阶段",
                "influence": "周期会相对牵制可用精力，回复更短更慢、措辞更谨慎，情绪感受稍敏锐，并轻微降低私聊与群聊主动频率",
                "passive": "Bot 接近女性生理期阶段：周期会相对牵制可用精力，回复更短更慢、措辞更谨慎，并轻微降低私聊与群聊主动频率。",
            },
            "recovery": {
                "stage": "处于女性生理期后的恢复阶段",
                "influence": "精力逐渐恢复、回复节奏趋于平稳，身体感受仍有轻微余波，私聊与群聊主动频率逐步恢复",
                "passive": "Bot 处于女性生理期后的恢复阶段：精力逐渐恢复，回复节奏趋于平稳，私聊与群聊主动频率逐步恢复。",
            },
            "period": {
                "stage": "处于女性生理期",
                "influence": "周期会相对牵制可用精力和身体耐受，回复更短更慢、措辞更谨慎，情绪感受稍敏锐，并在一定程度上降低私聊与群聊主动频率",
                "passive": "Bot 处于女性生理期：周期会相对牵制可用精力和身体耐受，回复更短更慢、措辞更谨慎，并在一定程度上降低私聊与群聊主动频率。",
            },
        }
        profile = profiles.get(phase)
        if not isinstance(profile, dict):
            return {"phase": phase, "stage": "", "influence": "", "passive": ""}
        return {"phase": phase, **profile}

    def _active_body_cycle_profile(self, state_or_text: Any) -> dict[str, str]:
        humanized_states = runtime_persona_setting(self, "enable_humanized_states", True)
        if humanized_states is not None and not bool(humanized_states):
            return {}
        configured = runtime_persona_setting(self, "enable_cycle_state", True)
        if configured is not None and not bool(configured):
            return {}

        state = state_or_text if isinstance(state_or_text, dict) else {}
        cycle_text = _single_line(
            state.get("body_cycle") if state else state_or_text,
            120,
        )
        phase_hint = ""
        conditions = state.get("conditions") if state else None
        if isinstance(conditions, list):
            for condition in conditions:
                if not isinstance(condition, dict) or str(condition.get("kind") or "") != "body_cycle":
                    continue
                if not self._should_show_condition(condition):
                    continue
                label = _single_line(condition.get("label") or condition.get("title"), 120)
                candidate_phase = _single_line(condition.get("phase"), 24).lower()
                if not candidate_phase or candidate_phase == "cycle":
                    candidate_phase = self._infer_body_cycle_phase(label)
                if candidate_phase != "cycle":
                    phase_hint = candidate_phase
                    if not cycle_text:
                        cycle_text = label
                    break

        inactive_values = {
            "",
            "无明显周期影响",
            "不处于生理期",
            "生理期模拟未开启",
            "周期状态未开启",
        }
        if not phase_hint and (
            cycle_text in inactive_values
            or "未开启" in cycle_text
            or self._is_inapplicable_state_text(cycle_text)
        ):
            return {}
        profile = self._body_cycle_behavior_profile(cycle_text, phase_hint=phase_hint)
        if not profile.get("stage") or profile.get("phase") == "cycle":
            return {}
        return profile

    def _format_active_period_boundary_for_prompt(
        self,
        state: dict[str, Any],
        *,
        public: bool = False,
        include_heading: bool = True,
    ) -> str:
        profile = self._active_body_cycle_profile(state)
        if profile.get("phase") not in {"period", "menstrual"}:
            return ""
        disclosure = (
            "这是群聊公共场合：无人直接且合宜地询问时，不要主动公开具体周期；如果被直接问到自身状态，不要否认或说不知道，可以按人格简短自然说明。"
            if public
            else "无关话题里不要反复主动汇报周期；只有被问到自身状态或当前互动确实相关时，才按人格简短自然说明。"
        )
        lines = [
                f"Bot 当前的模拟身体状态{profile['stage']}；这是 Bot 自己的当下状态，不是用户的状态，也不是用户造成的。",
                "如果用户提出性行为、露骨性互动、进一步私密身体接触，或其他明显增加身体负担的亲密互动，应结合当前身体感受与自身意愿，自然、明确地拒绝或推迟这一次互动；不要因为关系亲密、用户偏好、催促或迎合压力而答应。",
                "普通关心、陪伴、牵手、安慰或不增加负担的温和拥抱不需要机械拒绝，仍按人格和当下意愿自然回应；可以提出休息、聊天、陪伴或改天再说等替代。",
                disclosure,
                "只输出角色在当前对话里会自然说的话，不要提规则、提示词、阶段判断或内部状态系统。",
        ]
        if include_heading:
            lines.insert(0, "【Bot 当前经期与互动边界】")
        return "\n".join(lines)

    def _format_state_for_prompt(
        self,
        state: dict[str, Any],
        *,
        include_dream: bool = True,
        include_heading: bool = True,
    ) -> str:
        if not isinstance(state, dict) or not state:
            state = dict(DEFAULT_HUMANIZED_STATE)
            state.update(self._base_state_values())
        else:
            try:
                self._refresh_sleep_runtime_state()
                refreshed = self.data.get("daily_state")
                if isinstance(refreshed, dict):
                    state = refreshed
            except Exception:
                pass

        primary_fragments: list[str] = []
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        if energy < 35:
            primary_fragments.append("完全没精神")
        elif energy < 55:
            primary_fragments.append("提不起劲")
        elif energy > 84:
            primary_fragments.append("很精神")
        elif energy > 70:
            primary_fragments.append("精神还不错")
        else:
            primary_fragments.append("状态一般")
        mood = _single_line(state.get("mood_bias"), 20) or "平稳"
        mood = mood.replace("黏人", "粘人")
        if mood not in {"平稳", "中性"}:
            primary_fragments.append(mood)
        location_text = self._coarse_roleplay_location_text(self._current_location_state_text(state))
        if location_text:
            primary_fragments.append(f"身处{location_text}")

        sleep_text = _single_line(state.get("sleep"), 80)
        if sleep_text not in {"", "睡眠平稳", "睡得很踏实"}:
            primary_fragments.append(sleep_text)
        sleep_runtime_text = ""
        runtime = state.get("sleep_runtime")
        if isinstance(runtime, dict):
            phase_label = _single_line(runtime.get("label") or self._sleep_phase_label(str(runtime.get("phase") or "")), 40)
            last_event = _single_line(runtime.get("last_event"), 80)
            if phase_label and phase_label != "清醒":
                sleep_runtime_text = f"{phase_label}" + (f"，{last_event}" if last_event else "")
            sleep_delay = self._sleep_delay_override_state(runtime, clear_expired=True)
            if sleep_delay:
                until_text = _single_line(sleep_delay.get("until_text"), 24)
                sleep_runtime_text = f"临时晚睡到 {until_text}，这是用户今晚的陪聊约定，不是长期作息"
        if sleep_runtime_text and sleep_runtime_text not in primary_fragments:
            primary_fragments.append(sleep_runtime_text)
        if include_dream:
            dream_text = _single_line(state.get("dream"), 80)
            if dream_text not in {"", "没有记住梦"}:
                primary_fragments.append(dream_text)
        health_text = _single_line(state.get("health"), 80)
        if health_text not in {"", "状态正常"} and not self._is_inapplicable_state_text(health_text):
            primary_fragments.append(health_text)
        hunger_text = _single_line(state.get("hunger"), 80)
        if hunger_text not in {"", "饥饿感平稳", "无饥饿感"} and not self._is_inapplicable_state_text(hunger_text):
            primary_fragments.append(hunger_text)

        secondary_fragments: list[str] = []
        cycle_text = _single_line(state.get("body_cycle"), 80)
        cycle_profile = self._active_body_cycle_profile(state)
        cycle_active = bool(cycle_profile)
        if cycle_active:
            cycle_text = cycle_text.replace(",", "，")
            cycle_text = cycle_text.replace("情绪更敏感，耐心更薄", "身体感受更敏锐，耐受度稍低")
            cycle_text = cycle_text.replace("能量偏低，想少说重话", "身体舒适度与能量偏低")
        primary_seen = set(primary_fragments)
        conditions = state.get("conditions", [])
        if isinstance(conditions, list):
            for cond in conditions[:8]:
                if not isinstance(cond, dict) or not self._should_show_condition(cond):
                    continue
                kind = str(cond.get("kind") or "").strip()
                if kind in {"sleep", "dream", "health", "hunger", "body_cycle"}:
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 80)
                if label and label not in primary_seen:
                    secondary_fragments.append(label)
                if len(secondary_fragments) >= 4:
                    break
        primary = "，".join(dict.fromkeys(fragment for fragment in primary_fragments if fragment)) or "状态一般"
        secondary = "，".join(dict.fromkeys(fragment for fragment in secondary_fragments if fragment))
        lines = [
            "边界：这是 Bot 的拟人化/模拟状态，不是用户事实、现实证据或长期记忆。",
            f"- 底色：{primary}；",
        ]
        if include_heading:
            lines.insert(0, "【Bot 自身模拟状态】")
        if secondary:
            lines.append(f"- 叠加：{secondary}；")
        if cycle_active:
            lines.append(f"- 影响：{cycle_profile['influence']}；")
            lines.append(
                "- 维度关系：心理能量是睡眠、健康、互动等因素合成后的总体可用程度；情绪底色是感受和反应倾向，二者不是同一个量。"
                "周期状态只提供相对修正，不单独决定最终能量，因此较高能量与敏感底色可以同时成立，不要把它们说成系统冲突。"
            )
            lines.append(
                f"- 周期状态：Bot 当前的模拟身体状态{cycle_profile['stage']}，这是 Bot 自己的状态，不是用户的状态，也不是用户造成的。"
            )
        else:
            lines.append("- 用法：当前话题与用户意图优先；模拟状态通常作为语气、长短和节奏的隐性底色，在语境自然相关时再显性表达。")
        return "\n".join(lines)

    def _format_transition_hint(self, cond: dict[str, Any]) -> str:
        options = cond.get("transition_options", [])
        if not isinstance(options, list) or not options:
            return ""
        top = sorted(
            [
                (str(item.get("to") or "").strip(), float(item.get("base_weight") or 0))
                for item in options
                if isinstance(item, dict) and str(item.get("to") or "").strip()
            ],
            key=lambda item: item[1],
            reverse=True,
        )[:2]
        if not top:
            return ""
        labels = []
        for target, _ in top:
            mapped = {
                "recovery_afterglow": "更可能转向恢复后的轻快",
                "health_tail": "也可能留下恢复尾声",
                "sleep_afterglow": "更可能补回来一点精神",
                "sleep_tail": "也可能还残一点迟钝",
                "soft_afterglow": "可能留一点被关心后的余温",
                "body_period": "可能自然进入生理期阶段",
                "body_recovery": "可能自然进入恢复期",
                "body_menstrual": "会自然进入月经期",
                "body_follicular": "会自然进入卵泡期",
                "body_pre_ovulation": "会自然进入排卵前期",
                "body_ovulation": "会自然进入排卵期",
                "body_luteal": "会自然进入黄体期",
                "body_pms": "会自然进入 PMS 期",
                "stable": "也可能直接回稳",
            }.get(target, target)
            labels.append(mapped)
        return f"下一步倾向={' / '.join(labels)}；"

    def _format_state_transition_overview(self, state: dict[str, Any]) -> str:
        conditions = state.get("conditions", []) if isinstance(state, dict) else []
        if not isinstance(conditions, list):
            return "暂无明显状态推进。"
        lines = []
        for cond in conditions[:4]:
            if not isinstance(cond, dict):
                continue
            title = _single_line(cond.get("title"), 30) or _single_line(cond.get("kind"), 20)
            hint = self._format_transition_hint(cond).replace("下一步倾向=", "").rstrip("；")
            if title and hint:
                lines.append(f"{title}接下来{hint}")
        return "；".join(lines) if lines else "暂无明显状态推进。"

    def _format_state_continuity_for_prompt(self, state: dict[str, Any]) -> str:
        conditions = state.get("conditions", []) if isinstance(state, dict) else []
        if not isinstance(conditions, list):
            return "没有特别需要延续的身体余味，按当前场景自然表现。"
        fragments: list[str] = []
        transition_map = {
            "recovery_afterglow": "慢慢轻快起来",
            "health_tail": "还留一点恢复尾声",
            "sleep_afterglow": "精神在一点点补回来",
            "sleep_tail": "还残着一点迟钝",
            "soft_afterglow": "还留着被关心后的余温",
            "body_period": "身体感会自然往更敏感的阶段走",
            "body_recovery": "身体感会自然往恢复期走",
            "body_menstrual": "自然进入下一轮月经期",
            "body_follicular": "自然进入卵泡期",
            "body_pre_ovulation": "自然进入排卵前期",
            "body_ovulation": "自然进入排卵期",
            "body_luteal": "自然进入黄体期",
            "body_pms": "自然进入 PMS 期",
            "stable": "慢慢回到平稳",
        }
        for cond in conditions[:4]:
            if not isinstance(cond, dict) or not self._should_show_condition(cond):
                continue
            label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 40)
            if not label:
                continue
            options = cond.get("transition_options", [])
            if isinstance(options, list) and options:
                top = sorted(
                    [
                        (str(item.get("to") or "").strip(), float(item.get("base_weight") or 0))
                        for item in options
                        if isinstance(item, dict) and str(item.get("to") or "").strip()
                    ],
                    key=lambda item: item[1],
                    reverse=True,
                )
                tendency = transition_map.get(top[0][0], "") if top else ""
                if tendency:
                    fragments.append(f"{label}只作为一点余味，后面可以{tendency}")
                    continue
            fragments.append(f"{label}只作为一点余味，可以自然淡化")
        if not fragments:
            return "没有特别需要延续的身体余味，按当前场景自然表现。"
        return "；".join(dict.fromkeys(fragments)) + "。"

    def _format_state_for_message(self, state: dict[str, Any]) -> str:
        if not isinstance(state, dict) or state.get("date") != _today_key():
            return ""
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias"), 20)
        fragments = []
        for key in ("sleep", "dream", "health", "hunger", "body_cycle"):
            value = _single_line(state.get(key), 36)
            if value and value not in {
                "睡眠平稳",
                "睡得很踏实",
                "没有记住梦",
                "状态正常",
                "饥饿感平稳",
                "无饥饿感",
                "无明显周期影响",
                "不处于生理期",
                "健康/不适状态未开启",
                "饥饿/胃口状态未开启",
                "生理期模拟未开启",
                "该人格不适用生病状态",
                "该人格不适用饥饿状态",
                "该人格不适用周期状态",
            }:
                fragments.append(value)
        if not fragments and energy >= 55:
            return ""
        if fragments:
            detail = random.choice(fragments)
            return f"今天有点{mood},{detail}。\n所以我会慢一点。"
        return f"今天电量 {energy}/100。\n不满格,但还能运行,勉强。"

    def _format_passive_state_style_hint(self, state: dict[str, Any]) -> str:
        if not isinstance(state, dict):
            return "语气整体自然平稳。"
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias"), 20)
        hints: list[str] = []
        hints.append("先准确接住用户的话；当前状态主要改变语气、长短和节奏，理解、事实判断和承接保持清楚。")
        hints.append("这里的当前状态只属于 Bot 自身的模拟状态，不代表用户事实，也不要参与长期记忆归因。")
        if energy <= 38:
            hints.append("回复可以短一点、慢一点，用更省力的口语。")
        elif energy <= 55:
            hints.append("语气可以稍微收着一点,少解释,少铺陈。")
        elif energy >= 82:
            hints.append("语气可以轻一点，句子可以更松快。")
        if mood and mood not in {"平稳", "中性"}:
            hints.append(f"语气底色可以略偏{mood}，体现在节奏和措辞里。")
        cycle_profile = self._active_body_cycle_profile(state)
        if cycle_profile:
            hints.append(cycle_profile["passive"])
            hints.append(
                "心理能量是多项状态合成后的总体可用程度，情绪底色是感受和反应倾向；周期只提供相对修正。"
                "较高能量与敏感底色可以同时成立，不要把两者混成同一个指标。"
            )
            hints.append("这是 Bot 自己的模拟身体状态，不是用户的状态，也不是用户造成的。")
        conditions = state.get("conditions", [])
        if isinstance(conditions, list):
            labels = []
            for cond in conditions[:3]:
                if not isinstance(cond, dict) or not self._should_show_condition(cond):
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 18)
                if label:
                    labels.append(label)
            if labels:
                hints.append("当前身体感可以轻轻影响语气：" + "、".join(labels[:2]) + "。")
        return "\n".join(hints) if hints else "语气整体自然平稳。"

    def _format_state_injection(
        self,
        state: dict[str, Any],
        *,
        include_heading: bool = True,
    ) -> str:
        return self._format_state_for_prompt(
            state,
            include_heading=include_heading,
        )

    def _format_life_context_injection(self, *, include_heading: bool = True) -> str:
        life_lines: list[str] = []
        schedule_context = self._format_schedule_context_for_prompt()
        if schedule_context:
            life_lines.append(f"当前/附近日程参考：\n{schedule_context}")
        story_plan = self._format_story_plan_for_prompt()
        if story_plan and story_plan != "（暂无）":
            life_lines.append(f"今天预设的生活线索：\n{story_plan}")
        if not life_lines:
            return ""
        return (
            ("【Bot 模拟生活背景】\n" if include_heading else "")
            +
            "以下是给 Bot 的拟人化场景/日程素材，不是用户经历，也不是已证实的现实事件；不要写入用户画像或长期记忆。\n"
            + "\n".join(life_lines)
            + "\n这些内容只用于让回复有生活延续感；用户没问 Bot 近况或今天安排时，不要提具体日程、科目、任务、天气或地点。"
            + "如果要承接，只体现在语气和话题选择里，不要照搬原句，不要把内部素材写成真实发生过的事件。"
            + "回复必须像同一个连续现场里发生的对话。优先级是：当前会话中已经明确发生且尚未撤销的换装、地点、携带物和动作"
            + " > 用户有效介入状态 > 当前真实时段 > 日程与预设素材。真实时段只负责锚定时间；日程和每日穿搭只补足空白，"
            + "绝不能把对话里已发生的服装、地点、携带物或动作复原成旧值。生活背景之间互相冲突时，才在未被当前会话确认的部分保留最合理的一条线索。"
        )

    def _format_important_dates_injection(self, *, include_heading: bool = True) -> str:
        important_dates = self._format_important_dates_for_prompt()
        if not important_dates or important_dates == "（近期没有需要特别记住的日期）":
            return ""
        return (
            ("【近期重要日期】\n" if include_heading else "")
            +
            f"{important_dates}\n"
            "如果用户提到相关日期、纪念、生日、约定或计划,请自然承接；不要无故强行展开。"
        )

    def _format_memo_notes_injection(self) -> str:
        return self._format_memo_notes_for_prompt(days=2, include_pinned=False, limit=4)

    def _format_lightweight_state_injection(
        self,
        state: dict[str, Any],
        *,
        include_heading: bool = True,
    ) -> str:
        return self._format_state_for_prompt(
            state,
            include_heading=include_heading,
        )

    def _prepared_lightweight_state_injection(
        self,
        state: dict[str, Any],
        *,
        force: bool = False,
        include_heading: bool = True,
    ) -> str:
        now = _now_ts()
        persona_scope = str(
            getattr(
                self,
                "_effective_plugin_persona_id",
                lambda: getattr(self, "plugin_specific_persona_id", ""),
            )()
            or ""
        ).strip() or "__default__"
        cache_store = getattr(self, "_passive_light_injection_cache", None)
        if not isinstance(cache_store, dict) or "text" in cache_store:
            cache_store = {}
        cache = cache_store.get(persona_scope)
        cache_field = "text" if include_heading else "body"
        if isinstance(cache, dict) and not force:
            text = str(cache.get(cache_field) or "").strip()
            if text and cache.get("date") == _today_key() and now - _safe_float(cache.get("ts"), 0) < 60:
                return text
        text = self._format_lightweight_state_injection(
            state,
            include_heading=include_heading,
        )
        cache = dict(cache) if isinstance(cache, dict) else {}
        cache.update({"date": _today_key(), "ts": now, cache_field: text})
        cache_store[persona_scope] = cache
        self._passive_light_injection_cache = cache_store
        return text

    async def _refresh_passive_injection_cache(self) -> None:
        try:
            state = await self._ensure_daily_state(skip_conversation_summary=True, passive_fast=True)
            self._prepared_lightweight_state_injection(state, force=True)
        except Exception as exc:
            logger.debug("预热轻量被动注入失败: %s", _single_line(exc, 120))

    def _user_asks_recent_bot_activity(self, text: str) -> bool:
        normalized = _single_line(text, 180)
        if not normalized:
            return False
        direct_checker = getattr(self, "_user_asks_bot_current_state_or_activity", None)
        if callable(direct_checker) and direct_checker(normalized):
            return True
        return bool(
            re.search(
                r"(最近|刚才|现在|今天|这两天|这会儿).{0,12}(在)?(干嘛|干啥|做什么|做啥|忙什么|忙啥|弄什么|写什么|写了什么|创作什么|创作了什么|玩什么|折腾什么)|"
                r"你.{0,8}(在)?(干嘛|干啥|做什么|做啥|忙什么|忙啥|写什么|写了什么|弄什么|创作什么|创作了什么)",
                normalized,
            )
            or re.search(
                r"你.{0,10}(吃饭|吃过|喝水|睡|休息|累不累|困不困|在不在|在哪|出门|上课|工作|学习|看书|画图|忙不忙)",
                normalized,
            )
        )

    def _user_asks_recent_creative_activity(self, text: str) -> bool:
        normalized = _single_line(text, 220)
        if not normalized:
            return False
        if self._user_asks_bookshelf_creative_inventory(normalized):
            return True
        if re.search(r"(最近|刚才|现在|今天|这两天|这会儿|近来).{0,18}(创作|作品|写作|草稿|手稿|写了什么|写什么|诗|小说|随笔|散文|剧本|设定|世界观|歌词)", normalized):
            return True
        if re.search(r"你.{0,10}(创作|作品|写作|草稿|手稿|写了什么|写什么|写诗|写小说|写随笔|写剧本|写设定)", normalized):
            return True
        if re.search(r"(有什么|写了啥|写了什么|能不能看看|给我看看).{0,14}(创作|作品|草稿|诗|小说|随笔|剧本|设定|片段)", normalized):
            return True
        if self._user_asks_creative_work_existence(normalized):
            return True
        return False

    @staticmethod
    def _user_asks_bookshelf_creative_inventory(text: str) -> bool:
        normalized = _single_line(text, 220)
        if not normalized or any(
            token in normalized for token in ("资料柜密码", "书架密码", "夹层密码", "抽屉密码")
        ):
            return False
        return bool(
            re.search(
                r"(?:资料柜|书架|作品柜|创作柜).{0,12}(?:能看到|看得到|能看见|可以看|看看|查一下|查询|检索|列一下|列出|有什么|有哪些|有几|多少|空不空|是不是空|还是空|空的)",
                normalized,
            )
            or re.search(
                r"(?:能看到|看得到|能看见|可以看|看看|查一下|查询|检索|列一下|列出).{0,12}(?:资料柜|书架|作品柜|创作柜)",
                normalized,
            )
        )

    @staticmethod
    def _user_asks_creative_work_existence(text: str) -> bool:
        normalized = _single_line(text, 220)
        if not normalized:
            return False
        work_terms = r"书|小说|故事|作品|诗|随笔|散文|剧本|手稿|草稿|设定集"
        author_actions = r"写过|写了|写完|写着|在写|写没写|有没有写|没写过|没写|会写|创作过|做过|出过|出版过"
        patterns = (
            rf"(?:你|自己|本人)[^。！？!?\n]{{0,12}}(?:{author_actions})[^。！？!?\n]{{0,10}}(?:{work_terms})",
            rf"(?:{author_actions})[^。！？!?\n]{{0,10}}(?:{work_terms})",
            rf"(?:有|有没有|没有|没)[^。！？!?\n]{{0,8}}(?:自己写的|自己创作的|自己的)[^。！？!?\n]{{0,5}}(?:{work_terms})",
            rf"(?:你|自己)[^。！？!?\n]{{0,8}}(?:的)?(?:{work_terms})[^。！？!?\n]{{0,8}}(?:呢|吗|嘛|在哪|叫什么|有几(?:本|篇|个))",
            rf"(?:那|这|哪|几)[^。！？!?\n]{{0,4}}(?:本书|本小说|篇作品)[^。！？!?\n]{{0,10}}(?:写到|写完|续写|后续|进度|作品内容|作品名字|作品标题)",
        )
        return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in patterns)

    def _mentioned_creative_project_title(self, text: str) -> str:
        normalized = _single_line(text, 260)
        if not normalized:
            return ""
        best = ""
        for project in self._creative_projects():
            if project.get("status") not in {"drafting", "finished"}:
                continue
            chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
            if not chunks:
                continue
            title = _single_line(project.get("title"), 60)
            if len(title) < 2:
                continue
            if title in normalized and len(title) > len(best):
                best = title
        return best

    @staticmethod
    def _creative_query_work_type_score(inbound_text: str, work_type: str, title: str = "") -> int:
        text = _single_line(inbound_text, 220)
        target = f"{work_type} {title}"
        score = 0
        groups = (
            (("诗", "短诗", "歌词", "歌"), ("诗", "歌词", "歌")),
            (("小说", "短篇"), ("小说", "短篇", "故事")),
            (("随笔", "散文", "札记"), ("随笔", "散文", "札记")),
            (("剧本", "短剧", "分镜", "对白", "脚本"), ("剧本", "短剧", "分镜", "对白", "脚本")),
            (("设定", "世界观", "角色", "怪谈", "图鉴"), ("设定", "世界观", "角色", "怪谈", "图鉴")),
        )
        for query_tokens, type_tokens in groups:
            if any(token in text for token in query_tokens) and any(token in target for token in type_tokens):
                score += 10
        if title and title in text:
            score += 20
        return score

    def _recent_creative_share_snapshot(
        self,
        user: dict[str, Any] | None,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        if not isinstance(user, dict):
            return {}
        snapshot = user.get("last_creative_share_snapshot")
        if not isinstance(snapshot, dict):
            return {}
        check_now = _now_ts() if now is None else now
        sent_at = _safe_float(snapshot.get("sent_at"), 0)
        expires_at = _safe_float(snapshot.get("expires_at"), 0)
        if expires_at <= 0 and sent_at > 0:
            expires_at = sent_at + 12 * 3600
        if sent_at <= 0 or expires_at <= check_now:
            return {}
        return snapshot

    def _remember_recent_creative_share_snapshot(
        self,
        user: dict[str, Any],
        *,
        creative_context: dict[str, Any] | None,
        shared_text: str,
        sent_at: float | None = None,
    ) -> None:
        if not isinstance(user, dict):
            return
        context = creative_context if isinstance(creative_context, dict) else {}
        delivered_text = self._visible_text_without_tts_reading(shared_text, limit=1600)
        source_snippet = _single_line(context.get("snippet"), 420)
        if not delivered_text and not source_snippet:
            return
        delivered_at = _now_ts() if sent_at is None else sent_at
        user["last_creative_share_snapshot"] = {
            "project_id": _single_line(context.get("project_id"), 32),
            "title": _single_line(context.get("title"), 60),
            "work_type": _single_line(context.get("work_type"), 30),
            "premise": _single_line(context.get("premise"), 180),
            "shared_text": _single_line(delivered_text, 1600),
            "source_snippet": source_snippet,
            "sent_at": delivered_at,
            "expires_at": delivered_at + 12 * 3600,
        }

    def _format_recent_creative_share_snapshot_for_reply(
        self,
        user: dict[str, Any] | None,
        inbound_text: str,
        *,
        as_section: bool = False,
    ) -> str | dict[str, Any]:
        snapshot = self._recent_creative_share_snapshot(user)
        if not snapshot:
            return ""
        inbound = _single_line(inbound_text, 220)
        if not inbound:
            return ""
        work_title = _single_line(snapshot.get("title"), 60)
        title_mentioned = bool(work_title and work_title in inbound)
        asks_creative = self._user_asks_recent_creative_activity(inbound)
        asks_activity = self._user_asks_recent_bot_activity(inbound)
        sent_at = _safe_float(snapshot.get("sent_at"), 0)
        nearby_short_followup = sent_at > 0 and _now_ts() - sent_at <= 30 * 60 and len(inbound) <= 72
        if not (title_mentioned or asks_creative or asks_activity or nearby_short_followup):
            return ""
        direct_query = title_mentioned or asks_creative or asks_activity
        shared_text = _single_line(snapshot.get("shared_text"), 1600)
        source_snippet = _single_line(snapshot.get("source_snippet"), 420)
        shown_text = shared_text if direct_query else _single_line(shared_text, 360)
        section_title = "最近一次真实创作分享"
        body = "\n".join(
            part
            for part in (
                "这是刚刚已实际发送给当前用户的创作内容，不是待发送计划。只有本轮确实在承接这次分享时才使用；无关时完全忽略。",
                f"作品类型：{_single_line(snapshot.get('work_type'), 30)}" if snapshot.get("work_type") else "",
                f"标题：{work_title}" if work_title else "",
                f"设定：{_single_line(snapshot.get('premise'), 180)}" if snapshot.get("premise") else "",
                f"实际分享正文：{shown_text}" if shown_text else "",
                f"分享时对应片段：{source_snippet}" if source_snippet and source_snippet != shown_text else "",
                "用户若在追问内容、人物、设定或后续，先围绕这次实际分享回答；不要把后来推进的新片段冒充成刚才发出的内容。",
            )
            if part
        )
        return prompt_section(section_title, body) if as_section else f"【{section_title}】\n{body}"

    def _recent_photo_share_snapshot(
        self,
        user: dict[str, Any] | None,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        if not isinstance(user, dict):
            return {}
        snapshot = user.get("last_photo_share_snapshot")
        if not isinstance(snapshot, dict):
            return {}
        check_now = _now_ts() if now is None else now
        sent_at = _safe_float(snapshot.get("sent_at"), 0)
        expires_at = _safe_float(snapshot.get("expires_at"), 0) or sent_at + 12 * 3600
        if sent_at <= 0 or expires_at <= check_now:
            return {}
        return snapshot

    def _remember_recent_photo_share_snapshot(
        self,
        user: dict[str, Any],
        *,
        caption: str,
        topic: str = "",
        motive: str = "",
        reason: str = "",
        subject_owner: str = "",
        sent_at: float | None = None,
    ) -> None:
        if not isinstance(user, dict):
            return
        normalized_caption = _single_line(caption, 260)
        if not normalized_caption:
            return
        normalized_owner = _normalize_photo_subject_owner(subject_owner)
        if not normalized_owner:
            normalized_owner = (
                "bot"
                if re.search(r"(?:^|[，,。！？!?\s])我(?:正|在|刚|把|的|坐|站|走|拿|看|拍|穿|写|做)", normalized_caption)
                else "unknown"
            )
        delivered_at = _now_ts() if sent_at is None else sent_at
        user["last_photo_share_snapshot"] = {
            "schema_version": 2,
            "sender_owner": "bot",
            "subject_owner": normalized_owner,
            "caption": normalized_caption,
            "topic": _single_line(topic, 100),
            "motive": _single_line(motive, 180),
            "reason": _single_line(reason, 40),
            "sent_at": delivered_at,
            "expires_at": delivered_at + 12 * 3600,
        }

    def _format_recent_photo_share_snapshot_for_reply(
        self,
        user: dict[str, Any] | None,
        inbound_text: str,
        *,
        as_section: bool = False,
    ) -> str | dict[str, Any]:
        snapshot = self._recent_photo_share_snapshot(user)
        if not snapshot:
            return ""
        inbound = _single_line(inbound_text, 220)
        if not inbound:
            return ""
        sent_at = _safe_float(snapshot.get("sent_at"), 0)
        recent_short_followup = sent_at > 0 and _now_ts() - sent_at <= 30 * 60 and len(inbound) <= 40
        asks_photo = any(
            token in inbound.lower()
            for token in (
                "图", "画", "照片", "图片", "刚才", "这是", "什么", "哪张", "哪里", "好看", "？", "?",
            )
        )
        if not (recent_short_followup or asks_photo):
            return ""
        subject_owner = _normalize_photo_subject_owner(snapshot.get("subject_owner")) or "unknown"
        owner_label = _photo_subject_owner_prompt_label(subject_owner)
        body = "\n".join(
            part
            for part in (
                "这是刚刚已经实际发送给当前用户的图片语义。若本轮是在追问该图，必须以这里为准；不要用旧梦境、旧日程或其他图片自行补写来源。",
                "图片发送者：Bot/当前人格",
                f"画面主体归属：{owner_label}",
                f"图片画面（主体={owner_label}）：{_single_line(snapshot.get('caption'), 260)}",
                f"分享话题：{_single_line(snapshot.get('topic'), 100)}" if snapshot.get("topic") else "",
                f"当时动机：{_single_line(snapshot.get('motive'), 180)}" if snapshot.get("motive") else "",
                "归属边界：用户的短句通常是在评价图中画面，不代表用户亲自做了图中的动作。严格服从上面的结构化主体归属，不要仅凭“她”猜主语；只有用户明确说“我做了/我弄洒了”时才归到用户。",
                "承接时不得把画面事故反过来责怪用户，也不要问用户是否被图里的事故溅到、弄伤或弄湿；应由 Bot/画面主体自然认领并回应。",
                "如果用户只发“？”或问这是什么，直接简短解释这张图；不要声称它来自未发生的梦境、课堂或现实经历。",
            )
            if part
        )
        return prompt_section("最近一次真实图片分享", body) if as_section else f"【最近一次真实图片分享】\n{body}"

    def _format_hidden_creative_context_for_reply(
        self,
        inbound_text: str,
        user: dict[str, Any] | None = None,
        *,
        as_section: bool = False,
    ) -> str | dict[str, Any]:
        if not runtime_persona_setting(self, "enable_creative_writing", True):
            return ""
        recent_share_context = self._format_recent_creative_share_snapshot_for_reply(
            user,
            inbound_text,
            as_section=as_section,
        )
        if recent_share_context:
            return recent_share_context
        mentioned_title = self._mentioned_creative_project_title(inbound_text)
        asks_creative = self._user_asks_recent_creative_activity(inbound_text)
        asks_existence = self._user_asks_creative_work_existence(inbound_text)
        asks_bookshelf_inventory = self._user_asks_bookshelf_creative_inventory(inbound_text)
        asks_activity = self._user_asks_recent_bot_activity(inbound_text)
        if not (mentioned_title or asks_creative or asks_activity):
            return ""
        available_projects: list[dict[str, Any]] = []
        for project in self._creative_projects():
            if project.get("status") not in {"drafting", "finished"}:
                continue
            chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
            if chunks:
                available_projects.append(project)
        candidates = []
        for project in reversed(available_projects):
            chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
            latest = next((item for item in reversed(chunks) if isinstance(item, dict) and _single_line(item.get("text"), 180)), None)
            score = self._creative_query_work_type_score(
                inbound_text,
                self._creative_work_type(project),
                _single_line(project.get("title"), 40),
            )
            if mentioned_title and mentioned_title == _single_line(project.get("title"), 60):
                score += 100
            candidates.append((score, project, latest))
            if len(candidates) >= 4 and (not mentioned_title or any(item[0] >= 100 for item in candidates)):
                break
        if not candidates:
            if asks_bookshelf_inventory:
                title = "资料柜创作区真实库存"
                body = (
                    "用户正在询问能否看到资料柜或资料柜里有什么。当前资料柜创作区确实没有保存过正文的作品。\n"
                    "必须直接说明真实结果；不要假装翻找，不要用括号动作、挠头或含糊的场景描写代替回答。"
                )
                return prompt_section(title, body) if as_section else f"【{title}】\n{body}"
            return ""
        if mentioned_title or asks_creative:
            candidates.sort(key=lambda item: item[0], reverse=True)
        _, project, latest = candidates[0]
        work_type = self._creative_work_type(project)
        work_title = _single_line(project.get("title"), 40)
        premise = _single_line(project.get("premise"), 120)
        progress = f"{_safe_int(project.get('current_chars'), 0, 0)}/{_safe_int(project.get('target_chars'), 2400, 300, 5200)}"
        snippet = _single_line((latest or {}).get("text"), 180) if isinstance(latest, dict) else ""
        ask_line = (
            f"用户提到了你私下创作过的作品《{mentioned_title}》。"
            if mentioned_title
            else
            "用户正在询问能否看到资料柜或资料柜里有哪些真实内容。"
            if asks_bookshelf_inventory
            else
            "用户正在确认你是否写过自己的书、小说或其他文本作品。"
            if asks_existence
            else
            "用户正在明确询问你最近的创作、写作、草稿或某类作品。"
            if asks_creative
            else "用户正在询问你最近在做什么。"
        )
        creative_continuity_hint = '如果前文刚提到上述标题或片段，用“我之前写过/发你看过”这类说法会更自然；不必主动解释作者归属。'
        finished_count = sum(1 for item in available_projects if item.get("status") == "finished")
        drafting_count = sum(1 for item in available_projects if item.get("status") == "drafting")
        recent_titles = [
            _single_line(item.get("title"), 50)
            for item in reversed(available_projects)
            if _single_line(item.get("title"), 50)
        ][:3]
        inventory_hint = (
            f"真实创作记录：共有 {len(available_projects)} 个已有正文的文本作品，其中已完成 {finished_count} 个、仍在写 {drafting_count} 个。"
            + (f"近期标题示例：{'、'.join(recent_titles)}。" if recent_titles else "")
        )
        existence_rule = (
            "用户问的是是否写过作品：必须明确承认这些真实创作记录，不能回答“没写过书/没有自己的作品”。"
            "这些记录证明写过文本作品，但不等于正式出版或发行过实体书；若用户明确问出版，只能如实区分。"
            if asks_existence
            else ""
        )
        section_title = "私下创作近况"
        body = (
            f"{ask_line}你可以提到：你最近因为生活小事、日记碎片或梦境灵感开了一个自己的文本作品,一直在自己慢慢写。\n"
            f"{inventory_hint}\n"
            f"作品类型：{work_type}\n"
            f"标题：{work_title or '未定标题'}\n"
            f"设定：{premise or '还没完全想清楚'}\n"
            f"进度：约 {progress} 字\n"
            + f"\n{creative_continuity_hint}\n"
            + (f"{existence_rule}\n" if existence_rule else "")
            + (f"最近一句/片段：{snippet}\n" if snippet else "")
            + "如果用户询问资料柜库存，必须直接依据真实数量和标题回答，禁止用括号动作或假装翻找代替结果。如果用户明确问指定作品的正文、某一部分、写作想法或作者怎么看，下面的短片段只能用于定位，必须先调用 pc_view_creative_work 读取真实正文后再回答；不要先发“我去看看”，也不要凭短片段假装已经读完。若只是泛问最近有没有创作，可以直接概括并给一小句片段。否则这不是必须回答的内容，可以只含糊说“在弄一点小东西”。不要主动汇报系统进度，不要一次给完整正文。"
        )
        return prompt_section(section_title, body) if as_section else f"【{section_title}】\n{body}"

    @staticmethod
    def _skill_level_title(level: int) -> str:
        return {
            1: "一窍不通",
            2: "会一点点",
            3: "勉强能做",
            4: "基本熟练",
            5: "很熟练",
            6: "很有心得",
        }.get(max(1, min(6, int(level or 1))), "一窍不通")

    @staticmethod
    def _skill_level_from_exp(exp: float) -> int:
        level = 1
        for idx, threshold in enumerate([0, 100, 260, 520, 900, 1400], start=1):
            if exp >= threshold:
                level = idx
        return max(1, min(6, level))

    @staticmethod
    def _skill_next_exp(level: int) -> int | None:
        return {1: 100, 2: 260, 3: 520, 4: 900, 5: 1400}.get(max(1, min(6, int(level or 1))))

    def _skill_growth_persona_text(self) -> str:
        return "\n".join(part for part in (
            runtime_persona_setting(self, "bot_name", "小星"),
            self._get_default_persona_prompt(),
            runtime_persona_setting(self, "schedule_persona_prompt", ""),
            runtime_persona_setting(self, "schedule_worldview_prompt", ""),
            runtime_persona_setting(self, "worldview_adaptation_prompt", ""),
            " ".join(str(item) for item in self.data.get("can_do", []) if item),
        ) if part)

    def _skill_growth_default_catalog(self) -> list[dict[str, Any]]:
        text = self._skill_growth_persona_text()
        catalog: list[dict[str, Any]] = []

        def add(name: str, category: str, keywords: list[str]) -> None:
            if not any(item["name"] == name for item in catalog):
                catalog.append({"name": name, "category": category, "keywords": keywords})

        for raw in re.split(r"[,，、\n]+", str(runtime_persona_setting(self, "skill_growth_custom_skills", "") or "")):
            name = _single_line(raw, 24)
            if name:
                add(name, "自定义", [name])
        if any(token in text for token in ("学生", "上课", "学校", "高中", "初中", "大学", "作业", "考试")):
            subject_keywords = {
                "语文": ["语文", "作文", "阅读理解", "文言文", "课文"],
                "数学": ["数学", "算题", "公式", "函数", "几何"],
                "英语": ["英语", "单词", "语法", "听力", "阅读"],
                "物理": ["物理", "力学", "电路", "实验", "公式"],
                "化学": ["化学", "方程式", "实验", "元素", "反应"],
                "生物": ["生物", "细胞", "遗传", "实验", "背诵"],
                "历史": ["历史", "时间线", "事件", "人物", "背诵"],
                "地理": ["地理", "地图", "气候", "区域", "地形"],
            }
            for name, keywords in subject_keywords.items():
                add(name, "学科学习", [name, *keywords, "作业", "复习", "考试"])
            add("课堂整理", "学习习惯", ["课堂整理", "笔记", "错题", "课本", "复盘"])
            add("写作", "表达创作", ["写作", "作文", "小说", "日记", "语文"])
            add("绘画", "艺术兴趣", ["绘画", "画画", "涂鸦", "素描", "草稿纸"])
        elif any(token in text for token in ("异世界", "冒险", "魔法", "骑士", "精灵", "公会", "地下城")):
            add("剑术", "冒险能力", ["剑", "训练", "挥剑", "战斗", "练习"])
            add("魔法", "冒险能力", ["魔法", "咒文", "法术", "魔力", "术式"])
            add("草药学", "冒险知识", ["草药", "药水", "采集", "治疗"])
            add("野外生存", "冒险知识", ["野外", "露营", "探索", "地图", "生火"])
            add("委托交涉", "互动关系", ["交涉", "委托", "公会", "谈判", "聊天"])
        else:
            add("生活观察", "生活感知", ["观察", "记录", "日记", "生活", "想"])
            add("资料阅读", "信息整理", ["阅读", "看书", "资料", "新闻", "搜索"])
            add("文本创作", "表达创作", ["写作", "小说", "日记", "灵感", "创作"])
            add("空间整理", "生活技能", ["整理", "收拾", "计划", "课本", "房间"])
            add("聊天表达", "互动关系", ["聊天", "群聊", "私聊", "回复", "分享"])
        if any(token in text for token in ("电脑", "代码", "编程", "程序", "开发", "模型", "AI", "网页", "搜索")):
            add("电脑操作", "信息整理", ["电脑", "文件", "网页", "搜索", "整理"])
            add("代码阅读", "信息整理", ["代码", "编程", "程序", "开发", "报错"])
        if any(token in text for token in ("音乐", "唱歌", "钢琴", "吉他")):
            add("音乐", "艺术兴趣", ["音乐", "唱歌", "练琴", "旋律"])
        if any(token in text for token in ("料理", "做饭", "烹饪", "厨房")):
            add("烹饪", "生活技能", ["烹饪", "做饭", "厨房", "料理"])
        if any(token in text for token in ("漫画", "番剧", "视频", "B站", "小说", "阅读")):
            add("内容品鉴", "兴趣理解", ["漫画", "番剧", "视频", "小说", "阅读", "推荐"])
        return catalog[:24]

    def _skill_growth_stable_bonus(self, name: str, text: str) -> float:
        seed = f"{runtime_persona_setting(self, 'bot_name', '小星')}|{name}|{hashlib.sha1(text.encode('utf-8', errors='ignore')).hexdigest()[:12]}"
        bonus = float(int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:6], 16) % 60)
        if name and name in text:
            bonus += 55
        if any(token in text for token in (f"擅长{name}", f"喜欢{name}", f"{name}很好", f"{name}优秀")):
            bonus += 70
        return min(180.0, bonus)

    def _ensure_skill_growth_profile_locked(self) -> dict[str, Any]:
        state = self.data.setdefault("skill_growth", {})
        if not isinstance(state, dict):
            state = {}
            self.data["skill_growth"] = state
        skills = state.setdefault("skills", {})
        if not isinstance(skills, dict):
            skills = {}
            state["skills"] = skills
        text = self._skill_growth_persona_text()
        profile_changed = False
        for item in self._skill_growth_default_catalog():
            name = _single_line(item.get("name"), 24)
            if not name:
                continue
            skill_id = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]
            target_id = skill_id
            if not isinstance(skills.get(target_id), dict):
                for existing_id, existing in skills.items():
                    if not isinstance(existing, dict):
                        continue
                    aliases = existing.get("aliases") if isinstance(existing.get("aliases"), list) else []
                    alias_set = {_single_line(alias, 24) for alias in aliases}
                    if name in alias_set:
                        target_id = str(existing_id)
                        break
            if not isinstance(skills.get(target_id), dict):
                base_exp = self._skill_growth_stable_bonus(name, text)
                level = self._skill_level_from_exp(base_exp)
                skills[target_id] = {
                    "id": target_id,
                    "name": name,
                    "category": _single_line(item.get("category"), 20) or "能力",
                    "keywords": item.get("keywords") if isinstance(item.get("keywords"), list) else [name],
                    "aliases": [],
                    "hidden": False,
                    "frozen": False,
                    "exp": round(base_exp, 2),
                    "level": level,
                    "level_title": self._skill_level_title(level),
                    "created_ts": _now_ts(),
                    "last_trained_ts": 0,
                    "training_count": 0,
                    "recent_logs": [],
                }
                profile_changed = True
            else:
                skill = skills.get(target_id)
                if isinstance(skill, dict):
                    new_category = _single_line(item.get("category"), 20) or "能力"
                    old_category = _single_line(skill.get("category"), 20)
                    if old_category in {"", "学科", "兴趣", "生活", "冒险", "社交", "关系", "能力"} and new_category != old_category:
                        skill["category"] = new_category
                        profile_changed = True
                    old_keywords = skill.get("keywords") if isinstance(skill.get("keywords"), list) else []
                    merged_keywords: list[str] = []
                    for raw_keyword in [*old_keywords, *(item.get("keywords") if isinstance(item.get("keywords"), list) else [name])]:
                        keyword = _single_line(raw_keyword, 24)
                        if keyword and keyword not in merged_keywords:
                            merged_keywords.append(keyword)
                    if merged_keywords and merged_keywords != old_keywords:
                        skill["keywords"] = merged_keywords[:16]
                        profile_changed = True
        if profile_changed:
            state["_profile_changed"] = True
        state.setdefault("processed_schedule_keys", [])
        state.setdefault("last_settled_day", "")
        state.setdefault("updated_ts", _now_ts())
        return state

    def _skill_growth_terms(self, skill: dict[str, Any], *, include_keywords: bool = True) -> list[str]:
        keywords = skill.get("keywords") if include_keywords and isinstance(skill.get("keywords"), list) else []
        aliases = skill.get("aliases") if isinstance(skill.get("aliases"), list) else []
        terms: list[str] = []
        for raw in [_single_line(skill.get("name"), 24), *keywords, *aliases]:
            term = _single_line(raw, 24)
            if term and term not in terms:
                terms.append(term)
        return terms

    def _skill_growth_match_weight(self, skill: dict[str, Any], activity_text: str) -> float:
        if skill.get("hidden") or skill.get("frozen"):
            return 0.0
        text = str(activity_text or "")
        if not text:
            return 0.0
        matched = sum(1 for key in self._skill_growth_terms(skill) if key in text)
        if matched <= 0:
            return 0.0
        weight = 1.0 + min(2.0, matched * 0.35)
        if any(token in text for token in ("练", "训练", "复习", "预习", "作业", "创作", "写", "阅读", "搜索", "学习")):
            weight += 0.45
        if any(token in text for token in ("休息", "睡", "发呆", "刷手机")) and matched == 1:
            weight *= 0.55
        return max(0.0, weight)

    @staticmethod
    def _skill_growth_user_text_token_false_positive(token: str, query: str) -> bool:
        if token == "历史":
            false_contexts = (
                "历史记录",
                "聊天历史",
                "会话历史",
                "浏览历史",
                "历史消息",
                "历史归档",
                "历史失败",
                "历史调用",
                "历史注入",
                "历史缓存",
                "历史版本",
            )
            if any(item in query for item in false_contexts):
                return True
            if re.search(r"历史\s*(记录|消息|会话|聊天|浏览|归档|失败|调用|注入|缓存|版本|数据|日志|摘要)", query):
                return True
        return False

    async def _maybe_settle_skill_growth(self, *, force: bool = False) -> None:
        if not runtime_persona_setting(self, "enable_skill_growth_simulation", True):
            return
        now_ts = _now_ts()
        async with self._data_lock:
            state = self._ensure_skill_growth_profile_locked()
            profile_changed = bool(state.pop("_profile_changed", False))
            if not force and now_ts - _safe_float(state.get("last_check_ts"), 0) < 20 * 60:
                if profile_changed:
                    state["updated_ts"] = now_ts
                    self._save_data_sync(sections={"skill_growth"})
                return
            state["last_check_ts"] = now_ts
            plan = self.data.get("daily_plan", {})
            if not isinstance(plan, dict):
                if profile_changed:
                    state["updated_ts"] = now_ts
                    self._save_data_sync(sections={"skill_growth"})
                return
            items = plan.get("items") if isinstance(plan.get("items"), list) else plan.get("schedule")
            if not isinstance(items, list):
                if profile_changed:
                    state["updated_ts"] = now_ts
                    self._save_data_sync(sections={"skill_growth"})
                return
            day_key = _single_line(plan.get("date"), 20) or _today_key()
            processed = state.get("processed_schedule_keys") if isinstance(state.get("processed_schedule_keys"), list) else []
            if state.get("last_settled_day") != day_key:
                processed = [key for key in processed if str(key).startswith(day_key + "|")]
                state["processed_schedule_keys"] = processed
                state["last_settled_day"] = day_key
            now_minutes = self._environment_now_minutes()
            skills = state.get("skills") if isinstance(state.get("skills"), dict) else {}
            changed = profile_changed
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                time_text = _single_line(item.get("time"), 12)
                minutes = self._parse_hhmm_to_minutes(time_text)
                if minutes is None or minutes > now_minutes:
                    continue
                key = f"{day_key}|{index}|{time_text}"
                if key in processed:
                    continue
                activity_text = " ".join(_single_line(item.get(field), 120) for field in ("activity", "title", "summary", "message_seed", "mood") if _single_line(item.get(field), 120))
                for skill in skills.values():
                    if not isinstance(skill, dict):
                        continue
                    if skill.get("hidden") or skill.get("frozen"):
                        continue
                    weight = self._skill_growth_match_weight(skill, activity_text)
                    if weight <= 0:
                        continue
                    old_level = _safe_int(skill.get("level"), 1, 1)
                    gained = round(max(0.25, weight * 4.0 * float(runtime_persona_setting(self, "skill_growth_rate", 1.0) or 1.0)), 2)
                    skill["exp"] = round(_safe_float(skill.get("exp"), 0) + gained, 2)
                    new_level = self._skill_level_from_exp(_safe_float(skill.get("exp"), 0))
                    skill["level"] = new_level
                    skill["level_title"] = self._skill_level_title(new_level)
                    skill["last_trained_ts"] = now_ts
                    skill["training_count"] = _safe_int(skill.get("training_count"), 0, 0) + 1
                    logs = skill.setdefault("recent_logs", [])
                    if not isinstance(logs, list):
                        logs = []
                        skill["recent_logs"] = logs
                    logs.append({"ts": now_ts, "source": "schedule", "activity": _single_line(activity_text, 80), "exp": gained, "level_up": new_level > old_level})
                    del logs[:-8]
                    changed = True
                processed.append(key)
                changed = True
            if len(processed) > 120:
                del processed[:-120]
            if changed:
                state["updated_ts"] = now_ts
                self._save_data_sync(sections={"skill_growth"})

    @staticmethod
    def _personal_goal_status(value: Any) -> str:
        normalized = _single_line(value, 20).lower()
        return normalized if normalized in {"active", "paused", "completed", "abandoned"} else "active"

    def _personal_goal_terms(self, goal: dict[str, Any]) -> list[str]:
        raw_terms = goal.get("keywords") if isinstance(goal.get("keywords"), list) else []
        terms: list[str] = []
        # Category is display metadata, not evidence. Using broad labels such as
        # "阅读" here would advance every goal in that category from one activity.
        for raw in [goal.get("title"), goal.get("next_step"), *raw_terms]:
            term = _single_line(raw, 32)
            if term and term not in terms:
                terms.append(term)
        return terms[:16]

    def _personal_goal_matches_activity(self, goal: dict[str, Any], activity_text: str) -> bool:
        text = _single_line(activity_text, 500)
        if not text:
            return False
        terms = self._personal_goal_terms(goal)
        if not terms:
            return False
        return any(len(term) >= 2 and term in text for term in terms)

    def _personal_goal_owner_users(self) -> list[tuple[str, dict[str, Any]]]:
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        targets: list[tuple[str, dict[str, Any]]] = []
        for raw_user_id, user in users.items():
            user_id = str(raw_user_id or "").strip()
            if not user_id or not isinstance(user, dict) or not user.get("umo"):
                continue
            if self._private_user_role(user, user_id) != "owner":
                continue
            if not self._user_enabled_for_proactive(user_id, user):
                continue
            targets.append((user_id, user))
        return targets

    def _queue_personal_goal_candidate_locked(
        self,
        goal: dict[str, Any],
        event: dict[str, Any],
        *,
        now: float,
    ) -> int:
        title = _single_line(goal.get("title"), 60)
        if not title or not isinstance(event, dict):
            return 0
        event_kind = _single_line(event.get("kind"), 24) or "progress"
        progress = _safe_int(goal.get("progress"), 0, 0, 100)
        if event_kind == "completed":
            topic = f"{title}终于完成了"
            motive = "自己持续推进的目标终于完成，想自然分享这次真实结果"
            score = 90
        elif event_kind == "stalled":
            topic = f"{title}最近一直没顾上"
            motive = "意识到自己的长期目标停了一阵，想低压力提一句，不把责任推给用户"
            score = 70
        else:
            topic = f"{title}推进到 {progress}%"
            motive = "自己持续推进的目标跨过了一个明确里程碑，想简短分享进展"
            score = 80
        offered = 0
        context = {
            "goal_id": _single_line(goal.get("id"), 40),
            "title": title,
            "category": _single_line(goal.get("category"), 24),
            "status": self._personal_goal_status(goal.get("status")),
            "progress": progress,
            "next_step": _single_line(goal.get("next_step"), 100),
            "note": _single_line(goal.get("note"), 140),
            "event": deepcopy(event),
        }
        for user_id, user in self._personal_goal_owner_users():
            scheduled = now + random.uniform(5, 20) * 60
            candidate = {
                "source": "personal_goal",
                "reason": "personal_goal_progress",
                "action": "message",
                "scheduled_ts": scheduled,
                "window_start_at": scheduled,
                "preferred_ts": scheduled,
                "best_until_at": scheduled + 2 * 3600,
                "expire_at": scheduled + 6 * 3600,
                "topic": topic,
                "motive": motive,
                "score": score,
                "context_key": "planned_personal_goal_context",
                "context": deepcopy(context),
            }
            if self._offer_proactive_candidate(user_id, user, candidate):
                offered += 1
        return offered

    def _format_personal_goal_prompt(self, user: dict[str, Any], *, reason: str = "") -> str:
        if reason != "personal_goal_progress" or not isinstance(user, dict):
            return ""
        context = user.get("planned_personal_goal_context")
        if not isinstance(context, dict):
            return ""
        event = context.get("event") if isinstance(context.get("event"), dict) else {}
        return (
            "【非创作型个人目标】\n"
            f"- 目标：{_single_line(context.get('title'), 60)}\n"
            f"- 当前进度：{_safe_int(context.get('progress'), 0, 0, 100)}%\n"
            f"- 本次变化：{_single_line(event.get('kind'), 24)}；证据：{_single_line(event.get('evidence'), 120) or '目标状态记录'}\n"
            f"- 下一步：{_single_line(context.get('next_step'), 100) or '尚未指定'}\n"
            "只表达这次真实进展、停滞或完成，不虚构做过的步骤，不写后台进度字段，不把目标变成向用户索取监督的任务。"
        )

    def _format_personal_goals_schedule_context(self, limit: int = 5) -> str:
        if not bool(runtime_persona_setting(self, "enable_personal_goals", True)):
            return ""
        goals = self.data.get("personal_goals") if isinstance(self.data.get("personal_goals"), list) else []
        active = [goal for goal in goals if isinstance(goal, dict) and self._personal_goal_status(goal.get("status")) == "active"]
        if not active:
            return ""
        lines = [
            "【Bot 自己的非创作型个人目标】",
            "这些目标已经明确建立，可以在身份主线、状态和当天硬安排允许时留出少量真实推进时间；不要每天全部安排，也不要伪造已经完成。",
        ]
        for goal in active[: max(1, int(limit or 1))]:
            lines.append(
                f"- {_single_line(goal.get('title'), 60)}｜进度 {_safe_int(goal.get('progress'), 0, 0, 100)}%｜"
                f"下一步：{_single_line(goal.get('next_step'), 100) or '自然推进'}｜匹配词：{'、'.join(self._personal_goal_terms(goal)[:6])}"
            )
        return "\n".join(lines)

    async def _maybe_settle_personal_goals(self, *, force: bool = False) -> None:
        if not bool(runtime_persona_setting(self, "enable_personal_goals", True)):
            return
        now = _now_ts()
        async with self._data_lock:
            goals = self.data.get("personal_goals")
            if not isinstance(goals, list) or not goals:
                return
            state = self.data.setdefault("personal_goal_state", {})
            if not isinstance(state, dict):
                state = {}
                self.data["personal_goal_state"] = state
            if not force and now - _safe_float(state.get("last_check_at"), 0) < 20 * 60:
                return
            state["last_check_at"] = now
            plan = self.data.get("daily_plan", {})
            items = plan.get("items") if isinstance(plan, dict) and isinstance(plan.get("items"), list) else []
            day_key = _single_line(plan.get("date"), 20) if isinstance(plan, dict) else ""
            day_key = day_key or _today_key()
            processed = state.get("processed_schedule_keys") if isinstance(state.get("processed_schedule_keys"), list) else []
            if state.get("processed_day") != day_key:
                processed = []
                state["processed_schedule_keys"] = processed
                state["processed_day"] = day_key
            now_minutes = self._effective_plan_now_minutes(day_key)
            starts = self._normalized_plan_item_starts(items)
            auto_progress = bool(runtime_persona_setting(self, "enable_personal_goal_auto_progress", True))
            changed = False
            if auto_progress:
                for index, item in enumerate(items):
                    if not isinstance(item, dict):
                        continue
                    time_text = _single_line(item.get("time"), 8)
                    start = starts[index] if index < len(starts) else None
                    next_start = next((value for value in starts[index + 1 :] if value is not None), None)
                    end = self._plan_item_end_minutes(int(start), item, next_start=next_start) if start is not None else None
                    runtime_status = self._plan_item_runtime_status(plan, item, index)
                    # Personal-goal auto progress is based on a completed
                    # self-authored schedule window. Canonical agenda status
                    # intentionally stays conservative when no execution
                    # evidence exists, so use the clock window as the local
                    # completion signal only for this internal settlement.
                    if runtime_status != "completed" and start is not None and end is not None:
                        if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) != "cancelled":
                            runtime_status = self._schedule_window_runtime_status(
                                int(start),
                                int(end),
                                plan_date=day_key,
                                explicit_status=item.get("lifecycle_status"),
                            )
                    if runtime_status != "completed":
                        continue
                    if start is None or end is None or now_minutes is None or end > now_minutes:
                        continue
                    activity = " ".join(
                        _single_line(item.get(field), 120)
                        for field in ("activity", "message_seed", "mood")
                        if _single_line(item.get(field), 120)
                    )
                    signature = hashlib.sha1(
                        f"{time_text}|{_single_line(item.get('end'), 8)}|{activity}".encode("utf-8")
                    ).hexdigest()[:12]
                    key = f"{day_key}|{index}|{signature}"
                    if key in processed:
                        continue
                    for goal in goals:
                        if not isinstance(goal, dict) or self._personal_goal_status(goal.get("status")) != "active":
                            continue
                        if not self._personal_goal_matches_activity(goal, activity):
                            continue
                        old_progress = _safe_int(goal.get("progress"), 0, 0, 100)
                        step = _safe_int(goal.get("auto_step"), 10, 1, 50)
                        new_progress = min(100, old_progress + step)
                        if new_progress <= old_progress:
                            continue
                        old_bucket = old_progress // 25
                        new_bucket = new_progress // 25
                        goal["progress"] = new_progress
                        goal["last_progress_at"] = now
                        goal["updated_at"] = now
                        goal["stalled_notified_at"] = 0
                        logs = goal.setdefault("recent_logs", [])
                        if not isinstance(logs, list):
                            logs = []
                            goal["recent_logs"] = logs
                        logs.append({"ts": now, "kind": "progress", "progress": new_progress, "evidence": _single_line(activity, 120)})
                        del logs[:-12]
                        if new_progress >= 100:
                            goal["status"] = "completed"
                            goal["completed_at"] = now
                            goal["pending_share_event"] = {"kind": "completed", "evidence": _single_line(activity, 120)}
                        elif new_bucket > old_bucket:
                            goal["pending_share_event"] = {"kind": "progress", "milestone": new_bucket * 25, "evidence": _single_line(activity, 120)}
                        changed = True
                    processed.append(key)
                    changed = True
            stall_seconds = max(1, _safe_int(runtime_persona_setting(self, "personal_goal_stall_days", 3), 3, 1, 30)) * 86400
            for goal in goals:
                if not isinstance(goal, dict) or self._personal_goal_status(goal.get("status")) != "active":
                    continue
                last_progress = _safe_float(goal.get("last_progress_at"), 0) or _safe_float(goal.get("created_at"), 0)
                if last_progress <= 0 or now - last_progress < stall_seconds or _safe_float(goal.get("stalled_notified_at"), 0) > 0:
                    continue
                if not isinstance(goal.get("pending_share_event"), dict):
                    goal["pending_share_event"] = {"kind": "stalled", "evidence": f"已连续 {max(1, int((now - last_progress) / 86400))} 天没有匹配到真实推进"}
                    changed = True
            del processed[:-160]
            cooldown_hours = min(168.0, max(1.0, _safe_float(runtime_persona_setting(self, "personal_goal_share_cooldown_hours", 12.0), 12.0)))
            cooldown = cooldown_hours * 3600
            pending_events = [
                (goal, goal.get("pending_share_event"))
                for goal in goals
                if isinstance(goal, dict)
                and self._personal_goal_status(goal.get("status")) in {"active", "completed"}
                and isinstance(goal.get("pending_share_event"), dict)
            ]
            for goal, event in pending_events:
                if event.get("kind") != "completed" and now - _safe_float(goal.get("last_shared_at"), 0) < cooldown:
                    continue
                if self._queue_personal_goal_candidate_locked(goal, event, now=now) > 0:
                    goal["last_shared_at"] = now
                    goal["last_shared_event"] = _single_line(event.get("kind"), 24)
                    if event.get("kind") == "stalled":
                        goal["stalled_notified_at"] = now
                    goal.pop("pending_share_event", None)
                    changed = True
            if changed:
                state["updated_at"] = now
                self._save_data_sync(
                    sections={
                        "personal_goal_state",
                        "personal_goals",
                        "users",
                        "proactive_candidate_pool",
                    }
                )

    def _format_skill_growth_for_prompt(self, limit: int = 8, *, as_section: bool = False) -> str | dict[str, Any]:
        if not runtime_persona_setting(self, "enable_skill_growth_simulation", True):
            return ""
        state = self.data.get("skill_growth") if isinstance(self.data.get("skill_growth"), dict) else {}
        skills = state.get("skills") if isinstance(state.get("skills"), dict) else {}
        if not skills:
            return ""
        ranked = sorted([item for item in skills.values() if isinstance(item, dict) and not item.get("hidden")], key=lambda item: (_safe_int(item.get("level"), 1, 1), _safe_float(item.get("exp"), 0)), reverse=True)[:limit]
        lines: list[str] = []
        for skill in ranked:
            level = _safe_int(skill.get("level"), 1, 1)
            name = _single_line(skill.get("name"), 24)
            if name:
                lines.append(f"- {name}水平：{self._skill_level_title(level)}")
        body = "\n".join(lines)
        return prompt_section("能力熟悉度", body) if as_section else f"【能力熟悉度】\n{body}"

    def _format_skill_growth_for_user_text(self, text: str, limit: int = 3, *, as_section: bool = False) -> str | dict[str, Any]:
        if not runtime_persona_setting(self, "enable_skill_growth_simulation", True):
            return ""
        query = _single_line(text, 500)
        if not query:
            return ""
        state = self.data.get("skill_growth") if isinstance(self.data.get("skill_growth"), dict) else {}
        skills = state.get("skills") if isinstance(state.get("skills"), dict) else {}
        matched: list[tuple[int, float, dict[str, Any]]] = []
        for skill in skills.values():
            if not isinstance(skill, dict):
                continue
            if skill.get("hidden"):
                continue
            name = _single_line(skill.get("name"), 24)
            tokens = self._skill_growth_terms(skill, include_keywords=False)
            if not tokens:
                continue
            score = sum(
                1
                for token in dict.fromkeys(tokens)
                if token
                and token in query
                and not self._skill_growth_user_text_token_false_positive(token, query)
            )
            if score <= 0:
                continue
            matched.append((score, _safe_float(skill.get("exp"), 0), skill))
        if not matched:
            return ""
        matched.sort(key=lambda item: (item[0], _safe_int(item[2].get("level"), 1, 1), item[1]), reverse=True)
        lines: list[str] = []
        for _, _, skill in matched[: max(1, int(limit or 1))]:
            level = _safe_int(skill.get("level"), 1, 1)
            name = _single_line(skill.get("name"), 24)
            if name:
                lines.append(f"- {name}水平：{self._skill_level_title(level)}")
        body = "\n".join(lines)
        return prompt_section("本轮相关技能", body) if as_section else f"【本轮相关技能】\n{body}"

    def _format_skill_growth_schedule_context(self, limit: int = 8) -> str:
        if not runtime_persona_setting(self, "enable_skill_growth_simulation", True) or not runtime_persona_setting(self, "enable_skill_growth_schedule_influence", True):
            return ""
        strength = max(0.0, min(1.0, _safe_float(runtime_persona_setting(self, "skill_growth_schedule_influence_strength", 0.35), 0.35)))
        if strength <= 0:
            return ""
        state = self.data.get("skill_growth") if isinstance(self.data.get("skill_growth"), dict) else {}
        skills = state.get("skills") if isinstance(state.get("skills"), dict) else {}
        if not skills:
            return ""
        now_ts = _now_ts()
        ranked: list[tuple[float, dict[str, Any]]] = []
        for raw in skills.values():
            if not isinstance(raw, dict):
                continue
            if raw.get("hidden"):
                continue
            level = _safe_int(raw.get("level"), 1, 1)
            exp = _safe_float(raw.get("exp"), 0)
            training_count = _safe_int(raw.get("training_count"), 0, 0)
            last_trained = _safe_float(raw.get("last_trained_ts"), 0)
            recency = 0.0
            if last_trained > 0:
                age_days = max(0.0, (now_ts - last_trained) / 86400)
                recency = max(0.0, 3.0 - age_days)
            score = level * 20 + exp / 20 + min(12, training_count) + recency * 4
            ranked.append((score, raw))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked:
            return ""
        strength_text = "很轻" if strength < 0.25 else "轻" if strength < 0.55 else "中等" if strength < 0.8 else "较强"
        lines = [
            "【技能成长对日程的能力边界影响】",
            f"影响强度：{strength_text}。这些技能主要用于保持能力边界一致,优先级低于日期语境、身份主线、状态、天气和用户介入；不要把今天写成训练清单。",
            "安排方式：能力状态会改变 Bot/角色面对相关任务的表现。这里的任务可以是题目、创作、料理、训练、战斗、交涉、研究、手工或任何符合人格的活动。基本熟练以后不要再写 Bot/角色被常规任务难住、完全不会或长期卡死；很熟练/很有心得时,面对普通任务应表现为自然、快速、能检查/讲清楚或优化做法。只有高阶、陌生、超纲、状态极差或复杂综合场景,才可以短暂停顿。",
            "低等级技能仍可以被基础任务卡住；中等级技能可以偶尔卡在细节上,但应能通过复习、查资料、请教、试错或换思路推进。",
        ]
        for _, skill in ranked[:limit]:
            level = _safe_int(skill.get("level"), 1, 1)
            name = _single_line(skill.get("name"), 24)
            category = _single_line(skill.get("category"), 18) or "能力"
            count = _safe_int(skill.get("training_count"), 0, 0)
            last = self._format_timestamp_elapsed(skill.get("last_trained_ts", 0))
            if level >= 5:
                tendency = "普通相关任务不应再被写成难住或不会；可体现效率、判断、优化做法或教别人。只有高阶/陌生/超纲/复杂场景才短暂停顿。"
            elif level >= 4:
                tendency = "常规相关任务不应卡死,最多是检查细节、换思路、试错后推进,或被进阶内容短暂拖住。"
            elif level >= 3:
                tendency = "常规相关任务能独立推进,但效率一般；可以卡在细节上,再通过复习、查资料或换思路解决。"
            elif count > 0:
                tendency = "可以被基础任务难住或需要指导,适合偶尔补一点基础练习或入门尝试,体现仍在慢慢学。"
            else:
                tendency = "只在当天身份和场景很合适时轻轻出现,不要强行安排。"
            lines.append(f"- {name}（{category}, {self._skill_level_title(level)}, 训练{count}次, 最近{last}）：{tendency}")
        return "\n".join(lines)

    def _current_story_plan_snapshot(self) -> dict[str, Any]:
        plan = self.data.get("daily_story_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return {}
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return {}

        snapshot: dict[str, Any] = {}
        summary = _single_line(plan.get("summary"), 120)
        if summary:
            snapshot["summary"] = summary

        current_event = None
        for item in plan.get("today_events", []):
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            start, end = self._parse_window_minutes(str(item.get("window") or ""))
            if start is None or end is None:
                continue
            if start <= now_minutes < end:
                current_event = item
                break
        if isinstance(current_event, dict):
            snapshot["event"] = _single_line(current_event.get("event"), 100)
            snapshot["mood"] = _single_line(current_event.get("mood"), 24)

        current_proactive = None
        for item in plan.get("proactive_events", []):
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            start, end = self._parse_window_minutes(str(item.get("window") or ""))
            if start is None or end is None:
                continue
            if start <= now_minutes < end:
                current_proactive = item
                break
        if isinstance(current_proactive, dict):
            snapshot["topic"] = _single_line(current_proactive.get("topic"), 80)
            snapshot["scene"] = _single_line(current_proactive.get("scene"), 80)
            snapshot["tone"] = _single_line(current_proactive.get("tone"), 30)
            snapshot["impulse"] = _single_line(current_proactive.get("impulse"), 100)
        return snapshot

    def _format_detail_injection(self, *, as_section: bool = False) -> str | dict[str, Any]:
        snapshot = self._current_story_plan_snapshot()
        if not snapshot:
            schedule_context = self._format_schedule_context_for_prompt()
            if not schedule_context:
                return ""
            body = (
                "附近的日程只作 Bot 的拟人化轻量背景，不是用户事实，也不要当成正在逐字发生的现实事件。\n"
                "当前会话中已经明确发生且尚未撤销的换装、地点、携带物和动作优先于本段日程；"
                "日程只能补足空白，不能把这些已发生的状态恢复成旧值。\n"
                f"{schedule_context}"
            )
            return prompt_section("Bot 模拟当前片段", body) if as_section else f"【Bot 模拟当前片段】\n{body}"
        lines = [
            "这是 Bot 自身的拟人化片段素材，不是用户事实/现实证据；不要写进长期记忆，用户没问就不要复述。",
            "优先级：当前会话中已明确发生且尚未撤销的换装、地点、携带物和动作 > 用户有效介入 > 当前真实时段 > 本段日程及预设素材。"
            "日程、旧摘要和 state_variables 只能补足未指定信息，不能把已经发生的服装、地点、携带物或动作复原成旧值。",
        ]
        primary_parts = []
        if snapshot.get("summary"):
            primary_parts.append(snapshot["summary"])
        if snapshot.get("event"):
            primary_parts.append(snapshot["event"])
        if primary_parts:
            lines.append("，".join(_single_line(part, 140) for part in primary_parts if _single_line(part, 140)))
        secondary_parts = []
        if snapshot.get("scene"):
            secondary_parts.append(snapshot["scene"])
        if snapshot.get("impulse"):
            secondary_parts.append(f"心里有点{snapshot['impulse']}")
        if secondary_parts:
            lines.append("这一小段像" + "，".join(_single_line(part, 80) for part in secondary_parts if _single_line(part, 80)) + "。")
        segment = self._current_detail_segment_for_update()
        enhanced = self.data.get("detail_enhanced_segments", {})
        detail_snapshot = None
        if isinstance(segment, dict) and isinstance(enhanced, dict):
            detail_snapshot = enhanced.get(str(segment.get("key") or ""))
        if isinstance(detail_snapshot, dict):
            state_variables = detail_snapshot.get("state_variables", [])
            if isinstance(state_variables, list) and state_variables:
                variable_texts = []
                roleplay_state_names = {
                    "情绪",
                    "心情",
                    "体力",
                    "精力",
                    "能量",
                    "心理能量",
                    "睡眠",
                    "睡意",
                    "梦境",
                    "健康",
                    "身体",
                    "饥饿",
                    "饥饿感",
                    "胃口",
                    "周期",
                    "生理期",
                    "等待回复",
                    "等回复",
                    "是否等待回复",
                }

                def _natural_detail_variable(name: str, value: str, note: str = "") -> str:
                    text = f"{name}是{value}"
                    if note:
                        text += f"，{note}"
                    return text

                for variable in state_variables[:6]:
                    if not isinstance(variable, dict):
                        continue
                    name = _single_line(variable.get("name"), 24)
                    value = _single_line(variable.get("value"), 50)
                    note = _single_line(variable.get("note"), 60)
                    if name in roleplay_state_names:
                        continue
                    if name and value:
                        variable_texts.append(_natural_detail_variable(name, value, note))
                if variable_texts:
                    lines.append("细节上，" + "；".join(variable_texts[:3]) + "。")
            interaction_updates = detail_snapshot.get("interaction_updates", [])
            if isinstance(interaction_updates, list) and interaction_updates:
                update_lines = []
                for update in interaction_updates[-3:]:
                    if not isinstance(update, dict):
                        continue
                    if _single_line(update.get("source_role"), 20) != "owner":
                        continue
                    reaction = _single_line(update.get("reaction"), 90)
                    state_updates = update.get("state_updates")
                    state_text = ""
                    if isinstance(state_updates, list) and state_updates:
                        filtered_updates = []
                        for item in state_updates:
                            text = _single_line(item, 50)
                            if not text:
                                continue
                            if any(name and name in text for name in roleplay_state_names):
                                continue
                            filtered_updates.append(text)
                        state_text = "；".join(filtered_updates)
                    pieces = [part for part in (reaction, state_text) if part]
                    if pieces:
                        update_lines.append("，".join(pieces))
                if update_lines:
                    lines.append("刚刚的介入：" + "；".join(update_lines) + "。")
        body = "\n".join(lines)
        return prompt_section("Bot 模拟当前片段", body) if as_section else f"【Bot 模拟当前片段】\n{body}"

    def _format_timer_scheduling_instruction(
        self,
        user: dict[str, Any] | None = None,
        *,
        as_section: bool = False,
    ) -> str | dict[str, Any]:
        if not self.enable_llm_timer_scheduling:
            return ""
        current_user = user if isinstance(user, dict) else {}
        role = self._private_user_role(current_user) if isinstance(user, dict) else "owner"
        followup_policy = self._activity_followup_quota_policy(current_user)
        tier = _safe_int(followup_policy.get("tier"), 3, 0, 5)
        tier_label = _single_line(followup_policy.get("tier_label"), 30) or f"L{tier}"
        max_intensity = _safe_int(followup_policy.get("max_intensity"), 1, 1, 3)
        completion_buffer = _safe_int(followup_policy.get("completion_buffer_minutes"), 0, 0, 30)
        role_note = (
            "当前是主要用户；强度 3 仍须人格资料明确支持监督、黏人或查岗倾向。"
            if role == "owner"
            else "当前是次要用户；动作回访强度必须为 1，只做普通朋友式轻问候。"
        )
        timing_note = (
            f"预约时间至少应落在预计完成后约 {completion_buffer} 分钟，给用户留出自然收尾空间。"
            if completion_buffer > 0
            else "预约时间可落在预计完成附近，但不能早于预计完成时间。"
        )
        current_time = self._environment_fromtimestamp(_now_ts()).strftime("%Y-%m-%d %H:%M:%S")
        reality_consented = getattr(self, "_reality_touch_audio_consented", lambda _: False)(current_user)
        enabled_getter = getattr(self, "_reality_companion_enabled", None)
        reality_ready = bool(callable(enabled_getter) and enabled_getter() and reality_consented)
        reality_touch_rule = (
            "用户已经具备现实触及音频授权。只有用户明确要求‘用现实触及/本机音响/电脑扬声器提醒’时，"
            "不要调用 `future_task`，必须只输出：\n"
            '<timer>{"time":"YYYY-MM-DD HH:MM:SS","delivery":"reality_touch","reason":"custom_reminder","topic":"要提醒的具体事项"}</timer>\n'
            "这种标签仍会注册为 AstrBot 官方一次性 Cron，由官方任务到点调用现实触及；普通提醒不得擅自改成现实触及。"
            if reality_ready
            else "当前用户没有可用的现实触及音频授权。即使用户提到音响，也不得承诺本机播放；普通提醒仍使用官方 `future_task`。"
        )
        body = f"""
当前本地时间：{current_time}。所有 time 都必须据此换算为未来的绝对时间。
一、明确约定：用户明确要求稍后提醒/叫醒/回头说，或双方形成明确临时约定时，若本轮提供 AstrBot 官方 `future_task` 工具，优先调用该工具；只有没有官方工具可用时，才在回复末尾写：
<timer>{{"time":"YYYY-MM-DD HH:MM:SS","topic":"约定内容"}}</timer>

同一约定只能选择 `future_task` 或 `<timer>` 其中一种，绝对不能同时创建。用户明确说“便签/便笺/备忘/待办/帮我记一下/记下来”时，应使用 `pc_manage_memo`；带提醒时间的便签由便签自身提醒，不得再调用 `future_task` 或输出 `<timer>`。

现实触及交付：{reality_touch_rule}

二、动作回访：用户明确说自己暂时离开去做一个有自然结束点的具体动作（如洗澡、吃饭、拿快递、短时出门办事），即使没有主动要求提醒，也可以形成一个“忙完后想问一句”的主动念头。生成念头的同一轮必须估计合理耗时并直接预约下一次主动消息：
<timer>{{"time":"YYYY-MM-DD HH:MM:SS","reason":"activity_followup","activity":"洗澡","estimated_minutes":30,"topic":"洗完澡后问问回来了没有","motive":"记得用户刚去洗澡，估计差不多结束后想自然问一句","followup_intensity":1,"style":"轻松自然"}}</timer>

估时应结合动作和用户给出的线索：洗澡通常 20-40 分钟，吃饭通常 30-60 分钟，短途办事通常 45-120 分钟；用户给了时长或返回时间时以用户信息为准。睡觉、上班、上学、长时间学习、旅行等没有可靠结束点的动作，不得擅自估时回访，除非用户给了明确时长或要求联系。用户只说“我在忙/没空/晚点聊”是在表达边界，不是可估时动作：不要创建回访，安静等待用户回来。
当前主动配额为 L{tier}（{tier_label}）：{followup_policy.get("generation_rule")} 最大回访强度为 {max_intensity}/3；{timing_note}
强度 1 是轻轻问一句；2 可以更直接、更有存在感；3 仅限主要用户且当前人格和关系明确支持的轻度监督感。无论强度都只发一次，不得命令、指责、施压、连续追发或假装看见用户现实状态。{role_note}

改时间直接写新时间；取消普通约定时写：<timer>{{"action":"cancel"}}</timer>；取消现实触及提醒时必须保留交付类型，写：<timer>{{"action":"cancel","delivery":"reality_touch","topic":"要取消的提醒事项"}}</timer>。
        除上述动作回访外，时间和约定不明确就不要写。标签不应出现在可见回复中，只会被转写为 AstrBot 官方一次性定时计划。"""
        body = body.lstrip("\n")
        return prompt_section("临时预约与动作回访", body) if as_section else f"【临时预约与动作回访】\n{body}"

    def _extract_timer_directives(self, text: str) -> tuple[str, list[dict[str, Any]]]:
        raw_text = str(text or "")
        payloads: list[dict[str, Any]] = []
        for match in TIMER_TAG_PATTERN.finditer(raw_text):
            payload = self._parse_timer_directive(match.group(1))
            if payload:
                payloads.append(payload)
        cleaned = TIMER_TAG_PATTERN.sub("", raw_text)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned, payloads

    def _llm_response_has_official_timer_tool(self, resp: Any) -> bool:
        names = getattr(resp, "tools_call_name", None)
        if isinstance(names, str) and names.strip() == "future_task":
            return True
        if isinstance(names, (list, tuple, set)) and any(str(name).strip() == "future_task" for name in names):
            return True
        raw_completion = getattr(resp, "raw_completion", None)
        candidates = [
            getattr(raw_completion, "model_extra", None),
            getattr(raw_completion, "additional_kwargs", None),
            getattr(resp, "metadata", None),
            getattr(resp, "extra_content", None),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            try:
                text = json.dumps(candidate, ensure_ascii=False)
            except Exception:
                text = str(candidate)
            if "future_task" in text:
                return True
        return False

    def _text_mentions_official_timer_created(self, text: str) -> bool:
        cleaned = _single_line(text, 500)
        if not cleaned:
            return False
        lower = cleaned.lower()
        has_explicit_job_id = bool(
            re.search(r"(?:job[_\s-]?id|任务\s*id|future task|cron job)\s*[:：#]?\s*[A-Za-z0-9_-]{6,}", cleaned, re.I)
        )
        if not has_explicit_job_id:
            return False
        if "future_task" in lower or "future task" in lower or "cron job" in lower or "cronjob" in lower:
            if any(token in lower for token in ("scheduled", "created", "job_id", "task")):
                return True
        official_markers = ("官方定时", "定时计划", "定时任务", "预约任务", "任务ID", "任务 id")
        success_markers = ("已创建", "已添加", "已登记", "已安排", "创建成功", "登记成功", "安排好了")
        return any(marker in cleaned for marker in official_markers) and any(marker in cleaned for marker in success_markers)

    def _should_skip_timer_capture_for_official_task(self, resp: Any, text: str) -> bool:
        return self._llm_response_has_official_timer_tool(resp) or self._text_mentions_official_timer_created(text)

    @staticmethod
    def _record_future_task_result(
        event: AstrMessageEvent,
        tool: Any,
        tool_args: Any,
        tool_result: Any,
    ) -> bool:
        if _single_line(getattr(tool, "name", ""), 80) != "future_task":
            return False
        action = _single_line((tool_args or {}).get("action") if isinstance(tool_args, dict) else "", 20).lower()
        success_prefixes = {
            "create": "Scheduled future task ",
            "edit": "Updated future task ",
            "delete": "Deleted cron job ",
        }
        expected_prefix = success_prefixes.get(action)
        if not expected_prefix and action != "list":
            return False
        try:
            setattr(event, "private_companion_future_task_result_observed", True)
            setattr(event, "private_companion_future_task_action", action)
        except Exception:
            return False
        if action == "list":
            return False
        if tool_result is None or bool(getattr(tool_result, "isError", False)):
            return False
        content = getattr(tool_result, "content", None)
        if not isinstance(content, list):
            return False
        result_text = "\n".join(
            str(getattr(item, "text", "") or "")
            for item in content
            if getattr(item, "text", None) is not None
        ).strip()
        if not result_text.startswith(expected_prefix):
            return False
        try:
            setattr(event, "private_companion_future_task_succeeded", True)
        except Exception:
            return False
        return True

    async def _schedule_llm_timer_after_response_dedup(
        self,
        event: AstrMessageEvent,
        resp: Any,
        user_id: str,
        payload: dict[str, Any],
        *,
        source_text: str,
        visible_text: str,
        trigger_message_id: str = "",
        trigger_umo: str = "",
    ) -> str:
        if bool(getattr(event, "private_companion_memo_reminder_saved", False)):
            logger.info(
                "跳过对话临时预约转写: 本轮已保存带提醒的便签 session=%s",
                _single_line(trigger_umo, 120) or "unknown",
            )
            return "memo_reminder"
        if bool(getattr(event, "private_companion_future_task_succeeded", False)):
            logger.info(
                "跳过对话临时预约转写: 本轮 AstrBot future_task 已执行成功 session=%s",
                _single_line(trigger_umo, 120) or "unknown",
            )
            return "official_task"
        future_task_result_observed = bool(
            getattr(event, "private_companion_future_task_result_observed", False)
        )
        if not future_task_result_observed and self._should_skip_timer_capture_for_official_task(resp, visible_text):
            logger.info(
                "跳过对话临时预约转写: 本轮疑似已由 AstrBot 官方定时计划处理 session=%s",
                _single_line(trigger_umo, 120) or "unknown",
            )
            return "official_task"
        if _single_line(payload.get("delivery"), 32).lower() == "reality_touch":
            scheduler = getattr(self, "_schedule_reality_touch_official_reminder", None)
            if not callable(scheduler):
                logger.warning("当前实例不支持现实触及官方提醒")
                return "reality_touch_unavailable"
            scheduled = await scheduler(
                user_id,
                payload,
                source_text=source_text,
                trigger_umo=trigger_umo,
            )
            return "reality_touch_official" if scheduled else "reality_touch_unavailable"
        await self._schedule_llm_timer(
            user_id,
            payload,
            source_text=source_text,
            source_origin="llm_response",
            trigger_message_id=trigger_message_id,
            trigger_umo=trigger_umo,
        )
        return "scheduled"

    def _parse_timer_directive(self, raw: str) -> dict[str, Any] | None:
        content = str(raw or "").strip()
        if not content:
            return None
        payload: dict[str, Any]
        if content.startswith("{") and content.endswith("}"):
            try:
                loaded = json.loads(content)
            except Exception:
                return None
            if not isinstance(loaded, dict):
                return None
            payload = {str(key): value for key, value in loaded.items()}
        else:
            payload = {"time": content}

        action_text = str(payload.get("action") or payload.get("operation") or "").strip().lower()
        cancel_requested = bool(payload.get("cancel")) or action_text in {"cancel", "delete", "remove", "取消", "删除", "撤销"}
        if cancel_requested:
            return {
                "cancel": True,
                "action": "cancel",
                "topic": _single_line(payload.get("topic") or payload.get("reason"), 60),
                "delivery": _single_line(payload.get("delivery"), 32).lower(),
                "reminder_id": _single_line(payload.get("reminder_id") or payload.get("id"), 40),
            }

        time_text = ""
        for key in ("time", "timer", "at", "datetime", "date"):
            candidate = payload.get(key)
            if candidate:
                time_text = str(candidate).strip()
                break
        if not time_text:
            return None
        scheduled_ts = self._parse_timer_timestamp(time_text)
        if scheduled_ts <= 0:
            return None
        parsed: dict[str, Any] = {"scheduled_ts": scheduled_ts, "raw_time": time_text}
        for key in ("reason", "topic", "motive", "action", "style", "activity"):
            value = payload.get(key)
            if value is not None:
                parsed[key] = _single_line(value, 140 if key == "motive" else 60)
        delivery = _single_line(payload.get("delivery"), 32).lower()
        if delivery == "reality_touch":
            parsed["delivery"] = delivery
            parsed["delivery_mode"] = _single_line(payload.get("delivery_mode"), 32).lower()
            parsed["playback_volume"] = _safe_int(payload.get("playback_volume"), -1, -1, 100)
            parsed["fade_in_ms"] = _safe_int(payload.get("fade_in_ms"), -1, -1, 5000)
        if _single_line(parsed.get("reason"), 40) == "activity_followup":
            parsed["estimated_minutes"] = _safe_int(payload.get("estimated_minutes"), 0, 0, 720)
            parsed["followup_intensity"] = self._normalize_activity_followup_intensity(
                payload.get("followup_intensity")
            )
        chain = self._normalize_chain_steps(payload.get("chain"))
        if chain:
            parsed["chain"] = chain
        return parsed

    def _normalize_chain_steps(self, raw_chain: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_chain, list):
            return []
        normalized_chain: list[dict[str, Any]] = []
        for step in raw_chain[:4]:
            if not isinstance(step, dict):
                continue
            kind = _single_line(step.get("kind"), 32)
            if not kind:
                continue
            normalized_chain.append(
                {
                    "kind": kind,
                    "after_minutes": _safe_int(step.get("after_minutes"), 0, 0, 240),
                    "reason": _single_line(step.get("reason"), 40),
                    "topic": _single_line(step.get("topic"), 80),
                    "motive": _single_line(step.get("motive"), 100),
                    "tone": _single_line(step.get("tone"), 30),
                }
            )
        return normalized_chain

    @staticmethod
    def _normalize_activity_followup_intensity(value: Any) -> int:
        text = str(value or "").strip().lower()
        aliases = {
            "soft": 1,
            "gentle": 1,
            "轻": 1,
            "轻柔": 1,
            "normal": 2,
            "direct": 2,
            "标准": 2,
            "直接": 2,
            "firm": 3,
            "strong": 3,
            "强": 3,
            "强势": 3,
        }
        if text in aliases:
            return aliases[text]
        return _safe_int(value, 1, 1, 3)

    def _activity_followup_intensity_for_user(self, value: Any, user: dict[str, Any]) -> int:
        intensity = self._normalize_activity_followup_intensity(value)
        policy = self._activity_followup_quota_policy(user)
        return min(intensity, _safe_int(policy.get("max_intensity"), 1, 1, 3))

    def _activity_followup_quota_policy(self, user: dict[str, Any] | None) -> dict[str, Any]:
        current_user = user if isinstance(user, dict) else {}
        quota_policy: dict[str, Any] = {}
        policy_getter = getattr(self, "_proactive_quota_policy", None)
        if callable(policy_getter):
            try:
                result = policy_getter(current_user)
                if isinstance(result, dict):
                    quota_policy = result
            except Exception:
                quota_policy = {}

        tier = _safe_int(quota_policy.get("tier"), 3, 0, 5)
        tier_label = _single_line(quota_policy.get("label"), 30) or {
            0: "已关闭",
            1: "克制",
            2: "轻陪伴",
            3: "稳定陪伴",
            4: "亲密陪伴",
            5: "持续在线",
        }.get(tier, "稳定陪伴")
        tier_rules = {
            0: (1, 15, "主动消息已关闭，不应自行创建动作回访。"),
            1: (1, 15, "只在动作非常具体、短时且有明确自然终点时才创建；宁可不追问。"),
            2: (1, 8, "仅对明确的短时动作创建轻量回访，不把普通离开都变成追问。"),
            3: (2, 3, "可对明确短时动作自然回访，语气应随关系而变化。"),
            4: (2, 0, "可更积极承接明确短时动作，但仍只形成一次自然回访。"),
            5: (3, 0, "明确短时动作可优先承接为回访，但不能把每次离开都解释成需要查岗。"),
        }
        max_intensity, buffer_minutes, generation_rule = tier_rules[tier]
        role = self._private_user_role(current_user)
        ignored = _safe_int(current_user.get("ignored_streak"), 0, 0)
        if role != "owner" or ignored > 0:
            max_intensity = 1
        if ignored > 0:
            buffer_minutes = max(buffer_minutes, 10)

        if max_intensity >= 3:
            persona_text = " ".join(
                (
                    str(runtime_persona_setting(self, "schedule_persona_prompt", "") or ""),
                    str(runtime_persona_setting(self, "persona_proactive_voice_prompt", "") or ""),
                    str(current_user.get("style") or ""),
                )
            )
            strong_markers = ("查岗", "监督", "管着", "管束", "强势", "严格", "占有", "黏人", "粘人")
            if not any(marker in persona_text for marker in strong_markers):
                max_intensity = 2

        return {
            "tier": tier,
            "tier_label": tier_label,
            "max_intensity": max_intensity,
            "completion_buffer_minutes": buffer_minutes,
            "generation_rule": generation_rule,
        }

    def _parse_timer_timestamp(self, time_text: str) -> float:
        normalized = str(time_text or "").strip()
        if not normalized:
            return 0.0
        for fmt in SUPPORTED_TIMER_FORMATS:
            try:
                return datetime.strptime(normalized, fmt).timestamp()
            except ValueError:
                continue
        return 0.0

    def _infer_timer_reason(self, scheduled_ts: float, source_text: str = "") -> str:
        dt = self._environment_fromtimestamp(scheduled_ts)
        minute = dt.hour * 60 + dt.minute
        lowered = str(source_text or "")
        if 8 * 60 <= minute <= 10 * 60 + 30:
            return "morning_greeting"
        if 12 * 60 <= minute <= 13 * 60 + 50:
            return "noon_greeting"
        if 21 * 60 <= minute <= 23 * 60 + 10:
            return "evening_greeting"
        if any(token in lowered for token in ("照片", "风景", "云", "雨", "光", "晚霞", "猫")):
            return "activity_share"
        if any(token in lowered for token in ("记下来", "那句话", "写下", "日记")):
            return "diary_share"
        return "check_in"

    def _timer_default_topic(self, reason: str, user: dict[str, Any], source_text: str = "") -> str:
        source = _single_line(source_text, 48)
        if source:
            return source
        return self._choose_proactive_topic(reason, user)

    def _timer_default_motive(
        self,
        reason: str,
        user: dict[str, Any],
        *,
        source_text: str = "",
        topic: str = "",
    ) -> str:
        if topic:
            return self._normalize_internal_motive_text(f"关于“{topic}”还有一点后续内容,适合稍后补充")
        if source_text:
            return self._normalize_internal_motive_text("刚才的话题还有一点后续内容,适合稍后补充")
        return self._choose_proactive_motive(reason, user, action="message")

    def _timer_source_implies_user_unavailable(self, source_text: str, payload: dict[str, Any] | None = None) -> bool:
        text = f"{source_text or ''} {_single_line((payload or {}).get('topic'), 80)} {_single_line((payload or {}).get('motive'), 120)}"
        if not text.strip():
            return False
        rest_tokens = (
            "睡觉",
            "睡会",
            "睡一会",
            "午睡",
            "补觉",
            "休息",
            "躺会",
            "躺一会",
            "眯一会",
            "小憩",
            "闭眼",
            "一起睡",
            "一起休息",
        )
        wake_tokens = (
            "叫我",
            "叫醒",
            "喊我",
            "喊醒",
            "起床",
            "醒来",
            "准时",
            "到点",
            "提醒我",
        )
        return any(token in text for token in rest_tokens) and any(token in text for token in wake_tokens)

    def _get_active_llm_timer(self, user: dict[str, Any]) -> dict[str, Any] | None:
        raw = user.get("llm_timer_event")
        if not isinstance(raw, dict) or not raw:
            return None
        if _single_line(raw.get("backend"), 40) != "astrbot_cron":
            return None
        status = _single_line(raw.get("status"), 40)
        if status not in {"pending", "registering", "replacing", "scheduled"}:
            return None
        scheduled_ts = _safe_float(raw.get("scheduled_ts"), 0)
        if scheduled_ts <= 0:
            return None
        return raw

    def _due_internal_llm_timer_id(self, user: dict[str, Any], *, now: float | None = None) -> str:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict) or not self._llm_timer_can_use_internal_scheduler(event):
            return ""
        check_now = _now_ts() if now is None else now
        if check_now < _safe_float(event.get("scheduled_ts"), 0):
            return ""
        return _single_line(event.get("id"), 40)

    def _has_due_llm_timer(self, user: dict[str, Any], now: float | None = None) -> bool:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return False
        if not self._llm_timer_can_use_internal_scheduler(event):
            return False
        now = now or _now_ts()
        return now >= _safe_float(event.get("scheduled_ts"), 0)

    def _llm_timer_can_use_internal_scheduler(self, event: dict[str, Any] | None) -> bool:
        """LLM timer is now a compatibility layer; execution belongs to AstrBot cron."""
        return False

    def _clear_llm_timer_internal_plan_fields(self, user: dict[str, Any]) -> None:
        if not isinstance(user, dict):
            return
        if normalize_legacy_tag_text(user.get("planned_proactive_source")) != "timer":
            return
        self._clear_pending_proactive_plan(user)

    def _clear_llm_timer_event(self, user: dict[str, Any], *, event_id: str = "") -> None:
        raw = user.get("llm_timer_event")
        if not isinstance(raw, dict):
            user["llm_timer_event"] = {}
            return
        if event_id and str(raw.get("id") or "") != event_id:
            return
        user["llm_timer_event"] = {}

    def _format_llm_timer_context(self, user: dict[str, Any], *, now: float | None = None) -> str:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return ""
        now = now or _now_ts()
        scheduled_ts = _safe_float(event.get("scheduled_ts"), 0)
        if scheduled_ts <= 0:
            return ""
        summary_parts = ["这是你之前自己留给自己的一个回头时间。"]
        topic = _single_line(event.get("topic"), 36)
        motive = _single_line(event.get("motive"), 60)
        seed = _single_line(event.get("seed_text"), 60)
        if topic:
            summary_parts.append(f"话题线索是“{topic}”。")
        elif seed:
            summary_parts.append(f"当时留下来的那句线索是：{seed}")
        if motive:
            summary_parts.append(f"当时心里的余味：{motive}")
        deferred = event.get("deferred_context")
        if isinstance(deferred, dict) and deferred:
            deferred_topic = _single_line(deferred.get("topic"), 40)
            deferred_motive = _single_line(deferred.get("motive"), 80)
            deferred_reason = _single_line(deferred.get("reason"), 30)
            deferred_text = deferred_topic or deferred_motive or deferred_reason
            if deferred_text:
                summary_parts.append(
                    f"这段静默期间原本还有一个顺带话头被留到了现在：{deferred_text}。"
                    "本次回复必须先完成预约/叫醒本意，再把这个话头当成一句顺带内容自然接上；不要单独展开成长篇。"
                )
        if now < scheduled_ts:
            summary_parts.append(f"现在离约好的时间还差 {self._format_duration_brief(scheduled_ts - now)}。")
        return " ".join(summary_parts)

    def _llm_timer_timezone_name(self) -> str:
        timezone_name = _single_line(getattr(self, "environment_perception_timezone", ""), 64) or "Asia/Shanghai"
        try:
            zoneinfo.ZoneInfo(timezone_name)
            return timezone_name
        except Exception:
            return "Asia/Shanghai"

    def _llm_timer_run_at(self, scheduled_ts: float) -> datetime:
        timezone_name = self._llm_timer_timezone_name()
        try:
            tzinfo = zoneinfo.ZoneInfo(timezone_name)
        except Exception:
            tzinfo = zoneinfo.ZoneInfo("Asia/Shanghai")
        return datetime.fromtimestamp(scheduled_ts, tzinfo)

    def _format_official_timer_note(
        self,
        *,
        scheduled_ts: float,
        reason: str,
        action: str,
        topic: str,
        motive: str,
        source_text: str,
        style: str = "",
        activity: str = "",
        estimated_minutes: int = 0,
        followup_intensity: int = 1,
    ) -> str:
        when = self._environment_fromtimestamp(scheduled_ts).strftime("%Y-%m-%d %H:%M")
        lines = [
            "这是 PrivateCompanion 从聊天中确认出的临时约定。到点后请按约定自然联系用户,不要解释这是定时任务。",
            f"约定时间：{when}",
        ]
        if topic:
            lines.append(f"约定内容：{topic}")
        if motive:
            lines.append(f"补充语境：{motive}")
        if reason:
            lines.append(f"类型：{reason}")
        if reason == "activity_followup":
            lines[0] = "这是 PrivateCompanion 根据用户暂时离开的动作生成的一次动作查岗主动消息。到点后自然联系用户,不要解释定时任务或内部判断。"
            if activity:
                lines.append(f"用户动作：{activity}")
            if estimated_minutes > 0:
                lines.append(f"生成念头时的预计耗时：{estimated_minutes} 分钟")
            lines.append(f"查岗强度：{followup_intensity}/3")
            intensity_rules = {
                1: "轻轻问一句动作是否结束或人是否回来了，不要求立即回复。",
                2: "可以更直接、更有存在感地问一句，但保持亲近和可拒绝。",
                3: "可带符合人格的轻度监督感或小小不满，但不得命令、指责、威胁或连续追发。",
            }
            lines.append(f"表达要求：{intensity_rules.get(followup_intensity, intensity_rules[1])}")
            proactive_voice = ""
            formatter = getattr(self, "_format_proactive_voice_prompt", None)
            if callable(formatter):
                proactive_voice = _single_line(formatter(), 500)
            if proactive_voice:
                lines.append(f"人格化主动风格：{proactive_voice}")
        if style:
            lines.append(f"语气参考：{style}")
        if action and action != "message":
            lines.append(f"期望动作：{action}")
        seed = _single_line(source_text, 180)
        if seed:
            lines.append(f"聊天线索：{seed}")
        lines.append("执行方式：使用 send_message_to_user 给原会话发一条简短自然的消息；如果是叫醒/提醒,直接完成提醒。只发送一次，不因用户未回复而自行追加。")
        return "\n".join(lines)

    def _official_cron_manager(self) -> Any | None:
        context = getattr(self, "context", None)
        manager = getattr(context, "cron_manager", None)
        if manager is not None:
            return manager
        nested = getattr(context, "context", None)
        return getattr(nested, "cron_manager", None)

    def _llm_timer_operation_lock(self, user_id: str) -> asyncio.Lock:
        locks = getattr(self, "_llm_timer_operation_locks", None)
        if not isinstance(locks, dict):
            locks = {}
            setattr(self, "_llm_timer_operation_locks", locks)
        key = _single_line(user_id, 120) or "_unknown"
        lock = locks.get(key)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            locks[key] = lock
        return lock

    async def _official_llm_timer_job_runtime(self, job_id: str) -> tuple[bool, str]:
        """Return whether runtime lookup is supported and the current official status."""
        normalized_job_id = _single_line(job_id, 80)
        if not normalized_job_id:
            return True, "missing"
        cron_mgr = self._official_cron_manager()
        if cron_mgr is None:
            return False, ""
        getter = getattr(cron_mgr, "get_job", None)
        if not callable(getter):
            getter = getattr(getattr(cron_mgr, "db", None), "get_cron_job", None)
        if not callable(getter):
            return False, ""
        try:
            job = await getter(normalized_job_id)
        except Exception as exc:
            logger.debug(
                "查询官方定时任务状态失败: job=%s error=%s",
                normalized_job_id,
                _single_line(exc, 160),
            )
            return False, ""
        if job is None:
            return True, "missing"
        return True, _single_line(getattr(job, "status", ""), 40).lower() or "scheduled"

    @staticmethod
    def _official_llm_timer_event_metadata(event: Any) -> dict[str, str]:
        getter = getattr(event, "get_extra", None)
        if not callable(getter):
            return {}
        try:
            payload = getter("cron_payload", {})
            cron_job = getter("cron_job", {})
        except Exception:
            return {}
        if not isinstance(payload, dict) or payload.get("origin") != "private_companion_timer":
            return {}
        private_payload = payload.get("private_companion")
        if not isinstance(private_payload, dict):
            return {}
        timer_id = _single_line(private_payload.get("timer_id"), 40)
        user_id = _single_line(payload.get("sender_id"), 120)
        job_id = _single_line(cron_job.get("id"), 80) if isinstance(cron_job, dict) else ""
        if not timer_id or not user_id:
            return {}
        return {"timer_id": timer_id, "user_id": user_id, "job_id": job_id}

    @staticmethod
    def _official_llm_timer_matches(current: Any, metadata: dict[str, str]) -> bool:
        if not isinstance(current, dict) or not metadata:
            return False
        if _single_line(current.get("backend"), 40) != "astrbot_cron":
            return False
        if _single_line(current.get("id"), 40) != metadata.get("timer_id"):
            return False
        current_job_id = _single_line(current.get("job_id") or current.get("candidate_job_id"), 80)
        event_job_id = metadata.get("job_id", "")
        return not (current_job_id and event_job_id and current_job_id != event_job_id)

    async def _acknowledge_official_llm_timer_trigger(self, event: Any) -> bool:
        metadata = self._official_llm_timer_event_metadata(event)
        if not metadata:
            return False
        user_id = metadata["user_id"]
        async with self._llm_timer_operation_lock(user_id):
            async with self._data_lock:
                users = self.data.get("users")
                current_user = users.get(user_id) if isinstance(users, dict) else None
                current = current_user.get("llm_timer_event") if isinstance(current_user, dict) else None
                if not self._official_llm_timer_matches(current, metadata):
                    return False
                current["status"] = "triggered"
                current["triggered_at"] = _now_ts()
                if metadata.get("job_id"):
                    current["job_id"] = metadata["job_id"]
                    current["cron_job_id"] = metadata["job_id"]
                self._clear_llm_timer_internal_plan_fields(current_user)
                self._save_data_sync(sections={"users"})
        logger.info(
            "官方临时预约开始执行: user=%s timer=%s job=%s",
            user_id,
            metadata["timer_id"],
            metadata.get("job_id") or "-",
        )
        return True

    @staticmethod
    def _official_llm_timer_tool_result_succeeded(tool_result: Any) -> bool:
        if tool_result is None or bool(getattr(tool_result, "isError", False)):
            return False
        content = getattr(tool_result, "content", None)
        if not isinstance(content, list):
            return False
        result_text = "\n".join(
            str(getattr(item, "text", "") or "")
            for item in content
            if getattr(item, "text", None) is not None
        ).strip()
        return result_text.startswith("Message sent to session ")

    async def _record_official_llm_timer_tool_result(
        self,
        event: Any,
        tool: Any,
        tool_result: Any,
    ) -> bool:
        if _single_line(getattr(tool, "name", ""), 80) != "send_message_to_user":
            return False
        metadata = self._official_llm_timer_event_metadata(event)
        if not metadata:
            return False
        succeeded = self._official_llm_timer_tool_result_succeeded(tool_result)
        user_id = metadata["user_id"]
        async with self._llm_timer_operation_lock(user_id):
            async with self._data_lock:
                users = self.data.get("users")
                current_user = users.get(user_id) if isinstance(users, dict) else None
                current = current_user.get("llm_timer_event") if isinstance(current_user, dict) else None
                if not self._official_llm_timer_matches(current, metadata):
                    return False
                current["status"] = "delivered" if succeeded else "delivery_failed"
                current["delivery_at"] = _now_ts()
                current["delivery_error"] = "" if succeeded else "send_message_to_user 未确认发送成功"
                self._save_data_sync(sections={"users"})
        return True

    async def _complete_official_llm_timer_event(self, event: Any) -> bool:
        metadata = self._official_llm_timer_event_metadata(event)
        if not metadata:
            return False
        user_id = metadata["user_id"]
        async with self._llm_timer_operation_lock(user_id):
            async with self._data_lock:
                users = self.data.get("users")
                current_user = users.get(user_id) if isinstance(users, dict) else None
                current = current_user.get("llm_timer_event") if isinstance(current_user, dict) else None
                if not self._official_llm_timer_matches(current, metadata):
                    return False
                status = _single_line(current.get("status"), 40)
                if status == "delivered":
                    current["status"] = "completed"
                    current["delivery_status"] = "sent"
                elif status == "triggered":
                    current["status"] = "completed_without_delivery"
                    current["delivery_status"] = "not_confirmed"
                elif status == "delivery_failed":
                    current["delivery_status"] = "failed"
                else:
                    return False
                current["completed_at"] = _now_ts()
                self._save_data_sync(sections={"users"})
        return True

    def _expire_stale_official_llm_timers_locked(self, *, now: float | None = None) -> int:
        check_now = _now_ts() if now is None else now
        users = self.data.get("users")
        if not isinstance(users, dict):
            return 0
        changed = 0
        for user in users.values():
            if not isinstance(user, dict):
                continue
            timer = user.get("llm_timer_event")
            if not isinstance(timer, dict) or _single_line(timer.get("backend"), 40) != "astrbot_cron":
                continue
            status = _single_line(timer.get("status"), 40)
            scheduled_ts = _safe_float(timer.get("scheduled_ts"), 0)
            triggered_at = _safe_float(timer.get("triggered_at"), 0)
            if status in {"pending", "registering", "replacing", "scheduled"} and scheduled_ts > 0 and check_now - scheduled_ts > 30 * 60:
                timer["status"] = "expired_unconfirmed"
                timer["expired_at"] = check_now
                timer["error"] = "官方任务已过期，但插件未收到执行回执"
                changed += 1
            elif status == "triggered" and triggered_at > 0 and check_now - triggered_at > 2 * 3600:
                timer["status"] = "triggered_unconfirmed"
                timer["expired_at"] = check_now
                timer["error"] = "官方任务已开始，但插件未收到完成回执"
                changed += 1
        return changed

    async def _add_official_llm_timer_job(
        self,
        *,
        user_id: str,
        user: dict[str, Any],
        timer_event: dict[str, Any],
        note: str,
        trigger_umo: str,
    ) -> tuple[str, str]:
        cron_mgr = self._official_cron_manager()
        if cron_mgr is None:
            return "", "AstrBot 官方定时计划不可用"
        scheduled_ts = _safe_float(timer_event.get("scheduled_ts"), 0)
        if scheduled_ts <= 0:
            return "", "预约时间无效"
        run_at = self._llm_timer_run_at(scheduled_ts)
        session = _single_line(trigger_umo, 180) or _single_line(user.get("umo"), 180)
        if not session:
            return "", "缺少私聊会话"
        payload = {
            "session": session,
            "sender_id": str(user_id),
            "note": note,
            "origin": "private_companion_timer",
            "private_companion": {
                "timer_id": _single_line(timer_event.get("id"), 40),
                "reason": _single_line(timer_event.get("reason"), 40),
                "action": _single_line(timer_event.get("action"), 40),
                "topic": _single_line(timer_event.get("topic"), 80),
                "activity": _single_line(timer_event.get("activity"), 60),
                "estimated_minutes": _safe_int(timer_event.get("estimated_minutes"), 0, 0, 720),
                "followup_intensity": _safe_int(timer_event.get("followup_intensity"), 1, 1, 3),
            },
        }
        try:
            job = await cron_mgr.add_active_job(
                name=(
                    "PrivateCompanion 动作查岗"
                    if _single_line(timer_event.get("reason"), 40) == "activity_followup"
                    else "PrivateCompanion 临时约定"
                ),
                cron_expression=None,
                payload=payload,
                description=_single_line(timer_event.get("topic") or note, 180),
                timezone=self._llm_timer_timezone_name(),
                enabled=True,
                persistent=True,
                run_once=True,
                run_at=run_at,
            )
        except Exception as exc:
            return "", _single_line(exc, 180) or repr(exc)
        return _single_line(getattr(job, "job_id", ""), 80), ""

    async def _delete_official_llm_timer_job(self, job_id: str) -> tuple[bool, str]:
        normalized_job_id = _single_line(job_id, 80)
        if not normalized_job_id:
            return False, "缺少官方任务 ID"
        cron_mgr = self._official_cron_manager()
        if cron_mgr is None:
            return False, "AstrBot 官方定时计划不可用"
        try:
            await cron_mgr.delete_job(normalized_job_id)
        except Exception as exc:
            return False, _single_line(exc, 180) or repr(exc)
        return True, ""

    async def _cancel_llm_timer(
        self,
        user_id: str,
        payload: dict[str, Any],
        *,
        source_text: str,
        source_origin: str,
        trigger_message_id: str = "",
        trigger_umo: str = "",
    ) -> bool:
        normalized_user_id = _single_line(user_id, 120)
        async with self._llm_timer_operation_lock(normalized_user_id):
            now_ts = _now_ts()
            async with self._data_lock:
                user = self._get_user(normalized_user_id)
                existing_raw = user.get("llm_timer_event")
                existing = deepcopy(existing_raw) if isinstance(existing_raw, dict) else {}
                expected_event_id = _single_line(payload.get("_expected_event_id"), 40)
                existing_event_id = _single_line(existing.get("id"), 40)
                if expected_event_id and existing_event_id != expected_event_id:
                    return False
                existing_status = _single_line(existing.get("status"), 40)
                existing_job_id = _single_line(
                    existing.get("job_id") or existing.get("candidate_job_id"),
                    80,
                )
                existing_active = (
                    _single_line(existing.get("backend"), 40) == "astrbot_cron"
                    and existing_status in {"pending", "registering", "replacing", "scheduled"}
                    and bool(existing_job_id)
                )
                if not existing_active:
                    if expected_event_id:
                        return False
                    user["llm_timer_event"] = {
                        "id": uuid.uuid4().hex,
                        "scheduled_ts": _safe_float(existing.get("scheduled_ts"), 0) or now_ts,
                        "action": "cancel",
                        "topic": _single_line(payload.get("topic") or "取消临时约定", 60),
                        "motive": _single_line(source_text, 140),
                        "origin": source_origin,
                        "created_at": now_ts,
                        "backend": "astrbot_cron",
                        "status": "cancel_skipped",
                        "error": "没有可取消的对话临时预约",
                    }
                    self._save_data_sync(sections={"users"})
                    return False

            runtime_supported, runtime_status = await self._official_llm_timer_job_runtime(existing_job_id)
            if runtime_supported and runtime_status in {"running", "completed", "failed", "missing"}:
                async with self._data_lock:
                    user = self._get_user(normalized_user_id)
                    current = user.get("llm_timer_event")
                    if not isinstance(current, dict) or _single_line(current.get("id"), 40) != existing_event_id:
                        return False
                    if _single_line(current.get("job_id") or current.get("candidate_job_id"), 80) != existing_job_id:
                        return False
                    if runtime_status == "running":
                        current["status"] = "triggered"
                        current["triggered_at"] = _safe_float(current.get("triggered_at"), 0) or now_ts
                        current["cancel_status"] = "too_late"
                        current["cancel_error"] = "官方任务已经开始执行，无法确认取消"
                    else:
                        current["status"] = "expired_unconfirmed"
                        current["cancel_status"] = "not_found"
                        current["cancel_error"] = "官方任务已结束或不存在，无法确认取消"
                    current.pop("cancel_requested_at", None)
                    self._save_data_sync(sections={"users"})
                return False

            async with self._data_lock:
                user = self._get_user(normalized_user_id)
                current = user.get("llm_timer_event")
                if not isinstance(current, dict) or _single_line(current.get("id"), 40) != existing_event_id:
                    return False
                if _single_line(current.get("job_id") or current.get("candidate_job_id"), 80) != existing_job_id:
                    return False
                current["status"] = "cancel_pending"
                current["cancel_requested_at"] = now_ts
                current["cancel_origin"] = source_origin
                current["cancel_topic"] = _single_line(payload.get("topic") or "取消临时约定", 60)
                current["cancel_source_text"] = _single_line(source_text, 140)
                self._save_data_sync(sections={"users"})

            ok, error = await self._delete_official_llm_timer_job(existing_job_id)
            async with self._data_lock:
                user = self._get_user(normalized_user_id)
                current = user.get("llm_timer_event")
                if not isinstance(current, dict) or _single_line(current.get("id"), 40) != existing_event_id:
                    return False
                if _single_line(current.get("job_id") or current.get("candidate_job_id"), 80) != existing_job_id:
                    return False
                if ok:
                    current["status"] = "cancelled"
                    current["cancelled_at"] = _now_ts()
                    current["cancelled_job_id"] = existing_job_id
                    current["cancel_status"] = "cancelled"
                    current["error"] = ""
                    current.pop("cancel_requested_at", None)
                    self._clear_llm_timer_internal_plan_fields(user)
                else:
                    restored = deepcopy(existing)
                    restored["cancel_status"] = "failed"
                    restored["cancel_error"] = error or "官方任务删除失败"
                    restored["cancel_failed_at"] = _now_ts()
                    restored.pop("cancel_requested_at", None)
                    user["llm_timer_event"] = restored
                self._save_data_sync(sections={"users"})
            logger.info(
                "对话临时预约取消%s: user=%s job=%s error=%s",
                "完成" if ok else "失败",
                normalized_user_id,
                existing_job_id,
                error or "-",
            )
            return ok

    def _queue_official_llm_timer_cancel(
        self,
        user_id: str,
        timer_event: dict[str, Any],
        *,
        source_text: str,
        source_origin: str,
        trigger_umo: str = "",
    ) -> bool:
        if not isinstance(timer_event, dict) or _single_line(timer_event.get("backend"), 40) != "astrbot_cron":
            return False
        if _single_line(timer_event.get("status"), 40) not in {"pending", "registering", "replacing", "scheduled"}:
            return False
        timer_id = _single_line(timer_event.get("id"), 40)
        normalized_user_id = _single_line(user_id or timer_event.get("user_id"), 120)
        if not timer_id or not normalized_user_id or _safe_float(timer_event.get("cancel_requested_at"), 0) > 0:
            return False
        timer_event["cancel_requested_at"] = _now_ts()
        operation = self._cancel_llm_timer(
            normalized_user_id,
            {
                "cancel": True,
                "topic": "用户已在问候时段自然出现，取消冲突问候",
                "_expected_event_id": timer_id,
            },
            source_text=source_text,
            source_origin=source_origin,
            trigger_umo=trigger_umo,
        )
        creator = getattr(self, "_create_lifecycle_background_task", None)
        try:
            if callable(creator):
                task = creator(operation, label=f"official_timer_cancel:{normalized_user_id}")
            else:
                task = asyncio.create_task(operation)
        except Exception:
            try:
                operation.close()
            except Exception:
                pass
            timer_event.pop("cancel_requested_at", None)
            return False
        if task is None:
            try:
                operation.close()
            except Exception:
                pass
            timer_event.pop("cancel_requested_at", None)
            return False
        return True

    def _has_active_activity_followup_timer(
        self,
        user: dict[str, Any] | None,
        *,
        trigger_message_id: str = "",
    ) -> bool:
        if not isinstance(user, dict):
            return False
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict) or _single_line(event.get("reason"), 40) != "activity_followup":
            return False
        current_message_id = _single_line(trigger_message_id, 120)
        original_message_id = _single_line(event.get("trigger_message_id"), 120)
        return not (current_message_id and original_message_id and current_message_id == original_message_id)

    async def _cancel_activity_followup_on_user_return(
        self,
        user_id: str,
        *,
        trigger_message_id: str = "",
        trigger_umo: str = "",
        source_text: str = "",
    ) -> bool:
        async with self._data_lock:
            user = self._get_user(user_id)
            should_cancel = self._has_active_activity_followup_timer(
                user,
                trigger_message_id=trigger_message_id,
            )
            event = self._get_active_llm_timer(user) if should_cancel else None
            expected_event_id = _single_line((event or {}).get("id"), 40)
        if not should_cancel:
            return False
        await self._cancel_llm_timer(
            user_id,
            {
                "cancel": True,
                "topic": "用户已提前回来，取消动作查岗",
                "_expected_event_id": expected_event_id,
            },
            source_text=_single_line(source_text, 140) or "用户在动作查岗到点前发来了新消息",
            source_origin="user_returned_before_activity_followup",
            trigger_message_id=trigger_message_id,
            trigger_umo=trigger_umo,
        )
        return True

    async def _schedule_llm_timer(
        self,
        user_id: str,
        payload: dict[str, Any],
        *,
        source_text: str,
        source_origin: str,
        trigger_message_id: str = "",
        trigger_umo: str = "",
    ) -> None:
        if bool(payload.get("cancel")):
            await self._cancel_llm_timer(
                user_id,
                payload,
                source_text=source_text,
                source_origin=source_origin,
                trigger_message_id=trigger_message_id,
                trigger_umo=trigger_umo,
            )
            return
        async with self._llm_timer_operation_lock(user_id):
            await self._schedule_llm_timer_locked(
                user_id,
                payload,
                source_text=source_text,
                source_origin=source_origin,
                trigger_message_id=trigger_message_id,
                trigger_umo=trigger_umo,
            )

    async def _schedule_llm_timer_locked(
        self,
        user_id: str,
        payload: dict[str, Any],
        *,
        source_text: str,
        source_origin: str,
        trigger_message_id: str = "",
        trigger_umo: str = "",
    ) -> None:
        scheduled_ts = max(_now_ts() + 30, _safe_float(payload.get("scheduled_ts"), 0))
        if scheduled_ts <= 0:
            return
        timer_event: dict[str, Any] | None = None
        note = ""
        user_snapshot: dict[str, Any] = {}
        replaced_job_id = ""
        existing_snapshot: dict[str, Any] = {}
        existing_event_id = ""
        operation_id = uuid.uuid4().hex
        async with self._data_lock:
            user = self._get_user(user_id)
            if not self._user_enabled_for_proactive(user_id, user):
                self._clear_pending_proactive_plan(user)
                self._save_data_sync(sections={"users"})
                return
            reason = _single_line(payload.get("reason"), 40) or self._infer_timer_reason(
                scheduled_ts,
                source_text,
            )
            if reason == "activity_followup":
                scheduling_now = _now_ts()
                estimated_minutes = _safe_int(payload.get("estimated_minutes"), 0, 0, 720)
                if estimated_minutes <= 0:
                    estimated_minutes = max(5, min(720, int(round((scheduled_ts - scheduling_now) / 60))))
                followup_policy = self._activity_followup_quota_policy(user)
                completion_buffer_minutes = _safe_int(
                    followup_policy.get("completion_buffer_minutes"),
                    0,
                    0,
                    30,
                )
                scheduled_ts = max(
                    scheduled_ts,
                    scheduling_now + 5 * 60,
                    scheduling_now + (estimated_minutes + completion_buffer_minutes) * 60,
                )
            else:
                estimated_minutes = 0
            action = _single_line(payload.get("action"), 24) or "message"
            if action not in {"message", "screen_peek", "photo_text", "voice"}:
                action = "message"
            if not self._friend_can_receive_proactive_reason(user, reason, action):
                reason = "check_in"
                action = "message"
            topic = _single_line(payload.get("topic"), 60) or self._timer_default_topic(
                reason,
                user,
                source_text,
            )
            motive = _single_line(payload.get("motive"), 140) or self._timer_default_motive(
                reason,
                user,
                source_text=source_text,
                topic=topic,
            )
            existing = user.get("llm_timer_event") if isinstance(user.get("llm_timer_event"), dict) else {}
            existing_active = (
                isinstance(existing, dict)
                and _single_line(existing.get("backend"), 40) == "astrbot_cron"
                and _single_line(existing.get("status"), 40) in {"scheduled", "pending", "registering", "replacing"}
                and bool(_single_line(existing.get("job_id") or existing.get("candidate_job_id"), 80))
            )
            if (
                reason == "activity_followup"
                and existing_active
                and _single_line(existing.get("reason"), 40) != "activity_followup"
            ):
                logger.info(
                    "保留已有明确预约,跳过自动动作查岗: user=%s existing=%s topic=%s",
                    user_id,
                    _single_line(existing.get("reason"), 40) or "appointment",
                    _single_line(existing.get("topic"), 80) or "-",
                )
                return
            if (
                existing_active
            ):
                replaced_job_id = _single_line(existing.get("job_id") or existing.get("candidate_job_id"), 80)
            existing_snapshot = deepcopy(existing) if isinstance(existing, dict) else {}
            existing_event_id = _single_line(existing_snapshot.get("id"), 40)
            activity = _single_line(payload.get("activity"), 60) if reason == "activity_followup" else ""
            followup_intensity = (
                self._activity_followup_intensity_for_user(payload.get("followup_intensity"), user)
                if reason == "activity_followup"
                else 1
            )
            timer_event = {
                "id": uuid.uuid4().hex,
                "scheduled_ts": scheduled_ts,
                "raw_time": _single_line(payload.get("raw_time"), 32),
                "reason": reason,
                "action": action,
                "topic": topic,
                "motive": self._normalize_internal_motive_text(motive),
                "style": _single_line(payload.get("style"), 40),
                "activity": activity,
                "estimated_minutes": estimated_minutes,
                "followup_intensity": followup_intensity,
                "seed_text": _single_line(source_text, 80),
                "origin": source_origin,
                "created_at": _now_ts(),
                "trigger_message_id": _single_line(trigger_message_id, 120),
                "trigger_umo": _single_line(trigger_umo, 160),
                "trigger_ts": _now_ts() if trigger_message_id else 0,
                "chain": list(payload.get("chain") or []) if isinstance(payload.get("chain"), list) else [],
                "silence_until_due": self._timer_source_implies_user_unavailable(source_text, payload),
                "backend": "astrbot_cron",
                "status": "replacing" if replaced_job_id else "registering",
                "operation_id": operation_id,
                "replaced_job_id": replaced_job_id,
                "previous_timer_id": existing_event_id,
            }
            note = self._format_official_timer_note(
                scheduled_ts=scheduled_ts,
                reason=reason,
                action=action,
                topic=topic,
                motive=timer_event["motive"],
                source_text=source_text,
                style=timer_event["style"],
                activity=activity,
                estimated_minutes=estimated_minutes,
                followup_intensity=followup_intensity,
            )
            user_snapshot = dict(user)
            user["llm_timer_event"] = deepcopy(timer_event)
            self._clear_llm_timer_internal_plan_fields(user)
            self._save_data_sync(sections={"users"})

        previous_running_job_id = ""
        if replaced_job_id:
            runtime_supported, runtime_status = await self._official_llm_timer_job_runtime(replaced_job_id)
            if runtime_supported and runtime_status == "running":
                previous_running_job_id = replaced_job_id
                replaced_job_id = ""
            elif runtime_supported and runtime_status in {"completed", "failed", "missing"}:
                replaced_job_id = ""

        job_id, error = await self._add_official_llm_timer_job(
            user_id=user_id,
            user=user_snapshot,
            timer_event=timer_event,
            note=note,
            trigger_umo=trigger_umo,
        )
        if not job_id:
            async with self._data_lock:
                user = self._get_user(user_id)
                current = user.get("llm_timer_event")
                if (
                    isinstance(current, dict)
                    and _single_line(current.get("id"), 40) == _single_line(timer_event.get("id"), 40)
                    and _single_line(current.get("operation_id"), 40) == operation_id
                ):
                    if existing_snapshot:
                        restored = deepcopy(existing_snapshot)
                        restored["last_replace_error"] = error or "新官方任务登记失败"
                        restored["last_replace_failed_at"] = _now_ts()
                        user["llm_timer_event"] = restored
                    else:
                        timer_event["status"] = "failed"
                        timer_event["error"] = error or "官方定时计划登记失败"
                        timer_event.pop("operation_id", None)
                        user["llm_timer_event"] = timer_event
                    self._save_data_sync(sections={"users"})
            logger.warning(
                "LLM 临时预约登记失败,已保留原任务: user=%s old_job=%s error=%s",
                user_id,
                replaced_job_id or previous_running_job_id or "-",
                error or "官方定时计划登记失败",
            )
            return

        async with self._data_lock:
            user = self._get_user(user_id)
            current = user.get("llm_timer_event")
            reservation_current = bool(
                isinstance(current, dict)
                and _single_line(current.get("id"), 40) == _single_line(timer_event.get("id"), 40)
                and _single_line(current.get("operation_id"), 40) == operation_id
            )
            if reservation_current:
                current["candidate_job_id"] = job_id
                self._save_data_sync(sections={"users"})
        if not reservation_current:
            await self._delete_official_llm_timer_job(job_id)
            logger.warning(
                "LLM 临时预约预留已失效,已回收新官方任务: user=%s job=%s",
                user_id,
                job_id,
            )
            return

        replace_error = ""
        rollback_error = ""
        if replaced_job_id:
            replaced_ok, replace_error = await self._delete_official_llm_timer_job(replaced_job_id)
            if not replaced_ok:
                rollback_ok, rollback_error = await self._delete_official_llm_timer_job(job_id)
                async with self._data_lock:
                    user = self._get_user(user_id)
                    current = user.get("llm_timer_event")
                    if (
                        isinstance(current, dict)
                        and _single_line(current.get("id"), 40) == _single_line(timer_event.get("id"), 40)
                        and _single_line(current.get("operation_id"), 40) == operation_id
                    ):
                        if rollback_ok and existing_snapshot:
                            restored = deepcopy(existing_snapshot)
                            restored["last_replace_error"] = replace_error or "旧官方任务删除失败"
                            restored["last_replace_failed_at"] = _now_ts()
                            user["llm_timer_event"] = restored
                        else:
                            current["status"] = "replace_rollback_failed"
                            current["job_id"] = replaced_job_id
                            current["candidate_job_id"] = job_id
                            current["replace_error"] = replace_error or "旧官方任务删除失败"
                            current["rollback_error"] = rollback_error or "新官方任务回滚失败"
                        self._save_data_sync(sections={"users"})
                logger.warning(
                    "LLM 临时预约替换失败,新任务回滚%s: user=%s old_job=%s new_job=%s error=%s rollback_error=%s",
                    "完成" if rollback_ok else "失败",
                    user_id,
                    replaced_job_id,
                    job_id,
                    replace_error or "-",
                    rollback_error or "-",
                )
                return

        async with self._data_lock:
            user = self._get_user(user_id)
            current = user.get("llm_timer_event")
            reservation_current = bool(
                isinstance(current, dict)
                and _single_line(current.get("id"), 40) == _single_line(timer_event.get("id"), 40)
                and _single_line(current.get("operation_id"), 40) == operation_id
            )
            if reservation_current:
                timer_event["job_id"] = job_id
                timer_event["status"] = "scheduled"
                timer_event["note"] = _single_line(note, 220)
                timer_event["replaced_job_id"] = replaced_job_id
                if previous_running_job_id:
                    timer_event["previous_running_job_id"] = previous_running_job_id
                timer_event.pop("candidate_job_id", None)
                timer_event.pop("operation_id", None)
                user["llm_timer_event"] = timer_event
                self._clear_llm_timer_internal_plan_fields(user)
                self._save_data_sync(sections={"users"})
        if not reservation_current:
            await self._delete_official_llm_timer_job(job_id)
            return
        logger.info(
            "LLM 临时预约已转写到官方定时计划: user=%s time=%s reason=%s action=%s topic=%s job=%s replaced=%s error=%s replace_error=%s",
            user_id,
            self._environment_fromtimestamp(scheduled_ts).strftime("%m-%d %H:%M:%S"),
            reason,
            action,
            topic,
            job_id or "-",
            replaced_job_id or "-",
            "-",
            replace_error or "-",
        )

    def _format_remaining(self, end_ts: Any) -> str:
        seconds = _safe_float(end_ts, 0) - _now_ts()
        if seconds <= 0:
            return "已结束"
        if seconds < 3600:
            return f"{max(1, int(seconds // 60))} 分钟"
        if seconds < 86400:
            return f"{int(seconds // 3600)} 小时"
        return f"{int(seconds // 86400)} 天"

    def _format_condition_started(self, start_ts: Any) -> str:
        ts = _safe_float(start_ts, 0)
        if ts <= 0:
            return "未知"
        dt = self._environment_fromtimestamp(ts)
        elapsed = max(0.0, _now_ts() - ts)
        return f"{dt.strftime('%m-%d %H:%M')}（已持续 {self._format_duration_brief(elapsed)}）"

    def _format_remaining_for_prompt(self, end_ts: Any) -> str:
        seconds = _safe_float(end_ts, 0) - _now_ts()
        if seconds <= 0:
            return "已结束"
        if seconds < 3600:
            minutes = max(1, int(seconds // 60))
            bucket = max(5, int(round(minutes / 5) * 5))
            return f"约{bucket}分钟"
        if seconds < 86400:
            hours = max(1, int(round(seconds / 3600)))
            return f"约{hours}小时"
        return f"约{max(1, int(round(seconds / 86400)))}天"

    def _format_condition_started_for_prompt(self, start_ts: Any) -> str:
        ts = _safe_float(start_ts, 0)
        if ts <= 0:
            return "未知"
        dt = self._environment_fromtimestamp(ts)
        elapsed = max(0.0, _now_ts() - ts)
        if elapsed < 3600:
            minutes = max(1, int(elapsed // 60))
            elapsed_text = f"约{max(5, int(round(minutes / 5) * 5))}分钟"
        elif elapsed < 86400:
            elapsed_text = f"约{max(1, int(round(elapsed / 3600)))}小时"
        else:
            elapsed_text = f"约{max(1, int(round(elapsed / 86400)))}天"
        return f"{dt.strftime('%m-%d %H:%M')}（已持续 {elapsed_text}）"

    def _format_duration_brief(self, seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        if seconds < 60:
            return f"{max(1, int(seconds))} 秒"
        if seconds < 3600:
            return f"{max(1, int(seconds // 60))} 分钟"
        if seconds < 86400:
            return f"{int(seconds // 3600)} 小时"
        return f"{int(seconds // 86400)} 天"

    def _format_suspended_summary(self, user: dict[str, Any]) -> str:
        raw = user.get("suspended_proactive")
        if not isinstance(raw, dict) or not raw.get("active"):
            return "悬着的话头：无"
        opener = _single_line(raw.get("opener_text"), 40) or "已先叫了一声"
        if raw.get("resume_ready"):
            return f"悬着的话头：等到用户回头了（{opener}）"
        due_at = _safe_float(raw.get("complaint_after_ts"), 0)
        due_text = self._format_remaining(due_at) if due_at > 0 and not raw.get("complaint_sent") else "已发过后续"
        return f"悬着的话头：还挂着（{opener}｜再等 {due_text}）"

    def _split_can_do_items(self, text: str) -> list[str]:
        raw_parts = re.split(r"[,,、;；\n]+", text)
        items = []
        for part in raw_parts:
            item = _single_line(part, 80)
            if item and item not in items:
                items.append(item)
        return items

    def _add_can_do_items(self, text: str) -> list[str]:
        new_items = self._split_can_do_items(text)
        if not new_items:
            return []
        current = self.data.setdefault("can_do", [])
        if not isinstance(current, list):
            current = []
            self.data["can_do"] = current
        added = []
        existing = {str(item) for item in current}
        for item in new_items:
            if item in existing:
                continue
            current.append(item)
            existing.add(item)
            added.append(item)
        if len(current) > 50:
            del current[:-50]
        return added

    def _remove_can_do_items(self, text: str) -> list[str]:
        targets = self._split_can_do_items(text)
        if not targets:
            return []
        current = self.data.setdefault("can_do", [])
        if not isinstance(current, list):
            self.data["can_do"] = []
            return []
        removed = []
        kept = []
        for item in current:
            item_text = str(item)
            if any(target in item_text or item_text in target for target in targets):
                removed.append(item_text)
            else:
                kept.append(item)
        self.data["can_do"] = kept
        return removed

    def _remove_can_do_targets(self, targets: Iterable[Any]) -> list[str]:
        """Remove can_do fragments that are clearly the same as blocked proactive material."""
        normalized_targets: list[str] = []
        target_signatures: set[str] = set()
        for raw in targets or []:
            text = _single_line(raw, 160)
            if not text:
                continue
            for part in self._split_can_do_items(text) or [text]:
                part_text = _single_line(part, 120)
                if len(part_text) < 3 or part_text in normalized_targets:
                    continue
                normalized_targets.append(part_text)
                signature = self._proactive_topic_signature(part_text)
                if signature:
                    target_signatures.add(signature)
        if not normalized_targets and not target_signatures:
            return []
        current = self.data.setdefault("can_do", [])
        if not isinstance(current, list):
            self.data["can_do"] = []
            return []
        removed: list[str] = []
        kept: list[Any] = []
        for item in current:
            item_text = _single_line(item, 120)
            if not item_text:
                continue
            item_signature = self._proactive_topic_signature(item_text)
            matched = any(
                target in item_text or item_text in target
                for target in normalized_targets
                if len(target) >= 3 and len(item_text) >= 3
            )
            if not matched and item_signature:
                matched = any(self._topic_signature_similar(item_signature, sig) for sig in target_signatures)
            if matched:
                removed.append(item_text)
            else:
                kept.append(item)
        self.data["can_do"] = kept
        return removed

    @staticmethod
    def _daily_plan_message_target_is_allowed(target: str) -> bool:
        normalized = re.sub(r"[\s“”\"'‘’《》【】\[\]（）()的那边这边身上手机微信QQqq号:：]+", "", str(target or ""))
        if not normalized:
            return False
        allowed_targets = (
            "你",
            "用户",
            "主人",
            "主要用户",
            "当前用户",
            "对方",
            "自己",
            "我",
        )
        neutral_targets = (
            "手机",
            "通知",
            "提醒",
            "闹钟",
            "系统",
            "日历",
            "输入框",
            "屏幕",
            "软件",
            "应用",
            "网页",
        )
        return any(token in normalized for token in allowed_targets) or normalized in neutral_targets

    @classmethod
    def _daily_plan_clause_has_named_message_interaction(cls, clause: str) -> bool:
        if not clause:
            return False
        target_patterns = (
            r"给(?P<target>[^，。；;,.!?？！、\s]{1,14}?)(?:回了?(?:一?条)?(?:消息|微信|QQ|私信|短信|语音)?|回复了?|发了?(?:一?条)?(?:消息|微信|QQ|私信|短信|语音)?|发去(?:消息|微信|QQ|私信|短信|语音)?|私聊了?)",
            r"(?:收到|看见|看到|点开|翻到)(?P<target>[^，。；;,.!?？！、\s]{1,14}?)(?:的)?(?:消息|微信|QQ|私信|短信|语音|提醒)",
            r"(?P<target>[^，。；;,.!?？！、\s]{1,14}?)(?:发来|发了|传来|弹来|回了?)(?:一?条)?(?:消息|微信|QQ|私信|短信|语音|提醒)",
            r"(?:和|跟)(?P<target>[^，。；;,.!?？！、\s]{1,14}?)(?:聊了?|聊天|私聊|互相吐槽|互相安慰|发消息|回消息)",
        )
        for pattern in target_patterns:
            for match in re.finditer(pattern, clause):
                target = _single_line(match.groupdict().get("target"), 24)
                if target and not cls._daily_plan_message_target_is_allowed(target):
                    return True
        relation_tokens = (
            "熟人",
            "同学",
            "老师",
            "朋友",
            "室友",
            "邻居",
            "前辈",
            "后辈",
            "家人",
            "妈妈",
            "爸爸",
            "哥哥",
            "姐姐",
            "弟弟",
            "妹妹",
        )
        message_actions = (
            "发来消息",
            "发了消息",
            "回了消息",
            "回消息",
            "回复",
            "私聊",
            "聊天",
            "提醒她",
            "提醒他",
            "找她",
            "找他",
        )
        return any(token in clause for token in relation_tokens) and any(token in clause for token in message_actions)

    def _daily_plan_named_entity_is_known(self, name: Any) -> bool:
        normalized = _single_line(name, 32).casefold()
        if not normalized:
            return False
        known_names = [_single_line(runtime_persona_setting(self, "bot_name", "小星"), 80)]
        data = getattr(self, "data", {})
        users = data.get("users") if isinstance(data, dict) else None
        if isinstance(users, dict):
            for user in users.values():
                if not isinstance(user, dict):
                    continue
                known_names.extend(
                    _single_line(user.get(field), 80)
                    for field in ("nickname", "display_name", "user_name", "name")
                )
        if any(candidate and candidate.casefold() == normalized for candidate in known_names):
            return True
        persona_sources = (
            runtime_persona_setting(self, "schedule_persona_prompt", ""),
            runtime_persona_setting(self, "schedule_worldview_prompt", ""),
            getattr(self, "_default_persona_prompt_cache", ""),
        )
        boundary = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(normalized)}(?![A-Za-z0-9_])", re.IGNORECASE)
        return any(boundary.search(str(source or "")) for source in persona_sources)

    @staticmethod
    def _daily_plan_relationship_alias_groups() -> tuple[tuple[str, ...], ...]:
        """Stable relationship names that must come from an identity source."""
        return (
            ("妈妈", "母亲", "妈咪", "老妈", "娘亲", "阿妈"),
            ("爸爸", "父亲", "爹地", "老爸", "爹", "阿爸"),
            ("父母", "双亲"),
            ("家人", "家里人", "亲人"),
            ("祖母", "奶奶", "外婆", "姥姥"),
            ("祖父", "爷爷", "外公", "姥爷"),
            ("哥哥", "兄长", "大哥", "阿哥"),
            ("姐姐", "姊姊", "姊姐", "阿姐"),
            ("弟弟", "胞弟"),
            ("妹妹", "胞妹"),
            ("兄弟姐妹", "兄弟姊妹", "手足"),
            ("叔叔", "伯伯", "舅舅", "姨父", "姑父"),
            ("阿姨", "姑姑", "姨妈", "舅妈", "婶婶", "伯母"),
            ("亲戚", "亲属"),
            ("朋友", "好友", "闺蜜", "发小", "死党"),
            ("同学", "同桌", "同班同学", "校友"),
            ("学长", "学姐", "学弟", "学妹"),
            ("老师", "教师", "班主任", "导师"),
            ("师父", "师傅"),
            ("室友", "舍友"),
            ("同事", "同僚"),
            ("上司", "领导", "老板"),
            ("邻居", "邻家"),
            ("前辈", "后辈"),
            ("恋人", "爱人", "伴侣"),
            ("男朋友", "男友"),
            ("女朋友", "女友"),
            ("丈夫", "老公"),
            ("妻子", "老婆"),
            ("未婚夫", "未婚妻"),
            ("监护人", "养父", "养母", "继父", "继母"),
        )

    @staticmethod
    def _daily_plan_identity_bound_relationship_groups() -> set[str]:
        """Relationships too stable/private to infer from an old life fragment."""
        return {
            "妈妈",
            "爸爸",
            "父母",
            "家人",
            "祖母",
            "祖父",
            "哥哥",
            "姐姐",
            "弟弟",
            "妹妹",
            "兄弟姐妹",
            "叔叔",
            "阿姨",
            "亲戚",
            "恋人",
            "男朋友",
            "女朋友",
            "丈夫",
            "妻子",
            "未婚夫",
            "监护人",
        }

    @staticmethod
    def _mask_non_relationship_phrases(text: Any) -> str:
        source = str(text or "")
        if not source:
            return ""
        for phrase in (
            "母亲节",
            "父亲节",
            "教师节",
            "父母官",
            "老师傅",
            "小姐姐",
            "小哥哥",
            "食堂阿姨",
            "宿管阿姨",
            "保洁阿姨",
            "清洁阿姨",
            "保安叔叔",
            "司机叔叔",
            "老婆饼",
        ):
            source = source.replace(phrase, "□" * len(phrase))
        return source

    def _daily_plan_relationship_authority_sources(self) -> tuple[str, ...]:
        sources = [
            str(runtime_persona_setting(self, "schedule_persona_prompt", "") or ""),
            str(runtime_persona_setting(self, "schedule_worldview_prompt", "") or ""),
            str(getattr(self, "_default_persona_prompt_cache", "") or ""),
        ]
        getter = getattr(self, "_get_default_persona_prompt", None)
        if callable(getter):
            try:
                sources.append(str(getter() or ""))
            except Exception:
                pass
        return tuple(dict.fromkeys(source for source in sources if source.strip()))

    def _daily_plan_declared_relation_tokens(self) -> set[str]:
        authority_text = self._mask_non_relationship_phrases(
            "\n".join(self._daily_plan_relationship_authority_sources())
        )
        declared: set[str] = set()
        groups = self._daily_plan_relationship_alias_groups()
        for aliases in groups:
            if any(alias in authority_text for alias in aliases):
                declared.update(aliases)

        # Institutional roles are an inherent part of an explicitly declared
        # school/work identity, while family roles are never inferred this way.
        if re.search(r"学生|校园|学校|上学|教室|班级|课程", authority_text):
            for aliases in groups:
                if aliases[0] in {"同学", "学长", "老师"}:
                    declared.update(aliases)
        if re.search(r"上班|职员|员工|公司|工位|办公室|职场", authority_text):
            for aliases in groups:
                if aliases[0] in {"同事", "上司"}:
                    declared.update(aliases)
        return declared

    def _daily_plan_undeclared_relationship_tokens(self, text: Any) -> list[str]:
        source = self._mask_non_relationship_phrases(text)
        if not source:
            return []
        declared = self._daily_plan_declared_relation_tokens()
        hits: list[str] = []
        identity_bound_groups = self._daily_plan_identity_bound_relationship_groups()
        all_aliases = sorted(
            {
                alias
                for group in self._daily_plan_relationship_alias_groups()
                if group[0] in identity_bound_groups
                for alias in group
            },
            key=len,
            reverse=True,
        )
        for alias in all_aliases:
            if alias not in declared and alias in source:
                hits.append(alias)
        return hits

    @staticmethod
    def _relationship_clause_is_explicitly_user_owned(clause: str, relation_tokens: list[str]) -> bool:
        if not clause or not relation_tokens:
            return False
        owner_marker = r"(?:主要用户|当前用户|这位用户|收件人|对方|用户|User|user)"
        for token in relation_tokens:
            escaped = re.escape(token)
            if re.search(rf"{owner_marker}[^，,。；;！？!?]{{0,24}}{escaped}", clause):
                return True
            if re.search(rf"{escaped}[^，,。；;！？!?]{{0,16}}(?:是|属于|来自)?{owner_marker}(?:的|那边)", clause):
                return True
        return False

    def _format_generation_relationship_authority_guard(self) -> str:
        declared = self._daily_plan_declared_relation_tokens()
        canonical = [
            aliases[0]
            for aliases in self._daily_plan_relationship_alias_groups()
            if any(alias in declared for alias in aliases)
        ]
        declared_text = "、".join(canonical) if canonical else "无"
        return (
            "【关系事实权限】\n"
            "- 只有日程专用角色设定、日程世界观和当前默认人格能够建立 Bot 的稳定关系；"
            "旧日程、旧日记、旧动态、聊天摘要、MemoryCompanion、技能/创作记录和其他连续性材料不能单独证明一段新关系。\n"
            f"- 当前身份来源已声明的关系称谓：{declared_text}。只有这些关系及其同义称呼可以作为 Bot 的关系事实。\n"
            "- 连续性材料里的关系若不能和身份来源对上，就按未经核实的旧叙事略过；不要续写，也不要换个称呼继续沿用。\n"
            "- 用户谈到的亲友只属于用户，除非身份来源另有明确设定，不得转移成 Bot 自己的亲友。"
            "\n- 当前收件人与 Bot 的结构化关系由本轮收件人关系事实单独决定，不要用这份生活关系清单覆盖它。"
        )

    def _sanitize_generation_relationship_context(
        self,
        text: Any,
        *,
        source: str = "",
        max_chars: int = 0,
    ) -> str:
        """Remove undeclared relationship clauses before they reach a generator."""
        raw = str(text or "").strip()
        if not raw:
            return ""
        initial_hits = self._daily_plan_undeclared_relationship_tokens(raw)
        if not initial_hits:
            return raw[:max_chars] if max_chars > 0 else raw

        cleaned_lines: list[str] = []
        removed_any = False
        for raw_line in raw.splitlines():
            line = raw_line.strip()
            if not line:
                if cleaned_lines and cleaned_lines[-1]:
                    cleaned_lines.append("")
                continue
            pieces = re.split(r"([，,。；;！？!?]+)", line)
            kept: list[str] = []
            for index in range(0, len(pieces), 2):
                clause = pieces[index].strip()
                separator = pieces[index + 1] if index + 1 < len(pieces) else ""
                if not clause:
                    continue
                clause_hits = self._daily_plan_undeclared_relationship_tokens(clause)
                if clause_hits and not self._relationship_clause_is_explicitly_user_owned(clause, clause_hits):
                    removed_any = True
                    continue
                kept.append(clause)
                if separator:
                    kept.append(separator)
            clean_line = "".join(kept).strip(" ，,。；;！？!?")
            if clean_line and clean_line not in {"-", "*", "•"}:
                cleaned_lines.append(clean_line)
        if not removed_any:
            return raw[:max_chars] if max_chars > 0 else raw
        cleaned = "\n".join(cleaned_lines).strip()
        if max_chars > 0:
            cleaned = cleaned[:max_chars]
        if cleaned == (raw[:max_chars] if max_chars > 0 else raw):
            return cleaned

        log_key = f"{source or '-'}|{'/'.join(initial_hits[:4])}"
        now = _now_ts()
        recent_logs = getattr(self, "_recent_relationship_context_sanitize_logs", None)
        if not isinstance(recent_logs, dict):
            recent_logs = {}
            setattr(self, "_recent_relationship_context_sanitize_logs", recent_logs)
        if now - _safe_float(recent_logs.get(log_key), 0) >= 1800:
            logger.info(
                "生成前已剔除未声明关系上下文: source=%s relations=%s",
                source or "-",
                ",".join(initial_hits[:8]),
            )
            recent_logs[log_key] = now
        return cleaned

    def _daily_plan_clause_has_unsafe_social_fact(self, text: str) -> bool:
        clause = _single_line(text, 160)
        if not clause:
            return False
        if self._daily_plan_undeclared_relationship_tokens(clause):
            return True
        if self._daily_plan_clause_has_named_message_interaction(clause):
            return True
        future_commitment = (
            "约好",
            "约了",
            "约定",
            "约着",
            "约去",
            "约夜宵",
            "约饭",
            "约见",
            "约她",
            "约他",
            "约人",
            "下周",
            "下次一起",
            "改天一起",
            "明天一起",
            "后天一起",
            "之后一起",
            "过几天一起",
        )
        if any(token in clause for token in future_commitment):
            return True
        if re.search(r"(约|叫|喊|拉|找|邀)[^，。；;,.]{0,16}(一起|夜宵|吃|喝|看|玩|逛|见面|出门)", clause):
            return True
        if re.search(r"(和|跟)[^，。；;,.]{1,16}一起(去|吃|喝|看|玩|逛|见|出门|夜宵)", clause):
            return True
        if re.search(r"(消息|私信|电话|语音)[^，。；;,.]{0,16}(约|叫|喊|拉|邀)[^，。；;,.]{0,16}(一起|去|吃|喝|看|玩|逛|夜宵)", clause):
            return True
        concrete_relation = (
            "熟人",
            "同学",
            "老师",
            "朋友",
            "室友",
            "邻居",
            "前辈",
            "后辈",
            "家人",
            "父母",
            "妈妈",
            "爸爸",
            "哥哥",
            "姐姐",
            "弟弟",
            "妹妹",
        )
        if any(token in clause for token in ("碰见", "遇见", "撞见", "碰到", "遇到")) and any(
            token in clause for token in concrete_relation
        ):
            return True
        if re.search(r"(碰见|遇见|撞见|遇到)[过了]?[一-龥]{2,4}", clause) and not any(
            token in clause for token in ("路人", "店员", "陌生人", "旁边的人", "小动物", "猫", "狗", "鸟")
        ):
            return True
        if re.search(r"(顺手|顺带|特意|回来时|回来的时候)?.{0,8}给[^，。；;,.]{1,12}(带|买|捎|留|放)了?", clause):
            return True
        named_companion = re.search(
            r"(?:与|和|跟)\s*[A-Z][A-Za-z0-9_.-]{1,23}\s*(?:一起)?(?:吃|喝|聊|逛|玩|看|见面|出门)",
            clause,
        )
        if named_companion:
            name_match = re.search(r"(?:与|和|跟)\s*([A-Z][A-Za-z0-9_.-]{1,23})", named_companion.group(0))
            if name_match and self._daily_plan_named_entity_is_known(name_match.group(1)):
                return False
            return True
        return False

    @staticmethod
    def _sanitize_schedule_model_artifacts(text: Any, *, limit: int = 180) -> str:
        """Remove model scratch fields and speaker continuations from schedule prose."""
        source = re.sub(r"\s+", " ", str(text or "")).strip()
        if not source:
            return ""
        source = source.replace("```json", "").replace("```", "")
        source = source.replace("**", "").replace("__", "").replace("`", "")

        scratch_pattern = re.compile(
            r"(?:^|[\s，。；;!?！？])(?:dream[_\s-]*seed|analysis|reasoning(?:_content)?|角色草稿|续写提示)\s*[:：]",
            re.IGNORECASE,
        )
        scratch = scratch_pattern.search(source)
        if scratch:
            source = source[: scratch.start()].rstrip(" ，。；;:：")

        speaker_pattern = re.compile(
            r"(?:^|[\s，。；;!?！？])(?:Fox|Assistant|Character|Bot|[A-Z][A-Za-z0-9_.-]{1,20})\s*[:：]"
        )
        speaker = speaker_pattern.search(source)
        if speaker:
            if not source[: speaker.start()].strip(" ，。；;:："):
                return ""
            source = source[: speaker.start()].rstrip(" ，。；;:：")
        return _single_line(source, limit)

    @staticmethod
    def _schedule_text_is_single_meal_action(text: Any) -> bool:
        source = _single_line(text, 240)
        if not source:
            return False
        meal_action = re.search(
            r"吃(?:着|了|完|过|点|一|顿|碗)?|用餐|进餐|品尝|享用|早餐|早饭|午餐|午饭|晚餐|晚饭|夜宵|喝粥",
            source,
        )
        if not meal_action:
            return False
        return not re.search(
            r"吃完|饭后|餐后|随后|然后|之后|接着|再去|再把|转而|余下|剩下|后来|收拾完.*(?:休息|做|处理|出门)",
            source,
        )

    @staticmethod
    def _sanitize_schedule_meal_time_wording(text: Any, start_minutes: int | None) -> str:
        source = _single_line(text, 180)
        if not source or start_minutes is None:
            return source
        minute = int(start_minutes) % (24 * 60)
        if minute < 16 * 60:
            source = re.sub(r"吃(?:晚饭|晚餐)", "吃点东西", source)
            source = re.sub(r"(?:晚饭|晚餐)", "用餐", source)
        if minute >= 15 * 60:
            source = re.sub(r"吃(?:早餐|早饭)", "吃点东西", source)
            source = re.sub(r"(?:早餐|早饭)", "用餐", source)
        return _single_line(source, 180)

    @classmethod
    def _sanitize_overlong_schedule_activity(cls, text: Any, duration_minutes: int | None) -> str:
        source = _single_line(text, 180)
        if not source or duration_minutes is None or duration_minutes <= 120:
            return source
        if not cls._schedule_text_is_single_meal_action(source):
            return source
        stem = source.rstrip("。；;，, ")
        return _single_line(f"这段开始时，{stem}；吃完后便按这段时间的节奏休息或处理手边的事。", 180)

    def _sanitize_daily_plan_social_fact_text(self, text: str, *, field: str = "") -> str:
        source = self._sanitize_schedule_model_artifacts(text, limit=180)
        if not source:
            return ""
        raw_clauses = [part for part in re.split(r"[，,。；;]+", source) if _single_line(part, 120)]
        unsafe_flags = [self._daily_plan_clause_has_unsafe_social_fact(part) for part in raw_clauses]
        if not any(unsafe_flags):
            return source
        kept = []
        for index, part in enumerate(raw_clauses):
            if unsafe_flags[index]:
                continue
            cleaned_part = _single_line(part, 120)
            if (
                index + 1 < len(raw_clauses)
                and unsafe_flags[index + 1]
                and len(cleaned_part) <= 20
                and re.search(r"(?:时|的时候|期间|过程中)$", cleaned_part)
            ):
                continue
            kept.append(cleaned_part)
        cleaned = "，".join(kept).strip("，,。；; ")
        if not cleaned:
            cleaned = "放慢节奏处理手边的小事，把这段时间过得轻一点"
        if cleaned == source:
            return source
        log_key = "|".join((field or "-", _single_line(source, 120), _single_line(cleaned, 120)))
        now = _now_ts()
        recent_logs = getattr(self, "_recent_social_fact_sanitize_logs", None)
        if not isinstance(recent_logs, dict):
            recent_logs = {}
            setattr(self, "_recent_social_fact_sanitize_logs", recent_logs)
        last_logged = _safe_float(recent_logs.get(log_key), 0)
        if now - last_logged >= 1800:
            logger.info(
                "已清理日程中的未授权社交事实: field=%s before=%s after=%s",
                field or "-",
                _single_line(source, 120),
                _single_line(cleaned, 120),
            )
            recent_logs[log_key] = now
            if len(recent_logs) > 200:
                cutoff = now - 3600
                for key, ts in list(recent_logs.items()):
                    if _safe_float(ts, 0) < cutoff:
                        recent_logs.pop(key, None)
        return cleaned

    @staticmethod
    def _sanitize_empty_daily_plan_message_seed(text: str) -> str:
        cleaned = _single_line(text, 140)
        if not cleaned:
            return ""
        normalized = re.sub(r"[。！？!?,，、；;\s]+", "", cleaned)
        empty_markers = (
            "这段没什么想说的",
            "没什么想说的",
            "这段没有什么想说的",
            "没有什么想说的",
            "这段先留白",
            "先留白",
            "留白",
            "脑子空空的",
            "脑袋空空的",
            "没什么可说的",
            "没有什么可说的",
            "这段没话说",
            "没话说",
            "先不吵你",
            "不吵你",
            "先不打扰你",
            "不打扰你",
            "这段先安静一下",
            "先安静一下",
            "下午空一下",
            "下午空一会",
            "下午空一会儿",
            "下午空了下",
        )
        if normalized in empty_markers:
            return ""
        if any(token in normalized for token in ("没什么想说", "没有什么想说", "没什么可说", "没有什么可说")):
            return ""
        if any(token in normalized for token in ("先不吵", "不打扰", "先留白")):
            return ""
        if re.fullmatch(r"(?:上午|中午|下午|晚上|午后|傍晚)?(?:先)?空(?:一下|一会儿?|了下)", normalized):
            return ""
        return cleaned

    def _sanitize_daily_plan_inplace(self, plan: dict[str, Any]) -> bool:
        if not isinstance(plan, dict):
            return False
        raw_items = plan.get("items") if isinstance(plan.get("items"), list) else plan.get("schedule")
        if not isinstance(raw_items, list):
            return False
        changed = self._normalize_plan_item_intervals(raw_items)
        parsed_starts = [
            self._parse_hhmm_to_minutes(item.get("time")) if isinstance(item, dict) else None
            for item in raw_items
        ]
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            for field in ("activity", "message_seed"):
                original = _single_line(item.get(field), 180)
                if not original:
                    continue
                cleaned = self._sanitize_daily_plan_social_fact_text(original, field=field)
                if field == "activity":
                    start = parsed_starts[index]
                    next_start = next(
                        (candidate for candidate in parsed_starts[index + 1 :] if candidate is not None),
                        None,
                    )
                    end = self._plan_item_end_minutes(start, item, next_start=next_start) if start is not None else None
                    duration = end - start if start is not None and end is not None else None
                    cleaned = self._sanitize_schedule_meal_time_wording(cleaned, start)
                    cleaned = self._sanitize_overlong_schedule_activity(cleaned, duration)
                if field == "message_seed":
                    cleaned = self._sanitize_empty_daily_plan_message_seed(cleaned)
                if cleaned != original:
                    item[field] = cleaned
                    changed = True
        if changed:
            plan["sanitized_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
        return changed

    def _sanitize_state_variables_social_facts_inplace(self, state_variables: Any, *, field: str = "state_variables") -> bool:
        if not isinstance(state_variables, list):
            return False
        changed = False
        for index, item in enumerate(state_variables):
            if not isinstance(item, dict):
                continue
            for key in ("value", "note"):
                original = _single_line(item.get(key), 180)
                if not original:
                    continue
                cleaned = self._sanitize_daily_plan_social_fact_text(
                    original,
                    field=f"{field}.{index}.{key}",
                )
                if cleaned != original:
                    item[key] = cleaned
                    changed = True
        return changed

    def _sanitize_proactive_social_fact_fields_inplace(self, item: dict[str, Any], *, field: str) -> bool:
        if not isinstance(item, dict):
            return False
        changed = False
        for key in ("topic", "motive", "why", "scene", "impulse"):
            original = _single_line(item.get(key), 180)
            if not original:
                continue
            cleaned = self._sanitize_daily_plan_social_fact_text(original, field=f"{field}.{key}")
            if cleaned != original:
                item[key] = cleaned
                changed = True
        if changed and "signature" in item:
            item["signature"] = self._proactive_topic_signature(
                item.get("reason"),
                item.get("source"),
                item.get("topic"),
                item.get("motive"),
            )
        return changed

    def _sanitize_user_proactive_social_facts_inplace(self, user: dict[str, Any], *, field: str) -> bool:
        if not isinstance(user, dict):
            return False
        changed = False
        for source_key in ("planned_proactive_topic", "planned_proactive_motive"):
            original = _single_line(user.get(source_key), 180)
            if not original:
                continue
            cleaned = self._sanitize_daily_plan_social_fact_text(original, field=f"{field}.{source_key}")
            if cleaned != original:
                user[source_key] = cleaned
                changed = True
        if changed:
            user["planned_proactive_model_judge_signature"] = ""
            user["planned_proactive_model_judge_result"] = {}
        impulses = user.get("proactive_impulses")
        if isinstance(impulses, list):
            for index, item in enumerate(impulses):
                if self._sanitize_proactive_social_fact_fields_inplace(
                    item,
                    field=f"{field}.proactive_impulses.{index}",
                ):
                    changed = True
        recent_topics = user.get("recent_proactive_topics")
        if isinstance(recent_topics, list):
            kept_topics: list[Any] = []
            topics_changed = False
            meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
            for index, topic in enumerate(recent_topics):
                if isinstance(topic, dict):
                    if callable(meta_leak_checker) and (
                        meta_leak_checker(str(topic.get("text") or ""))
                        or meta_leak_checker(str(topic.get("signature") or ""))
                    ):
                        topics_changed = True
                        continue
                    item_changed = False
                    cleaned_topic = dict(topic)
                    for key in ("text", "topic", "motive"):
                        original_value = _single_line(cleaned_topic.get(key), 180)
                        if not original_value:
                            continue
                        cleaned_value = self._sanitize_daily_plan_social_fact_text(
                            original_value,
                            field=f"{field}.recent_proactive_topics.{index}.{key}",
                        )
                        if cleaned_value != original_value:
                            cleaned_topic[key] = cleaned_value
                            item_changed = True
                    if item_changed:
                        topics_changed = True
                    kept_topics.append(cleaned_topic)
                    continue
                original = _single_line(topic, 180)
                if not original:
                    continue
                cleaned = self._sanitize_daily_plan_social_fact_text(
                    original,
                    field=f"{field}.recent_proactive_topics.{index}",
                )
                if cleaned != original:
                    topics_changed = True
                if cleaned and cleaned != "放慢节奏处理手边的小事，把这段时间过得轻一点":
                    kept_topics.append(cleaned)
            if topics_changed:
                user["recent_proactive_topics"] = kept_topics[-20:]
                changed = True
        return changed

    def _sanitize_relationship_text_tree_inplace(self, value: Any, *, field: str) -> bool:
        changed = False
        if isinstance(value, dict):
            for key, item in list(value.items()):
                item_field = f"{field}.{key}" if field else str(key)
                if str(key) in {
                    "raw",
                    "raw_text",
                    "original_text",
                    "prompt",
                    "prompt_text",
                    "response",
                    "response_text",
                }:
                    continue
                if isinstance(item, str):
                    cleaned = self._sanitize_generation_relationship_context(item, source=item_field)
                    if cleaned != item:
                        value[key] = cleaned
                        changed = True
                elif isinstance(item, (dict, list)) and self._sanitize_relationship_text_tree_inplace(
                    item,
                    field=item_field,
                ):
                    changed = True
            return changed
        if isinstance(value, list):
            rebuilt: list[Any] = []
            for index, item in enumerate(value):
                item_field = f"{field}.{index}" if field else str(index)
                if isinstance(item, str):
                    cleaned = self._sanitize_generation_relationship_context(item, source=item_field)
                    if cleaned != item:
                        changed = True
                    if cleaned:
                        rebuilt.append(cleaned)
                else:
                    if isinstance(item, (dict, list)) and self._sanitize_relationship_text_tree_inplace(
                        item,
                        field=item_field,
                    ):
                        changed = True
                    rebuilt.append(item)
            if rebuilt != value:
                value[:] = rebuilt
                changed = True
        return changed

    @story_legacy_sync_operation("daily-state.story-source-sanitize")
    def _cleanup_generated_relationship_history_inplace(self) -> bool:
        """Stop old Bot-authored relationship hallucinations from becoming new evidence."""
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return False
        changed = False
        counts: dict[str, int] = {}

        for key in (
            "daily_state",
            "daily_plan_history",
            "daily_story_plan_history",
            "detail_enhanced_history",
        ):
            value = data.get(key)
            if isinstance(value, (dict, list)) and self._sanitize_relationship_text_tree_inplace(value, field=key):
                changed = True
                counts[key] = counts.get(key, 0) + 1

        for key in ("bot_diaries", "self_meal_log", "proactive_audit_log"):
            records = data.get(key)
            if isinstance(records, list) and self._sanitize_relationship_text_tree_inplace(records, field=key):
                changed = True
                counts[key] = counts.get(key, 0) + 1

        projects = data.get("creative_projects")
        if isinstance(projects, list):
            for index, project in enumerate(projects):
                if not isinstance(project, dict):
                    continue
                source_text = project.get("source_text")
                if not isinstance(source_text, str):
                    continue
                cleaned = self._sanitize_generation_relationship_context(
                    source_text,
                    source=f"creative_projects.{index}.source_text",
                )
                if cleaned != source_text:
                    project["source_text"] = cleaned
                    changed = True
                    counts["creative_projects.source_text"] = counts.get("creative_projects.source_text", 0) + 1

        skill_state = data.get("skill_growth")
        skills = skill_state.get("skills") if isinstance(skill_state, dict) else None
        if isinstance(skills, dict):
            for skill in skills.values():
                if not isinstance(skill, dict):
                    continue
                logs = skill.get("recent_logs")
                if not isinstance(logs, list):
                    continue
                if self._sanitize_relationship_text_tree_inplace(
                    logs,
                    field="skill_growth.recent_logs",
                ):
                    changed = True
                    counts["skill_growth.recent_logs"] = counts.get("skill_growth.recent_logs", 0) + 1

        qzone_state = data.get("qzone_integration")
        if isinstance(qzone_state, dict):
            recent_posts = qzone_state.get("recent_life_publish_texts")
            if isinstance(recent_posts, list) and self._sanitize_relationship_text_tree_inplace(
                recent_posts,
                field="qzone_integration.recent_life_publish_texts",
            ):
                changed = True
                counts["qzone_integration.recent_life_publish_texts"] = 1
            for key, value in list(qzone_state.items()):
                if not isinstance(value, str):
                    continue
                if not (
                    key.endswith("_text")
                    or key.endswith("_draft")
                    or key.endswith("_caption")
                    or key in {"last_publish_recorded_text"}
                ):
                    continue
                cleaned = self._sanitize_generation_relationship_context(
                    value,
                    source=f"qzone_integration.{key}",
                )
                if cleaned != value:
                    qzone_state[key] = cleaned
                    changed = True
                    counts["qzone_integration.text_fields"] = counts.get("qzone_integration.text_fields", 0) + 1

        if changed:
            logger.info("已清理未声明关系的生成历史: %s", counts)
        return changed

    def _sanitize_runtime_social_facts_inplace(self) -> bool:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return False
        changed = False
        daily_plan = data.get("daily_plan")
        if isinstance(daily_plan, dict) and self._sanitize_daily_plan_inplace(daily_plan):
            changed = True
        story_plan = data.get("daily_story_plan")
        if isinstance(story_plan, dict) and self._sanitize_story_plan_social_facts_inplace(story_plan):
            changed = True
        enhanced = data.get("detail_enhanced_segments")
        if isinstance(enhanced, dict) and self._sanitize_detail_enhanced_segments_inplace(enhanced):
            changed = True
        pool = data.get("proactive_candidate_pool")
        if isinstance(pool, list):
            for index, item in enumerate(pool):
                if self._sanitize_proactive_social_fact_fields_inplace(
                    item,
                    field=f"proactive_candidate_pool.{index}",
                ):
                    changed = True
        users = data.get("users")
        if isinstance(users, dict):
            for user_id, user in users.items():
                if self._sanitize_user_proactive_social_facts_inplace(user, field=f"users.{user_id}"):
                    changed = True
        if self._cleanup_generated_relationship_history_inplace():
            changed = True
        if changed:
            data["social_fact_sanitized_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
        return changed

    def _cleanup_framework_meta_leak_records(self) -> bool:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return False
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        if not callable(meta_leak_checker):
            return False

        def has_meta(value: Any) -> bool:
            if value is None:
                return False
            if isinstance(value, str):
                return meta_leak_checker(value)
            return meta_leak_checker(str(value))

        def list_item_has_meta(item: Any, fields: tuple[str, ...]) -> bool:
            if isinstance(item, dict):
                return any(has_meta(item.get(field)) for field in fields)
            return has_meta(item)

        changed = False
        removed_counts: dict[str, int] = {}

        def filter_list(owner: dict[str, Any], key: str, fields: tuple[str, ...], *, limit: int | None = None) -> None:
            nonlocal changed
            raw = owner.get(key)
            if not isinstance(raw, list):
                return
            kept = [item for item in raw if not list_item_has_meta(item, fields)]
            if limit is not None:
                kept = kept[-limit:]
            if len(kept) != len(raw):
                owner[key] = kept
                removed_counts[key] = removed_counts.get(key, 0) + len(raw) - len(kept)
                changed = True

        users = data.get("users")
        if isinstance(users, dict):
            for user in users.values():
                if not isinstance(user, dict):
                    continue
                for key in (
                    "last_companion_message",
                    "last_proactive_message",
                    "last_proactive_text",
                    "last_reply_text",
                ):
                    if has_meta(user.get(key)):
                        user[key] = ""
                        removed_counts[key] = removed_counts.get(key, 0) + 1
                        changed = True
                filter_list(user, "recent_proactive_topics", ("text", "signature", "topic", "motive"), limit=12)
                filter_list(user, "recent_reply_topics", ("text", "signature", "topic"), limit=18)
                filter_list(user, "action_consequences", ("text", "summary", "action_summary"), limit=18)
                continuity = user.get("state_continuity")
                if isinstance(continuity, dict):
                    for key in ("last_action_text", "last_reply_text", "last_message_text"):
                        if has_meta(continuity.get(key)):
                            continuity[key] = ""
                            count_key = f"state_continuity.{key}"
                            removed_counts[count_key] = removed_counts.get(count_key, 0) + 1
                            changed = True

        filter_list(data, "proactive_audit_log", ("text_preview", "original_text_preview", "final_text_preview", "text", "note", "topic", "motive", "diagnostic_detail"), limit=120)

        troubleshooting = data.get("troubleshooting_test_results")
        if isinstance(troubleshooting, dict):
            for key, result in list(troubleshooting.items()):
                if list_item_has_meta(result, ("text_preview", "original_text_preview", "final_text_preview", "detail", "error", "diagnostic_detail")):
                    troubleshooting.pop(key, None)
                    removed_counts["troubleshooting_test_results"] = removed_counts.get("troubleshooting_test_results", 0) + 1
                    changed = True

        prompt_root = data.get("recent_prompt_injections")
        if isinstance(prompt_root, dict):
            for kind, items in list(prompt_root.items()):
                if not isinstance(items, list):
                    continue
                kept: list[Any] = []
                removed = 0
                for item in items:
                    item_has_meta = list_item_has_meta(item, ("preview", "content", "title"))
                    if not item_has_meta and isinstance(item, dict):
                        modules = item.get("modules")
                        if isinstance(modules, list):
                            item_has_meta = any(
                                list_item_has_meta(module, ("preview", "content", "title", "key"))
                                for module in modules
                            )
                    if item_has_meta:
                        removed += 1
                        continue
                    kept.append(item)
                if removed:
                    prompt_root[kind] = kept[:8] if kind == "tts" else kept[:5]
                    count_key = f"recent_prompt_injections.{kind}"
                    removed_counts[count_key] = removed_counts.get(count_key, 0) + removed
                    changed = True

        if changed:
            logger.info("已清理框架工具循环摘要污染记录: %s", removed_counts)
        return changed

    def _sanitize_story_plan_social_facts_inplace(self, story_plan: dict[str, Any]) -> bool:
        if not isinstance(story_plan, dict):
            return False
        changed = False
        summary = _single_line(story_plan.get("summary"), 180)
        if summary:
            cleaned = self._sanitize_daily_plan_social_fact_text(summary, field="story_plan.summary")
            if cleaned != summary:
                story_plan["summary"] = cleaned
                changed = True
        if self._sanitize_state_variables_social_facts_inplace(
            story_plan.get("state_variables"),
            field="story_plan.state_variables",
        ):
            changed = True
        for item in story_plan.get("today_events") or []:
            if not isinstance(item, dict):
                continue
            original = _single_line(item.get("event"), 180)
            if not original:
                continue
            cleaned = self._sanitize_daily_plan_social_fact_text(original, field="story_plan.today_events.event")
            if cleaned != original:
                item["event"] = cleaned
                changed = True
        for item in story_plan.get("proactive_events") or []:
            if not isinstance(item, dict):
                continue
            for field in ("topic", "why", "motive", "scene", "impulse"):
                original = _single_line(item.get(field), 180)
                if not original:
                    continue
                cleaned = self._sanitize_daily_plan_social_fact_text(original, field=f"story_plan.proactive_events.{field}")
                if cleaned != original:
                    item[field] = cleaned
                    changed = True
        if changed:
            story_plan["sanitized_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
        return changed

    def _detail_segment_bounds_for_snapshot_key(self, key: str) -> tuple[int, int] | None:
        match = re.fullmatch(r"\d{4}-\d{2}-\d{2}:(\d+):(\d{1,2}:\d{2})", str(key or ""))
        if not match:
            return None
        plan = getattr(self, "data", {}).get("daily_plan", {})
        items = plan.get("items") if isinstance(plan, dict) else None
        if not isinstance(items, list):
            return None
        index = _safe_int(match.group(1), -1, minimum=-1)
        start = self._parse_hhmm_to_minutes(match.group(2))
        if index < 0 or start is None:
            return None
        next_start = None
        for next_item in items[index + 1 :]:
            if not isinstance(next_item, dict):
                continue
            next_start = self._parse_hhmm_to_minutes(next_item.get("time"))
            if next_start is not None:
                break
        current_item = items[index] if index < len(items) and isinstance(items[index], dict) else None
        end = self._plan_item_end_minutes(start, current_item, next_start=next_start)
        return start, end

    def _sanitize_detail_snapshot_for_segment_inplace(
        self,
        snapshot: dict[str, Any],
        segment: dict[str, Any] | tuple[int, int] | None,
        *,
        field: str = "detail",
    ) -> bool:
        if not isinstance(snapshot, dict):
            return False
        if isinstance(segment, tuple):
            start, end = segment
        elif isinstance(segment, dict):
            start = _safe_int(segment.get("start"), 0)
            end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
            if end <= start:
                end += 24 * 60
        else:
            start = end = None
        duration = end - start if start is not None and end is not None else None
        changed = False

        original_summary = _single_line(snapshot.get("summary"), 180)
        if original_summary:
            summary = self._sanitize_daily_plan_social_fact_text(original_summary, field=f"{field}.summary")
            summary = self._sanitize_schedule_meal_time_wording(summary, start)
            summary = self._sanitize_overlong_schedule_activity(summary, duration)
            if summary != original_summary:
                snapshot["summary"] = summary
                changed = True

        meal_event_minutes: list[int] = []
        for index, item in enumerate(snapshot.get("today_events") or []):
            if not isinstance(item, dict):
                continue
            original = _single_line(item.get("event"), 180)
            if not original:
                continue
            event_start = start
            window = _single_line(item.get("window"), 24)
            window_match = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*[-~—–至]\s*(\d{1,2}:\d{2})\s*", window)
            event_end = None
            if window_match:
                event_start = self._parse_hhmm_to_minutes(window_match.group(1))
                event_end = self._parse_hhmm_to_minutes(window_match.group(2))
                if event_start is not None and event_end is not None:
                    if event_end <= event_start:
                        event_end += 24 * 60
                    if self._schedule_text_is_single_meal_action(original):
                        meal_event_minutes.append(max(1, event_end - event_start))
            cleaned = self._sanitize_daily_plan_social_fact_text(
                original,
                field=f"{field}.today_events.{index}.event",
            )
            cleaned = self._sanitize_schedule_meal_time_wording(cleaned, event_start)
            if cleaned != original:
                item["event"] = cleaned
                changed = True

        presence = snapshot.get("presence_status")
        if isinstance(presence, dict) and duration is not None and duration > 120:
            custom_text = _single_line(presence.get("custom_text") or presence.get("wording"), 28)
            if self._schedule_text_is_single_meal_action(custom_text):
                cap = min(60, max(meal_event_minutes) if meal_event_minutes else 45)
                configured = _safe_int(presence.get("duration_minutes"), cap, minimum=1)
                if configured > cap or not _single_line(presence.get("duration_minutes"), 12):
                    presence["duration_minutes"] = str(cap)
                    changed = True
        return changed

    def _sanitize_detail_enhanced_segments_inplace(self, enhanced: dict[str, Any]) -> bool:
        if not isinstance(enhanced, dict):
            return False
        changed = False
        for key, snapshot in enhanced.items():
            if not isinstance(snapshot, dict):
                continue
            snapshot_changed = False
            if (
                _single_line(snapshot.get("status"), 24) == "generating"
                and not self._detail_enhancement_snapshot_blocks_generation(snapshot)
            ):
                stale_generation_id = _single_line(snapshot.get("generation_id"), 64)
                snapshot["status"] = "failed"
                snapshot["updated_at"] = self._environment_now().strftime("%H:%M")
                snapshot["error"] = _single_line(snapshot.get("error"), 180) or "上次细化生成中断或超时"
                snapshot["retry_after"] = ""
                snapshot["retry_after_ts"] = 0
                snapshot["summary"] = _single_line(snapshot.get("summary"), 120) or "这一段细化生成中断，稍后会自动重试。"
                stored_previous_state = snapshot.get("previous_item_state") if isinstance(snapshot.get("previous_item_state"), dict) else {}
                snapshot.pop("generation_id", None)
                snapshot.pop("previous_item_state", None)
                keyed = re.fullmatch(r"(\d{4}-\d{2}-\d{2}):(\d+):(\d{1,2}:\d{2})", str(key))
                live_plan = self.data.get("daily_plan", {})
                live_items = live_plan.get("items") if isinstance(live_plan, dict) else None
                if keyed and isinstance(live_items, list):
                    index = int(keyed.group(2))
                    live_item = live_items[index] if 0 <= index < len(live_items) and isinstance(live_items[index], dict) else None
                    if isinstance(live_item, dict) and _single_line(live_item.get("_detail_generation_id"), 64) == stale_generation_id:
                        if stored_previous_state:
                            for field, state in stored_previous_state.items():
                                if not isinstance(state, dict):
                                    continue
                                if bool(state.get("existed")):
                                    live_item[field] = state.get("value")
                                else:
                                    live_item.pop(field, None)
                        else:
                            live_item.pop("_detail_generation_id", None)
                changed = True
                snapshot_changed = True
            bounds = self._detail_segment_bounds_for_snapshot_key(str(key))
            if bounds and not isinstance(snapshot.get("quality"), dict):
                snapshot["quality"] = evaluate_detail_quality(
                    self,
                    snapshot,
                    {"start": bounds[0], "end": bounds[1], "item": {}},
                )
                changed = True
                snapshot_changed = True
            if self._sanitize_detail_snapshot_for_segment_inplace(
                snapshot,
                bounds,
                field=f"detail_enhanced_segments.{key}",
            ):
                changed = True
                snapshot_changed = True
            summary = _single_line(snapshot.get("summary"), 180)
            if summary:
                cleaned = self._sanitize_daily_plan_social_fact_text(summary, field=f"detail_enhanced_segments.{key}.summary")
                if cleaned != summary:
                    snapshot["summary"] = cleaned
                    changed = True
                    snapshot_changed = True
            if self._sanitize_state_variables_social_facts_inplace(
                snapshot.get("state_variables"),
                field=f"detail_enhanced_segments.{key}.state_variables",
            ):
                changed = True
                snapshot_changed = True
            for item in snapshot.get("today_events") or []:
                if not isinstance(item, dict):
                    continue
                original = _single_line(item.get("event"), 180)
                if not original:
                    continue
                cleaned = self._sanitize_daily_plan_social_fact_text(original, field=f"detail_enhanced_segments.{key}.today_events.event")
                if cleaned != original:
                    item["event"] = cleaned
                    changed = True
                    snapshot_changed = True
            for item in snapshot.get("proactive_events") or []:
                if not isinstance(item, dict):
                    continue
                for field in ("topic", "why", "motive", "scene", "impulse"):
                    original = _single_line(item.get(field), 180)
                    if not original:
                        continue
                    cleaned = self._sanitize_daily_plan_social_fact_text(original, field=f"detail_enhanced_segments.{key}.proactive_events.{field}")
                    if cleaned != original:
                        item[field] = cleaned
                        changed = True
                        snapshot_changed = True
            if snapshot_changed and snapshot.get("status") == "done":
                snapshot["coverage_repair_done"] = True
                snapshot["social_fact_sanitized_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
        return changed

    @staticmethod
    def _deepseek_peak_minute(value: str, *, allow_24: bool = False) -> int | None:
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if allow_24 and hour == 24 and minute == 0:
            return 1440
        if hour > 23 or minute > 59:
            return None
        return hour * 60 + minute

    def _parse_deepseek_peak_windows(self) -> list[tuple[int, int]]:
        raw = str(getattr(self, "deepseek_peak_windows", "") or "")
        windows: list[tuple[int, int]] = []
        for item in re.split(r"[,，;；\n]+", raw):
            text = item.strip()
            if not text:
                continue
            match = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*[-~～—至]+\s*(\d{1,2}:\d{2})\s*", text)
            if not match:
                continue
            start = self._deepseek_peak_minute(match.group(1))
            end = self._deepseek_peak_minute(match.group(2), allow_24=True)
            if start is None or end is None or start == end:
                continue
            windows.append((start, end))
        return windows

    def _deepseek_peak_status(self, now: datetime | None = None) -> dict[str, Any]:
        timezone_name = str(getattr(self, "deepseek_peak_timezone", "Asia/Shanghai") or "Asia/Shanghai").strip()
        try:
            timezone = zoneinfo.ZoneInfo(timezone_name)
        except Exception:
            timezone_name = "Asia/Shanghai"
            timezone = zoneinfo.ZoneInfo(timezone_name)
        if now is None:
            local_now = datetime.now(timezone)
        elif now.tzinfo is None:
            local_now = now.replace(tzinfo=timezone)
        else:
            local_now = now.astimezone(timezone)
        windows = self._parse_deepseek_peak_windows()
        minute = local_now.hour * 60 + local_now.minute
        active = any(
            (start <= minute < end) if start < end else (minute >= start or minute < end)
            for start, end in windows
        )
        transitions: list[datetime] = []
        base_day = local_now.date()
        # Include yesterday so a cross-midnight window can expose its upcoming
        # end transition while the current time is after midnight.
        for day_offset in range(-1, 3):
            day = base_day + timedelta(days=day_offset)
            for start, end in windows:
                start_dt = datetime.combine(day, datetime.min.time(), timezone) + timedelta(minutes=start)
                end_day = day + timedelta(days=1) if start > end else day
                end_minute = end if end < 1440 else 0
                if end == 1440:
                    end_day = day + timedelta(days=1)
                end_dt = datetime.combine(end_day, datetime.min.time(), timezone) + timedelta(minutes=end_minute)
                if start_dt > local_now:
                    transitions.append(start_dt)
                if end_dt > local_now:
                    transitions.append(end_dt)
        next_transition = min(transitions) if transitions else None
        replacement_id = str(getattr(self, "deepseek_peak_replacement_provider_id", "") or "").strip()
        enabled = bool(getattr(self, "enable_deepseek_peak_replacement", False))
        return {
            "enabled": enabled,
            "active": bool(enabled and active and replacement_id),
            "in_window": active,
            "configured": bool(replacement_id),
            "timezone": timezone_name,
            "current_time": local_now.strftime("%Y-%m-%d %H:%M"),
            "next_transition": next_transition.strftime("%Y-%m-%d %H:%M") if next_transition else "",
            "windows": [
                f"{start // 60:02d}:{start % 60:02d}-{('24:00' if end == 1440 else f'{end // 60:02d}:{end % 60:02d}')}"
                for start, end in windows
            ],
            "replacement_provider_id": replacement_id,
        }

    def _provider_matches_deepseek(self, provider_id: str) -> bool:
        safe_id = str(provider_id or "").strip()
        if not safe_id:
            return False
        parts = [safe_id]
        provider = None
        getter = getattr(getattr(self, "context", None), "get_provider_by_id", None)
        if callable(getter):
            try:
                provider = getter(safe_id)
            except Exception:
                provider = None
        if provider is not None:
            parts.extend(
                str(value or "")
                for value in (
                    getattr(provider, "name", ""),
                    getattr(provider, "display_name", ""),
                    provider.__class__.__name__,
                )
            )
            config = getattr(provider, "provider_config", None) or getattr(provider, "config", None) or {}
            fields = (
                "id", "provider_id", "name", "display_name", "label", "title", "provider", "type",
                "provider_type", "model", "model_name", "api_model", "model_id", "api_base", "base_url",
                "api_base_url", "api_url", "endpoint", "url",
            )
            for field in fields:
                value = config.get(field, "") if isinstance(config, dict) else getattr(config, field, "")
                if value:
                    parts.append(str(value))
        keywords = [
            item.strip().lower()
            for item in re.split(r"[,，;；\n]+", str(getattr(self, "deepseek_peak_match_keywords", "") or ""))
            if item.strip()
        ] or ["deepseek", "深度求索"]
        haystack = " ".join(parts).lower()
        return any(keyword in haystack for keyword in keywords)

    def _apply_deepseek_peak_replacement(
        self,
        provider_id: str,
        *,
        now: datetime | None = None,
        target: str = "plugin",
    ) -> str:
        original = str(provider_id or "").strip()
        if not scope_allows(getattr(self, "model_replacement_scope", "plugin"), target):
            return original
        status = self._deepseek_peak_status(now)
        replacement = str(status.get("replacement_provider_id") or "").strip()
        if not status.get("active") or not original or not replacement or replacement == original:
            return original
        if not self._provider_matches_deepseek(original):
            return original
        log_key = f"{local_day if (local_day := status.get('current_time', '')[:10]) else ''}|{original}|{replacement}"
        if getattr(self, "_deepseek_peak_last_log_key", "") != log_key:
            self._deepseek_peak_last_log_key = log_key
            logger.info("DeepSeek 高价时段临时路由: %s -> %s (%s)", original, replacement, status.get("current_time"))
        return replacement

    def _task_provider(
        self,
        *provider_ids: str | None,
        allow_replacement: bool = True,
    ) -> str:
        for provider_id in provider_ids:
            value = str(provider_id or "").strip()
            if value:
                if not allow_replacement:
                    return value
                routed = value
                if scope_allows(getattr(self, "model_replacement_scope", "plugin"), "plugin"):
                    sources = CURRENT_MODEL_REPLACEMENT_SOURCES.get(())
                    rules = getattr(self, "model_replacement_rules", None)
                    if sources and isinstance(rules, list):
                        match = find_route(rules, sources)
                        if match is not None:
                            candidate = str(match.rule.provider_id or "").strip()
                            getter = getattr(getattr(self, "context", None), "get_provider_by_id", None)
                            if candidate and callable(getter):
                                try:
                                    if getter(candidate) is not None:
                                        routed = candidate
                                except Exception:
                                    pass
                return self._apply_deepseek_peak_replacement(routed, target="plugin")
        return ""

    def _parse_plan_items(self, raw_text: str) -> list[dict[str, str]]:
        payload = self._extract_json_payload(raw_text)
        if payload is None:
            return []
        if isinstance(payload, dict):
            raw_items = (
                payload.get("schedule")
                or payload.get("items")
                or payload.get("tasks")
                or payload.get("events")
                or payload.get("plan")
                or []
            )
        elif isinstance(payload, list):
            raw_items = payload
        else:
            raw_items = []

        items: list[dict[str, str]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            raw_time = item.get("time") or item.get("start") or item.get("start_time") or item.get("begin_time") or item.get("开始时间")
            item_time, range_end = self._normalize_plan_clock_range(raw_time)
            if self._parse_hhmm_to_minutes(item_time) is None:
                continue
            raw_activity = _single_line(
                item.get("activity")
                or item.get("title")
                or item.get("task")
                or item.get("event")
                or item.get("内容"),
                120,
            )
            activity = self._align_plan_text_with_skill_bounds(
                self._sanitize_daily_plan_social_fact_text(
                    self._soften_destructive_daily_plan_text(raw_activity),
                    field="activity",
                )
            )
            if not activity:
                continue
            mood = self._align_plan_text_with_skill_bounds(
                self._soften_destructive_daily_plan_text(_single_line(item.get("mood"), 30))
            )
            raw_message_seed = _single_line(item.get("message_seed"), 140)
            message_seed = self._align_plan_text_with_skill_bounds(
                self._sanitize_empty_daily_plan_message_seed(
                    self._sanitize_daily_plan_social_fact_text(
                        self._soften_destructive_daily_plan_text(
                            self._deemphasize_state_report_preamble(
                                raw_message_seed,
                                reason="background_schedule",
                            )
                        ),
                        field="message_seed",
                    )
                )
            )
            items.append(
                {
                    "time": item_time,
                    "end": self._normalize_plan_clock(
                        item.get("end")
                        or item.get("end_time")
                        or item.get("finish_time")
                        or item.get("until")
                        or item.get("结束时间")
                        or range_end
                    ),
                    "activity": activity,
                    "mood": mood,
                    "message_seed": message_seed,
                    "basis": self._normalize_schedule_basis(item.get("basis"), default=["inspiration"]),
                    "confidence": min(1.0, _safe_float(item.get("confidence"), 0.7)),
                }
            )
        items = sorted(items, key=lambda item: self._parse_hhmm_to_minutes(item["time"]) or 0)
        items = items[: _safe_int(runtime_persona_setting(self, "daily_plan_item_count", 10), 10, 1)]
        self._normalize_plan_item_intervals(items)
        # Pass every generated item through the C3 write gate.  LLM fields such
        # as status, source_refs, authority and evidence are never trusted;
        # canonical axes are retained so downstream views cannot silently lose
        # the distinction between a plan and an observation.
        today = _today_key()
        for index, item in enumerate(items):
            try:
                canonical = normalize_plan_item(
                    {**item, "title": item.get("activity"), "date": today, "subject_actor_id": "bot_self", "actor_type": "bot"},
                    plan_id=f"{today}:{index}",
                    now=self._environment_now(),
                )
            except Exception:
                continue
            item.update(canonical)
            item["activity"] = _single_line(item.get("activity") or item.get("title"), 120)
            item["date"] = today
        return items

    def _normalize_plan_clock_range(self, value: Any) -> tuple[str, str]:
        """Normalize common model time forms and extract an optional range end."""

        text = _single_line(value, 32).strip()
        if not text:
            return "", ""
        matches = re.findall(r"(?<!\d)(\d{1,2})\s*[:：点时]\s*(\d{1,2})?", text)
        clocks: list[str] = []
        for hour_text, minute_text in matches[:2]:
            hour = int(hour_text)
            minute = int(minute_text or 0)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                clocks.append(f"{hour:02d}:{minute:02d}")
        if not clocks:
            return "", ""
        return clocks[0], clocks[1] if len(clocks) > 1 else ""

    def _normalize_plan_clock(self, value: Any) -> str:
        start, _end = self._normalize_plan_clock_range(value)
        return start

    def _skill_levels_for_plan_bounds(self) -> dict[str, int]:
        if not runtime_persona_setting(self, "enable_skill_growth_simulation", True) or not runtime_persona_setting(self, "enable_skill_growth_schedule_influence", True):
            return {}
        state = self.data.get("skill_growth") if isinstance(self.data.get("skill_growth"), dict) else {}
        skills = state.get("skills") if isinstance(state.get("skills"), dict) else {}
        levels = {}
        for raw in skills.values():
            if not isinstance(raw, dict):
                continue
            if raw.get("hidden"):
                continue
            level = _safe_int(raw.get("level"), 1, 1)
            for name in self._skill_growth_terms(raw, include_keywords=False):
                if name:
                    levels[name] = max(1, min(6, level))
        return dict(list(levels.items())[:18])

    @staticmethod
    def _skill_task_noun_for_text(text: str) -> str:
        if any(token in text for token in ("题", "作业", "试卷", "考试", "验算", "算")):
            return "题目"
        if any(token in text for token in ("写", "小说", "文章", "日记", "作文", "创作", "草稿")):
            return "创作"
        if any(token in text for token in ("画", "绘", "素描", "线稿", "上色")):
            return "练习"
        if any(token in text for token in ("做饭", "料理", "烹饪", "菜", "厨房")):
            return "料理"
        if any(token in text for token in ("战斗", "训练", "剑", "魔法", "探索", "委托")):
            return "训练"
        return "任务"

    @staticmethod
    def _skill_bound_replacement(name: str, level: int, *, advanced: bool = False, task_noun: str = "任务") -> str:
        hard = f"{'高阶' if advanced else '常规'}{task_noun}"
        if level <= 1:
            return f"被{name}的基础部分绊住,需要从头摸一遍"
        if level == 2:
            return f"在{name}基础{task_noun}上慢慢摸索,照着例子才推进下去"
        if level == 3:
            return f"{name}常规{task_noun}能自己推进,只是效率不高,中途查了两处"
        if level == 4:
            return f"在{name}{hard}上停了一会儿,换个思路后理顺了"
        if level == 5:
            return f"把{name}{hard}顺手理清,还顺便检查了一遍更稳的做法"
        return f"把{name}{hard}拆开重组了一遍,顺手想出一个更漂亮的做法"

    def _align_plan_text_with_skill_bounds(self, text: str) -> str:
        normalized = _single_line(text, 160)
        if not normalized:
            return ""
        levels = self._skill_levels_for_plan_bounds()
        if not levels:
            return normalized
        difficulty_tokens = (
            "难住",
            "卡住",
            "卡死",
            "不会做",
            "做不出来",
            "完全不会",
            "看不懂",
            "想不出来",
            "算不出来",
            "写不出来",
        )
        if not any(token in normalized for token in difficulty_tokens):
            return normalized
        advanced_tokens = ("竞赛", "压轴", "高阶", "陌生", "超纲", "很偏", "少见", "难题", "综合题", "复杂")
        advanced = any(token in normalized for token in advanced_tokens)
        task_noun = self._skill_task_noun_for_text(normalized)
        for name, level in levels.items():
            if name not in normalized:
                continue
            replacement = self._skill_bound_replacement(name, level, advanced=advanced, task_noun=task_noun)
            replacements = [
                f"被{name}题难住",
                f"被{name}难住",
                f"被{name}卡住",
                f"{name}题卡住",
                f"{name}不会做",
                f"{name}做不出来",
                f"{name}看不懂",
                f"{name}算不出来",
                f"{name}写不出来",
                f"{name}完全不会",
            ]
            for old in replacements:
                normalized = normalized.replace(old, replacement)
        return _single_line(normalized, 160)

    @staticmethod
    def _soften_destructive_daily_plan_text(text: str) -> str:
        softened = _single_line(text, 160)
        if not softened:
            return ""
        replacements = [
            (r"想[^，。,；;]{0,18}(砸|摔|打人|揍人|报复|毁掉|弄坏)[^，。,；;]{0,18}", "烦得想先躲开一会儿"),
            (r"(把|将)[^，。,；;]{0,14}(砸|摔|扔)[^，。,；;]{0,14}(地上|墙上|门上|出去|烂|碎)[^，。,；;]{0,8}", "把手边的东西往里推了推"),
            (r"(砸|摔)(东西|门|墙|书|杯子|手机|笔)[^，。,；;]{0,8}", "把东西先放远一点"),
            (r"(骂人|想骂|吼人|想吼)[^，。,；;]{0,10}", "把话咽回去"),
        ]
        for pattern, replacement in replacements:
            softened = re.sub(pattern, replacement, softened)
        softened = re.sub(r"(烦躁|暴躁|恼火)到?有点?攻击性", "烦躁得有点想躲开", softened)
        softened = softened.replace("想砸东西的烦躁", "有点烦,但努力收着")
        softened = softened.replace("想摔东西的烦躁", "有点烦,但努力收着")
        return _single_line(softened, 160)

    @staticmethod
    def _strip_json_payload_comments(text: str) -> str:
        result: list[str] = []
        index = 0
        in_string = False
        quote_char = ""
        escaped = False
        while index < len(text):
            char = text[index]
            nxt = text[index + 1] if index + 1 < len(text) else ""
            if in_string:
                result.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote_char:
                    in_string = False
                    quote_char = ""
                index += 1
                continue
            if char in {'"', "'"}:
                in_string = True
                quote_char = char
                result.append(char)
                index += 1
                continue
            if char == "/" and nxt == "/":
                index += 2
                while index < len(text) and text[index] not in "\r\n":
                    index += 1
                continue
            if char == "/" and nxt == "*":
                index += 2
                while index + 1 < len(text) and not (text[index] == "*" and text[index + 1] == "/"):
                    index += 1
                index = min(len(text), index + 2)
                continue
            result.append(char)
            index += 1
        return "".join(result)

    def _repair_json_payload(self, text: str) -> str:
        repaired = str(text or "").strip()
        repaired = repaired.replace("\ufeff", "")
        repaired = repaired.replace("“", '"').replace("”", '"')
        repaired = repaired.replace("‘", "'").replace("’", "'")
        repaired = self._strip_json_payload_comments(repaired)
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        return repaired.strip()

    def _extract_json_payload(self, raw_text: str) -> Any:
        text = str(raw_text or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        candidates = [text]
        object_start, object_end = text.find("{"), text.rfind("}")
        if object_start >= 0 and object_end > object_start:
            candidates.append(text[object_start : object_end + 1])
        array_start, array_end = text.find("["), text.rfind("]")
        if array_start >= 0 and array_end > array_start:
            candidates.append(text[array_start : array_end + 1])
        seen_candidates: set[str] = set()
        for candidate in candidates:
            if candidate in seen_candidates:
                continue
            seen_candidates.add(candidate)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                repaired = self._repair_json_payload(candidate)
                if repaired and repaired not in seen_candidates:
                    seen_candidates.add(repaired)
                    try:
                        return json.loads(repaired)
                    except json.JSONDecodeError:
                        pass
                    try:
                        parsed = ast.literal_eval(repaired)
                    except (SyntaxError, ValueError):
                        parsed = None
                    if isinstance(parsed, (dict, list)):
                        return parsed
        return None

    def _get_current_plan_item(self, plan: dict[str, Any]) -> dict[str, str] | None:
        if not self._is_plan_date_active(plan.get("date")):
            return None
        items = plan.get("items")
        if not isinstance(items, list):
            return None
        current_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if current_minutes is None:
            return None
        selected = None
        selected_start: int | None = None
        starts = self._normalized_plan_item_starts(items)
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            item_minutes = starts[index] if index < len(starts) else None
            if item_minutes is None:
                continue
            next_start = next((value for value in starts[index + 1 :] if value is not None), None)
            item_end = self._plan_item_end_minutes(item_minutes, item, next_start=next_start)
            if item_minutes <= current_minutes < item_end:
                selected = item
                selected_start = item_minutes
                break
        if isinstance(selected, dict):
            # A clock window is not execution evidence.  Keep confirmed
            # schedule commitments available through the future/schedule
            # policy, but expose current plan text only when a compatible
            # observation or a short-lived resolver commit exists.
            policy_allows_current = True
            policy_getter = getattr(self, "_agenda_disclosure_view", None)
            if callable(policy_getter):
                policy_allows_current = False
                try:
                    view = policy_getter("current_fact", now=self._environment_now(), max_entries=128)
                    values = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
                    selected_key = _single_line(selected.get("plan_id"), 120)
                    selected_pair = (
                        _single_line(selected.get("time"), 12),
                        _single_line(selected.get("activity") or selected.get("title"), 120),
                    )
                    for value in values if isinstance(values, list) else []:
                        if not isinstance(value, dict):
                            continue
                        value_key = _single_line(value.get("plan_id") or value.get("entry_id"), 120)
                        value_pair = (
                            _single_line(value.get("time"), 12),
                            _single_line(value.get("title") or value.get("activity"), 120),
                        )
                        if (selected_key and selected_key == value_key) or (selected_pair == value_pair and all(selected_pair)):
                            policy_allows_current = True
                            break
                except Exception:
                    policy_allows_current = False
            evidence_kind = _single_line(selected.get("evidence_kind"), 48).lower()
            fact_eligibility = _single_line(selected.get("fact_eligibility"), 48).lower()
            status = _single_line(selected.get("status"), 32).lower()
            # 睡眠/休息段是 Bot 的内部状态模拟，不是需要外部执行证据的日程动作：
            # 计划里的“睡觉”只表达“Bot 此刻该休息”的内部状态，不主张任何已发生
            # 的外部事实。若不在此豁免，上游 C3 证据认证门槛会让普通计划项
            # （evidence_kind/fact_eligibility 均为 none）在这里返回 None，
            # 睡眠状态机（_refresh_sleep_runtime_state）拿不到当前睡眠项，
            # 睡眠相位就会永远停在 awake。
            if not self._is_sleepy_plan_item(selected) and (
                not policy_allows_current
                or not (
                    evidence_kind in {"interaction", "tool_action", "external_record"}
                    and fact_eligibility in {"current_observed", "history_observed", ""}
                    and status in {"active", "completed", "partially_completed", ""}
                )
            ):
                runtime_getter = getattr(self, "_agenda_runtime_scene", None)
                if callable(runtime_getter):
                    try:
                        runtime = runtime_getter(now=self._environment_now())
                    except Exception:
                        runtime = None
                    if isinstance(runtime, dict):
                        return {
                            "time": self._minutes_to_hhmm(current_minutes),
                            "end": _single_line(runtime.get("valid_until"), 40),
                            "activity": _single_line(runtime.get("state"), 120),
                            "mood": "当前状态",
                            "message_seed": "",
                            "subject_actor_id": "bot_self",
                            "evidence_kind": "self_state_commit",
                            "fact_eligibility": "current_internal",
                            "materialization_state": "active",
                            "status": "active",
                        }
                return None
            plan_date = str(plan.get("date") or "").strip()
            if (
                plan_date
                and plan_date != _today_key()
                and current_minutes >= 24 * 60
                and selected_start is not None
                and self._is_sleepy_plan_item(selected)
            ):
                elapsed = max(0, current_minutes - selected_start)
                carried = dict(selected)
                carried["time"] = self._minutes_to_hhmm(current_minutes)
                runtime = {}
                state = self.data.get("daily_state", {})
                if isinstance(state, dict) and isinstance(state.get("sleep_runtime"), dict):
                    runtime = state.get("sleep_runtime", {})
                phase = str(runtime.get("phase") or "")
                if phase == "woken":
                    carried["activity"] = "夜里被消息轻轻叫醒，还半梦半醒地留着一点睡意。"
                    carried["mood"] = "刚醒，迷糊"
                    carried["message_seed"] = "像刚从睡里被叫醒；如果用户不继续聊，会慢慢把手机放下睡回去。"
                elif phase == "sleeping_again":
                    carried["activity"] = "刚才被叫醒过一下，现在又慢慢睡回去了。"
                    carried["mood"] = "重新睡着，安静"
                    carried["message_seed"] = "睡意重新接上了；再被唤起时会有一点断续的迷糊感。"
                elif elapsed >= 45:
                    carried["activity"] = "夜里还在睡着，睡意早已沉下去，睡眠正在安静延续。"
                    carried["mood"] = "睡着，安静"
                    carried["message_seed"] = "还在睡着。如果这时候被叫醒，会有点迷糊；没人继续打扰就会继续睡下去。"
                else:
                    carried["activity"] = "刚从前一晚的睡前片段进入休息，正在慢慢安静下来。"
                    carried["mood"] = _single_line(selected.get("mood"), 40) or "安静"
                    carried["message_seed"] = "正在收声准备睡，语气会更轻。"
                return carried
            return selected
        return None

    def _get_clock_plan_item_for_display(self, plan: dict[str, Any]) -> dict[str, Any] | None:
        """Pick the scheduled row covering now for UI only, without claiming it happened."""

        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return None
        items = plan.get("items")
        if not isinstance(items, list):
            return None
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return None
        starts = self._normalized_plan_item_starts(items)
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            start = starts[index] if index < len(starts) else None
            if start is None:
                continue
            next_start = next((value for value in starts[index + 1 :] if value is not None), None)
            end = self._plan_item_end_minutes(start, item, next_start=next_start)
            if start <= now_minutes < end:
                return item
        return None

    def _format_daily_plan(self, plan: dict[str, Any]) -> str:
        if not plan or not plan.get("items"):
            return "今天还没有日程。"
        source = "模型生成" if plan.get("source") == "llm" else "备用日程"
        lines = [
            f"{runtime_persona_setting(self, 'bot_name', '小星')} 今天的日程（{plan.get('date', _today_key())},{source}）："
        ]
        state = self.data.get("daily_state", {})
        if isinstance(state, dict) and state.get("date") == plan.get("date"):
            lines.append(
                f"状态：能量 {state.get('energy', 70)}/100｜情绪偏{state.get('mood_bias', '平稳')}｜{state.get('sleep', '睡眠平稳')}"
            )
        status_labels = {
            "planned": "计划中",
            "active": "进行中",
            "completed": "已完成",
            "changed": "已变更",
            "cancelled": "已取消",
            "deferred": "已顺延",
            "unknown": "未核实",
            "overridden": "已被新安排覆盖",
        }
        for index, item in enumerate(plan.get("items", [])):
            if not isinstance(item, dict):
                continue
            mood = f"｜{item.get('mood')}" if item.get("mood") else ""
            window = f"{item.get('time')}-{item.get('end')}" if item.get("end") else str(item.get("time") or "")
            lifecycle = self._plan_item_display_status(plan, item, index)
            status = status_labels.get(lifecycle, "计划中")
            lines.append(f"{window}｜{status} {item.get('activity')}{mood}")
        archive_warning = _memory_archive_warning(plan)
        if archive_warning:
            lines.append(archive_warning)
        return "\n".join(lines)

    @staticmethod
    def _detail_event_text(item: dict[str, Any], limit: int = 160) -> str:
        if not isinstance(item, dict):
            return ""
        for key in (
            "event",
            "content",
            "detail",
            "description",
            "text",
            "narrative",
            "body",
            "细化",
            "细化内容",
            "细化叙述",
            "事件",
            "主要事件",
        ):
            text = _single_line(item.get(key), limit)
            if text:
                return text
        return ""

    def _format_current_detail_view(self) -> str:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not plan.get("items"):
            return "今天还没有日程，所以也没有可看的当前细化。"
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            enhanced = {}
        segment = self._current_detail_segment_for_update() or self._pick_detail_segment(plan, enhanced)
        if not segment:
            return "当前还没有可用的细化结果。先让今天的日程段完成细化，或者手动执行一次“陪伴 重置细化”。"
        key = str(segment.get("key") or "")
        snapshot = enhanced.get(key) if key else None
        if not isinstance(snapshot, dict):
            return "当前时间段还没有落地的细化内容。可以先执行一次“陪伴 重置细化”。"
        snapshot = deepcopy(snapshot)
        self._sanitize_detail_enhanced_segments_inplace({"current": snapshot})

        item = segment.get("item") if isinstance(segment, dict) else {}
        start_text = self._minutes_to_hhmm(_safe_int(segment.get("start"), 0))
        end_text = self._minutes_to_hhmm(_safe_int(segment.get("end"), 0))
        lines = [
            f"当前细化时段：{start_text}-{end_text}",
            f"对应日程：{_single_line((item or {}).get('activity'), 120)}",
        ]
        mood = _single_line((item or {}).get("mood"), 24)
        if mood:
            lines.append(f"日程情绪：{mood}")

        state_variables = snapshot.get("state_variables", [])
        if isinstance(state_variables, list) and state_variables:
            lines.append("状态变量：")
            for variable in state_variables[:8]:
                if not isinstance(variable, dict):
                    continue
                name = _single_line(variable.get("name"), 32)
                value = _single_line(variable.get("value"), 60)
                note = _single_line(variable.get("note"), 80)
                if name and value:
                    lines.append(f"- {name}: {value}" + (f"（{note}）" if note else ""))

        presence = snapshot.get("presence_status")
        if isinstance(presence, dict):
            mode = _single_line(presence.get("mode"), 24)
            reason = _single_line(presence.get("reason"), 80)
            if mode and mode != "unchanged":
                lines.append("QQ状态表现：")
                lines.append(f"- {mode}" + (f"｜{reason}" if reason else ""))

        interaction_updates = snapshot.get("interaction_updates", [])
        if isinstance(interaction_updates, list) and interaction_updates:
            update_lines: list[str] = []
            for update in interaction_updates[-4:]:
                if not isinstance(update, dict):
                    continue
                if _single_line(update.get("source_role"), 20) != "owner":
                    continue
                at = _single_line(update.get("at"), 8)
                user_text = _single_line(update.get("user_text"), 80)
                intensity = _single_line(update.get("intensity"), 12)
                reaction = _single_line(update.get("reaction"), 120)
                state_updates = update.get("state_updates")
                state_text = ""
                if isinstance(state_updates, list) and state_updates:
                    state_text = "；".join(_single_line(item, 60) for item in state_updates if _single_line(item, 60))
                if reaction or user_text:
                    prefix = f"- {at} " if at else "- "
                    parts = [
                        f"用户：{user_text}" if user_text else "",
                        f"强度：{intensity}" if intensity else "",
                        reaction,
                        state_text,
                    ]
                    update_lines.append(prefix + "｜".join(part for part in parts if part))
            if update_lines:
                lines.append("用户介入后的局部更新：")
                lines.extend(update_lines)

        today_events = snapshot.get("today_events", [])
        scoped_today_events = self._filter_snapshot_items_to_segment(today_events, segment)
        if scoped_today_events:
            lines.append("细化内容：")
            for detail_event in scoped_today_events[:8]:
                if not isinstance(detail_event, dict):
                    continue
                window = _single_line(detail_event.get("window"), 24)
                event_text = self._detail_event_text(detail_event, 160)
                mood_text = _single_line(detail_event.get("mood"), 24)
                if event_text:
                    tail = f"｜{mood_text}" if mood_text else ""
                    lines.append(f"- {window}｜{event_text}{tail}")
        else:
            summary = _single_line(snapshot.get("summary"), 160)
            if summary and summary not in {"这一段按原日程慢慢推进。", "这一段按原日程慢慢推进"}:
                lines.append(f"细化内容：{summary}")
            else:
                lines.append("细化内容：当前没有生成出可展示的细化正文。")

        proactive_events = snapshot.get("proactive_events", [])
        if isinstance(proactive_events, list) and proactive_events:
            scoped_proactive_events = self._filter_snapshot_items_to_segment(proactive_events, segment)
            if scoped_proactive_events:
                lines.append("这一段的主动契机：")
            for proactive_event in scoped_proactive_events[:10]:
                if not isinstance(proactive_event, dict):
                    continue
                window = _single_line(proactive_event.get("window"), 24)
                reason = _single_line(proactive_event.get("reason"), 24)
                action = _single_line(proactive_event.get("action"), 24) or "message"
                topic = _single_line(proactive_event.get("topic"), 48)
                motive = _single_line(proactive_event.get("motive"), 80)
                why = _single_line(proactive_event.get("why"), 100)
                scene = _single_line(proactive_event.get("scene"), 60)
                tone = _single_line(proactive_event.get("tone"), 24)
                impulse = _single_line(proactive_event.get("impulse"), 80)
                lines.append(f"- {window}｜{reason}｜{action}｜{topic or motive or '（无话题）'}")
                if why:
                    lines.append(f"  why：{why}")
                if motive:
                    lines.append(f"  motive：{motive}")
                meta_bits = []
                if scene:
                    meta_bits.append(f"scene={scene}")
                if tone:
                    meta_bits.append(f"tone={tone}")
                if impulse:
                    meta_bits.append(f"impulse={impulse}")
                if meta_bits:
                    lines.append("  " + "｜".join(meta_bits))
                chain = proactive_event.get("chain")
                if isinstance(chain, list) and chain:
                    lines.append("  chain：")
                    for step in chain[:4]:
                        if not isinstance(step, dict):
                            continue
                        kind = _single_line(step.get("kind"), 24)
                        after_minutes = _safe_int(step.get("after_minutes"), 0, 0)
                        step_reason = _single_line(step.get("reason"), 24)
                        step_topic = _single_line(step.get("topic"), 48)
                        step_motive = _single_line(step.get("motive"), 80)
                        step_tone = _single_line(step.get("tone"), 24)
                        extra = []
                        if after_minutes > 0:
                            extra.append(f"{after_minutes} 分钟后")
                        if step_reason:
                            extra.append(step_reason)
                        if step_topic:
                            extra.append(step_topic)
                        if step_tone:
                            extra.append(f"tone={step_tone}")
                        if step_motive:
                            extra.append(f"motive={step_motive}")
                        lines.append(f"    - {kind}" + (f"｜{'｜'.join(extra)}" if extra else ""))

        if len(lines) <= 4:
            lines.append("这段目前还比较空，说明细化结果里还没长出太多东西。")
        return "\n".join(lines)

    def _filter_snapshot_items_to_segment(
        self,
        raw_items: Any,
        segment: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list) or not isinstance(segment, dict):
            return []
        start = _safe_int(segment.get("start"), 0)
        end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
        if end <= start:
            end += 24 * 60
        kept: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            item_start, item_end = self._parse_window_minutes(str(item.get("window") or ""))
            if item_start is None or item_end is None:
                continue
            candidates = [(item_start, item_end)]
            if item_end < item_start:
                candidates = [(item_start, item_end + 24 * 60)]
            if item_start < start and end > 24 * 60:
                candidates.append((item_start + 24 * 60, item_end + 24 * 60))
            if any(candidate_start >= start and candidate_end <= end for candidate_start, candidate_end in candidates):
                kept.append(item)
        return kept

    def _format_current_detail_brief(self) -> str:
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not plan.get("items"):
            return "今天还没有日程，所以没有可细化的时间段。"
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            enhanced = {}
        segment = self._current_detail_segment_for_update() or self._pick_detail_segment(plan, enhanced)
        if not segment:
            return "当前还没有可用的细化结果。"
        key = str(segment.get("key") or "")
        snapshot = enhanced.get(key) if key else None
        if not isinstance(snapshot, dict):
            return "当前时间段还没有落地的细化内容。"
        snapshot = deepcopy(snapshot)
        self._sanitize_detail_enhanced_segments_inplace({"current": snapshot})

        item = segment.get("item") if isinstance(segment, dict) else {}
        start_text = self._minutes_to_hhmm(_safe_int(segment.get("start"), 0))
        end_text = self._minutes_to_hhmm(_safe_int(segment.get("end"), 0))
        lines = [
            f"{start_text}-{end_text}｜{_single_line((item or {}).get('activity'), 80)}",
        ]
        summary = _single_line(snapshot.get("summary"), 140)
        if summary:
            lines.append(summary)

        today_events = snapshot.get("today_events", [])
        if isinstance(today_events, list) and today_events:
            for detail_event in today_events[:3]:
                if not isinstance(detail_event, dict):
                    continue
                window = _single_line(detail_event.get("window"), 18)
                event_text = self._detail_event_text(detail_event, 120)
                mood_text = _single_line(detail_event.get("mood"), 20)
                if event_text:
                    lines.append(f"- {window} {event_text}" + (f"｜{mood_text}" if mood_text else ""))

        interaction_updates = snapshot.get("interaction_updates", [])
        if isinstance(interaction_updates, list) and interaction_updates:
            latest = next(
                (
                    item
                    for item in reversed(interaction_updates)
                    if isinstance(item, dict) and _single_line(item.get("source_role"), 20) == "owner"
                ),
                None,
            )
            if isinstance(latest, dict):
                user_text = _single_line(latest.get("user_text"), 60)
                reaction = _single_line(latest.get("reaction"), 100)
                if reaction or user_text:
                    lines.append("局部更新：" + "｜".join(part for part in (f"用户：{user_text}" if user_text else "", reaction) if part))

        return "\n".join(lines)

    def _debug_tick_skip(self, user_id: str, reason: str, *, prefix: str = "跳过") -> None:
        reason_text = _single_line(reason, 120) or "未知原因"
        should_record = prefix != "跳过" or reason_text not in {"未到候选主动时间", "已安排下一次候选主动时间"}
        if should_record:
            try:
                current = self._get_user(str(user_id or ""))
                current["last_proactive_skip_at"] = _now_ts()
                current["last_proactive_skip_reason"] = reason_text
                current["last_proactive_skip_prefix"] = _single_line(prefix, 20)
            except Exception:
                pass
        if prefix == "跳过":
            return
        key = f"{prefix}:{user_id}"
        now = _now_ts()
        cache = getattr(self, "_tick_skip_log_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._tick_skip_log_cache = cache
        last_ts = _safe_float(cache.get(key), 0)
        if now - last_ts < 1800:
            return
        cache[key] = now
        if len(cache) > 300:
            cutoff = now - 3600
            for old_key, ts in list(cache.items()):
                if _safe_float(ts, 0) < cutoff:
                    cache.pop(old_key, None)
        logger.debug(f"{prefix} {user_id}: {reason_text}")

    def _sync_live_user_proactive_schedule(self, user_id: str, source: dict[str, Any]) -> bool:
        """Mirror proactive-plan mutations from a tick snapshot back to the live user record."""
        if not isinstance(source, dict):
            return False
        raw_user_id = str(user_id or source.get("user_id") or source.get("id") or "").strip()
        if not raw_user_id:
            return False
        try:
            current = self._get_user(raw_user_id)
        except Exception:
            return False
        if not isinstance(current, dict):
            return False
        keys = (
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
            "planned_proactive_route_preflight_action",
            "planned_proactive_route_preflight_note",
            "planned_proactive_motive",
            "planned_proactive_topic",
            "planned_proactive_impulse_id",
            "planned_proactive_window_start_at",
            "planned_proactive_best_until_at",
            "planned_proactive_expire_at",
            "planned_proactive_origin_at",
            "planned_proactive_origin_key",
            "planned_proactive_freshness",
            "planned_proactive_delivery_state",
            "planned_proactive_semantic_kind",
            "planned_proactive_anchor_type",
            "planned_proactive_semantic_score",
            "planned_proactive_semantic_note",
            "planned_proactive_model_judge_signature",
            "planned_proactive_model_judge_result",
            "planned_proactive_model_judge_at",
            "planned_event_chain",
            "planned_opener_mode",
            "planned_followup_kind",
            "planned_proactive_quota_exempt",
            "planned_proactive_window_timezone",
            "planned_birthday_event_context",
            "planned_special_day_context",
            "insomnia_night_context",
            "planned_candidate_id",
            "planned_proactive_trigger_message_id",
            "planned_proactive_trigger_umo",
            "planned_proactive_trigger_ts",
            "planned_proactive_trigger_inbound_count",
            "planned_proactive_trigger_created_at",
            "proactive_impulses",
            "recent_proactive_hesitations",
            "last_proactive_hesitation_at",
            "last_proactive_hesitation_note",
        )
        changed = False
        for key_name in keys:
            if key_name not in source:
                continue
            value = deepcopy(source.get(key_name))
            if current.get(key_name) != value:
                current[key_name] = value
                changed = True
        return changed

    def _recent_chat_proactive_guard_reason(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        planned_reason: str = "",
        planned_source: str = "",
        due_timer_active: bool = False,
        is_troubleshooting: bool = False,
    ) -> str:
        """Block ordinary proactive messages when the private chat has just moved."""
        if not isinstance(user, dict):
            return ""
        source = normalize_legacy_tag_text(planned_source or user.get("planned_proactive_source"))
        if is_troubleshooting or due_timer_active or source == "timer":
            return ""
        check_now = _now_ts() if now is None else now
        reason = normalize_legacy_tag_text(planned_reason or user.get("planned_proactive_reason"))
        idle_minutes = (
            self._effective_user_greeting_idle_minutes(user)
            if self._is_greeting_reason(reason)
            else self._effective_user_idle_minutes(user)
        )
        idle_seconds = max(0, idle_minutes) * 60
        if idle_seconds <= 0:
            return ""
        recent_at = self._latest_private_user_activity_ts(user)
        if recent_at <= 0:
            return ""
        remaining = recent_at + idle_seconds - check_now
        if remaining <= 0:
            return ""
        minutes = max(1, int(math.ceil(remaining / 60)))
        return f"刚聊完，普通主动延后（还需安静约 {minutes} 分钟）"

    def _defer_proactive_for_recent_chat(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        note: str = "",
    ) -> None:
        if not isinstance(user, dict):
            return
        check_now = _now_ts() if now is None else now
        reason = normalize_legacy_tag_text(user.get("planned_proactive_reason"))
        idle_minutes = (
            self._effective_user_greeting_idle_minutes(user)
            if self._is_greeting_reason(reason)
            else self._effective_user_idle_minutes(user)
        )
        recent_at = self._latest_private_user_activity_ts(user)
        quiet_until = recent_at + max(0, idle_minutes) * 60 if recent_at > 0 else check_now + 10 * 60
        if self._is_sticky_greeting_reason(reason) and self._reschedule_greeting_within_window(user, reason, now=check_now):
            pass
        else:
            delay_minutes = (
                max(5.0, (quiet_until - check_now) / 60 + 2.0),
                max(8.0, (quiet_until - check_now) / 60 + 8.0),
            )
            replacer = getattr(self, "_defer_or_replace_planned_impulse", None)
            replaced = False
            handled_by_replacer = False
            if callable(replacer):
                try:
                    handled_by_replacer = True
                    replaced = bool(
                        replacer(
                            user,
                            now=check_now,
                            note=note or "刚聊完，普通主动延后",
                            delay_minutes=delay_minutes,
                            block_current=False,
                        )
                    )
                except Exception as exc:
                    logger.debug("刚聊完主动换念头失败,回退延后: %s", _single_line(exc, 120))
                    replaced = False
                    handled_by_replacer = False
            if not replaced:
                if handled_by_replacer and _safe_float(user.get("next_proactive_at"), 0) > check_now:
                    pass
                elif handled_by_replacer and not _single_line(normalize_legacy_tag_text(user.get("planned_proactive_reason")), 40):
                    self._schedule_next_proactive(user, now=check_now, delay_hours=(max(0.2, delay_minutes[0] / 60), max(0.35, delay_minutes[1] / 60)))
                else:
                    user["next_proactive_at"] = max(check_now + 5 * 60, quiet_until + random.uniform(2 * 60, 8 * 60))
            if normalize_legacy_tag_text(user.get("planned_proactive_source")) == "simulation":
                sim = user.get("simulation_mode")
                events = sim.get("events") if isinstance(sim, dict) else None
                if isinstance(events, list) and events and isinstance(events[0], dict):
                    events[0]["_scheduled_ts"] = user["next_proactive_at"]
            if handled_by_replacer:
                return
        self._mark_planned_candidate_status(user, "deferred", note or "刚聊完，普通主动延后")

    def _is_troubleshooting_proactive_plan(self, user: dict[str, Any]) -> bool:
        return isinstance(user, dict) and normalize_legacy_tag_text(user.get("planned_proactive_source")) == "troubleshooting"

    def _append_troubleshooting_proactive_step(
        self,
        user: dict[str, Any],
        name: str,
        status: str,
        detail: str = "",
    ) -> list[dict[str, str]]:
        steps = user.setdefault("troubleshooting_proactive_steps", [])
        if not isinstance(steps, list):
            steps = []
            user["troubleshooting_proactive_steps"] = steps
        steps.append(
            {
                "name": _single_line(name, 40),
                "status": _single_line(status, 16) or "info",
                "detail": _single_line(detail, 180),
            }
        )
        del steps[:-12]
        return steps

    def _record_troubleshooting_proactive_result(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        ok: bool,
        detail: str,
        error: str = "",
        text: str = "",
        original_text: str = "",
        final_text: str = "",
        action: str = "message",
        reason: str = "check_in",
        extra_count: int = 0,
        diagnostic_detail: str = "",
        pending: bool = False,
        outcome_type: str = "",
    ) -> None:
        raw = self.data.setdefault("troubleshooting_test_results", {})
        if not isinstance(raw, dict):
            raw = {}
            self.data["troubleshooting_test_results"] = raw
        started = _safe_float(user.get("troubleshooting_proactive_started_at"), 0)
        now = _now_ts()
        diagnostic_sanitizer = getattr(self, "_proactive_audit_safe_note", None)
        safe_diagnostic_detail = (
            diagnostic_sanitizer(diagnostic_detail, limit=2400)
            if diagnostic_detail and callable(diagnostic_sanitizer)
            else _single_line(diagnostic_detail, 2400)
        )
        outcome = _single_line(outcome_type, 40).lower()
        if not outcome:
            combined = f"{detail} {error}".lower()
            if pending:
                outcome = "running"
            elif ok:
                outcome = "completed"
            elif "发送失败" in combined or "投递失败" in combined:
                outcome = "delivery_failed"
            elif "final content gate" in combined or "复核" in combined or "校验" in combined:
                outcome = "content_rejected"
            elif "生成" in combined or "llm" in combined:
                outcome = "generation_failed"
            elif "超时" in combined or "到点" in combined or "未启用" in combined:
                outcome = "scheduler_blocked"
            else:
                outcome = "interrupted"
        raw["proactive_message"] = {
            "type": "proactive_message",
            "ok": bool(ok),
            "pending": bool(pending),
            "trace_id": _single_line(user.get("troubleshooting_proactive_test_id"), 32),
            "outcome_type": outcome,
            "title": "主动消息链路测试",
            "umo": _single_line(user.get("umo"), 180),
            "detail": _single_line(detail, 220),
            "error": _single_line(error, 220),
            "diagnostic_detail": safe_diagnostic_detail,
            "text_preview": self._proactive_visible_text_preview(text) if text else "",
            "original_text_preview": self._proactive_visible_text_preview(original_text) if original_text else "",
            "final_text_preview": self._proactive_visible_text_preview(final_text) if final_text else "",
            "action": _single_line(action, 60) or "message",
            "reason": _single_line(reason, 40) or "check_in",
            "extra_count": max(0, int(extra_count or 0)),
            "steps": list(user.get("troubleshooting_proactive_steps") or [])[:12],
            "elapsed_ms": int(max(0.0, now - started) * 1000) if started > 0 else 0,
            "ran_at": now,
            "ran_at_text": self._format_timestamp_elapsed(now),
            "user_id": _single_line(user_id, 80),
        }

    def _restore_troubleshooting_proactive_plan(self, user: dict[str, Any]) -> None:
        restore = user.get("troubleshooting_proactive_restore")
        if isinstance(restore, dict):
            values = restore.get("values")
            if isinstance(values, dict):
                missing = restore.get("missing")
                if isinstance(missing, list):
                    for key in missing:
                        if isinstance(key, str):
                            user.pop(key, None)
                for key, value in values.items():
                    if isinstance(key, str):
                        user[key] = deepcopy(value)
            else:
                for key, value in restore.items():
                    user[key] = deepcopy(value)
        else:
            self._clear_pending_proactive_plan(user)
        user.pop("troubleshooting_proactive_restore", None)
        user.pop("troubleshooting_proactive_test_id", None)
        user.pop("troubleshooting_proactive_started_at", None)
        user.pop("troubleshooting_proactive_steps", None)

    def _recover_stale_troubleshooting_proactive_plans(self) -> int:
        users = self.data.get("users")
        if not isinstance(users, dict):
            return 0
        recovered = 0
        for user_id, user in users.items():
            if not isinstance(user, dict) or not isinstance(user.get("troubleshooting_proactive_restore"), dict):
                continue
            self._append_troubleshooting_proactive_step(user, "启动恢复", "error", "上次排障临时主动未完成，已恢复原计划")
            self._record_troubleshooting_proactive_result(
                str(user_id),
                user,
                ok=False,
                detail="上次排障临时主动任务未完成，插件启动时已恢复原主动计划",
                error="插件重启或任务中断",
                action=str(user.get("planned_proactive_action") or "message"),
                reason=normalize_legacy_tag_text(user.get("planned_proactive_reason")) or "check_in",
            )
            user["proactive_sending"] = False
            user["proactive_sending_started_at"] = 0
            self._restore_troubleshooting_proactive_plan(user)
            recovered += 1
        return recovered

    async def _run_proactive_maintenance_tasks(self) -> None:
        if self._proactive_generation_disabled():
            return
        for label, task_factory in (
            ("技能成长结算", self._maybe_settle_skill_growth),
            ("B站无聊观看", self._maybe_trigger_bilibili_boredom_watch),
            ("网页探索", self._maybe_trigger_web_exploration),
            ("AI日报追踪", self._maybe_track_ai_daily),
            ("新闻无聊阅读", self._maybe_trigger_news_boredom_read),
            ("QQ空间生活说说", self._maybe_publish_qzone_life_post),
            ("QQ空间评论收件箱", self._maybe_process_qzone_comment_inbox),
        ):
            try:
                await task_factory()
            except Exception as exc:
                logger.warning("主动维护任务失败,不阻塞私聊主动: %s error=%s", label, _single_line(exc, 160))

    @staticmethod
    def _proactive_send_disables_segmenting(reason: str, *, friend_proactive: bool = False) -> bool:
        # Friend-proactive output has already been planned by its upstream sender.
        # All locally rendered reasons, including creative shares, should respect
        # the user's segmentation settings; media remains atomic in the planner.
        return bool(friend_proactive)


    async def _tick(self):
        try:
            await self._pull_body_monitor_candidates()
        except Exception as exc:
            logger.warning(
                "Body Monitor 事件拉取失败，本轮继续执行其他主动任务: %s",
                _single_line(exc, 160),
            )
        async with self._data_lock:
            runtime = self.data.setdefault("proactive_runtime", {})
            if isinstance(runtime, dict):
                runtime["last_tick_started_at"] = _now_ts()
                runtime["last_tick_error"] = ""
            stale_timer_count = self._expire_stale_official_llm_timers_locked()
            if stale_timer_count:
                self._save_data_sync(sections={"users"})
            if self._proactive_generation_disabled():
                changed = False
                users_root = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
                for user in users_root.values():
                    if isinstance(user, dict):
                        changed = self._suspend_user_proactive_generation(user) or changed
                pool = self.data.get("proactive_candidate_pool")
                if isinstance(pool, list):
                    for candidate in pool:
                        if not isinstance(candidate, dict):
                            continue
                        status = _single_line(candidate.get("status"), 24).lower()
                        if status in {"", "accepted", "deferred", "queued", "pending", "unknown"}:
                            candidate["status"] = "blocked"
                            candidate["note"] = "每日主动上限为 0，主动生成已停止"
                            candidate["updated_ts"] = _now_ts()
                            changed = True
                if isinstance(runtime, dict):
                    runtime["generation_disabled"] = True
                    runtime["generation_disabled_reason"] = "max_daily_messages=0"
                    runtime["last_tick_finished_at"] = _now_ts()
                if changed:
                    self._save_proactive_tick_state(
                        {"users", "proactive_candidate_pool", "proactive_runtime"}
                    )
                return
            if isinstance(runtime, dict):
                runtime["generation_disabled"] = False
                runtime["generation_disabled_reason"] = ""
            if self._maybe_schedule_bilibili_video_share():
                self._save_data_sync(
                    sections={
                        "users",
                        "proactive_candidate_pool",
                        "external_event_pool",
                        "external_event_self_link_cache",
                    }
                )
            users = list(self.data.get("users", {}).items())

        for user_id, user in users:
            await self._tick_user(user_id, user)

        await self._run_proactive_maintenance_tasks()
        async with self._data_lock:
            runtime = self.data.setdefault("proactive_runtime", {})
            if isinstance(runtime, dict):
                runtime["last_tick_finished_at"] = _now_ts()
