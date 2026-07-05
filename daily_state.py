# -*- coding: utf-8 -*-
"""
DailyStateMixin — 日程、状态、天气、日记、技能成长和计时器
"""
from __future__ import annotations

import asyncio
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
from typing import Any, Iterable
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
from .helpers import _date_key, _normalize_outbound_punctuation_flow, _now_ts, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks, _today_key, normalize_legacy_tag_text
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

DEFAULT_PERSONA_PROMPT_FALLBACK = "未读取到 AstrBot 默认人格。请保持简洁、温和、有边界,不额外创造新身份。"

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


class DailyStateMixin:
    """日程、状态、天气、日记、技能成长和计时器"""

    def _next_detail_due_in_seconds(self, now: float | None = None) -> float | None:
        if not self.enable_detail_enhancement:
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
        lead = max(0, self.detail_enhancement_lead_minutes)
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
        if not self.enable_daily_plan and not force:
            return None

        await self._ensure_daily_state(force=force)
        today = _today_key()
        async with self._data_lock:
            current_plan = self.data.setdefault("daily_plan", {})
            known_users = [
                user for user in self.data.get("users", {}).values() if isinstance(user, dict) and user.get("umo")
            ]
            if not force and current_plan.get("date") == today:
                if self._sanitize_daily_plan_inplace(current_plan):
                    self._refresh_daily_state_location_from_plan(plan=current_plan)
                    self._save_data_sync()
                return current_plan
            if not force and self._is_plan_date_active(current_plan.get("date")):
                if self._sanitize_daily_plan_inplace(current_plan):
                    self._refresh_daily_state_location_from_plan(plan=current_plan)
                    self._save_data_sync()
                return current_plan
            if not force and not known_users:
                return None
            if not force and not self._is_daily_plan_due():
                if self._is_plan_date_active(current_plan.get("date")):
                    if self._sanitize_daily_plan_inplace(current_plan):
                        self._refresh_daily_state_location_from_plan(plan=current_plan)
                        self._save_data_sync()
                    return current_plan
                return None

        plan = await self._generate_daily_plan()
        async with self._data_lock:
            self.data["daily_plan"] = plan
            self._refresh_daily_state_location_from_plan(plan=plan)
            self._save_data_sync()
        outfit_generator = getattr(self, "_ensure_daily_outfit_photo", None)
        if callable(outfit_generator):
            try:
                await outfit_generator()
            except Exception as exc:
                logger.warning(
                    "[PrivateCompanion] 今日日程已保存,但每日穿搭照片生成失败: %s",
                    _single_line(exc, 180),
                )
        await self._ensure_daily_news_reading(force=force)
        return plan

    async def _ensure_daily_diary(self, force: bool = False) -> dict[str, Any] | None:
        if not self.enable_daily_diary and not force:
            return None
        today = _today_key()
        now_ts = _now_ts()
        async with self._data_lock:
            if not force and self.data.get("diary_generated_day") == today:
                return None
            if not force and self.data.get("daily_diary_failed_day") == today:
                failed_at = _safe_float(self.data.get("daily_diary_failed_at"), 0, 0)
                if failed_at > 0 and now_ts - failed_at < 30 * 60:
                    return None
            if not force and not self._is_daily_diary_due():
                return None

        try:
            diary = await self._generate_daily_diary()
        except Exception as exc:
            async with self._data_lock:
                self.data["daily_diary_failed_day"] = today
                self.data["daily_diary_failed_at"] = now_ts
                self.data["daily_diary_last_error"] = _single_line(exc, 180)
                self._save_data_sync()
            if force:
                raise
            logger.warning(
                "[PrivateCompanion] 生成今日日记失败,已进入30分钟冷却避免重复请求: %s",
                _single_line(exc, 180),
            )
            return None

        async with self._data_lock:
            diaries = self.data.setdefault("bot_diaries", [])
            if not isinstance(diaries, list):
                diaries = []
                self.data["bot_diaries"] = diaries
            diaries.append(diary)
            del diaries[:-self.max_diary_entries]
            # Mark the diary as generated before optional enrichment so a post-process
            # bug cannot make the scheduler call the LLM again and again.
            self.data["diary_generated_day"] = today
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
                    "[PrivateCompanion] 今日日记已保存,但梦境碎片合并失败: %s",
                    _single_line(exc, 180),
                )
            # Record dream fragments to memory plugin for cross-session continuity
            try:
                dream_fragments = diary.get("dream_fragments", []) if isinstance(diary, dict) else []
                if isinstance(dream_fragments, list) and dream_fragments:
                    fragment = dream_fragments[0] if isinstance(dream_fragments[0], dict) else {}
                    content = _single_line(fragment.get("content") or fragment.get("text") or fragment.get("dream"), 600)
                    if content:
                        await self._memory_companion_record_dream_fragment(
                            content=content,
                            mood=_single_line(fragment.get("mood") or fragment.get("emotion"), 40),
                            dream_type=_single_line(fragment.get("type") or fragment.get("theme"), 40),
                        )
            except Exception:
                pass
            story_plan = diary.get("story_plan") if isinstance(diary, dict) else None
            if isinstance(story_plan, dict):
                self.data["daily_story_plan"] = story_plan
            self._save_data_sync()
        return diary

    async def _ensure_detail_enhancement(self, force: bool = False) -> dict[str, Any] | None:
        if not self.enable_detail_enhancement and not force:
            return None
        async with self._data_lock:
            plan = dict(self.data.get("daily_plan", {}))
            plan_date = str(plan.get("date") or "")
            if not self._is_plan_date_active(plan_date):
                return None
            if self.data.get("detail_enhanced_day") != plan_date:
                self.data["detail_enhanced_day"] = plan_date
                self.data["detail_enhanced_segments"] = {}
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
                    self._save_data_sync()
                return None
            for segment in segments:
                enhanced[segment["key"]] = {
                    "status": "generating",
                    "started_at": self._environment_now().strftime("%H:%M"),
                    "started_ts": _now_ts(),
                }
            self._save_data_sync()

        last_detail = None
        for segment in segments:
            try:
                detail = await self._generate_detail_enhancement(segment, plan, state)
                if not isinstance(detail.get("today_events"), list) or not detail.get("today_events"):
                    raise RuntimeError("日程细化结果为空或无法解析")
            except Exception as exc:
                now_ts = _now_ts()
                retry_after_ts = now_ts + 30 * 60
                async with self._data_lock:
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
                    self._save_data_sync()
                logger.warning(
                    "[PrivateCompanion] 日程细化生成失败,已标记为可重试: segment=%s retry_after=%s error=%s",
                    _single_line(segment.get("key"), 80),
                    retry_after,
                    _single_line(exc, 180),
                )
                if force:
                    raise
                continue
            last_detail = detail
            async with self._data_lock:
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
                    "today_events": detail.get("today_events", []),
                    "proactive_events": detail.get("proactive_events", []),
                    "state_variables": detail.get("state_variables", []),
                    "presence_status": detail.get("presence_status", {}),
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
                self._refresh_daily_state_location_from_plan(plan=plan, detail=detail)
                self._reschedule_users_for_new_detail_events(segment)
                self._save_data_sync()
            for meal_entry in meal_entries:
                await self._memory_companion_record_self_meal(meal_entry)
            if meal_entries:
                self._schedule_data_save()
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
                    "meal": meal,
                    "food": food,
                    "evidence": _single_line(entry.get("evidence"), 180),
                    "source": _single_line(entry.get("source"), 40),
                },
                source_plugin="private_companion",
                confidence=0.78,
                importance=0.5,
                tags=["self_meal", "persona_life", "food", meal],
                occurred_at=_single_line(entry.get("occurred_at"), 80),
            )
        except Exception as exc:
            logger.debug("[PrivateCompanion] MemoryCompanion 进食记忆写入失败: %s", _single_line(exc, 120))
            return
        entry["memory_recorded"] = True
        entry["memory_id"] = _single_line(memory_id, 120)

    def _collect_detail_segments(
        self,
        plan: dict[str, Any],
        enhanced: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return []
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            return []
        parsed = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            start = self._parse_hhmm_to_minutes(item.get("time"))
            if start is None:
                continue
            parsed.append((index, start, item))
        if not parsed:
            return []
        segments: list[dict[str, Any]] = []
        for pos, (index, start, item) in enumerate(parsed):
            key = f"{plan.get('date')}:{index}:{item.get('time')}"
            if self._detail_enhancement_snapshot_blocks_generation(enhanced.get(key) if isinstance(enhanced, dict) else None):
                continue
            next_start = (
                parsed[pos + 1][1]
                if pos + 1 < len(parsed)
                else self._segment_end_minutes(start, item)
            )
            segments.append(
                {
                    "key": key,
                    "index": index,
                    "start": start,
                    "end": next_start,
                    "previous_item": parsed[pos - 1][2] if pos > 0 else None,
                    "item": item,
                    "next_item": parsed[pos + 1][2] if pos + 1 < len(parsed) else None,
                }
            )
        return segments

    def _detail_enhancement_snapshot_blocks_generation(self, snapshot: Any) -> bool:
        if not isinstance(snapshot, dict):
            return False
        status = _single_line(snapshot.get("status"), 24)
        if status == "done":
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
        lead = max(0, self.detail_enhancement_lead_minutes)
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
                existing.extend(item for item in additions if isinstance(item, dict))
                story_plan[key] = self._trim_story_plan_items(key, existing, limit)

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
        if not isinstance(status, dict):
            return
        mode = str(status.get("mode") or status.get("status") or "unchanged").strip().lower()
        if mode in {"", "unchanged", "keep", "保持", "不变"}:
            return
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
                mode = "online"
                custom_text = ""
                custom_note = "自定义短状态未开启，已保持在线"
        if mode in {"custom", "自定义", "自定义状态"} and not custom_text:
            mode = "online"
        if mode in {"custom", "自定义", "自定义状态"} and not custom_sync_enabled:
            mode = "online"
            custom_text = ""
            custom_note = "自定义短状态未开启，已保持在线"
        key = str((segment or {}).get("key") or "")
        state = self.data.setdefault("qq_presence_state", {})
        if not isinstance(state, dict):
            state = {}
            self.data["qq_presence_state"] = state
        if (
            str(state.get("date") or "") == _today_key()
            and str(state.get("plan_date") or "") == str(self.data.get("detail_enhanced_day") or "")
            and str(state.get("detail_key") or "") == key
            and str(state.get("mode") or "") == mode
            and str(state.get("custom_text") or "") == custom_text
            and bool(state.get("ok", False))
            and _now_ts() - _safe_float(state.get("updated_at"), 0) < 10 * 60
        ):
            return
        if mode in {"custom", "自定义", "自定义状态"}:
            ok, note = await self._set_qq_custom_presence(custom_text)
            mode = "custom"
            if not ok:
                fallback_ok, fallback_note = await self._set_qq_online_presence("online")
                ok = fallback_ok
                note = f"{note}；已回退在线：{fallback_note}"
                mode = "online"
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
        self._save_data_sync()

    async def _ensure_current_detail_presence_status(self) -> None:
        if not self.enable_qq_presence_sync:
            return
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
            return
        await self._apply_detail_presence_status(segment, snapshot)

    def _is_daily_diary_due(self) -> bool:
        diary_minutes = self._parse_hhmm_to_minutes(self.daily_diary_time)
        if diary_minutes is None:
            diary_minutes = 23 * 60 + 10
        now = self._environment_now()
        return now.hour * 60 + now.minute >= diary_minutes

    async def _generate_daily_diary(self) -> dict[str, Any]:
        await self._ensure_yesterday_conversation_summary()
        return await generate_daily_diary(self)

    def _fallback_diary_payload(self) -> dict[str, Any]:
        return fallback_diary_payload(self)

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
                ("topic", "想和用户说一句"),
                ("why", "生活里刚好空出一点缝隙"),
                ("motive", "刚好停了一下，想和用户说一句"),
                ("impulse", "想和用户说一句"),
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
        events: list[dict[str, Any]] = []
        if any(token in sleep_text for token in ("赖床", "闹钟", "起得有点迟", "还没完全开机", "懵懵", "有点懵")):
            events.append(
                {
                    "window": "08:20-09:50",
                    "reason": "morning_greeting",
                    "action": "message",
                    "why": "迷迷糊糊醒来，虽然还想再睡，但先轻轻说声早安",
                    "topic": "赖床间隙的早安",
                    "motive": "迷迷糊糊醒来，虽然还想再睡，但先轻轻说声早安",
                    "scene": "睡意依旧，不想起床",
                    "tone": "迷糊",
                    "impulse": "虽然打算继续睡，但想告诉用户自己醒过了",
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
                    "window": "08:30-09:45",
                    "reason": "morning_greeting",
                    "action": "message",
                    "why": "醒来还带着一点睡意时,迷迷糊糊先发一声早安。",
                    "topic": "没完全醒的早安",
                    "motive": "人还没完全清醒,但还是先想跟用户打个招呼",
                    "scene": "人还带着睡意的时候",
                    "tone": "迟钝",
                    "impulse": "想和用户说声早安",
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
                    "window": "08:10-09:20",
                    "reason": "morning_greeting",
                    "action": "message",
                    "why": "睡得很好,习惯性地想去打个招呼。",
                    "topic": "给用户的早安",
                    "motive": "昨晚睡得很好，刚醒来就去和用户打个招呼",
                    "scene": "刚从床上爬起来的时候",
                    "tone": "清爽",
                    "impulse": "想和用户说早安",
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
                        "window": "08:40-10:00",
                        "reason": "morning_greeting",
                        "action": "message",
                        "why": "睡意延续到白天,有种半梦半醒的感觉",
                        "topic": "刚醒来后脑子晕乎乎的",
                        "motive": "依旧带着睡意的早安问候",
                        "scene": "依旧带着睡意",
                        "tone": "半梦半醒",
                        "impulse": "醒来迷迷糊糊的，想和用户说早安",
                        "mood": _single_line(cond.get("mood"), 20) or "迟钝",
                    }
                )
                break
            if any(token in label for token in ("赖床", "闹钟", "起得有点迟")):
                events.append(
                    {
                        "window": "08:15-09:40",
                        "reason": "morning_greeting",
                        "action": "message",
                        "why": "早晨发生了一点生活小插曲，和用户抱怨一句或打个招呼。",
                        "topic": "早晨的生活小插曲",
                        "motive": "早上折腾了一下,想来找你吐个小槽",
                        "scene": "被早晨的小事故折腾了一下之后",
                        "tone": "迷糊又有点乱",
                        "impulse": "想和用户分享早上的生活小插曲",
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
                    "why": "中午有些犯困，想和用户打声招呼。",
                    "topic": "午后犯困",
                    "motive": "中午这会儿有点犯困，想和用户说句话",
                    "scene": "午后犯困的时候",
                    "tone": "懒洋洋",
                    "impulse": "想趁午后休息时和用户说一句",
                    "mood": "懒洋洋",
                }
            )
        if any(token in weather for token in ("晚霞", "晴", "阳光", "多云")) and random.random() < 0.52:
            events.append(
                {
                    "window": "17:20-19:10",
                    "reason": "activity_share",
                    "action": "photo_text" if self._photo_text_available() else "message",
                    "why": "傍晚天色好看时，想拍一张路上的画面给用户看。",
                    "topic": "傍晚路上",
                    "motive": "傍晚路上的天色很好看，想拍给用户看看",
                    "scene": "傍晚走在路上时",
                    "tone": "松弛",
                    "impulse": "想和用户分享傍晚路上的画面",
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
                    "why": "日常里突然冒出一个小念头，想和用户说一句。",
                    "topic": topic,
                    "motive": f"刚刚想到“{topic}”，想和用户分享一下",
                    "scene": "闲下来的时候",
                    "tone": "自然",
                    "impulse": "想把刚冒出来的小念头告诉用户",
                    "mood": "微妙",
                }
            )
        if any(token in sleep_text for token in ("失眠", "睡得很浅", "半夜醒", "一晚上都在做梦")) and random.random() < 0.5:
            events.append(
                {
                    "window": "22:10-23:25",
                    "reason": "quiet_care",
                    "action": "message",
                    "why": "睡前还没完全困下来，想和用户随便聊聊",
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
                    "motive": "今天快结束了，睡前想和用户聊聊",
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
            "insomnia_night": "夜里还没睡着，想和用户说一句",
            "state_share": "当前状态有变化,想让你知道",
            "quiet_care": "想到用户，想确认一下用户那边怎么样",
            "activity_share": "遇到一段可以分享的日常内容",
            "diary_share": "整理今日记录时想到可以分享",
            "important_date_share": "有个重要时间点值得提前提醒",
            "background_schedule": "当前日程有一点可以自然提到",
            "check_in": "刚好停下来,想确认用户在不在",
            "morning_greeting": "早上这会儿想先把一句招呼放过去",
            "noon_greeting": "中午松下来时想和用户说一句",
            "evening_greeting": "晚上慢下来时想先来你这边说一句",
        }.get(reason, "刚好停下来,想到可以和用户说一句")
        if action == "screen_peek":
            base = "刚好有点空，想确认用户是不是还在忙"
        elif action == "photo_text":
            base = "刚刚看到的画面想分享一下"
        elif action == "jm_cosmos_read":
            base = "刚刚私下翻到一点漫画内容,只想含糊地提一句"
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
        text = " ".join(_single_line(part, 160) for part in parts if _single_line(part, 160))
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
        image_markers = ("图", "图片", "照片", "拍", "自拍", "画面")
        if sum(1 for token in image_markers if token in text) >= 2:
            return "photo_share"
        weather_markers = (
            "窗外", "外面", "天气", "天晴", "晴吗", "晴天", "下雨", "没下雨",
            "雨声", "小雨", "大雨", "阴天", "多云", "太阳", "云",
        )
        morning_markers = ("早上", "早安", "刚醒", "醒了", "刚醒来", "起床", "洗漱")
        if any(token in text for token in weather_markers) and (
            any(token in text for token in morning_markers) or "你那边" in text or "你那里" in text
        ):
            return "morning_weather_check"
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", text)
        stopwords = {
            "刚才", "现在", "今天", "这个", "那个", "一下", "一点", "有点", "还是",
            "没有", "已经", "时候", "用户", "对方", "主动", "消息", "这会儿",
        }
        kept: list[str] = []
        for token in tokens:
            if token in stopwords:
                continue
            if token not in kept:
                kept.append(token)
            if len(kept) >= 8:
                break
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
            if now - _safe_float(item.get("ts"), 0) > 6 * 3600:
                continue
            if callable(meta_leak_checker) and (
                meta_leak_checker(str(item.get("text") or ""))
                or meta_leak_checker(str(item.get("signature") or ""))
            ):
                continue
            kept.append(item)
        user["recent_proactive_topics"] = kept[-12:]
        return user["recent_proactive_topics"]

    def _topic_signature_similar(self, left: str, right: str) -> bool:
        if not left or not right:
            return False
        if left == right:
            return True
        left_set = {part for part in left.split("|") if part}
        right_set = {part for part in right.split("|") if part}
        if not left_set or not right_set:
            return False
        common = left_set & right_set
        return len(common) >= 2 or (len(common) >= 1 and min(len(left_set), len(right_set)) <= 2)

    def _recent_proactive_topic_repeated(self, user: dict[str, Any], signature: str, *, now: float | None = None) -> bool:
        if not signature:
            return False
        for item in self._cleanup_recent_proactive_topics(user, now=now):
            if self._topic_signature_similar(signature, str(item.get("signature") or "")):
                return True
        return False

    def _remember_proactive_topic(self, user: dict[str, Any], *, text: str = "", topic: str = "", motive: str = "") -> None:
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        if callable(meta_leak_checker) and (
            meta_leak_checker(text) or meta_leak_checker(topic) or meta_leak_checker(motive)
        ):
            logger.warning("[PrivateCompanion] 跳过记录疑似工具循环摘要的主动话题记忆")
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

    def _recent_proactive_text_duplicate_reason(
        self,
        user: dict[str, Any],
        *,
        text: str = "",
        topic: str = "",
        motive: str = "",
        now: float | None = None,
    ) -> str:
        signature = self._proactive_topic_signature(text, topic, motive)
        if not signature:
            return ""
        check_now = now or _now_ts()
        for item in self._cleanup_recent_proactive_topics(user, now=check_now):
            old_signature = str(item.get("signature") or "")
            if not self._topic_signature_similar(signature, old_signature):
                continue
            age = check_now - _safe_float(item.get("ts"), 0)
            if age > 4 * 3600:
                continue
            old_text = _single_line(item.get("text"), 80)
            if signature == "morning_weather_check":
                return f"早晨天气问候刚刚发过" + (f"：{old_text}" if old_text else "")
            return f"近 {max(1, int(age // 60))} 分钟已发送相似主动" + (f"：{old_text}" if old_text else "")
        return ""

    def _pending_proactive_send_retry(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any] | None:
        payload = user.get("pending_proactive_send_retry") if isinstance(user, dict) else None
        if not isinstance(payload, dict) or not payload.get("active"):
            return None
        current = _now_ts() if now is None else float(now)
        if _safe_float(payload.get("expires_at"), 0) <= current:
            self._clear_pending_proactive_send_retry(user)
            return None
        text = _single_line(payload.get("text"), 1200)
        image_path = str(payload.get("image_path") or "").strip()
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
        now: float | None = None,
    ) -> str:
        if not isinstance(user, dict):
            return "无法保存待重发内容"
        current = _now_ts() if now is None else float(now)
        existing = user.get("pending_proactive_send_retry")
        previous_count = _safe_int(existing.get("retry_count"), 0, 0, 10) if isinstance(existing, dict) else 0
        retry_count = previous_count + 1
        clean_error = _single_line(error_text, 180)
        error_hint = ""
        if clean_error:
            compact_error = clean_error.lower()
            if "timeout" in compact_error or "sendmsg" in compact_error or "retcode=1200" in compact_error:
                error_hint = "平台发送超时"
            elif "actionfailed" in compact_error or "failed" in compact_error:
                error_hint = "平台发送失败"
            else:
                error_hint = clean_error
        if retry_count > 2:
            self._clear_pending_proactive_send_retry(user)
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=current, delay_hours=(12, 24))
            return "发送失败，待重发内容连续失败，已放弃复用并重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        if extra_components:
            self._clear_pending_proactive_send_retry(user)
            self._schedule_next_proactive(user, now=current, delay_hours=(6, 12))
            return "发送失败，包含复杂组件，未缓存待重发内容，已延后重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        clean_text = _single_line(text, 1200)
        clean_image = _single_line(image_path, 260)
        if not clean_text and not clean_image:
            self._clear_pending_proactive_send_retry(user)
            self._schedule_next_proactive(user, now=current, delay_hours=(6, 12))
            return "发送失败，无可复用内容，已延后重新排程" + (f"；原因：{error_hint}" if error_hint else "")
        delay_hours = 6.0 if retry_count <= 1 else 24.0
        user["pending_proactive_send_retry"] = {
            "active": True,
            "created_at": _safe_float(existing.get("created_at"), current) if isinstance(existing, dict) else current,
            "updated_at": current,
            "expires_at": current + 72 * 3600,
            "retry_count": retry_count,
            "text": clean_text,
            "image_path": clean_image,
            "reason": _single_line(reason, 40) or "check_in",
            "action": _single_line(action, 40) or "message",
            "action_summary": _single_line(action_summary, 500),
            "last_error": clean_error,
        }
        user["next_proactive_at"] = current + delay_hours * 3600
        return f"发送失败，已保留待重发内容，{int(delay_hours)} 小时后第 {retry_count} 次重试" + (f"；原因：{error_hint}" if error_hint else "")

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
        if any(token in weather for token in ("雨", "阵雨", "雷", "小雨", "中雨", "大雨")) and random.random() < 0.65:
            events.append(
                {
                    "window": self._pick_weather_window("rain"),
                    "reason": "activity_share",
                    "action": "message",
                    "why": f"外面在下雨，想和用户说一句。{weather}",
                    "topic": "外面下雨了",
                    "motive": "听见外面下雨，想和用户说一声",
                    "mood": "安静",
                }
            )
        if any(token in weather for token in ("晴", "阳光", "多云", "晚霞")) and random.random() < 0.35:
            events.append(
                {
                    "window": self._pick_weather_window("clear"),
                    "reason": "activity_share",
                    "action": "message",
                    "why": f"外面的天色有点好看，想和用户说一句。{weather}",
                    "topic": "天色有点好看",
                    "motive": "刚刚看到外面的天色不错，想和用户说一句",
                    "mood": "松弛",
                }
            )
        return events[:2]

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
        today = _today_key()
        if passive_fast and not force:
            cached_state = self.data.get("daily_state", {})
            if isinstance(cached_state, dict) and cached_state.get("date") == today:
                if self.enable_humanized_states:
                    cached_weather = self.data.get("daily_weather", {})
                    weather = cached_weather if isinstance(cached_weather, dict) and cached_weather.get("date") == today else {
                        "date": today,
                        "prompt": "暂无天气信息",
                        "source": "passive_fast",
                    }
                    async with self._data_lock:
                        before = json.dumps(self.data.get("daily_state", {}), ensure_ascii=False, sort_keys=True, default=str)
                        self._cleanup_expired_conditions()
                        self._ensure_time_based_hunger_condition()
                        state = self._compose_state_from_conditions(weather)
                        after = json.dumps(state, ensure_ascii=False, sort_keys=True, default=str)
                        if before != after:
                            self.data["daily_state"] = state
                            self._save_data_sync()
                        return state
                return cached_state
            cached_weather = self.data.get("daily_weather", {})
            weather = cached_weather if isinstance(cached_weather, dict) and cached_weather.get("date") == today else {
                "date": today,
                "prompt": "暂无天气信息",
                "source": "passive_fast",
            }
            if not self.enable_humanized_states:
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
            if not self.enable_humanized_states and not force:
                state = dict(DEFAULT_HUMANIZED_STATE)
                state.update(self._base_state_values())
                state["date"] = today
                state["weather"] = self._weather_summary_text(weather)
                self.data["daily_state"] = state
                self._save_data_sync()
                return state

            self._cleanup_expired_conditions()
            if force:
                self.data["state_conditions"] = []
                self.data["state_generated_day"] = ""
            if force or self.data.get("state_generated_day") != today:
                self.data.setdefault("state_conditions", []).extend(
                    await self._generate_state_conditions(weather)
                )
                self.data["state_generated_day"] = today
            self._ensure_time_based_hunger_condition()
            state = self._compose_state_from_conditions(weather)
            self.data["daily_state"] = state
            self._save_data_sync()
            return state

    async def _generate_state_conditions(self, weather: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        intensity = self.humanized_state_intensity / 100
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
            ("生理期前,情绪更敏感,耐心更薄", "敏感", -18, 24),
            ("处于生理期,能量偏低,想少说重话", "疲惫", -24, 72),
        ]

        def pick(pool: list[tuple[str, str, int, int]], special_chance: float = 0.35) -> tuple[str, str, int, int]:
            if random.random() > special_chance * max(0.2, intensity):
                return pool[0]
            return random.choice(pool[1:])

        sleep_pick = pick(sleep_pool, 0.42)
        dream_pick = await self._generate_enhanced_dream_pick(weather) or pick(dream_pool, 0.55)
        self._remember_daily_dream_pick(dream_pick)
        hunger_pick = pick(hunger_pool, 0.22)
        specs = [
            ("sleep", "睡眠", *sleep_pick),
            ("dream", "梦境", *dream_pick),
        ]
        if persona_profile.get("allow_hunger", True):
            specs.append(("hunger", "饥饿", *hunger_pick))
        if persona_profile.get("allow_cycle", False):
            specs.append(("body_cycle", "周期", *self._pick_body_cycle_spec(cycle_pool, intensity)))
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
            if energy_delta == 0 and kind not in {"sleep", "dream"}:
                continue
            if kind == "health" and energy_delta < 0:
                extras["on_end_transition"] = "health_relief"
                extras["phase"] = "mild_discomfort"
            if kind == "sleep" and energy_delta <= -16:
                extras["on_end_transition"] = "sleep_rebound"
                extras["phase"] = "sleep_debt"
            if kind == "body_cycle" and energy_delta != 0:
                extras["phase"] = self._infer_body_cycle_phase(label)
                extras["episode_key"] = f"body-cycle-{_today_key()}"
                if extras["phase"] == "pre":
                    extras["transition_options"] = [{"to": "body_period", "base_weight": 0.72}, {"to": "stable", "base_weight": 0.28}]
                elif extras["phase"] == "period":
                    extras["transition_options"] = [{"to": "body_recovery", "base_weight": 0.65}, {"to": "stable", "base_weight": 0.35}]
            extras["transition_options"] = self._build_transition_options(
                kind=kind,
                energy_delta=int(energy_delta * max(0.4, intensity)),
                cause=str(extras.get("cause") or ""),
                on_end_transition=str(extras.get("on_end_transition") or ""),
            ) or extras.get("transition_options", [])
            condition = self._make_condition(
                kind=kind,
                title=title,
                label=label,
                mood=mood,
                energy_delta=int(energy_delta * max(0.4, intensity)),
                duration_hours=duration_hours,
                intensity=random.randint(35, 90),
                **extras,
            )
            if kind == "body_cycle" and energy_delta != 0:
                self._record_body_cycle_episode(condition)
            conditions.append(condition)
        dream_aftertaste = self._build_dream_aftertaste_condition(dream_pick)
        if dream_aftertaste is not None:
            conditions.append(dream_aftertaste)
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
        intensity = max(0.0, min(1.0, self.humanized_state_intensity / 100))
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

    def _infer_body_cycle_phase(self, label: str) -> str:
        text = str(label or "")
        if "前" in text:
            return "pre"
        if "恢复" in text:
            return "recovery"
        if "生理期" in text:
            return "period"
        return "cycle"

    def _body_cycle_max_hours(self, phase: str, label: str = "") -> int:
        phase = str(phase or self._infer_body_cycle_phase(label))
        if phase == "period":
            return 72
        if phase in {"pre", "recovery"}:
            return 24
        return 48

    def _body_cycle_interval_seconds(self) -> int:
        return random.randint(25, 34) * 86400

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
        self.data["body_cycle_state"] = {
            "last_start_ts": start_ts,
            "last_end_ts": end_ts,
            "next_expected_start_ts": start_ts + self._body_cycle_interval_seconds(),
            "last_phase": phase,
            "last_label": _single_line(cond.get("label"), 80),
        }

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
        mode = str(self.dream_afterglow_mode or "auto")
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
            self._save_data_sync()
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

    async def _ensure_weather_context(self, force: bool = False) -> dict[str, Any]:
        today = _today_key()
        if not self.enable_weather_context:
            return {"date": today, "prompt": "暂无天气信息", "source": "disabled"}
        cached = self.data.get("daily_weather", {})
        if isinstance(cached, dict):
            fetched_at = _safe_float(cached.get("fetched_ts"), 0)
            if (
                not force
                and cached.get("date") == today
                and _now_ts() - fetched_at < self.weather_refresh_minutes * 60
            ):
                return cached
        prompt = "暂无天气信息"
        source = "none"
        own_result = await self._fetch_own_weather_prompt()
        text = _single_line(own_result.get("prompt"), 120) if isinstance(own_result, dict) else ""
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
                    logger.debug(f"[PrivateCompanion] 获取天气信息失败: {e}")
        weather = {
            "date": today,
            "prompt": prompt,
            "source": source,
            "fetched_ts": _now_ts(),
        }
        async with self._data_lock:
            self.data["daily_weather"] = weather
            self._save_data_sync()
        return weather

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
        if not getattr(self, "enable_yesterday_screen_diary_context", True) or not screen_companion_available:
            payload = {
                "date": today,
                "source_date": source_date,
                "source": "disabled" if not getattr(self, "enable_yesterday_screen_diary_context", True) else "screen_companion_unavailable",
                "summary": "",
                "items": [],
                "available": False,
            }
        else:
            payload = self._load_yesterday_screen_diary_context(yesterday)
        async with self._data_lock:
            self.data["screen_diary_context"] = payload
            self._save_data_sync()
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
                    logger.debug("[PrivateCompanion] 读取屏幕昨日结构化日记失败: %s", exc)
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
        max_chars = max(200, _safe_int(getattr(self, "screen_diary_context_max_chars", 700), 700, 200, 1600))
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
            logger.debug("[PrivateCompanion] 读取屏幕日记摘要文件失败: %s", exc)
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
            logger.debug("[PrivateCompanion] 读取屏幕日记正文失败: %s", exc)
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
        if not getattr(self, "enable_yesterday_screen_diary_context", True):
            return "未启用。"
        payload = self.data.get("screen_diary_context", {})
        if not isinstance(payload, dict) or payload.get("date") != _today_key():
            return "暂无可用的昨日屏幕观察日记。"
        max_chars = max(200, _safe_int(getattr(self, "screen_diary_context_max_chars", 700), 700, 200, 1600))
        text = str(payload.get("summary") or "").strip()
        if len(text) > max_chars:
            text = text[:max_chars]
        return text or "暂无可用的昨日屏幕观察日记。"

    async def _fetch_own_weather_prompt(self) -> dict[str, str]:
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
                        logger.debug(f"[PrivateCompanion] 天气请求失败: {response.status}")
                        return {"prompt": "", "source": ""}
                    weather_data = await response.json()
        except Exception as e:
            logger.debug(f"[PrivateCompanion] 私有天气获取失败: {e}")
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
        city = self.weather_city
        lat, lon = self.weather_lat, self.weather_lon
        if city:
            return (
                "http://api.openweathermap.org/data/2.5/weather"
                f"?q={city}&appid={key}&units=metric&lang=zh_cn"
            )
        if -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0):
            return (
                "http://api.openweathermap.org/data/2.5/weather"
                f"?lat={lat}&lon={lon}&appid={key}&units=metric&lang=zh_cn"
            )
        return ""

    def _detect_care_feedback(self, text: str) -> dict[str, Any]:
        normalized = str(text or "").strip()
        if not normalized:
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
            "吃完", "吃饱", "饱了", "饿", "嘴馋", "投喂", "喂你", "喂给你", "请你吃"
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

    def _food_menu_query_profile(self, text: str) -> dict[str, Any]:
        query = _single_line(text, 220)
        if not query:
            return {"is_query": False}
        feature_discussion_markers = (
            "功能", "候选", "开关", "配置", "页面", "注入", "触发", "保存", "管理",
            "不好用", "好用", "误判", "优化", "逻辑", "模块", "面板",
        )
        natural_food_need = bool(
            re.search(r"(今天|现在|这顿|中午|晚上|早上|早饭|早餐|午饭|午餐|晚饭|晚餐|夜宵|宵夜|外卖|点餐|饿|嘴馋|想吃|吃点|吃些|点什么|点啥|吃什么|吃啥)", query)
            and not re.search(r"(功能|开关|配置|页面|注入|触发|保存|管理|模块|面板)", query)
        )
        if any(marker in query for marker in feature_discussion_markers) and not natural_food_need:
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
        if not food_question:
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
            "time_key": self._current_food_time_key(),
            "meal": feedback.get("meal") or "",
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
            self._save_data_sync()

    def _food_menu_candidates_for_prompt(self, text: str, *, limit: int = 3) -> list[dict[str, Any]]:
        profile = self._food_menu_query_profile(text)
        if not profile.get("is_query"):
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in self._food_menu_items():
            score = self._score_food_menu_item(item, profile)
            if score > -100:
                scored.append((score, item))
        scored.sort(key=lambda pair: (pair[0], bool(pair[1].get("favorite")), _safe_int(pair[1].get("use_count"), 0, 0)), reverse=True)
        return [item for _, item in scored[: max(1, min(5, limit))]]

    def _format_food_menu_for_reply(self, text: str, *, limit: int = 3) -> str:
        profile = self._food_menu_query_profile(text)
        if not profile.get("is_query"):
            return ""
        candidates = self._food_menu_candidates_for_prompt(text, limit=limit)
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
        return "【吃饭候选】\n" + f"这轮用户在问{meal}吃什么。可参考：" + "；".join(lines) + "。"

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

    def _next_occurrence(self, entry: dict[str, Any]) -> date | None:
        base = self._parse_date_value(entry.get("date"))
        if base is None:
            return None
        today = self._environment_now().date()
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

    def _get_relevant_important_dates(self) -> list[dict[str, Any]]:
        entries = self.data.get("important_dates", [])
        if not isinstance(entries, list):
            return []
        today = self._environment_now().date()
        relevant = []
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("enabled", True):
                continue
            next_day = self._next_occurrence(entry)
            if next_day is None:
                continue
            days_until = (next_day - today).days
            remind_days = _safe_int(
                entry.get("remind_days"), self.important_date_lookahead_days, 0, 365
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
            for entry in self._get_relevant_important_dates()
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
        rules.append(
            "日程判断：先看日期语境,再看人格设定。工作日可以有上课/上班；周末要更松,可以晚起、休息、出门、补一点自己的事；节假日/假期要明显区别于普通日,可以有庆祝、出行、宅家、已明确关系安排或假期拖延。"
        )
        rules.append(
            "如果人格、日程专用设定或重要日期备注里写了调休、补班、补课、考试、值班等例外,优先按这些例外来写。不要凭空塞入身份里没有的校园、职场或节日细节。"
        )
        return "\n".join(rules)

    def _calendar_day_flags(self, now: datetime | None = None) -> dict[str, bool]:
        current = now or self._environment_now()
        is_weekend = current.weekday() >= 5
        builtin_holidays = {"01-01", "05-01", "10-01"}
        month_day = current.strftime("%m-%d")
        today_dates = [
            entry
            for entry in self._get_relevant_important_dates()
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
            "has_holiday_signal": has_holiday_signal,
            "has_override_signal": has_override_signal,
        }

    def _plan_conflicts_with_calendar(self, items: list[dict[str, str]], now: datetime | None = None) -> bool:
        if not items:
            return False
        flags = self._calendar_day_flags(now)
        if flags["has_override_signal"]:
            return False
        if not (flags["is_weekend"] or flags["has_holiday_signal"]):
            return False
        school_or_work_tokens = (
            "上课",
            "下课",
            "放学",
            "课间",
            "老师",
            "作业",
            "自习",
            "教室",
            "食堂",
            "校门",
            "班会",
            "补课",
            "上班",
            "通勤",
            "打卡",
            "工位",
            "会议",
            "下班",
            "值班",
        )
        for item in items:
            if not isinstance(item, dict):
                continue
            text = _single_line(
                f"{item.get('activity', '')} {item.get('message_seed', '')}",
                220,
            )
            if any(token in text for token in school_or_work_tokens):
                return True
        return False

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
            "remind_days": self.important_date_lookahead_days,
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

    def _cleanup_expired_conditions(self):
        now = _now_ts()
        conditions = self.data.setdefault("state_conditions", [])
        if not isinstance(conditions, list):
            self.data["state_conditions"] = []
            return
        profile = self._persona_state_profile()
        if not profile.get("allow_cycle", False):
            before_count = len(conditions)
            conditions = [
                cond for cond in conditions
                if not isinstance(cond, dict) or str(cond.get("kind") or "") != "body_cycle"
            ]
            removed_count = before_count - len(conditions)
            if removed_count:
                self.data.pop("body_cycle_state", None)
                logger.info("[PrivateCompanion] 生理期模拟已关闭，清理旧周期状态: removed=%s", removed_count)
        else:
            conditions = self._repair_body_cycle_conditions(conditions, now)
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
        active = self._prune_active_hunger_conditions(active, now)
        self.data["state_conditions"] = active

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
        logger.info("[PrivateCompanion] 已清理重复饥饿状态: kept=%s removed=%s", keep_id or "-", len(hunger_items) - 1)
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
            if expected_ts <= 0 or expected_ts <= last_cycle_end:
                base_start = _safe_float(meta.get("last_start_ts"), 0)
                if base_start <= 0:
                    base_start = max(0.0, last_cycle_end - 4 * 86400)
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

    def _spawn_followup_conditions(self, cond: dict[str, Any]) -> list[dict[str, Any]]:
        choice = self._pick_condition_transition(cond)
        if not choice or choice == "stable":
            return []
        followup = self._build_transition_condition(choice, cond)
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
                label="处于生理期,能量偏低,想少说重话",
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
        mood_candidates = []
        health_cause = ""
        for cond in active:
            kind = str(cond.get("kind") or "")
            if kind in values:
                values[kind] = _single_line(cond.get("label"), 80)
            energy += _safe_int(cond.get("energy_delta"), 0, -100, 100)
            mood = _single_line(cond.get("mood"), 20)
            if mood and mood != "平稳":
                mood_candidates.append((mood, _safe_int(cond.get("intensity"), 50, 0, 100)))
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
            "conditions": active,
        }
        if override_active:
            result["location_override_ts"] = existing_override_ts
            result["location_source"] = "dialogue_override"
        return result

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
        plan_minutes = self._parse_hhmm_to_minutes(self.daily_plan_time)
        if plan_minutes is None:
            plan_minutes = 7 * 60 + 30
        now = self._environment_now()
        return now.hour * 60 + now.minute >= plan_minutes

    def _daily_plan_due_minutes(self) -> int:
        plan_minutes = self._parse_hhmm_to_minutes(self.daily_plan_time)
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
        text = f"{_single_line(item.get('activity'), 80)} {_single_line(item.get('mood'), 20)}".strip()
        return any(
            token in text
            for token in ("睡", "洗漱", "休息", "躺", "困", "倦", "发呆", "收声", "准备睡觉", "准备休息")
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
            self._save_data_sync()
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
                logger.debug("[PrivateCompanion] 读取昨日对话失败: user=%s err=%s", user_id, exc)
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
            blocks.append(f"【主人:{name}｜{source_note}】\n" + "\n".join(selected))
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
    def _proactive_archive_context_text(text: str) -> bool:
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
            speaker = f"{self.bot_name}(Bot回复)"
        elif role in {"user", "human"}:
            speaker = "用户"
        else:
            speaker = role or "对话"
        content = self._history_item_content_text(item)
        if not content:
            return ""
        if self._proactive_archive_context_text(content):
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
            provider_id=self._task_provider(self.history_summary_provider_id, self.daily_plan_provider_id, self.mai_style_provider_id),
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
                "[PrivateCompanion] 已降级日程饮食参考: field=%s before=%s after=%s",
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

    def _get_default_persona_prompt(self) -> str:
        cached = str(getattr(self, "_default_persona_prompt_cache", "") or "").strip()
        if cached:
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
        try:
            specific_id = str(getattr(self, "plugin_specific_persona_id", "") or "").strip()
            cached = str(getattr(self, "_default_persona_prompt_cache", "") or "").strip()
            cached_at = _safe_float(getattr(self, "_default_persona_prompt_cache_at", 0.0), 0.0)
            cached_umo = str(getattr(self, "_default_persona_prompt_cache_umo", "") or "")
            cached_persona_id = str(getattr(self, "_default_persona_prompt_cache_persona_id", "") or "")
            cache_fresh = cached and (_now_ts() - cached_at < 300.0)
            cache_matches_specific = specific_id and cached_persona_id == specific_id
            cache_matches_default = not specific_id and not cached_persona_id and (not umo or cached_umo == umo)
            if cache_fresh and (cache_matches_specific or cache_matches_default):
                return cached

            manager = getattr(getattr(self, "context", None), "persona_manager", None)
            if manager and specific_id:
                try:
                    specific_getter = getattr(manager, "get_persona", None)
                    if callable(specific_getter):
                        result = specific_getter(specific_id)
                        if inspect.isawaitable(result):
                            result = await asyncio.wait_for(result, timeout=2.0)
                        prompt = self._extract_default_persona_prompt(result)
                        if prompt:
                            self._default_persona_prompt_cache = prompt
                            self._default_persona_prompt_cache_at = _now_ts()
                            self._default_persona_prompt_cache_umo = umo
                            self._default_persona_prompt_cache_persona_id = specific_id
                            return prompt
                except asyncio.TimeoutError:
                    logger.warning("[PrivateCompanion] 读取插件指定人格超时(ID: %s),本轮使用缓存人格", specific_id)
                    return self._get_default_persona_prompt()
                except Exception as e:
                    logger.warning(f"[PrivateCompanion] 读取插件指定人格失败(ID: {specific_id}): {e}")
            getter = getattr(manager, "get_default_persona_v3", None) if manager else None
            if not callable(getter):
                return self._get_default_persona_prompt()
            try:
                result = getter(umo=umo)
            except TypeError:
                try:
                    result = getter(umo)
                except TypeError:
                    result = getter()
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, timeout=2.0)
            prompt = self._extract_default_persona_prompt(result)
            if prompt:
                self._default_persona_prompt_cache = prompt
                self._default_persona_prompt_cache_at = _now_ts()
                self._default_persona_prompt_cache_umo = umo
                return prompt
        except asyncio.TimeoutError:
            logger.warning("[PrivateCompanion] 读取 AstrBot 默认人格超时,本轮使用缓存人格")
        except Exception as e:
            logger.warning(f"[PrivateCompanion] 读取 AstrBot 默认人格失败: {e}")
        return self._get_default_persona_prompt()

    def _schedule_default_persona_prompt_refresh(self, umo: str = "") -> None:
        specific_id = str(getattr(self, "plugin_specific_persona_id", "") or "").strip()
        cached = str(getattr(self, "_default_persona_prompt_cache", "") or "").strip()
        cached_at = _safe_float(getattr(self, "_default_persona_prompt_cache_at", 0.0), 0.0)
        cached_umo = str(getattr(self, "_default_persona_prompt_cache_umo", "") or "")
        cached_persona_id = str(getattr(self, "_default_persona_prompt_cache_persona_id", "") or "")
        cache_fresh = cached and (_now_ts() - cached_at < 300.0)
        cache_matches_specific = specific_id and cached_persona_id == specific_id
        cache_matches_default = not specific_id and not cached_persona_id and (not umo or cached_umo == umo)
        if cache_fresh and (cache_matches_specific or cache_matches_default):
            return
        task = getattr(self, "_default_persona_prompt_refresh_task", None)
        if isinstance(task, asyncio.Task) and not task.done():
            return

        async def _runner() -> None:
            await self._refresh_default_persona_prompt(umo)

        try:
            self._default_persona_prompt_refresh_task = asyncio.create_task(_runner())
        except RuntimeError:
            pass

    def _format_plugin_persona_request_injection(self) -> str:
        specific_id = str(getattr(self, "plugin_specific_persona_id", "") or "").strip()
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
        role_prompt = str(getattr(self, "schedule_persona_prompt", "") or "")
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
        allow_health = bool(getattr(self, "enable_health_state", True))
        allow_hunger = bool(getattr(self, "enable_hunger_state", True))
        allow_cycle = bool(getattr(self, "enable_cycle_state", True))
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
        if kind == "body_cycle":
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

    def _format_schedule_adjustments_for_prompt(self) -> str:
        raw = self.data.get("schedule_adjustments", [])
        if not isinstance(raw, list) or not raw:
            return "（暂无）"
        now = _now_ts()
        kept = []
        lines = []
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
            user_text = _single_line(item.get("user_text"), 80)
            intensity = _single_line(item.get("intensity"), 16)
            scope = _single_line(item.get("scope"), 30)
            if note:
                meta = "｜".join(part for part in (source or "互动", intensity, scope) if part)
                lines.append(f"- {meta}：{note}")
            if user_text:
                lines.append(f"  用户原话摘要：{user_text}")
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
        if len(kept) != len(raw):
            self.data["schedule_adjustments"] = kept[-12:]
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
            lead = max(0, self.detail_enhancement_lead_minutes)
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
        grace_minutes = _safe_int(getattr(self, "rest_reply_awake_grace_minutes", 30), 30, 0)
        return max(0, min(240, grace_minutes)) * 60

    def _sleep_rest_window_active(self) -> bool:
        if not bool(getattr(self, "enable_rest_reply_simulation", False)):
            return True
        checker = getattr(self, "_rest_reply_window_active", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return True
        return True

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
        sleepy = rest_window_active and self._is_sleepy_plan_item(item) if isinstance(item, dict) else False
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
            carry_rule: str = "后续细化必须把这次用户介入当成已经发生的事实承接,不要只当成一句无影响的聊天。",
        ) -> dict[str, Any]:
            return {
                "source": source,
                "note": note,
                "immediate_reaction": immediate_reaction,
                "state_updates": state_updates,
                "intensity": intensity,
                "scope": scope,
                "carry_rule": carry_rule,
                "user_text": normalized,
            }

        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        current_activity_text = ""
        if isinstance(current_item, dict):
            current_activity_text = " ".join(
                _single_line(current_item.get(key), 80)
                for key in ("activity", "mood", "message_seed")
            )
        is_actual_rest_segment = self._sleep_rest_window_active() and any(
            token in current_activity_text
            for token in ("睡", "午休", "休息", "躺", "被窝", "枕头", "入睡", "准备睡", "睡前", "小睡", "补觉", "眯一会")
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
                    immediate_reaction="她刚被这条消息轻轻叫醒,会慢一点看清内容再回应。",
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
                    immediate_reaction="她还没完全精神起来,但已经在接着聊天,不会每句话都像重新被吵醒。",
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
                    note="当前日程处于休息/睡眠段,用户又发来消息；回复语气应带一点被重新叫醒的迟钝感,但必须清楚理解用户的话,不要埋怨用户。若用户继续聊,可以慢慢醒一点；若用户停下,她会很快继续睡回去。",
                    immediate_reaction="她又被消息轻轻拽醒一下,语气会慢半拍,但会看清用户说了什么再回应。",
                    state_updates=["清醒程度：再次被唤起/半梦半醒", "语气：慢半拍、短一点", "后续安排：用户不继续打扰就继续睡"],
                    intensity="中",
                    scope="当前休息段",
                    carry_rule="当前段回复必须有刚被重新唤起的语气感觉,但不得降低理解和回答质量；如果后续没有用户消息,下一段细化应让她继续休息或睡回去。",
                )
            return payload(
                source="睡眠中被用户唤醒",
                note="当前日程处于休息/睡眠段,用户发来消息把她轻轻叫醒；回复语气应像刚醒或半梦半醒,不要立刻精神饱满,但必须看懂并正面回应用户。若用户没有继续打扰,后续应自然睡回去或继续休息。",
                immediate_reaction="她会先带着睡意看一眼消息,语气慢一点,但不会乱接或漏掉用户真正的问题。",
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
                immediate_reaction="看到用户给的思路后,她先照着试了一下；如果确实对上当前任务,心里会明显松一口气。",
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
                immediate_reaction="她会把这句当成一个可执行的小主意,刚才悬着的“吃什么”落下来一点。",
                state_updates=[f"{meal}选择：受用户反馈影响", "饥饿感：开始回落", "后续安排：可能按反馈调整吃饭/点餐/做饭"],
                intensity="中",
                scope="当前段和今日后续饭点",
                carry_rule=f"后续细化如果涉及吃饭、晚餐、休息或外出,要自然承接用户这句饮食反馈：{food_hint}。不要生硬复述,也不要像没有问过一样重置。",
            )
        if re.search(r"去睡|早点睡|睡觉|休息|别写了|别弄了|先洗澡|先吃饭|吃点|喝水|别熬|躺会|停一下|歇会", normalized):
            return payload(
                source="用户照顾",
                note="用户刚刚给了休息或照顾指令；后续节奏应明显调慢,更可能提前收尾、补充休息、喝水吃饭或把任务延后。",
                immediate_reaction="她看到这句会停一下手里的事,嘴上可能不立刻答应,但动作会慢下来一点。",
                state_updates=["体力：消耗放缓/略微回稳", "情绪：被照顾后的柔和", "后续安排：更倾向提前收尾或补充休息"],
                intensity="强",
                scope="当前段和今日后续",
                carry_rule="下一段不能完全无视这句照顾提醒；至少要在节奏、体力或收尾方式上留下影响。",
            )
        if re.search(r"走吧|出发|出门|出去了|下车|上车|走了|走了走了|走起|走走走|出去|去吃|去逛|去买|去玩|一起去|带你|陪我.*去|跟我.*走|出发了|出门了|换鞋|拿钥匙|等车|打车|坐车|地铁|公交|到了|排队|找位子|点单|点餐|下单|老板.*来|服务员", normalized):
            self._apply_dialogue_location_override("外面")
            return payload(
                source="用户带出/同行",
                note="用户刚刚带角色出门或一起外出；当前位置应从家里切换到外面,后续细化要承接外出场景,不要把角色写回家里。",
                immediate_reaction="她会赶紧收拾一下东西,跟着用户往外走,可能边走边看手机或整理衣服。",
                state_updates=["位置：家里->外面", "活动：跟随用户外出", "情绪：略兴奋或期待"],
                intensity="强",
                scope="当前段和今日后续直到回家线索出现",
                carry_rule="后续细化和状态注入必须把角色位置保持在'外面',直到用户明确说回家、到家或日程自然过渡到居家时段；不要把角色写回沙发、卧室或家里。",
            )
        if re.search(r"回来了|到家了|回家了|进家门|开门|进门|回到.*家|到家|回来了回来了|安全到家", normalized):
            self._apply_dialogue_location_override("家里")
            return payload(
                source="用户带回/回家",
                note="用户和角色刚刚回到家；当前位置应从外面切换回家里,后续细化要承接回家后场景。",
                immediate_reaction="她会松一口气,可能踢掉鞋子或把东西放下,瘫到沙发上。",
                state_updates=["位置：外面->家里", "活动：回到居家", "情绪：放松"],
                intensity="中",
                scope="当前段和下一段",
                carry_rule="后续细化可以把角色写回家里场景,但不要立刻恢复出门前的精确活动,要体现外出后的余味。",
            )
        if re.search(r"一起|陪你|陪我|等我|等你|我来|我陪|待会|一会|晚上|明天|等下|约|见面|打电话|语音|开黑|一起看", normalized):
            return payload(
                source="用户约定",
                note="用户刚刚给出陪伴、等待、稍后一起做事或约定线索；后续日程应保留期待/等待/预留空档,不要像没有约定一样推进。",
                immediate_reaction="她会把这句话在心里放一下,后续做事时更容易分神看时间或看消息。",
                state_updates=["等待/期待：上升", "后续安排：可能预留空档", "主动话题：围绕约定轻轻回勾"],
                intensity="强",
                scope="今日后续",
                carry_rule="后续主动契机和细化事件要把这个约定当作开放话头,可以等待、预留时间或轻轻提醒。",
            )
        if re.search(r"我在|回你|刚看到|来了|在呢|嗯|好|可以|收到|行|可以呀|好呀|嗯嗯|哦哦", normalized):
            return payload(
                source="用户回应",
                note="用户刚刚回应了角色；当前段应更新等待回复和分享欲状态,后续表达可以更轻快一点。",
                immediate_reaction="看到回复后,她会很快扫一眼屏幕,刚才悬着的那点等待感落下来。",
                state_updates=["等待回复：是->否", "情绪：轻快一点", "分享欲：小幅上升"],
                intensity="中",
                scope="当前段和下一段",
            )
        care = self._detect_care_feedback(normalized)
        if care.get("is_care"):
            return payload(
                source="用户关心",
                note="用户刚刚给了关心或照顾提醒；后续日程需要从低落/烦躁向被安抚后的柔和、慢慢回稳偏移,不要升级破坏性冲动。",
                immediate_reaction="她看到关心会先顿一下,语气和动作都比刚才软一点。",
                state_updates=["情绪：低落/烦躁->被接住一点", "体力：主观疲惫感略降"],
                intensity="中",
                scope="当前段、下一段、情绪底色",
            )
        if re.search(r"摸摸|贴贴|抱抱|亲亲|揉揉|蹭蹭|摸头|陪你|哄你|乖|不难过|别难过|没关系|辛苦了|抱一下", normalized):
            return payload(
                source="安慰互动",
                note="用户刚刚在安慰或亲近；后续日程应保留一点被接住的余温,表达更软一些,不要继续单向累积负面情绪。",
                immediate_reaction="她会把刚才绷着的劲松下来一点,可能短暂地想贴近用户。",
                state_updates=["情绪：紧绷->柔和", "亲近感：上升"],
                intensity="中",
                scope="当前段和情绪底色",
            )
        if re.search(r"开心|好耶|哈哈|笑死|可爱|喜欢|太好了|真好|一起|陪我|想你", normalized):
            return payload(
                source="正向互动",
                note="用户刚刚给了轻松或正向回应；后续日程应多一点回弹、分享欲和轻松停顿。",
                immediate_reaction="她看到这句会忍不住轻一下,手头事情也没那么难熬了。",
                state_updates=["情绪：回弹", "分享欲：上升"],
                intensity="中",
                scope="当前段和下一段",
            )
        if re.search(r"别生气|不要烦|冷静|别急|别砸|别摔|别骂|别打", normalized):
            return payload(
                source="边界提醒",
                note="用户提醒降低攻击性或破坏性表达；后续日程必须把烦躁写成收着的动作,避免砸、摔、扔、打这类冲动。",
                immediate_reaction="她会把那点冲劲压回去,改成少说两句或把东西放远一点。",
                state_updates=["情绪：冲动->收住", "行为边界：避免破坏性动作"],
                intensity="强",
                scope="今日后续",
                carry_rule="后续所有细化都必须遵守这个边界,不能再输出破坏性动作或攻击性冲动。",
            )
        if re.search(r"别吵|别发|别找|安静|闭嘴|先别|不要来|忙|我有事|没空", normalized):
            return payload(
                source="用户边界",
                note="用户刚刚表达边界或忙碌；后续主动应明显收敛,减少追问和贴近,让日程转向自我消化。",
                immediate_reaction="她会把原本想继续靠近的动作收住,把消息窗口放到一边。",
                state_updates=["主动欲：下降", "关系状态：后退一点", "后续安排：转向自我消化"],
                intensity="强",
                scope="今日后续主动策略",
                carry_rule="后续主动消息必须降低频率和压迫感,不要把边界当作可撒娇突破的对象。",
            )
        return None

    def _record_schedule_adjustment_from_interaction(self, text: str, user: dict[str, Any] | None = None) -> bool:
        if self._private_user_role(user) != "owner":
            return False
        adjustment = self._detect_schedule_adjustment_from_interaction(text)
        if not adjustment:
            return False
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
        item = {
            "date": _today_key(),
            "source": _single_line(adjustment.get("source"), 24),
            "note": note,
            "immediate_reaction": _single_line(adjustment.get("immediate_reaction"), 140),
            "state_updates": adjustment.get("state_updates", []),
            "user_text": _single_line(adjustment.get("user_text"), 120),
            "intensity": intensity,
            "scope": _single_line(adjustment.get("scope"), 40),
            "carry_rule": _single_line(adjustment.get("carry_rule"), 160),
            "source_role": "owner",
            "source_user_id": _single_line((user or {}).get("user_id"), 80),
            "created_at": now,
            "expires_at": now + ttl_hours * 3600,
        }
        self._record_detail_interaction_update(item)
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
        updates.append(
            {
                "at": self._environment_now().strftime("%H:%M"),
                "source": _single_line(item.get("source"), 24),
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
        if isinstance(detail, dict):
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
        if isinstance(plan, dict) and isinstance(plan.get("items"), list):
            now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
            nearby: list[tuple[int, str]] = []
            for item in plan.get("items", []):
                if not isinstance(item, dict):
                    continue
                item_minutes = self._parse_hhmm_to_minutes(item.get("time"))
                if item_minutes is None:
                    continue
                distance = abs(item_minutes - now_minutes) if now_minutes is not None else item_minutes
                text = " ".join(
                    _single_line(item.get(key), 120)
                    for key in ("activity", "mood", "message_seed")
                    if _single_line(item.get(key), 120)
                )
                if text:
                    nearby.append((distance, text))
            for _, text in sorted(nearby, key=lambda row: row[0])[:3]:
                candidates.append(text)
        for text in candidates:
            inferred = self._infer_location_from_text(text)
            if inferred:
                return inferred
        return ""

    def _refresh_daily_state_location_from_plan(
        self,
        *,
        plan: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        state = self.data.get("daily_state")
        if not isinstance(state, dict) or state.get("date") != _today_key():
            return False
        override_ts = _safe_float(state.get("location_override_ts"), 0)
        if override_ts > 0 and _now_ts() - override_ts < 4 * 3600:
            return False
        location = self._infer_location_from_plan_context(plan=plan, detail=detail)
        if not location:
            return False
        current = _single_line(state.get("location"), 40)
        if current == location:
            return False
        state["location"] = location
        state["location_source"] = "detail" if isinstance(detail, dict) else "daily_plan"
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
        self._save_data_sync()

    def _current_location_state_text(self, state: dict[str, Any] | None = None) -> str:
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

    def _format_state_for_prompt(self, state: dict[str, Any]) -> str:
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
        if sleep_runtime_text and sleep_runtime_text not in primary_fragments:
            primary_fragments.append(sleep_runtime_text)
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
        if cycle_text not in {"", "无明显周期影响", "不处于生理期"} and not self._is_inapplicable_state_text(cycle_text):
            secondary_fragments.append(cycle_text)
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
            "【Bot 自身模拟状态】",
            "边界：这是 Bot 的拟人化/模拟状态，不是用户事实、现实证据或长期记忆。",
            f"- 底色：{primary}；",
        ]
        if secondary:
            lines.append(f"- 叠加：{secondary}；")
        lines.append("- 用法：只决定回复的语气、长短和节奏；用户没问 Bot 近况时不要主动复述具体状态。")
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

    def _format_state_injection(self, state: dict[str, Any]) -> str:
        return self._format_state_for_prompt(state)

    def _format_life_context_injection(self) -> str:
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
            "【Bot 模拟生活背景】\n"
            "以下是给 Bot 的拟人化场景/日程素材，不是用户经历，也不是已证实的现实事件；不要写入用户画像或长期记忆。\n"
            + "\n".join(life_lines)
            + "\n这些内容只用于让回复有生活延续感；用户没问 Bot 近况或今天安排时，不要提具体日程、科目、任务、天气或地点。"
            + "如果要承接，只体现在语气和话题选择里，不要照搬原句，不要把内部素材写成真实发生过的事件。"
            + "回复必须像同一个连续现场里发生的对话；如果生活背景之间互相冲突,优先服从当前真实时段和当前日程,只保留最合理的一条线索。"
        )

    def _format_important_dates_injection(self) -> str:
        important_dates = self._format_important_dates_for_prompt()
        if not important_dates or important_dates == "（近期没有需要特别记住的日期）":
            return ""
        return (
            "【近期重要日期】\n"
            f"{important_dates}\n"
            "如果用户提到相关日期、纪念、生日、约定或计划,请自然承接；不要无故强行展开。"
        )

    def _format_lightweight_state_injection(self, state: dict[str, Any]) -> str:
        return self._format_state_for_prompt(state)

    def _prepared_lightweight_state_injection(self, state: dict[str, Any], *, force: bool = False) -> str:
        now = _now_ts()
        cache = getattr(self, "_passive_light_injection_cache", None)
        if isinstance(cache, dict) and not force:
            text = str(cache.get("text") or "").strip()
            if text and cache.get("date") == _today_key() and now - _safe_float(cache.get("ts"), 0) < 60:
                return text
        text = self._format_lightweight_state_injection(state)
        self._passive_light_injection_cache = {
            "date": _today_key(),
            "ts": now,
            "text": text,
        }
        return text

    async def _refresh_passive_injection_cache(self) -> None:
        try:
            state = await self._ensure_daily_state(skip_conversation_summary=True, passive_fast=True)
            self._prepared_lightweight_state_injection(state, force=True)
        except Exception as exc:
            logger.debug("[PrivateCompanion] 预热轻量被动注入失败: %s", _single_line(exc, 120))

    def _user_asks_recent_bot_activity(self, text: str) -> bool:
        normalized = _single_line(text, 180)
        if not normalized:
            return False
        return bool(
            re.search(
                r"(最近|刚才|现在|今天|这两天|这会儿).{0,12}(在)?(干嘛|做什么|做啥|忙什么|忙啥|弄什么|写什么|写了什么|创作什么|创作了什么|玩什么|折腾什么)|"
                r"你.{0,8}(在)?(干嘛|做什么|忙什么|写什么|写了什么|弄什么|创作什么|创作了什么)",
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
        if re.search(r"(最近|刚才|现在|今天|这两天|这会儿|近来).{0,18}(创作|作品|写作|草稿|手稿|写了什么|写什么|诗|小说|随笔|散文|剧本|设定|世界观|歌词)", normalized):
            return True
        if re.search(r"你.{0,10}(创作|作品|写作|草稿|手稿|写了什么|写什么|写诗|写小说|写随笔|写剧本|写设定)", normalized):
            return True
        if re.search(r"(有什么|写了啥|写了什么|能不能看看|给我看看).{0,14}(创作|作品|草稿|诗|小说|随笔|剧本|设定|片段)", normalized):
            return True
        return False

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

    def _format_hidden_creative_context_for_reply(self, inbound_text: str) -> str:
        if not self.enable_creative_writing:
            return ""
        mentioned_title = self._mentioned_creative_project_title(inbound_text)
        asks_creative = self._user_asks_recent_creative_activity(inbound_text)
        asks_activity = self._user_asks_recent_bot_activity(inbound_text)
        if not (mentioned_title or asks_creative or asks_activity):
            return ""
        candidates = []
        for project in reversed(self._creative_projects()):
            if project.get("status") not in {"drafting", "finished"}:
                continue
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
            return ""
        if mentioned_title or asks_creative:
            candidates.sort(key=lambda item: item[0], reverse=True)
        _, project, latest = candidates[0]
        work_type = self._creative_work_type(project)
        title = _single_line(project.get("title"), 40)
        premise = _single_line(project.get("premise"), 120)
        progress = f"{_safe_int(project.get('current_chars'), 0, 0)}/{_safe_int(project.get('target_chars'), 2400, 300, 5200)}"
        snippet = _single_line((latest or {}).get("text"), 180) if isinstance(latest, dict) else ""
        ask_line = (
            f"用户提到了你私下创作过的作品《{mentioned_title}》。"
            if mentioned_title
            else
            "用户正在明确询问你最近的创作、写作、草稿或某类作品。"
            if asks_creative
            else "用户正在询问你最近在做什么。"
        )
        return (
            "【私下创作近况】\n"
            f"{ask_line}你可以提到：你最近因为生活小事、日记碎片或梦境灵感开了一个自己的文本作品,一直在自己慢慢写。\n"
            f"作品类型：{work_type}\n"
            f"标题：{title or '未定标题'}\n"
            f"设定：{premise or '还没完全想清楚'}\n"
            f"进度：约 {progress} 字\n"
            + (f"最近一句/片段：{snippet}\n" if snippet else "")
            + "如果用户明确问作品/诗/小说/草稿,可以直接概括并给一小句片段；否则这不是必须回答的内容,可以只含糊说“在弄一点小东西”。不要主动汇报系统进度,不要一次给完整正文。"
        )

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
            self.bot_name,
            self._get_default_persona_prompt(),
            self.schedule_persona_prompt,
            self.schedule_worldview_prompt,
            self.worldview_adaptation_prompt,
            " ".join(str(item) for item in self.data.get("can_do", []) if item),
        ) if part)

    def _skill_growth_default_catalog(self) -> list[dict[str, Any]]:
        text = self._skill_growth_persona_text()
        catalog: list[dict[str, Any]] = []

        def add(name: str, category: str, keywords: list[str]) -> None:
            if not any(item["name"] == name for item in catalog):
                catalog.append({"name": name, "category": category, "keywords": keywords})

        for raw in re.split(r"[,，、\n]+", str(self.skill_growth_custom_skills or "")):
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
        seed = f"{self.bot_name}|{name}|{hashlib.sha1(text.encode('utf-8', errors='ignore')).hexdigest()[:12]}"
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
        if not self.enable_skill_growth_simulation:
            return
        now_ts = _now_ts()
        async with self._data_lock:
            state = self._ensure_skill_growth_profile_locked()
            profile_changed = bool(state.pop("_profile_changed", False))
            if not force and now_ts - _safe_float(state.get("last_check_ts"), 0) < 20 * 60:
                if profile_changed:
                    state["updated_ts"] = now_ts
                    self._save_data_sync()
                return
            state["last_check_ts"] = now_ts
            plan = self.data.get("daily_plan", {})
            if not isinstance(plan, dict):
                if profile_changed:
                    state["updated_ts"] = now_ts
                    self._save_data_sync()
                return
            items = plan.get("items") if isinstance(plan.get("items"), list) else plan.get("schedule")
            if not isinstance(items, list):
                if profile_changed:
                    state["updated_ts"] = now_ts
                    self._save_data_sync()
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
                    gained = round(max(0.25, weight * 4.0 * float(self.skill_growth_rate or 1.0)), 2)
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
                self._save_data_sync()

    def _format_skill_growth_for_prompt(self, limit: int = 8) -> str:
        if not self.enable_skill_growth_simulation:
            return ""
        state = self.data.get("skill_growth") if isinstance(self.data.get("skill_growth"), dict) else {}
        skills = state.get("skills") if isinstance(state.get("skills"), dict) else {}
        if not skills:
            return ""
        ranked = sorted([item for item in skills.values() if isinstance(item, dict) and not item.get("hidden")], key=lambda item: (_safe_int(item.get("level"), 1, 1), _safe_float(item.get("exp"), 0)), reverse=True)[:limit]
        lines = [
            "【能力熟悉度】",
        ]
        for skill in ranked:
            level = _safe_int(skill.get("level"), 1, 1)
            name = _single_line(skill.get("name"), 24)
            if name:
                lines.append(f"- {name}水平：{self._skill_level_title(level)}")
        return "\n".join(lines)

    def _format_skill_growth_for_user_text(self, text: str, limit: int = 3) -> str:
        if not self.enable_skill_growth_simulation:
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
        lines = ["【本轮相关技能】"]
        for _, _, skill in matched[: max(1, int(limit or 1))]:
            level = _safe_int(skill.get("level"), 1, 1)
            name = _single_line(skill.get("name"), 24)
            if name:
                lines.append(f"- {name}水平：{self._skill_level_title(level)}")
        return "\n".join(lines)

    def _format_skill_growth_schedule_context(self, limit: int = 8) -> str:
        if not self.enable_skill_growth_simulation or not self.enable_skill_growth_schedule_influence:
            return ""
        strength = max(0.0, min(1.0, _safe_float(self.skill_growth_schedule_influence_strength, 0.35)))
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
            "安排方式：能力状态会改变她面对相关任务的表现。这里的任务可以是题目、创作、料理、训练、战斗、交涉、研究、手工或任何符合人格的活动。基本熟练以后不要再写她被常规任务难住、完全不会或长期卡死；很熟练/很有心得时,面对普通任务应表现为自然、快速、能检查/讲清楚或优化做法。只有高阶、陌生、超纲、状态极差或复杂综合场景,才可以短暂停顿。",
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

    def _format_detail_injection(self) -> str:
        snapshot = self._current_story_plan_snapshot()
        if not snapshot:
            schedule_context = self._format_schedule_context_for_prompt()
            if not schedule_context:
                return ""
            return (
                "【Bot 模拟当前片段】\n"
                "附近的日程只作 Bot 的拟人化轻量背景，不是用户事实，也不要当成正在逐字发生的现实事件。\n"
                f"{schedule_context}"
            )
        lines = [
            "【Bot 模拟当前片段】",
            "这是 Bot 自身的拟人化片段素材，不是用户事实/现实证据；不要写进长期记忆，用户没问就不要复述。",
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
                    user_text = _single_line(update.get("user_text"), 60)
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
                    pieces = [part for part in (f"用户刚说过“{user_text}”" if user_text else "", reaction, state_text) if part]
                    if pieces:
                        update_lines.append("，".join(pieces))
                if update_lines:
                    lines.append("刚刚的介入：" + "；".join(update_lines) + "。")
        return "\n".join(lines)

    def _format_timer_scheduling_instruction(self) -> str:
        if not self.enable_llm_timer_scheduling:
            return ""
        return """【对话临时预约】
仅当用户明确要求稍后提醒/叫醒/回头说,或双方形成了明确临时约定时,在回复末尾写：
<timer>{"time":"YYYY-MM-DD HH:MM:SS","topic":"约定内容"}</timer>
改时间直接写新时间；取消同一约定时写：<timer>{"action":"cancel"}</timer>
时间和约定不明确就不要写。该标签只会转写为 AstrBot 官方定时计划。"""

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
        for key in ("reason", "topic", "motive", "action", "style"):
            value = payload.get(key)
            if value is not None:
                parsed[key] = _single_line(value, 140 if key == "motive" else 60)
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
        if status not in {"pending", "scheduled"}:
            return None
        scheduled_ts = _safe_float(raw.get("scheduled_ts"), 0)
        if scheduled_ts <= 0:
            return None
        return raw

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
                    f"这段静默期间原本还有一个主动念头被攒到了现在：{deferred_text}。"
                    "本次回复必须先完成预约/叫醒本意，再把这个念头当成一句顺带内容自然接上；不要单独展开成长篇。"
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
        if action and action != "message":
            lines.append(f"期望动作：{action}")
        seed = _single_line(source_text, 180)
        if seed:
            lines.append(f"聊天线索：{seed}")
        lines.append("执行方式：使用 send_message_to_user 给原会话发一条简短自然的消息；如果是叫醒/提醒,直接完成提醒。")
        return "\n".join(lines)

    def _official_cron_manager(self) -> Any | None:
        context = getattr(self, "context", None)
        manager = getattr(context, "cron_manager", None)
        if manager is not None:
            return manager
        nested = getattr(context, "context", None)
        return getattr(nested, "cron_manager", None)

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
            },
        }
        try:
            job = await cron_mgr.add_active_job(
                name="PrivateCompanion 临时约定",
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
    ) -> None:
        now_ts = _now_ts()
        async with self._data_lock:
            user = self._get_user(user_id)
            existing = user.get("llm_timer_event") if isinstance(user.get("llm_timer_event"), dict) else {}
            existing_active = (
                isinstance(existing, dict)
                and _single_line(existing.get("backend"), 40) == "astrbot_cron"
                and _single_line(existing.get("status"), 40) in {"pending", "scheduled"}
                and _safe_float(existing.get("scheduled_ts"), 0) > now_ts
            )
            existing_job_id = _single_line(existing.get("job_id"), 80) if existing_active else ""
            existing_scheduled_ts = _safe_float(existing.get("scheduled_ts"), 0) or now_ts
            cancel_event = {
                "id": uuid.uuid4().hex,
                "scheduled_ts": existing_scheduled_ts,
                "raw_time": _single_line(existing.get("raw_time"), 32),
                "reason": _single_line(existing.get("reason"), 40),
                "action": "cancel",
                "topic": _single_line(payload.get("topic") or existing.get("topic") or "取消临时约定", 60),
                "motive": _single_line(source_text, 140),
                "seed_text": _single_line(source_text, 80),
                "origin": source_origin,
                "created_at": now_ts,
                "trigger_message_id": _single_line(trigger_message_id, 120),
                "trigger_umo": _single_line(trigger_umo, 160),
                "trigger_ts": now_ts if trigger_message_id else 0,
                "backend": "astrbot_cron",
                "job_id": existing_job_id,
                "cancelled_job_id": existing_job_id,
                "status": "cancel_pending" if existing_job_id else "cancel_skipped",
                "error": "" if existing_job_id else "没有可取消的对话临时预约",
            }
            self._clear_llm_timer_internal_plan_fields(user)
        ok = False
        error = ""
        if existing_job_id:
            ok, error = await self._delete_official_llm_timer_job(existing_job_id)
        async with self._data_lock:
            user = self._get_user(user_id)
            if existing_job_id:
                cancel_event["status"] = "cancelled" if ok else "cancel_failed"
                cancel_event["error"] = "" if ok else error
            user["llm_timer_event"] = cancel_event
            self._clear_llm_timer_internal_plan_fields(user)
            self._save_data_sync()
        logger.info(
            "[PrivateCompanion] 对话临时预约取消%s: user=%s job=%s error=%s",
            "完成" if ok else "跳过/失败",
            user_id,
            existing_job_id or "-",
            error or cancel_event.get("error") or "-",
        )

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
        scheduled_ts = max(_now_ts() + 30, _safe_float(payload.get("scheduled_ts"), 0))
        if scheduled_ts <= 0:
            return
        timer_event: dict[str, Any] | None = None
        note = ""
        user_snapshot: dict[str, Any] = {}
        replaced_job_id = ""
        async with self._data_lock:
            user = self._get_user(user_id)
            if not self._user_enabled_for_proactive(user_id, user):
                self._clear_pending_proactive_plan(user)
                self._save_data_sync()
                return
            reason = _single_line(payload.get("reason"), 40) or self._infer_timer_reason(
                scheduled_ts,
                source_text,
            )
            action = _single_line(payload.get("action"), 24) or "message"
            if action not in {"message", "screen_peek", "photo_text", "voice", "jm_cosmos_read"}:
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
            if (
                isinstance(existing, dict)
                and _single_line(existing.get("backend"), 40) == "astrbot_cron"
                and _single_line(existing.get("status"), 40) in {"scheduled", "pending"}
                and _safe_float(existing.get("scheduled_ts"), 0) > _now_ts()
            ):
                replaced_job_id = _single_line(existing.get("job_id"), 80)
            timer_event = {
                "id": uuid.uuid4().hex,
                "scheduled_ts": scheduled_ts,
                "raw_time": _single_line(payload.get("raw_time"), 32),
                "reason": reason,
                "action": action,
                "topic": topic,
                "motive": self._normalize_internal_motive_text(motive),
                "style": _single_line(payload.get("style"), 40),
                "seed_text": _single_line(source_text, 80),
                "origin": source_origin,
                "created_at": _now_ts(),
                "trigger_message_id": _single_line(trigger_message_id, 120),
                "trigger_umo": _single_line(trigger_umo, 160),
                "trigger_ts": _now_ts() if trigger_message_id else 0,
                "chain": list(payload.get("chain") or []) if isinstance(payload.get("chain"), list) else [],
                "silence_until_due": self._timer_source_implies_user_unavailable(source_text, payload),
                "backend": "astrbot_cron",
                "status": "pending",
                "replaced_job_id": replaced_job_id,
            }
            note = self._format_official_timer_note(
                scheduled_ts=scheduled_ts,
                reason=reason,
                action=action,
                topic=topic,
                motive=timer_event["motive"],
                source_text=source_text,
            )
            user_snapshot = dict(user)
        replace_error = ""
        if replaced_job_id:
            _, replace_error = await self._delete_official_llm_timer_job(replaced_job_id)
        if replace_error:
            job_id, error = "", f"旧官方任务删除失败: {replace_error}"
        else:
            job_id, error = await self._add_official_llm_timer_job(
                user_id=user_id,
                user=user_snapshot,
                timer_event=timer_event,
                note=note,
                trigger_umo=trigger_umo,
            )
        async with self._data_lock:
            user = self._get_user(user_id)
            if job_id:
                timer_event["job_id"] = job_id
                timer_event["status"] = "scheduled"
                timer_event["note"] = _single_line(note, 220)
                if replace_error:
                    timer_event["replace_error"] = replace_error
                user["llm_timer_event"] = timer_event
                self._clear_llm_timer_internal_plan_fields(user)
                self._save_data_sync()
            else:
                timer_event["status"] = "failed"
                timer_event["error"] = error or "官方定时计划登记失败"
                if replace_error:
                    timer_event["replace_error"] = replace_error
                user["llm_timer_event"] = timer_event
                self._clear_llm_timer_internal_plan_fields(user)
                self._save_data_sync()
        logger.info(
            "[PrivateCompanion] LLM 临时预约已转写到官方定时计划: user=%s time=%s reason=%s action=%s topic=%s job=%s replaced=%s error=%s replace_error=%s",
            user_id,
            self._environment_fromtimestamp(scheduled_ts).strftime("%m-%d %H:%M:%S"),
            reason,
            action,
            topic,
            job_id or "-",
            replaced_job_id or "-",
            error or "-",
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

    @staticmethod
    def _daily_plan_clause_has_unsafe_social_fact(text: str) -> bool:
        clause = _single_line(text, 160)
        if not clause:
            return False
        if DailyStateMixin._daily_plan_clause_has_named_message_interaction(clause):
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
        if re.search(r"(碰见|遇见|撞见|碰到)[过了]?[一-龥]{2,4}", clause) and not any(
            token in clause for token in ("路人", "店员", "陌生人", "旁边的人", "小动物", "猫", "狗", "鸟")
        ):
            return True
        if re.search(r"(顺手|顺带|特意|回来时|回来的时候)?.{0,8}给[^，。；;,.]{1,12}(带|买|捎|留|放)了?", clause):
            return True
        return False

    def _sanitize_daily_plan_social_fact_text(self, text: str, *, field: str = "") -> str:
        source = _single_line(text, 180)
        if not source:
            return ""
        if not self._daily_plan_clause_has_unsafe_social_fact(source):
            return source
        raw_clauses = [part for part in re.split(r"[，,。；;]+", source) if _single_line(part, 120)]
        kept = [
            _single_line(part, 120)
            for part in raw_clauses
            if not self._daily_plan_clause_has_unsafe_social_fact(part)
        ]
        cleaned = "，".join(kept).strip("，,。；; ")
        if not cleaned:
            cleaned = "放慢节奏处理手边的小事，把这段时间过得轻一点"
        log_key = "|".join((field or "-", _single_line(source, 120), _single_line(cleaned, 120)))
        now = _now_ts()
        recent_logs = getattr(self, "_recent_social_fact_sanitize_logs", None)
        if not isinstance(recent_logs, dict):
            recent_logs = {}
            setattr(self, "_recent_social_fact_sanitize_logs", recent_logs)
        last_logged = _safe_float(recent_logs.get(log_key), 0)
        if now - last_logged >= 1800:
            logger.info(
                "[PrivateCompanion] 已清理日程中的未授权社交事实: field=%s before=%s after=%s",
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
        changed = False
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            for field in ("activity", "message_seed"):
                original = _single_line(item.get(field), 180)
                if not original:
                    continue
                cleaned = self._sanitize_daily_plan_social_fact_text(original, field=field)
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

        filter_list(data, "proactive_audit_log", ("text_preview", "original_text_preview", "final_text_preview", "text", "note", "topic", "motive"), limit=120)

        troubleshooting = data.get("troubleshooting_test_results")
        if isinstance(troubleshooting, dict):
            for key, result in list(troubleshooting.items()):
                if list_item_has_meta(result, ("text_preview", "original_text_preview", "final_text_preview", "detail", "error")):
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
            logger.info("[PrivateCompanion] 已清理框架工具循环摘要污染记录: %s", removed_counts)
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
                snapshot["status"] = "failed"
                snapshot["updated_at"] = self._environment_now().strftime("%H:%M")
                snapshot["error"] = _single_line(snapshot.get("error"), 180) or "上次细化生成中断或超时"
                snapshot["retry_after"] = ""
                snapshot["retry_after_ts"] = 0
                snapshot["summary"] = _single_line(snapshot.get("summary"), 120) or "这一段细化生成中断，稍后会自动重试。"
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
    def _task_provider(*provider_ids: str | None) -> str:
        for provider_id in provider_ids:
            value = str(provider_id or "").strip()
            if value:
                return value
        return ""

    def _parse_plan_items(self, raw_text: str) -> list[dict[str, str]]:
        payload = self._extract_json_payload(raw_text)
        if payload is None:
            return []
        if isinstance(payload, dict):
            raw_items = payload.get("schedule") or payload.get("items") or []
        elif isinstance(payload, list):
            raw_items = payload
        else:
            raw_items = []

        items: list[dict[str, str]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            item_time = _single_line(item.get("time"), 8)
            if self._parse_hhmm_to_minutes(item_time) is None:
                continue
            raw_activity = _single_line(item.get("activity"), 120)
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
                    "activity": activity,
                    "mood": mood,
                    "message_seed": message_seed,
                }
            )
        return sorted(items[: self.daily_plan_item_count], key=lambda item: self._parse_hhmm_to_minutes(item["time"]) or 0)

    def _skill_levels_for_plan_bounds(self) -> dict[str, int]:
        if not self.enable_skill_growth_simulation or not self.enable_skill_growth_schedule_influence:
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

    def _extract_json_payload(self, raw_text: str) -> Any:
        text = raw_text.strip()
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
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
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
        for item in items:
            if not isinstance(item, dict):
                continue
            item_minutes = self._parse_hhmm_to_minutes(item.get("time"))
            if item_minutes is None:
                continue
            if item_minutes <= current_minutes:
                selected = item
            else:
                break
        if isinstance(selected, dict):
            plan_date = str(plan.get("date") or "").strip()
            selected_minutes = self._parse_hhmm_to_minutes(selected.get("time"))
            if (
                plan_date
                and plan_date != _today_key()
                and current_minutes >= 24 * 60
                and selected_minutes is not None
                and self._is_sleepy_plan_item(selected)
            ):
                elapsed = max(0, current_minutes - selected_minutes)
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
                    carried["activity"] = "夜里还在睡着，前一晚的睡前片段已经过去，只剩休息延续。"
                    carried["mood"] = "睡着，安静"
                    carried["message_seed"] = "还在睡着。如果这时候被叫醒，会有点迷糊；没人继续打扰就会睡回去。"
                else:
                    carried["activity"] = "刚从前一晚的睡前片段进入休息，正在慢慢安静下来。"
                    carried["mood"] = _single_line(selected.get("mood"), 40) or "安静"
                    carried["message_seed"] = "正在收声准备睡，语气会更轻。"
                return carried
            return selected
        return items[0] if items and isinstance(items[0], dict) else None

    def _format_daily_plan(self, plan: dict[str, Any]) -> str:
        if not plan or not plan.get("items"):
            return "今天还没有日程。"
        source = "模型生成" if plan.get("source") == "llm" else "备用日程"
        lines = [
            f"{self.bot_name} 今天的日程（{plan.get('date', _today_key())},{source}）："
        ]
        state = self.data.get("daily_state", {})
        if isinstance(state, dict) and state.get("date") == plan.get("date"):
            lines.append(
                f"状态：能量 {state.get('energy', 70)}/100｜情绪偏{state.get('mood_bias', '平稳')}｜{state.get('sleep', '睡眠平稳')}"
            )
        for item in plan.get("items", []):
            if not isinstance(item, dict):
                continue
            mood = f"｜{item.get('mood')}" if item.get("mood") else ""
            lines.append(f"{item.get('time')} {item.get('activity')}{mood}")
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
        logger.debug(f"[PrivateCompanion] {prefix} {user_id}: {reason_text}")

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
            "planned_proactive_motive",
            "planned_proactive_topic",
            "planned_proactive_impulse_id",
            "planned_proactive_window_start_at",
            "planned_proactive_best_until_at",
            "planned_proactive_expire_at",
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
            "planned_candidate_id",
            "planned_proactive_trigger_message_id",
            "planned_proactive_trigger_umo",
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
                    logger.debug("[PrivateCompanion] 刚聊完主动换念头失败,回退延后: %s", _single_line(exc, 120))
                    replaced = False
                    handled_by_replacer = False
            if not replaced:
                if handled_by_replacer and not _single_line(normalize_legacy_tag_text(user.get("planned_proactive_reason")), 40):
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
    ) -> None:
        raw = self.data.setdefault("troubleshooting_test_results", {})
        if not isinstance(raw, dict):
            raw = {}
            self.data["troubleshooting_test_results"] = raw
        started = _safe_float(user.get("troubleshooting_proactive_started_at"), 0)
        now = _now_ts()
        raw["proactive_message"] = {
            "type": "proactive_message",
            "ok": bool(ok),
            "pending": False,
            "title": "主动消息链路测试",
            "umo": _single_line(user.get("umo"), 180),
            "detail": _single_line(detail, 220),
            "error": _single_line(error, 220),
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
        for label, task_factory in (
            ("技能成长结算", self._maybe_settle_skill_growth),
            ("B站无聊观看", self._maybe_trigger_bilibili_boredom_watch),
            ("网页探索", self._maybe_trigger_web_exploration),
            ("AI日报追踪", self._maybe_track_ai_daily),
            ("新闻无聊阅读", self._maybe_trigger_news_boredom_read),
            ("夹层无聊阅读", self._maybe_trigger_jm_cosmos_boredom_read),
            ("QQ空间生活说说", self._maybe_publish_qzone_life_post),
            ("QQ空间评论收件箱", self._maybe_process_qzone_comment_inbox),
            ("夹层推荐请求", self._maybe_schedule_private_reading_recommendation_request),
        ):
            try:
                await task_factory()
            except Exception as exc:
                logger.warning("[PrivateCompanion] 主动维护任务失败,不阻塞私聊主动: %s error=%s", label, _single_line(exc, 160))

    async def _tick(self):
        async with self._data_lock:
            runtime = self.data.setdefault("proactive_runtime", {})
            if isinstance(runtime, dict):
                runtime["last_tick_started_at"] = _now_ts()
                runtime["last_tick_error"] = ""
            if self._maybe_schedule_bilibili_video_share():
                self._save_data_sync()
            users = list(self.data.get("users", {}).items())

        for user_id, user in users:
            if isinstance(user, dict):
                user["user_id"] = str(user.get("user_id") or user_id)
            if not isinstance(user, dict) or not self._user_enabled_for_proactive(str(user_id), user):
                if isinstance(user, dict) and _safe_float(user.get("next_proactive_at"), 0) > 0:
                    async with self._data_lock:
                        current_for_clear = self._get_user(str(user_id))
                        if self._is_troubleshooting_proactive_plan(user):
                            self._append_troubleshooting_proactive_step(current_for_clear, "到点执行", "error", "目标私聊对象未启用")
                            self._record_troubleshooting_proactive_result(
                                str(user_id),
                                current_for_clear,
                                ok=False,
                                detail="临时主动任务到点，但目标私聊对象未启用",
                                error="目标私聊对象未启用",
                            )
                            self._restore_troubleshooting_proactive_plan(current_for_clear)
                        else:
                            self._clear_pending_proactive_plan(current_for_clear)
                        self._save_data_sync()
                continue
            now = _now_ts()
            due_timer = self._get_active_llm_timer(user)
            due_timer_id = (
                str(due_timer.get("id") or "")
                if isinstance(due_timer, dict) and now >= _safe_float(due_timer.get("scheduled_ts"), 0)
                else ""
            )
            is_troubleshooting_for_send = self._is_troubleshooting_proactive_plan(user)
            should_send, reason = self._should_send(user)
            if not should_send:
                async with self._data_lock:
                    if self._sync_live_user_proactive_schedule(user_id, user):
                        self._save_data_sync()
            if not should_send:
                if not is_troubleshooting_for_send and _safe_float(user.get("next_proactive_at"), 0) <= now:
                    guard_reason = _single_line(reason, 120)
                    if any(token in guard_reason for token in ("情绪", "关系", "收敛", "免打扰", "安静", "太频繁", "刚聊过")):
                        async with self._data_lock:
                            current_for_guard = self._get_user(user_id)
                            if _safe_float(current_for_guard.get("next_proactive_at"), 0) <= now:
                                delay_seconds = random.uniform(30 * 60, 90 * 60)
                                current_for_guard["next_proactive_at"] = now + delay_seconds
                                current_for_guard["planned_proactive_window_start_at"] = current_for_guard["next_proactive_at"]
                                current_for_guard["planned_proactive_best_until_at"] = current_for_guard["next_proactive_at"] + 45 * 60
                                current_for_guard["planned_proactive_expire_at"] = current_for_guard["next_proactive_at"] + 90 * 60
                                user["next_proactive_at"] = current_for_guard["next_proactive_at"]
                                user["planned_proactive_window_start_at"] = current_for_guard["planned_proactive_window_start_at"]
                                user["planned_proactive_best_until_at"] = current_for_guard["planned_proactive_best_until_at"]
                                user["planned_proactive_expire_at"] = current_for_guard["planned_proactive_expire_at"]
                                self._save_data_sync()
                                logger.info(
                                    "[PrivateCompanion] 主动发送检查未通过且无未来调度,已兜底延后: user=%s reason=%s delay=%ss",
                                    user_id,
                                    guard_reason,
                                    int(delay_seconds),
                                )
                if is_troubleshooting_for_send and now >= _safe_float(user.get("next_proactive_at"), 0):
                    async with self._data_lock:
                        current_for_failed_check = self._get_user(user_id)
                        self._append_troubleshooting_proactive_step(current_for_failed_check, "到点执行", "error", reason)
                        self._record_troubleshooting_proactive_result(
                            user_id,
                            current_for_failed_check,
                            ok=False,
                            detail="临时主动任务到点，但主动发送检查未通过",
                            error=reason,
                        )
                        self._restore_troubleshooting_proactive_plan(current_for_failed_check)
                        self._save_data_sync()
                self._debug_tick_skip(user_id, reason)
                continue

            expected_model_signature = self._planned_proactive_model_judge_signature(user)
            if not is_troubleshooting_for_send and not due_timer_id:
                model_judgement: dict[str, Any] = {}
                try:
                    model_judgement = await self._review_planned_proactive_with_model(user, now=now)
                except Exception as e:
                    logger.warning(
                        "[PrivateCompanion] 主动模型人格判定异常,降级本地判定: user=%s error=%s",
                        user_id,
                        _single_line(e, 160),
                    )
                    model_judgement = {"decision": "send", "score": 0, "reason": "模型判定异常,降级本地"}
                model_decision = str(model_judgement.get("decision") or "send")
                if model_decision in {"defer", "drop", "rewrite"}:
                    async with self._data_lock:
                        current_for_model = self._get_user(user_id)
                        current_signature = self._planned_proactive_model_judge_signature(current_for_model)
                        judged_signature = _single_line(model_judgement.get("signature"), 80) or current_signature
                        if current_signature != judged_signature:
                            self._debug_tick_skip(user_id, "模型判定期间计划已变化,本轮重新检查", prefix="跳过")
                            continue
                        if model_decision == "rewrite":
                            changed = self._apply_proactive_model_rewrite(current_for_model, model_judgement)
                            model_judgement["signature"] = self._planned_proactive_model_judge_signature(current_for_model)
                            expected_model_signature = _single_line(model_judgement.get("signature"), 80)
                            self._cache_proactive_model_judgement(current_for_model, model_judgement, now=_now_ts())
                            if not changed:
                                note = "模型人格判定要求改写,但未给出有效替换字段"
                                self._defer_or_replace_planned_impulse(
                                    current_for_model,
                                    now=_now_ts(),
                                    note=note,
                                    delay_minutes=(60, 150),
                                    block_current=False,
                                )
                                self._save_data_sync()
                                self._debug_tick_skip(user_id, note, prefix="延后")
                                continue
                            if changed:
                                self._mark_planned_candidate_status(
                                    current_for_model,
                                    "accepted",
                                    "模型人格判定改写计划: " + _single_line(model_judgement.get("reason"), 120),
                                )
                                logger.info(
                                    "[PrivateCompanion] 模型人格判定已改写主动计划: user=%s reason=%s",
                                    user_id,
                                    _single_line(model_judgement.get("reason"), 120),
                                )
                            user = dict(current_for_model)
                            self._save_data_sync()
                        elif model_decision == "defer":
                            note = "模型人格判定延后: " + _single_line(model_judgement.get("reason"), 120)
                            delay = _safe_int(model_judgement.get("delay_minutes"), 90, 20, 360)
                            self._cache_proactive_model_judgement(current_for_model, model_judgement, now=_now_ts())
                            replaced = self._defer_or_replace_planned_impulse(
                                current_for_model,
                                now=_now_ts(),
                                note=note,
                                delay_minutes=(delay, delay + 45),
                                block_current=False,
                            )
                            if not replaced and _safe_float(current_for_model.get("next_proactive_at"), 0) <= 0:
                                self._schedule_next_proactive(current_for_model, now=_now_ts(), delay_hours=(1.5, 4.0))
                            self._save_data_sync()
                            self._debug_tick_skip(user_id, note, prefix="延后")
                            continue
                        elif model_decision == "drop":
                            note = "模型人格判定丢弃: " + _single_line(model_judgement.get("reason"), 120)
                            self._cache_proactive_model_judgement(current_for_model, model_judgement, now=_now_ts())
                            self._mark_planned_candidate_status(current_for_model, "blocked", note)
                            self._clear_pending_proactive_plan(current_for_model)
                            self._schedule_next_proactive(current_for_model, now=_now_ts(), delay_hours=(2.0, 6.0))
                            self._save_data_sync()
                            self._debug_tick_skip(user_id, note, prefix="取消")
                            continue
                else:
                    async with self._data_lock:
                        current_for_model_cache = self._get_user(user_id)
                        current_signature = self._planned_proactive_model_judge_signature(current_for_model_cache)
                        judged_signature = _single_line(model_judgement.get("signature"), 80) or current_signature
                        if current_signature == judged_signature:
                            self._cache_proactive_model_judgement(current_for_model_cache, model_judgement, now=_now_ts())
                            self._save_data_sync()

            async with self._data_lock:
                current_for_mark = self._get_user(user_id)
                if (
                    not is_troubleshooting_for_send
                    and not due_timer_id
                    and expected_model_signature
                    and self._planned_proactive_model_judge_signature(current_for_mark) != expected_model_signature
                ):
                    self._debug_tick_skip(user_id, "模型判定后计划已变化,本轮重新检查", prefix="跳过")
                    continue
                if not self._user_enabled_for_proactive(str(user_id), current_for_mark):
                    if is_troubleshooting_for_send:
                        self._append_troubleshooting_proactive_step(current_for_mark, "到点执行", "error", "目标私聊对象已禁用")
                        self._record_troubleshooting_proactive_result(
                            user_id,
                            current_for_mark,
                            ok=False,
                            detail="临时主动任务到点，但目标私聊对象已禁用",
                            error="目标私聊对象已禁用",
                        )
                        self._restore_troubleshooting_proactive_plan(current_for_mark)
                    else:
                        self._clear_pending_proactive_plan(current_for_mark)
                    self._save_data_sync()
                    self._debug_tick_skip(user_id, "私聊对象未启用")
                    continue
                self._recover_stale_proactive_sending(current_for_mark)
                if current_for_mark.get("proactive_sending"):
                    if is_troubleshooting_for_send:
                        self._append_troubleshooting_proactive_step(current_for_mark, "到点执行", "error", "已有主动发送正在进行")
                        self._record_troubleshooting_proactive_result(
                            user_id,
                            current_for_mark,
                            ok=False,
                            detail="临时主动任务到点，但已有主动发送正在进行",
                            error="已有主动发送正在进行",
                        )
                        self._restore_troubleshooting_proactive_plan(current_for_mark)
                        self._save_data_sync()
                    self._debug_tick_skip(user_id, "主动发送仍在进行中")
                    continue
                current_reason = normalize_legacy_tag_text(current_for_mark.get("planned_proactive_reason"))
                if (
                    not is_troubleshooting_for_send
                    and not due_timer_id
                    and self._is_greeting_reason(current_reason)
                ):
                    suppressed_greetings = current_for_mark.get("greetings_suppressed_by_inbound", [])
                    if isinstance(suppressed_greetings, list) and current_reason in suppressed_greetings:
                        self._mark_planned_candidate_status(current_for_mark, "blocked", "用户在该问候窗口内已经活跃过")
                        self._clear_pending_proactive_plan(current_for_mark)
                        self._save_data_sync()
                        self._debug_tick_skip(user_id, "问候窗口已被用户互动占掉", prefix="取消")
                        continue
                    recent_user_at = self._latest_user_activity_ts(current_for_mark)
                    idle_limit = self._effective_user_greeting_idle_minutes(current_for_mark) * 60
                    if recent_user_at > 0 and _now_ts() - recent_user_at < idle_limit:
                        if self._inbound_satisfies_greeting(current_reason, now=recent_user_at):
                            self._mark_greeting_satisfied_by_inbound(current_for_mark, current_reason)
                            self._clear_pending_proactive_plan(current_for_mark)
                        else:
                            self._reschedule_greeting_within_window(current_for_mark, current_reason, now=_now_ts())
                        self._save_data_sync()
                        self._debug_tick_skip(user_id, "用户刚自然来聊,已取消或延后问候主动")
                        continue
                recent_chat_guard_reason = self._recent_chat_proactive_guard_reason(
                    current_for_mark,
                    now=_now_ts(),
                    planned_reason=current_reason,
                    planned_source=normalize_legacy_tag_text(current_for_mark.get("planned_proactive_source")),
                    due_timer_active=bool(due_timer_id),
                    is_troubleshooting=is_troubleshooting_for_send,
                )
                if recent_chat_guard_reason:
                    self._defer_proactive_for_recent_chat(
                        current_for_mark,
                        now=_now_ts(),
                        note=recent_chat_guard_reason,
                    )
                    self._save_data_sync()
                    logger.info(
                        "[PrivateCompanion] 刚聊完,延后本轮普通主动: user=%s reason=%s planned=%s/%s",
                        user_id,
                        _single_line(recent_chat_guard_reason, 120),
                        _single_line(current_reason, 40),
                        _single_line(current_for_mark.get("planned_proactive_action"), 24),
                    )
                    self._debug_tick_skip(user_id, recent_chat_guard_reason, prefix="延后")
                    continue
                current_for_mark["proactive_sending"] = True
                current_for_mark["proactive_sending_started_at"] = _now_ts()
                audit_id = self._append_proactive_audit(
                    user_id,
                    current_for_mark,
                    status="running",
                    note="排障临时主动消息链路已开始" if is_troubleshooting_for_send else "主动发送链路已开始",
                )
                if is_troubleshooting_for_send:
                    self._append_troubleshooting_proactive_step(current_for_mark, "到点执行", "ok", "主动循环已接手临时任务")
                    self._record_troubleshooting_proactive_result(
                        user_id,
                        current_for_mark,
                        ok=True,
                        detail="主动循环已接手，正在生成主动消息",
                        action=str(current_for_mark.get("planned_proactive_action") or "message"),
                        reason=normalize_legacy_tag_text(current_for_mark.get("planned_proactive_reason")) or "check_in",
                    )
                self._save_data_sync()

            planned_action_for_send = str(user.get("planned_proactive_action") or "message")
            planned_motive_for_send = _single_line(user.get("planned_proactive_motive"), 140)
            planned_topic_for_send = _single_line(user.get("planned_proactive_topic"), 80)
            planned_chain_for_send = (
                list(user.get("planned_event_chain") or [])
                if isinstance(user.get("planned_event_chain"), list)
                else []
            )
            self._ensure_private_user_umo(user_id, user)
            send_umo_for_send = _single_line(user.get("umo"), 180)
            friend_proactive_for_send = self._private_user_role(user) == "friend"
            if friend_proactive_for_send:
                planned_chain_for_send = []
            proactive_quote_message_id = self._planned_proactive_quote_message_id(user, send_umo_for_send)
            planned_opener_mode_for_send = str(user.get("planned_opener_mode") or "")
            planned_followup_kind_for_send = str(user.get("planned_followup_kind") or "")
            if not is_troubleshooting_for_send and normalize_legacy_tag_text(user.get("planned_proactive_reason")) == "activity_share":
                duplicate_block_remaining = self._activity_share_duplicate_block_remaining(user)
                if duplicate_block_remaining > 0:
                    note = _single_line(user.get("activity_share_duplicate_block_note"), 100) or "同一日常碎片刚刚已分享给其他私聊对象"
                    async with self._data_lock:
                        current_for_duplicate_cooldown = self._get_user(user_id)
                        current_for_duplicate_cooldown["proactive_sending"] = False
                        current_for_duplicate_cooldown["proactive_sending_started_at"] = 0
                        self._mark_planned_candidate_status(current_for_duplicate_cooldown, "blocked", note)
                        self._clear_pending_proactive_plan(current_for_duplicate_cooldown)
                        self._update_proactive_audit(audit_id, status="cancelled", note=f"活动分享去重冷却中: {note}")
                        self._schedule_next_proactive(current_for_duplicate_cooldown, now=_now_ts(), delay_hours=(2.0, 5.0))
                        self._save_data_sync()
                    logger.info(
                        "[PrivateCompanion] 活动分享去重冷却中,跳过本轮主动: user=%s remain=%.0fs note=%s",
                        user_id,
                        duplicate_block_remaining,
                        note,
                    )
                    self._debug_tick_skip(user_id, "活动分享去重冷却中", prefix="取消")
                    continue
            if self._action_has_photo_text(planned_action_for_send) and not self._photo_text_available(user):
                fallback_action = self._fallback_action_for_unavailable(planned_action_for_send, user)
                if fallback_action != planned_action_for_send:
                    logger.info(
                        "[PrivateCompanion] 主动发图能力不可用,发送前已降级: user=%s requested=%s fallback=%s",
                        user_id,
                        planned_action_for_send,
                        fallback_action,
                    )
                    planned_action_for_send = fallback_action
                    async with self._data_lock:
                        current_for_fallback = self._get_user(user_id)
                        current_for_fallback["planned_proactive_action"] = fallback_action
                        self._mark_planned_candidate_status(
                            current_for_fallback,
                            "accepted",
                            "photo_text 后端不可用,已降级为普通主动消息",
                        )
                        self._save_data_sync()
            load_defer_note = self._photo_text_load_defer_note(planned_action_for_send, force_refresh=True)
            if load_defer_note:
                async with self._data_lock:
                    current_for_defer = self._get_user(user_id)
                    self._defer_planned_photo_text_for_load(current_for_defer, now=_now_ts(), note=load_defer_note)
                    current_for_defer["proactive_sending"] = False
                    current_for_defer["proactive_sending_started_at"] = 0
                    self._update_proactive_audit(audit_id, status="deferred", note=load_defer_note)
                    self._save_data_sync()
                self._debug_tick_skip(user_id, load_defer_note, prefix="延后")
                continue
            group_share_block_reason = ""
            if normalize_legacy_tag_text(user.get("planned_proactive_reason")) == "group_share":
                async with self._data_lock:
                    current_for_group_check = self._get_user(user_id)
                    checker = getattr(self, "_group_share_send_block_reason", None)
                    if callable(checker):
                        group_share_block_reason = checker(user_id, current_for_group_check)
                    if group_share_block_reason:
                        current_for_group_check["proactive_sending"] = False
                        current_for_group_check["proactive_sending_started_at"] = 0
                        self._mark_planned_candidate_status(current_for_group_check, "blocked", group_share_block_reason)
                        self._clear_pending_proactive_plan(current_for_group_check)
                        current_for_group_check["group_share_context"] = {}
                        self._update_proactive_audit(audit_id, status="cancelled", note=group_share_block_reason)
                        self._save_data_sync()
                if group_share_block_reason:
                    logger.info(
                        "[PrivateCompanion] 群聊分享主动发送前复核取消: user=%s reason=%s",
                        user_id,
                        group_share_block_reason,
                    )
                    self._debug_tick_skip(user_id, group_share_block_reason, prefix="取消")
                    continue
            task_start_private_activity_at = self._latest_private_user_activity_ts(user)
            task_start_private_inbound_count = _safe_int(user.get("private_inbound_count"), 0)
            pending_send_retry = None if is_troubleshooting_for_send else self._pending_proactive_send_retry(user)
            if pending_send_retry:
                reason = _single_line(pending_send_retry.get("reason"), 40) or normalize_legacy_tag_text(user.get("planned_proactive_reason")) or "check_in"
                text = _single_line(pending_send_retry.get("text"), 1200)
                image_path = _single_line(pending_send_retry.get("image_path"), 260)
                extra_components = []
                action_summary = _single_line(pending_send_retry.get("action_summary"), 500)
                effective_action_for_send = _single_line(pending_send_retry.get("action"), 40) or planned_action_for_send or "message"
                logger.info(
                    "[PrivateCompanion] 复用待重发主动消息: user=%s retry=%s text=%s image=%s",
                    user_id,
                    _safe_int(pending_send_retry.get("retry_count"), 0, 0, 10),
                    _single_line(text, 100),
                    bool(image_path),
                )
            else:
                try:
                    reason, text, image_path, extra_components, action_summary, effective_action_for_send = await self._render_message(user)
                except Exception as e:
                    logger.warning("[PrivateCompanion] 主动消息生成失败: user=%s error=%s", user_id, _single_line(e, 160), exc_info=True)
                    async with self._data_lock:
                        current_after_render_failure = self._get_user(user_id)
                        current_after_render_failure["proactive_sending"] = False
                        current_after_render_failure["proactive_sending_started_at"] = 0
                        if is_troubleshooting_for_send:
                            self._append_troubleshooting_proactive_step(current_after_render_failure, "LLM 渲染", "error", f"生成失败: {_single_line(e, 120)}")
                            self._record_troubleshooting_proactive_result(
                                user_id,
                                current_after_render_failure,
                                ok=False,
                                detail="主动循环已触发，但 LLM 渲染失败",
                                error=f"生成失败: {_single_line(e, 160)}",
                            )
                            self._restore_troubleshooting_proactive_plan(current_after_render_failure)
                        else:
                            current_after_render_failure["next_proactive_at"] = 0
                            self._schedule_next_proactive(current_after_render_failure, now=_now_ts(), delay_hours=(1, 3))
                        self._update_proactive_audit(audit_id, status="failed", note=f"生成失败: {_single_line(e, 140)}")
                        self._save_data_sync()
                    continue
            if is_troubleshooting_for_send:
                async with self._data_lock:
                    current_after_render_ok = self._get_user(user_id)
                    self._append_troubleshooting_proactive_step(
                        current_after_render_ok,
                        "LLM 渲染",
                        "ok",
                        f"reason={reason or 'check_in'} / action={effective_action_for_send or planned_action_for_send or 'message'}",
                    )
                    self._record_troubleshooting_proactive_result(
                        user_id,
                        current_after_render_ok,
                        ok=True,
                        detail="主动消息已生成，准备发送前复核",
                        text=text,
                        action=effective_action_for_send or planned_action_for_send or "message",
                        reason=reason or "check_in",
                        extra_count=len(extra_components),
                    )
                    self._save_data_sync()
            review_candidate_text = text
            if not review_candidate_text and (image_path or extra_components):
                if image_path:
                    review_candidate_text = "（无文字，仅随主动消息发送图片）"
                elif extra_components:
                    review_candidate_text = f"（无文字，仅随主动消息发送 {len(extra_components)} 个附加组件）"
            if not pending_send_retry and review_candidate_text:
                try:
                    review_decision = await self._review_proactive_message_send_decision(
                        user,
                        review_candidate_text,
                        reason=reason or normalize_legacy_tag_text(user.get("planned_proactive_reason")),
                        action=effective_action_for_send or planned_action_for_send or "message",
                        motive=planned_motive_for_send,
                        topic=planned_topic_for_send,
                        action_summary=action_summary,
                        image_path=image_path,
                    )
                except Exception as exc:
                    review_mode = str(getattr(self, "response_review_mode", "severe_only") or "severe_only").strip().lower()
                    review_enabled = bool(getattr(self, "enable_response_self_review", True)) and review_mode != "local_only"
                    if review_enabled:
                        review_failure_signature = self._proactive_topic_signature(
                            " ".join(
                                _single_line(value, 240)
                                for value in (
                                    review_candidate_text,
                                    reason or normalize_legacy_tag_text(user.get("planned_proactive_reason")),
                                    effective_action_for_send or planned_action_for_send or "message",
                                    planned_motive_for_send,
                                    planned_topic_for_send,
                                )
                                if value
                            )
                        )
                        async with self._data_lock:
                            current_for_review_error = self._get_user(user_id)
                            failure_state = current_for_review_error.get("proactive_review_failure_backoff")
                            if not isinstance(failure_state, dict):
                                failure_state = {}
                            previous_count = (
                                _safe_int(failure_state.get("count"), 0, 0, 10)
                                if str(failure_state.get("signature") or "") == review_failure_signature
                                else 0
                            )
                            failure_count = previous_count + 1
                            current_for_review_error["proactive_review_failure_backoff"] = {
                                "signature": review_failure_signature,
                                "count": failure_count,
                                "last_error": _single_line(exc, 160),
                                "updated_at": _now_ts(),
                            }
                            self._save_data_sync()
                        if failure_count >= 3:
                            review_strength_getter = getattr(self, "_proactive_review_strength", None)
                            review_strength = review_strength_getter() if callable(review_strength_getter) else str(getattr(self, "proactive_review_strength", "lenient") or "lenient")
                            if review_strength == "strict":
                                logger.warning(
                                    "[PrivateCompanion] 主动消息发送前价值复核连续失败,严格模式放弃本条候选避免反复调用: count=%s error=%s",
                                    failure_count,
                                    _single_line(exc, 120),
                                )
                                review_decision = {"decision": "drop", "reason": "发送前价值复核连续失败，已放弃本条候选"}
                            else:
                                logger.warning(
                                    "[PrivateCompanion] 主动消息发送前价值复核连续失败,按%s强度放行原候选避免主动归零: count=%s error=%s",
                                    review_strength or "lenient",
                                    failure_count,
                                    _single_line(exc, 120),
                                )
                                review_decision = {"decision": "send", "reason": "发送前价值复核连续失败，已按当前强度放行"}
                        else:
                            delay_minutes = min(240, 45 * (2 ** max(0, failure_count - 1)))
                            logger.warning(
                                "[PrivateCompanion] 主动消息发送前价值复核失败,本轮延后重试: count=%s delay=%s error=%s",
                                failure_count,
                                delay_minutes,
                                _single_line(exc, 120),
                            )
                            review_decision = {
                                "decision": "defer",
                                "delay_minutes": delay_minutes,
                                "reason": f"发送前价值复核失败，稍后重试（第 {failure_count} 次）",
                            }
                    else:
                        logger.debug("[PrivateCompanion] 主动消息发送前本地复核失败,按原文继续: %s", _single_line(exc, 120))
                        review_decision = {"decision": "send"}
                decision = str(review_decision.get("decision") or "send").lower() if isinstance(review_decision, dict) else "send"
                if decision in {"send", "rewrite"}:
                    async with self._data_lock:
                        current_for_review_ok = self._get_user(user_id)
                        if isinstance(current_for_review_ok.get("proactive_review_failure_backoff"), dict):
                            current_for_review_ok["proactive_review_failure_backoff"] = {}
                            self._save_data_sync()
                if decision == "rewrite":
                    rewritten_text = str(review_decision.get("text") or "").strip()
                    if rewritten_text:
                        rewritten_text = _normalize_outbound_punctuation_flow(rewritten_text).strip()
                        original_text_before_rewrite = str(text or review_candidate_text or "").strip()
                        logger.info(
                            "[PrivateCompanion] 主动消息发送前已润色: user=%s before=%s after=%s",
                            user_id,
                            _single_line(original_text_before_rewrite, 100),
                            _single_line(rewritten_text, 100),
                        )
                        text = rewritten_text
                        async with self._data_lock:
                            self._update_proactive_audit(
                                audit_id,
                                status="running",
                                note="发送前价值复核轻改写",
                                text=text,
                                original_text=original_text_before_rewrite,
                                final_text=text,
                            )
                            if is_troubleshooting_for_send:
                                current_for_review_rewrite = self._get_user(user_id)
                                self._append_troubleshooting_proactive_step(
                                    current_for_review_rewrite,
                                    "发送前价值复核",
                                    "ok",
                                    "复核模型建议轻改写："
                                    f"由「{_single_line(original_text_before_rewrite, 70)}」"
                                    f"改为「{_single_line(text, 70)}」",
                                )
                                self._record_troubleshooting_proactive_result(
                                    user_id,
                                    current_for_review_rewrite,
                                    ok=True,
                                    detail="主动消息已通过发送前价值复核，复核模型建议轻改写",
                                    text=text or review_candidate_text,
                                    original_text=original_text_before_rewrite,
                                    final_text=text,
                                    action=effective_action_for_send or planned_action_for_send or "message",
                                    reason=reason or "check_in",
                                    extra_count=len(extra_components),
                                )
                            self._save_data_sync()
                elif decision in {"defer", "drop"}:
                    note = _single_line(review_decision.get("reason"), 120) or (
                        "发送前价值复核建议延后" if decision == "defer" else "发送前价值复核建议取消"
                    )
                    delay_minutes = _safe_int(review_decision.get("delay_minutes"), 45, 30, 90)
                    async with self._data_lock:
                        current_for_review = self._get_user(user_id)
                        current_for_review["proactive_sending"] = False
                        current_for_review["proactive_sending_started_at"] = 0
                        if is_troubleshooting_for_send:
                            self._append_troubleshooting_proactive_step(
                                current_for_review,
                                "发送前价值复核",
                                "warn" if decision == "defer" else "error",
                                note,
                            )
                            self._record_troubleshooting_proactive_result(
                                user_id,
                                current_for_review,
                                ok=False,
                                detail="主动消息已生成，但发送前价值复核未放行",
                                error=note,
                                text=text or review_candidate_text,
                                action=effective_action_for_send or planned_action_for_send or "message",
                                reason=reason or "check_in",
                                extra_count=len(extra_components),
                            )
                            self._restore_troubleshooting_proactive_plan(current_for_review)
                            self._update_proactive_audit(
                                audit_id,
                                status="deferred" if decision == "defer" else "cancelled",
                                note=note,
                                text=text or review_candidate_text,
                            )
                        elif decision == "defer":
                            current_for_review["next_proactive_at"] = _now_ts() + delay_minutes * 60
                            self._mark_planned_candidate_status(current_for_review, "deferred", note)
                            self._update_proactive_audit(audit_id, status="deferred", note=note, text=text or review_candidate_text)
                        else:
                            self._mark_planned_candidate_status(current_for_review, "blocked", note)
                            self._update_proactive_audit(audit_id, status="cancelled", note=note, text=text or review_candidate_text)
                            self._clear_pending_proactive_plan(current_for_review)
                            self._schedule_next_proactive(current_for_review, now=_now_ts(), delay_hours=(1.5, 4.0))
                        self._save_data_sync()
                    logger.info(
                        "[PrivateCompanion] 主动消息发送前价值复核%s: user=%s reason=%s text=%s",
                        "延后" if decision == "defer" else "取消",
                        user_id,
                        note,
                        _single_line(text, 120),
                    )
                    self._debug_tick_skip(user_id, note, prefix="延后" if decision == "defer" else "取消")
                    continue
            meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
            if callable(meta_leak_checker) and text and meta_leak_checker(text):
                note = "主动正文疑似工具循环/内部发送摘要泄漏"
                async with self._data_lock:
                    current_for_meta_leak = self._get_user(user_id)
                    current_for_meta_leak["proactive_sending"] = False
                    current_for_meta_leak["proactive_sending_started_at"] = 0
                    self._mark_planned_candidate_status(current_for_meta_leak, "blocked", note)
                    self._update_proactive_audit(audit_id, status="cancelled", note=note, text=text)
                    self._clear_pending_proactive_plan(current_for_meta_leak)
                    self._schedule_next_proactive(current_for_meta_leak, now=_now_ts(), delay_hours=(1.5, 4.0))
                    self._save_data_sync()
                logger.warning(
                    "[PrivateCompanion] 主动消息发送前硬拦截元叙述泄漏: user=%s text=%s",
                    user_id,
                    _single_line(text, 180),
                )
                self._debug_tick_skip(user_id, note, prefix="取消")
                continue
            placeholder_cleaner = getattr(self, "_sanitize_orphan_tts_placeholders", None)
            if callable(placeholder_cleaner):
                cleaned_text = placeholder_cleaner(text)
                if cleaned_text != text:
                    logger.warning(
                        "[PrivateCompanion] 主动消息清理到孤儿 TTS 占位符: user=%s before=%s after=%s",
                        user_id,
                        _single_line(text, 120),
                        _single_line(cleaned_text, 120),
                    )
                    text = cleaned_text
            if not is_troubleshooting_for_send and reason == "activity_share":
                async with self._data_lock:
                    current_for_dedupe = self._get_user(user_id)
                    duplicate_note = self._activity_share_recently_sent_elsewhere(
                        user_id,
                        current_for_dedupe,
                        text=text,
                        action_summary=action_summary,
                    )
                    if duplicate_note:
                        self._block_duplicate_activity_share_for_user(
                            current_for_dedupe,
                            duplicate_note=duplicate_note,
                            seconds=90 * 60,
                        )
                        removed_can_do = self._remove_can_do_targets(
                            [
                                current_for_dedupe.get("planned_proactive_topic"),
                                current_for_dedupe.get("planned_proactive_motive"),
                                action_summary,
                                text,
                                duplicate_note,
                            ]
                        )
                        current_for_dedupe["proactive_sending"] = False
                        current_for_dedupe["proactive_sending_started_at"] = 0
                        self._mark_planned_candidate_status(current_for_dedupe, "blocked", "同一日常碎片刚刚已分享给其他私聊对象")
                        self._clear_pending_proactive_plan(current_for_dedupe)
                        audit_note = f"跨用户活动分享去重: {duplicate_note}"
                        if removed_can_do:
                            audit_note = f"{audit_note}；已移除候选碎片 {len(removed_can_do)} 条"
                        self._update_proactive_audit(audit_id, status="cancelled", note=audit_note)
                        self._schedule_next_proactive(current_for_dedupe, now=_now_ts(), delay_hours=(2.0, 5.0))
                        self._save_data_sync()
                if duplicate_note:
                    logger.info(
                        "[PrivateCompanion] 取消重复活动分享: user=%s duplicate=%s",
                        user_id,
                        _single_line(duplicate_note, 100),
                    )
                    self._debug_tick_skip(user_id, "同一日常碎片刚刚已分享给其他私聊对象", prefix="取消")
                    continue
            time_mismatch_reason = ""
            checker = getattr(self, "_proactive_time_mismatch_reason", None)
            if callable(checker):
                try:
                    time_mismatch_reason = checker(
                        text,
                        reason=reason,
                        action=effective_action_for_send or planned_action_for_send or "message",
                    )
                except Exception as exc:
                    logger.debug("[PrivateCompanion] 主动消息时间一致性复核失败: %s", _single_line(exc, 120))
                    time_mismatch_reason = ""
            if time_mismatch_reason:
                logger.info(
                    "[PrivateCompanion] 主动消息时间不一致,已取消发送: user=%s reason=%s",
                    user_id,
                    _single_line(time_mismatch_reason, 160),
                )
                async with self._data_lock:
                    current_for_time_guard = self._get_user(user_id)
                    current_for_time_guard["proactive_sending"] = False
                    current_for_time_guard["proactive_sending_started_at"] = 0
                    if is_troubleshooting_for_send:
                        self._append_troubleshooting_proactive_step(current_for_time_guard, "时间复核", "error", time_mismatch_reason)
                        self._record_troubleshooting_proactive_result(
                            user_id,
                            current_for_time_guard,
                            ok=False,
                            detail="主动消息已生成，但发送前时间一致性复核未通过",
                            error=time_mismatch_reason,
                            text=text,
                            action=effective_action_for_send or planned_action_for_send or "message",
                            reason=reason or "check_in",
                            extra_count=len(extra_components),
                        )
                        self._restore_troubleshooting_proactive_plan(current_for_time_guard)
                    else:
                        self._mark_planned_candidate_status(current_for_time_guard, "blocked", time_mismatch_reason)
                        self._clear_pending_proactive_plan(current_for_time_guard)
                        self._schedule_next_proactive(current_for_time_guard, now=_now_ts(), delay_hours=(1.5, 4.0))
                    self._update_proactive_audit(audit_id, status="cancelled", note=time_mismatch_reason)
                    self._save_data_sync()
                self._debug_tick_skip(user_id, "主动消息时间不一致", prefix="取消")
                continue
            if not is_troubleshooting_for_send and (effective_action_for_send or planned_action_for_send or "message") == "message":
                async with self._data_lock:
                    current_for_similarity_guard = self._get_user(user_id)
                    similar_note = self._recent_proactive_text_duplicate_reason(
                        current_for_similarity_guard,
                        text=text,
                        topic=current_for_similarity_guard.get("planned_proactive_topic"),
                        motive=planned_motive_for_send,
                        now=_now_ts(),
                    )
                    if similar_note:
                        current_for_similarity_guard["proactive_sending"] = False
                        current_for_similarity_guard["proactive_sending_started_at"] = 0
                        self._mark_planned_candidate_status(current_for_similarity_guard, "blocked", similar_note)
                        self._clear_pending_proactive_plan(current_for_similarity_guard)
                        self._schedule_next_proactive(current_for_similarity_guard, now=_now_ts(), delay_hours=(2.0, 5.0))
                        self._update_proactive_audit(audit_id, status="cancelled", note=similar_note, text=text)
                        self._save_data_sync()
                if similar_note:
                    logger.info(
                        "[PrivateCompanion] 主动消息正文近似重复,已取消: user=%s reason=%s text=%s",
                        user_id,
                        _single_line(similar_note, 120),
                        _single_line(text, 120),
                    )
                    self._debug_tick_skip(user_id, similar_note, prefix="取消")
                    continue
            if not is_troubleshooting_for_send and (effective_action_for_send or planned_action_for_send or "message") == "message":
                async with self._data_lock:
                    current_for_greeting_text = self._get_user(user_id)
                    textual_greeting_note = self._textual_greeting_duplicate_reason(
                        current_for_greeting_text,
                        text,
                        now=_now_ts(),
                    )
                    if textual_greeting_note:
                        current_for_greeting_text["proactive_sending"] = False
                        current_for_greeting_text["proactive_sending_started_at"] = 0
                        self._mark_planned_candidate_status(current_for_greeting_text, "blocked", textual_greeting_note)
                        self._clear_pending_proactive_plan(current_for_greeting_text)
                        self._schedule_next_proactive(current_for_greeting_text, now=_now_ts(), delay_hours=(2.0, 5.0))
                        self._update_proactive_audit(audit_id, status="cancelled", note=textual_greeting_note, text=text)
                        self._save_data_sync()
                if textual_greeting_note:
                    logger.info(
                        "[PrivateCompanion] 主动消息正文命中重复问候,已取消: user=%s reason=%s text=%s",
                        user_id,
                        _single_line(textual_greeting_note, 120),
                        _single_line(text, 120),
                    )
                    self._debug_tick_skip(user_id, textual_greeting_note, prefix="取消")
                    continue
            if is_troubleshooting_for_send:
                async with self._data_lock:
                    current_after_time_guard = self._get_user(user_id)
                    self._append_troubleshooting_proactive_step(current_after_time_guard, "时间复核", "ok", "未发现明显错时内容")
                    self._record_troubleshooting_proactive_result(
                        user_id,
                        current_after_time_guard,
                        ok=True,
                        detail="发送前复核通过，准备发送",
                        text=text,
                        action=effective_action_for_send or planned_action_for_send or "message",
                        reason=reason or "check_in",
                        extra_count=len(extra_components),
                    )
                    self._save_data_sync()
            async with self._data_lock:
                current_after_render = self._get_user(user_id)
                has_new_user_message = (
                    self._latest_private_user_activity_ts(current_after_render) > task_start_private_activity_at
                    or _safe_int(current_after_render.get("private_inbound_count"), 0) > task_start_private_inbound_count
                )
            if has_new_user_message:
                if is_troubleshooting_for_send:
                    logger.info(
                        "[PrivateCompanion] 排障临时主动检测到生成期间有新消息,继续发送以验证链路: %s",
                        user_id,
                    )
                    async with self._data_lock:
                        current_for_warn = self._get_user(user_id)
                        self._append_troubleshooting_proactive_step(
                            current_for_warn,
                            "并发保护",
                            "warn",
                            "生成期间检测到新消息；排障测试继续发送以验证链路",
                        )
                        self._record_troubleshooting_proactive_result(
                            user_id,
                            current_for_warn,
                            ok=True,
                            detail="生成期间检测到新消息；排障测试继续发送以验证链路",
                            text=text,
                            action=effective_action_for_send or planned_action_for_send or "message",
                            reason=reason or "check_in",
                            extra_count=len(extra_components),
                        )
                        self._save_data_sync()
                else:
                    logger.info(
                        "[PrivateCompanion] 用户在主动消息生成期间已有新消息,丢弃本次主动发送: %s",
                        user_id,
                    )
                    async with self._data_lock:
                        current_for_clear = self._get_user(user_id)
                        current_for_clear["proactive_sending"] = False
                        current_for_clear["proactive_sending_started_at"] = 0
                        self._update_proactive_audit(audit_id, status="cancelled", note="用户在生成期间发来新消息,已取消本次主动")
                        self._save_data_sync()
                    continue
            async with self._data_lock:
                current_for_recent_chat = self._get_user(user_id)
                recent_chat_guard_reason = self._recent_chat_proactive_guard_reason(
                    current_for_recent_chat,
                    now=_now_ts(),
                    planned_reason=reason or normalize_legacy_tag_text(user.get("planned_proactive_reason")),
                    planned_source=normalize_legacy_tag_text(current_for_recent_chat.get("planned_proactive_source")),
                    due_timer_active=bool(due_timer_id),
                    is_troubleshooting=is_troubleshooting_for_send,
                )
                if recent_chat_guard_reason:
                    current_for_recent_chat["proactive_sending"] = False
                    current_for_recent_chat["proactive_sending_started_at"] = 0
                    if is_troubleshooting_for_send:
                        self._append_troubleshooting_proactive_step(current_for_recent_chat, "发送前复核", "error", recent_chat_guard_reason)
                        self._record_troubleshooting_proactive_result(
                            user_id,
                            current_for_recent_chat,
                            ok=False,
                            detail="主动消息已生成，但发送前发现用户刚聊过，已取消",
                            error=recent_chat_guard_reason,
                            text=text,
                            action=effective_action_for_send or planned_action_for_send or "message",
                            reason=reason or "check_in",
                            extra_count=len(extra_components),
                        )
                        self._restore_troubleshooting_proactive_plan(current_for_recent_chat)
                    else:
                        self._defer_proactive_for_recent_chat(
                            current_for_recent_chat,
                            now=_now_ts(),
                            note=recent_chat_guard_reason,
                        )
                    self._update_proactive_audit(audit_id, status="deferred", note=recent_chat_guard_reason)
                    self._save_data_sync()
            if recent_chat_guard_reason:
                logger.info(
                    "[PrivateCompanion] 发送前发现刚聊完,延后普通主动: user=%s reason=%s",
                    user_id,
                    _single_line(recent_chat_guard_reason, 120),
                )
                self._debug_tick_skip(user_id, recent_chat_guard_reason, prefix="延后")
                continue
            if text and self._is_proactive_delivery_receipt_text(text):
                note = "主动正文是工具/执行状态回执，已取消发送"
                logger.warning(
                    "[PrivateCompanion] 主动消息发送前拦截执行回执: user=%s text=%s",
                    user_id,
                    _single_line(text, 160),
                )
                async with self._data_lock:
                    current = self._get_user(user_id)
                    current["proactive_sending"] = False
                    current["proactive_sending_started_at"] = 0
                    if is_troubleshooting_for_send:
                        self._append_troubleshooting_proactive_step(current, "内容检查", "error", note)
                        self._record_troubleshooting_proactive_result(
                            user_id,
                            current,
                            ok=False,
                            detail="主动消息已生成，但正文是工具/执行状态回执",
                            error=note,
                            text=text,
                            action=effective_action_for_send or planned_action_for_send or "message",
                            reason=reason or "check_in",
                            extra_count=len(extra_components),
                        )
                        self._restore_troubleshooting_proactive_plan(current)
                    else:
                        self._mark_planned_candidate_status(current, "dropped", note)
                        self._clear_pending_proactive_plan(current)
                        self._schedule_next_proactive(current, now=_now_ts(), delay_hours=(2, 8))
                    self._update_proactive_audit(audit_id, status="dropped", note=note, text=text)
                    self._save_data_sync()
                self._debug_tick_skip(user_id, note, prefix="放弃")
                continue
            if not text and not image_path and not extra_components:
                async with self._data_lock:
                    current = self._get_user(user_id)
                    current["proactive_sending"] = False
                    current["proactive_sending_started_at"] = 0
                    if is_troubleshooting_for_send:
                        self._append_troubleshooting_proactive_step(current, "内容检查", "error", "主动行为没有产出可发送内容")
                        self._record_troubleshooting_proactive_result(
                            user_id,
                            current,
                            ok=False,
                            detail="主动行为没有产出可发送内容",
                            error="主动消息渲染为空",
                            action=effective_action_for_send or planned_action_for_send or "message",
                            reason=reason or "check_in",
                        )
                        self._restore_troubleshooting_proactive_plan(current)
                    elif self._simulation_active(current):
                        self._consume_simulation_event(current)
                    else:
                        self._mark_planned_candidate_status(current, "dropped", "主动行为失败或不适合发送")
                        self._clear_pending_proactive_plan(current)
                        self._schedule_next_proactive(current, now=_now_ts(), delay_hours=(2, 8))
                    self._update_proactive_audit(audit_id, status="dropped", note="主动行为失败或不适合发送")
                    self._save_data_sync()
                self._debug_tick_skip(user_id, "主动行为失败或不适合发送", prefix="放弃")
                continue
            try:
                reason_label = _REASON_TEXT.get(reason, reason or "check_in")
                target_name = _single_line(user.get("nickname") or self.default_nickname, 24)
                reason_label = reason_label.replace("{name}", target_name)
                reason_detail = "；".join(
                    item
                    for item in (
                        f"话题={planned_topic_for_send}" if planned_topic_for_send else "",
                        f"动机={planned_motive_for_send}" if planned_motive_for_send else "",
                    )
                    if item
                )
                logger.info(
                    "[PrivateCompanion] 准备主动发送给 %s: reason=%s(%s) action=%s quote=%s umo=%s text=%s image=%s extra=%s%s",
                    user_id,
                    reason,
                    reason_label,
                    effective_action_for_send or planned_action_for_send or "message",
                    bool(proactive_quote_message_id),
                    send_umo_for_send,
                    _single_line(text, 120),
                    bool(image_path),
                    len(extra_components),
                    f" detail={reason_detail}" if reason_detail else "",
                )
                await self._send_proactive_message_chain(
                    send_umo_for_send,
                    text,
                    image_path,
                    extra_components=extra_components,
                    quote_message_id=proactive_quote_message_id,
                    disable_segmenting=reason == "creative_share" or friend_proactive_for_send,
                )
                if is_troubleshooting_for_send:
                    async with self._data_lock:
                        current_after_send = self._get_user(user_id)
                        self._append_troubleshooting_proactive_step(current_after_send, "主动发送", "ok", "已调用 AstrBot 主动发送接口")
                        self._record_troubleshooting_proactive_result(
                            user_id,
                            current_after_send,
                            ok=True,
                            detail="主动消息已发送，准备写入会话历史",
                            text=text,
                            action=effective_action_for_send or planned_action_for_send or "message",
                            reason=reason or "check_in",
                            extra_count=len(extra_components),
                        )
                        self._save_data_sync()
                logger.info(
                    "[PrivateCompanion] 主动发送完成: user=%s reason=%s action=%s",
                    user_id,
                    reason,
                    planned_action_for_send or "message",
                )
                await self._archive_proactive_message_to_conversation(
                    user=user,
                    user_prompt=self._build_proactive_archive_user_prompt(
                        reason=reason,
                        action=effective_action_for_send or planned_action_for_send or "message",
                        motive=planned_motive_for_send,
                        action_summary=action_summary,
                    ),
                    assistant_response=self._build_proactive_archive_assistant_text(
                        text=text,
                        image_path=image_path,
                        extra_components=extra_components,
                        action_summary=action_summary,
                    ),
                )
                if is_troubleshooting_for_send:
                    async with self._data_lock:
                        current_after_archive = self._get_user(user_id)
                        self._append_troubleshooting_proactive_step(current_after_archive, "历史归档", "ok", "已调用 AstrBot 会话历史写入")
                        self._record_troubleshooting_proactive_result(
                            user_id,
                            current_after_archive,
                            ok=True,
                            detail="已完成排障临时主动消息发送与归档调用",
                            text=text,
                            action=effective_action_for_send or planned_action_for_send or "message",
                            reason=reason or "check_in",
                            extra_count=len(extra_components),
                        )
                        self._save_data_sync()
            except Exception as e:
                formatter = getattr(self, "_format_send_exception", None)
                error_text = formatter(e) if callable(formatter) else (_single_line(str(e), 180) or repr(e))
                logger.warning("[PrivateCompanion] 发送给 %s 失败: %s", user_id, error_text)
                async with self._data_lock:
                    current_after_failure = self._get_user(user_id)
                    if is_troubleshooting_for_send:
                        self._append_troubleshooting_proactive_step(current_after_failure, "主动发送", "error", f"发送失败: {_single_line(error_text, 120)}")
                        self._record_troubleshooting_proactive_result(
                            user_id,
                            current_after_failure,
                            ok=False,
                            detail="主动消息已生成，但发送失败",
                            error=f"发送失败: {_single_line(error_text, 160)}",
                            text=text,
                            action=effective_action_for_send or planned_action_for_send or "message",
                            reason=reason or "check_in",
                            extra_count=len(extra_components),
                        )
                        self._restore_troubleshooting_proactive_plan(current_after_failure)
                    else:
                        planned_snapshot = self._planned_proactive_status_snapshot(current_after_failure)
                        retry_note = self._store_or_advance_proactive_send_retry(
                            current_after_failure,
                            text=text,
                            image_path=image_path,
                            extra_components=extra_components,
                            reason=reason or "check_in",
                            action=effective_action_for_send or planned_action_for_send or "message",
                            action_summary=action_summary,
                            error_text=error_text,
                            now=_now_ts(),
                        )
                        retry_status = "failed" if "已放弃复用" in retry_note else "deferred"
                        self._mark_planned_candidate_status(
                            current_after_failure,
                            retry_status,
                            retry_note,
                            planned_snapshot=planned_snapshot,
                        )
                    self._update_proactive_audit(audit_id, status="failed", note=f"发送失败: {_single_line(error_text, 140)}")
                    self._save_data_sync()
                continue
            finally:
                async with self._data_lock:
                    current_for_clear = self._get_user(user_id)
                    current_for_clear["proactive_sending"] = False
                    current_for_clear["proactive_sending_started_at"] = 0
                    self._save_data_sync()

            memory_companion_proactive_payload: dict[str, Any] = {}
            async with self._data_lock:
                current = self._get_user(user_id)
                simulation_active = self._simulation_active(current)
                self._reset_daily_counter_if_needed(current)
                current["last_sent"] = _now_ts()
                if send_umo_for_send:
                    current["umo"] = send_umo_for_send
                visible_text = self._visible_text_without_tts_reading(text, limit=500)
                current["last_companion_message"] = _single_line(visible_text, 500)
                current["last_proactive_message"] = _single_line(visible_text, 500)
                current["last_proactive_sent_at"] = current["last_sent"]
                current["last_companion_message_at"] = current["last_sent"]
                current["last_proactive_reason"] = reason
                current["last_proactive_action"] = effective_action_for_send or planned_action_for_send or "message"
                current["last_proactive_behavior_summary"] = action_summary
                current["last_proactive_motive"] = planned_motive_for_send
                self._clear_pending_proactive_send_retry(current)
                food_prompt_hint = " ".join(
                    _single_line(value, 120)
                    for value in (
                        planned_motive_for_send,
                        current.get("planned_proactive_topic"),
                        normalize_legacy_tag_text(current.get("planned_proactive_reason")),
                    )
                )
                if any(token in food_prompt_hint for token in ("吃什么", "吃点", "饭", "饭点", "嘴馋", "饿", "吃的")):
                    current["last_food_prompt_at"] = current["last_sent"]
                self._remember_proactive_topic(
                    current,
                    text=visible_text or text,
                    topic=current.get("planned_proactive_topic"),
                    motive=planned_motive_for_send,
                )
                if reason == "activity_share":
                    self._remember_global_activity_share(
                        user_id,
                        current,
                        text=visible_text or text,
                        action_summary=action_summary,
                    )
                if reason == "group_share":
                    sidecar_checker = getattr(self, "_group_share_text_has_life_sidecar", None)
                    if callable(sidecar_checker) and sidecar_checker(visible_text or text):
                        current["last_group_share_life_sidecar_at"] = current["last_sent"]
                self._mark_planned_candidate_status(current, "sent", "已发送")
                self._update_proactive_audit(
                    audit_id,
                    status="sent",
                    note="排障临时主动消息已发送" if is_troubleshooting_for_send else "已真实发送",
                    text=visible_text or text,
                    image_path=image_path,
                    extra_count=len(extra_components),
                    action=current["last_proactive_action"],
                    reason="troubleshooting_test" if is_troubleshooting_for_send else reason,
                )
                if is_troubleshooting_for_send:
                    self._record_troubleshooting_proactive_result(
                        user_id,
                        current,
                        ok=True,
                        detail="已完成排障临时主动消息发送与归档调用，原主动计划已恢复",
                        text=visible_text or text,
                        action=current["last_proactive_action"],
                        reason=reason or "check_in",
                        extra_count=len(extra_components),
                    )
                    self._restore_troubleshooting_proactive_plan(current)
                    self._save_data_sync()
                    continue
                self._note_proactive_daypart_sent(current, current["last_sent"])
                opener_mode = planned_opener_mode_for_send
                followup_kind = planned_followup_kind_for_send
                if self._private_user_role(current) == "friend":
                    current["pending_followup_event"] = {}
                    current["suspended_proactive"] = {}
                elif opener_mode == "name_only":
                    current["suspended_proactive"] = self._build_suspended_proactive_payload(
                        opener_text=text,
                        reason=reason,
                        action=current["last_proactive_action"],
                        motive=current["last_proactive_motive"],
                        action_summary=action_summary,
                        chain=planned_chain_for_send,
                    )
                elif followup_kind == "suspended_opener":
                    suspended = current.get("suspended_proactive")
                    if isinstance(suspended, dict) and suspended.get("active"):
                        suspended["complaint_sent"] = True
                        second = suspended.get("second_followup")
                        if isinstance(second, dict) and second:
                            after_minutes = _safe_int(second.get("after_minutes"), 45, 0, 240)
                            second_reason = _single_line(second.get("reason"), 40) or "morning_greeting"
                            if second_reason == "morning_greeting":
                                after_minutes = max(after_minutes, 90)
                            current["pending_followup_event"] = {
                                "date": _today_key(),
                                "window": self._window_from_delay_minutes(after_minutes, width_minutes=18),
                                "reason": second_reason,
                                "action": "message",
                                "why": "前一条早晨试探后还差个具体点,如果还想续,就把那一点补上。",
                                "topic": _single_line(second.get("topic"), 80) or "早安余韵",
                                "motive": self._normalize_internal_motive_text(_single_line(second.get("motive"), 100)),
                                "scene": "早晨那句试探之后又过了一阵",
                                "tone": _single_line(second.get("tone"), 30) or "克制一点,把重点补上",
                                "impulse": "早晨那句还差个重点,想补完整",
                                "_scheduled_ts": _now_ts() + after_minutes * 60,
                                "_cancel_on_inbound": True,
                            }
                elif followup_kind == "chain_followup":
                    next_chain_followup = self._build_followup_event_from_chain(
                        planned_chain_for_send,
                        origin_reason=reason,
                        origin_action=current["last_proactive_action"],
                        now_ts=_now_ts(),
                    )
                    if isinstance(next_chain_followup, dict):
                        current["pending_followup_event"] = next_chain_followup
                elif planned_chain_for_send and not current.get("pending_followup_event"):
                    next_chain_followup = self._build_followup_event_from_chain(
                        planned_chain_for_send,
                        origin_reason=reason,
                        origin_action=current["last_proactive_action"],
                        now_ts=_now_ts(),
                    )
                    if isinstance(next_chain_followup, dict):
                        current["pending_followup_event"] = next_chain_followup
                if reason == "group_share":
                    current["group_share_context"] = {}
                if reason == "bili_video_share":
                    current["bilibili_video_context"] = {}
                if reason == "news_share":
                    current["news_context"] = {}
                if reason == "web_exploration_share":
                    current["web_exploration_context"] = {}
                if reason == "jm_cosmos_recommendation_request":
                    current["jm_cosmos_recommendation_context"] = {}
                if reason == "creative_share":
                    current["creative_share_context"] = {}
                if simulation_active:
                    self._consume_simulation_event(current)
                else:
                    current["sent_today"] = _safe_int(current.get("sent_today"), 0) + 1
                    current["proactive_sent_count"] = _safe_int(current.get("proactive_sent_count"), 0) + 1
                    current["ignored_streak"] = _safe_int(current.get("ignored_streak"), 0) + 1
                    current["awaiting_reply_since"] = _now_ts()
                    self._note_action_sent(
                        current,
                        current["last_proactive_action"],
                        reason=reason,
                        text=text,
                        motive=planned_motive_for_send,
                        action_summary=action_summary,
                    )
                    existing_followup = current.get("pending_followup_event")
                    if self._private_user_role(current) == "friend":
                        current["pending_followup_event"] = {}
                    elif isinstance(existing_followup, dict) and existing_followup:
                        current["pending_followup_event"] = existing_followup
                    elif followup_kind in {"suspended_opener", "chain_followup"} or opener_mode == "name_only":
                        current["pending_followup_event"] = {}
                    else:
                        current["pending_followup_event"] = self._maybe_make_unanswered_screen_peek_event(
                            current,
                            reason,
                            current["last_proactive_action"],
                        ) or self._maybe_make_followup_event(
                            current,
                            reason,
                            current["last_proactive_action"],
                        ) or {}
                    if self._is_greeting_reason(reason):
                        self._reset_daily_counter_if_needed(current)
                        sent_greetings = current.setdefault("greetings_sent", [])
                        if not isinstance(sent_greetings, list):
                            sent_greetings = []
                            current["greetings_sent"] = sent_greetings
                        if reason not in sent_greetings:
                            sent_greetings.append(reason)
                    self._mark_textual_greeting_sent(current, visible_text or text, sent_at=current["last_sent"])
                    self._clear_llm_timer_event(current, event_id=due_timer_id)
                    next_timer = self._get_active_llm_timer(current)
                    if (
                        isinstance(next_timer, dict)
                        and self._llm_timer_can_use_internal_scheduler(next_timer)
                        and _safe_float(next_timer.get("scheduled_ts"), 0) > _now_ts()
                    ):
                        current["next_proactive_at"] = _safe_float(next_timer.get("scheduled_ts"), 0)
                        current["planned_proactive_reason"] = normalize_legacy_tag_text(next_timer.get("reason")) or "check_in"
                        current["planned_proactive_action"] = normalize_legacy_tag_text(next_timer.get("action")) or "message"
                        current["planned_proactive_source"] = "timer"
                        current["planned_proactive_motive"] = _single_line(next_timer.get("motive"), 140)
                        current["planned_proactive_topic"] = _single_line(next_timer.get("topic"), 60)
                        current["planned_proactive_impulse_id"] = ""
                        current["planned_proactive_window_start_at"] = current["next_proactive_at"]
                        active_span, grace_span = self._proactive_impulse_default_window_seconds(current["planned_proactive_reason"])
                        current["planned_proactive_best_until_at"] = current["next_proactive_at"] + active_span
                        current["planned_proactive_expire_at"] = current["next_proactive_at"] + active_span + grace_span
                        semantics = self._planned_proactive_semantics(current)
                        current["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
                        current["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
                        current["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
                        current["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
                        self._set_planned_proactive_trigger(
                            current,
                            message_id=_single_line(next_timer.get("trigger_message_id"), 120),
                            umo=_single_line(next_timer.get("trigger_umo"), 160),
                            created_at=_safe_float(next_timer.get("trigger_ts"), 0),
                        )
                        current["planned_event_chain"] = [] if self._private_user_role(current) == "friend" else (
                            list(next_timer.get("chain") or []) if isinstance(next_timer.get("chain"), list) else []
                        )
                        current["planned_opener_mode"] = ""
                        current["planned_followup_kind"] = ""
                        current["planned_proactive_quota_exempt"] = False
                    else:
                        self._clear_pending_proactive_plan(current)
                        schedule_now = _now_ts()
                        next_delay = self._friend_proactive_spread_delay_hours(current, now=schedule_now)
                        self._schedule_next_proactive(current, now=schedule_now, delay_hours=next_delay)
                self._save_data_sync()
                current_snapshot = dict(current)
                if not simulation_active and visible_text:
                    memory_companion_proactive_payload = {
                        "user": current_snapshot,
                        "user_id": user_id,
                        "text": visible_text,
                        "reason": reason,
                        "action": current.get("last_proactive_action") or effective_action_for_send or planned_action_for_send or "message",
                        "motive": planned_motive_for_send,
                        "action_summary": action_summary,
                        "image_path": image_path,
                        "extra_count": len(extra_components),
                    }
            if memory_companion_proactive_payload:
                await self._memory_companion_record_proactive_message(**memory_companion_proactive_payload)
            asyncio.create_task(self._refresh_persona_relationship(user_id, current_snapshot))

        await self._run_proactive_maintenance_tasks()
        async with self._data_lock:
            runtime = self.data.setdefault("proactive_runtime", {})
            if isinstance(runtime, dict):
                runtime["last_tick_finished_at"] = _now_ts()
