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

from .conversation_prompt_section import (
    PromptRenderMode,
    PromptSection,
    prompt_section,
    render_prompt_sections,
)

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
from .daily_state_plan import DailyStatePlanMixin
from .daily_state_state import DailyStateStateMixin
from .daily_state_detail import DailyStateDetailMixin
from .daily_state_sanitize_util import DailyStateSanitizeUtilMixin
from .daily_state_proactive import DailyStateProactiveMixin
from .daily_state_diary import DailyStateDiaryMixin
from .daily_state_meal import DailyStateMealMixin
from .daily_state_sleep import DailyStateSleepMixin
from .daily_state_skill_growth import DailyStateSkillGrowthMixin
from .daily_state_context_snapshot import DailyStateContextSnapshotMixin
from .daily_state_timer import DailyStateTimerMixin
from .daily_state_weather import DailyStateWeatherMixin
from .memo_notes import memo_note_due_state, memo_note_sort_key, normalize_memo_note
from .agenda_contracts import normalize_plan_item
from .planning import (
    build_daily_plan_prompt,
    build_daily_plan_prompt_section,
    build_detail_enhancement_prompt,
    build_detail_enhancement_prompt_section,
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


class DailyStateMixin(DailyStateTickMixin, DailyStateWeatherMixin, DailyStateTimerMixin, DailyStateContextSnapshotMixin, DailyStateSkillGrowthMixin, DailyStateSleepMixin, DailyStateMealMixin, DailyStateDiaryMixin, DailyStateProactiveMixin, DailyStateSanitizeUtilMixin, DailyStateDetailMixin, DailyStateStateMixin, DailyStatePlanMixin):
    """日程、状态、天气、日记、技能成长和计时器"""

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
                    deleted_sections = set(self._cleanup_expired_conditions() or ())
                    self._ensure_time_based_hunger_condition()
                    state = self._compose_state_from_conditions(weather)
                    existing_state = self.data.get("daily_state")
                    if (not isinstance(existing_state, dict) or existing_state != state) or deleted_sections:
                        self.data["daily_state"] = state
                        save_sections = {
                            "daily_state",
                            "state_conditions",
                            "body_cycle_state",
                        } - set(deleted_sections)
                        if "body_cycle_state" in self.data:
                            deleted_sections.discard("body_cycle_state")
                            save_sections.add("body_cycle_state")
                        else:
                            save_sections.discard("body_cycle_state")
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
                deleted_sections = set(self._cleanup_expired_conditions() or ())
                self._ensure_time_based_hunger_condition()
                state = self._compose_state_from_conditions(weather)
                self.data["daily_state"] = state
                save_sections = {
                    "daily_state",
                    "state_conditions",
                    "body_cycle_state",
                    "hunger_window_attempts",
                } - set(deleted_sections)
                if "body_cycle_state" in self.data:
                    deleted_sections.discard("body_cycle_state")
                    save_sections.add("body_cycle_state")
                else:
                    save_sections.discard("body_cycle_state")
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
                deleted_sections = set(self._cleanup_expired_conditions() or ())
            else:
                deleted_sections = set(self._cleanup_expired_conditions() or ())
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
            if "body_cycle_state" in self.data:
                deleted_sections.discard("body_cycle_state")
                save_sections.add("body_cycle_state")
            else:
                save_sections.discard("body_cycle_state")
            self._save_data_sync(
                sections=save_sections,
                deleted_sections=deleted_sections,
            )
            return state

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


    async def _tick(self):
        try:
            last_poll = getattr(self, "_last_body_monitor_poll_ts", 0.0)
            poll_interval = _safe_float(
                getattr(self, "_body_monitor_poll_interval", 90.0),
                90.0,
                30.0,
                600.0,
            )
            if _now_ts() - last_poll >= poll_interval:
                await self._pull_body_monitor_candidates()
                self._last_body_monitor_poll_ts = _now_ts()
        except Exception as exc:
            logger.warning(
                "Body Monitor 事件拉取失败，本轮继续执行其他主动任务: %s",
                _single_line(exc, 160),
            )
        # HDSI 生命周期侧车在锁内只做标记，锁外执行（避免锁重入）。
        hdsi_sidecar_pending = False
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
                # HDSI life progression runs as an opt-in sidecar
                # 锁内只设标记：HDSI 生命周期侧车会重入 _data_lock，
                # 必须在释放锁之后再执行（见锁外调用）。
                hdsi_sidecar_pending = True
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
        if hdsi_sidecar_pending:
            # HDSI 生命周期侧车必须在 _data_lock 之外执行：
            # 其调用链会重新获取同一把非可重入 asyncio.Lock。
            await self._run_hdsi_life_tick_sidecar()
            return
        users = list(self.data.get("users", {}).items())

        for user_id, user in users:
            await self._tick_user(user_id, user)

        await self._run_proactive_maintenance_tasks()
        # HDSI life progression is an opt-in sidecar.
        await self._run_hdsi_life_tick_sidecar()
        async with self._data_lock:
            runtime = self.data.setdefault("proactive_runtime", {})
            if isinstance(runtime, dict):
                runtime["last_tick_finished_at"] = _now_ts()
