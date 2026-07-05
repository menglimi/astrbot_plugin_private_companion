# -*- coding: utf-8 -*-
"""
CoreStoreMixin — 配置、数据存储、用户/群组基础访问
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
import threading
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
from .helpers import (
    _date_key,
    _now_ts,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_internal_message_blocks,
    _strip_nonstandard_chat_control_tags,
    _today_key,
)
from .config_migration import _ensure_config_parent_dir
from .storage.store_manager import StoreManager
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




class CoreStoreMixin:
    """配置、数据存储、用户/群组基础访问"""

    def _rebuild_store_manager(self, *, reload_data: bool = False) -> None:
        backend = str(getattr(self, "storage_backend", "json") or "json").strip().lower() or "json"
        if backend not in {"json", "sqlite"}:
            backend = "json"
        sqlite_path = str(getattr(self, "storage_sqlite_path", "") or "").strip() or os.path.join(self.data_dir, "companions.db")
        self.storage_backend = backend
        self.storage_sqlite_effective_path = sqlite_path
        self.store_manager = StoreManager(
            backend_name=backend,
            data_file=self.data_file,
            sqlite_path=sqlite_path,
            ensure_defaults=self._ensure_store_defaults,
            new_store=self._new_store,
        )
        if reload_data:
            self.data = self.store_manager.load_initial_store()

    def _save_config_if_possible(self) -> None:
        for method_name in ("save_config", "save", "save_conf"):
            save = getattr(self.config, method_name, None)
            if not callable(save):
                continue
            try:
                _ensure_config_parent_dir(self.config, logger=logger)
                result = save()
                if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                    close = getattr(result, "close", None)
                    if callable(close):
                        close()
                    logger.debug("[PrivateCompanion] 自动保存配置返回异步对象，已跳过同步等待: %s", method_name)
                return
            except TypeError:
                continue
            except FileNotFoundError as exc:
                if _ensure_config_parent_dir(self.config, error=exc, logger=logger):
                    try:
                        result = save()
                        if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                            close = getattr(result, "close", None)
                            if callable(close):
                                close()
                        return
                    except Exception as retry_exc:
                        logger.debug("[PrivateCompanion] 自动保存配置重试失败: %s", _single_line(retry_exc, 120))
                        return
                logger.debug("[PrivateCompanion] 自动保存配置失败: %s", _single_line(exc, 120))
                return
            except Exception as exc:
                logger.debug("[PrivateCompanion] 自动保存配置失败: %s", _single_line(exc, 120))
                return

    def _set_runtime_bool_config(self, key: str, value: bool) -> None:
        setattr(self, key, bool(value))
        try:
            self.config[key] = bool(value)
        except Exception:
            setter = getattr(self.config, "set", None)
            if callable(setter):
                try:
                    setter(key, bool(value))
                except Exception:
                    pass

    async def _startup_prepare_today(self):
        try:
            await self._ensure_daily_state()
            await self._ensure_daily_plan()
            await self._ensure_daily_diary(force=not self._has_today_diary())
            await self._maybe_settle_skill_growth()
        except Exception as e:
            logger.warning(f"[PrivateCompanion] 启动时生成今日日志失败: {e}", exc_info=True)

    def _has_today_diary(self) -> bool:
        diaries = self.data.get("bot_diaries", [])
        if not isinstance(diaries, list):
            return False
        return any(
            isinstance(diary, dict) and diary.get("date") == _today_key()
            for diary in diaries
        )

    def _new_store(self) -> dict[str, Any]:
        return {
            "version": DATA_VERSION,
            "users": {},
            "groups": {},
            "daily_plan": {},
            "daily_plan_history": [],
            "daily_state": {},
            "state_conditions": [],
            "state_generated_day": "",
            "body_cycle_state": {},
            "bot_diaries": [],
            "dream_fragments": [],
            "daily_dream": {},
            "diary_generated_day": "",
            "daily_diary_failed_day": "",
            "daily_diary_failed_at": 0,
            "daily_diary_last_error": "",
            "daily_diary_postprocess_error": "",
            "daily_outfit_photo": {},
            "recent_photo_generations": [],
            "daily_story_plan": {},
            "daily_story_plan_history": [],
            "skill_growth": {},
            "detail_enhanced_day": "",
            "detail_enhanced_segments": {},
            "detail_enhanced_history": [],
            "schedule_adjustments": [],
            "yesterday_conversation_summary": {},
            "can_do": [],
            "important_dates": [],
            "qq_presence_state": {},
            "token_usage": {},
            "bilibili_integration": {},
            "news_integration": {},
            "web_exploration": {},
            "qzone_integration": {},
            "jm_cosmos_integration": {},
            "bookshelf_items": [],
            "bookshelf_secret": {},
            "creative_projects": [],
            "creative_memory_pool": [],
            "proactive_candidate_pool": [],
            "external_proactive_abilities": {},
            "worldbook_entries": [],
            "worldbook_member_profiles": {},
            "worldbook_group_profiles": {},
            "worldbook_import_state": {},
            "runtime_settings": {},
            "inbound_debounce_stats": {},
            "cache_metrics": {},
        }

    @staticmethod
    def _ensure_store_defaults(data: dict[str, Any]) -> dict[str, Any]:
        data.setdefault("version", DATA_VERSION)
        data.setdefault("users", {})
        data.setdefault("groups", {})
        data.setdefault("daily_plan", {})
        data.setdefault("daily_plan_history", [])
        data.setdefault("daily_state", {})
        data.setdefault("state_conditions", [])
        data.setdefault("state_generated_day", "")
        data.setdefault("body_cycle_state", {})
        data.setdefault("bot_diaries", [])
        data.setdefault("dream_fragments", [])
        data.setdefault("daily_dream", {})
        data.setdefault("diary_generated_day", "")
        data.setdefault("daily_diary_failed_day", "")
        data.setdefault("daily_diary_failed_at", 0)
        data.setdefault("daily_diary_last_error", "")
        data.setdefault("daily_diary_postprocess_error", "")
        data.setdefault("daily_outfit_photo", {})
        data.setdefault("recent_photo_generations", [])
        data.setdefault("daily_story_plan", {})
        data.setdefault("daily_story_plan_history", [])
        data.setdefault("skill_growth", {})
        data.setdefault("detail_enhanced_day", "")
        data.setdefault("detail_enhanced_segments", {})
        data.setdefault("detail_enhanced_history", [])
        data.setdefault("schedule_adjustments", [])
        data.setdefault("yesterday_conversation_summary", {})
        data.setdefault("can_do", [])
        data.setdefault("important_dates", [])
        data.setdefault("qq_presence_state", {})
        data.setdefault("token_usage", {})
        data.setdefault("bilibili_integration", {})
        data.setdefault("news_integration", {})
        data.setdefault("web_exploration", {})
        data.setdefault("qzone_integration", {})
        data.setdefault("jm_cosmos_integration", {})
        data.setdefault("bookshelf_items", [])
        data.setdefault("bookshelf_secret", {})
        data.setdefault("creative_projects", [])
        data.setdefault("creative_memory_pool", [])
        data.setdefault("proactive_candidate_pool", [])
        data.setdefault("external_proactive_abilities", {})
        data.setdefault("worldbook_entries", [])
        data.setdefault("worldbook_member_profiles", {})
        data.setdefault("worldbook_group_profiles", {})
        data.setdefault("worldbook_deleted_member_ids", [])
        data.setdefault("worldbook_deleted_group_ids", [])
        data.setdefault("worldbook_import_state", {})
        data.setdefault("runtime_settings", {})
        data.setdefault("atrelay_send_log", [])
        data.setdefault("inbound_debounce_stats", {})
        data.setdefault("cache_metrics", {})
        return data

    @staticmethod
    def _data_dict(data: Any, field: str) -> dict[str, Any]:
        value = data.get(field) if isinstance(data, dict) else None
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _data_list(data: Any, field: str) -> list[Any]:
        value = data.get(field) if isinstance(data, dict) else None
        return value if isinstance(value, list) else []

    @staticmethod
    def _data_str(data: Any, field: str, default: str = "") -> str:
        value = data.get(field) if isinstance(data, dict) else None
        return str(value) if value is not None else default

    def _record_cache_metric(self, namespace: str, *, hit: bool, detail: str = "") -> None:
        name = _single_line(namespace, 80)
        if not name:
            return
        metrics = self.data.setdefault("cache_metrics", {})
        if not isinstance(metrics, dict):
            metrics = {}
            self.data["cache_metrics"] = metrics
        item = metrics.setdefault(name, {})
        if not isinstance(item, dict):
            item = {}
            metrics[name] = item
        key = "hits" if hit else "misses"
        item[key] = _safe_int(item.get(key), 0, 0) + 1
        item["last_hit_ts" if hit else "last_miss_ts"] = _now_ts()
        if detail:
            item["last_hit_detail" if hit else "last_miss_detail"] = _single_line(detail, 160)

    def _sanitize_store_control_tags_inplace(self, value: Any) -> int:
        """Remove leaked pseudo-control tags from persisted companion data."""
        changed = 0
        if isinstance(value, dict):
            for key, item in list(value.items()):
                if isinstance(item, str):
                    cleaned = _strip_nonstandard_chat_control_tags(item)
                    if cleaned != item:
                        value[key] = cleaned
                        changed += 1
                elif isinstance(item, (dict, list)):
                    changed += self._sanitize_store_control_tags_inplace(item)
            return changed
        if isinstance(value, list):
            for idx, item in enumerate(list(value)):
                if isinstance(item, str):
                    cleaned = _strip_nonstandard_chat_control_tags(item)
                    if cleaned != item:
                        value[idx] = cleaned
                        changed += 1
                elif isinstance(item, (dict, list)):
                    changed += self._sanitize_store_control_tags_inplace(item)
            return changed
        return 0

    def _load_data_sync(self) -> dict[str, Any]:
        manager = getattr(self, "store_manager", None)
        if manager is not None:
            try:
                data = manager.load_initial_store()
                changed = self._sanitize_store_control_tags_inplace(data)
                if changed:
                    logger.warning("[PrivateCompanion] 启动读取数据时清理非标准控制标签: fields=%s", changed)
                return data
            except Exception as exc:
                logger.warning("[PrivateCompanion] StoreManager 读取失败,回退 JSON: %s", _single_line(exc, 160))
        if not os.path.exists(self.data_file):
            return self._new_store()
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return self._new_store()
            data = self._ensure_store_defaults(data)
            changed = self._sanitize_store_control_tags_inplace(data)
            if changed:
                logger.warning("[PrivateCompanion] 启动读取 JSON 时清理非标准控制标签: fields=%s", changed)
            return data
        except Exception as e:
            logger.warning(f"[PrivateCompanion] 读取数据失败,将使用空数据: {e}")
            return self._new_store()

    def _save_data_sync(self):
        changed = self._sanitize_store_control_tags_inplace(self.data)
        if changed:
            logger.warning("[PrivateCompanion] 保存数据前清理非标准控制标签: fields=%s", changed)
        manager = getattr(self, "store_manager", None)
        if manager is not None:
            manager.save_store(self.data)
            if getattr(self, "storage_backend", "json") == "sqlite":
                try:
                    manager.export_current_to_json(self.data)
                except Exception as exc:
                    logger.debug("[PrivateCompanion] SQLite 镜像 JSON 写出失败: %s", _single_line(exc, 160))
            return
        if not self.data.get("worldbook_entries") and os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing, dict) and existing.get("worldbook_entries"):
                    for key in (
                        "worldbook_entries",
                        "worldbook_member_profiles",
                        "worldbook_group_profiles",
                        "worldbook_import_state",
                    ):
                        self.data[key] = existing.get(key, self.data.get(key))
            except Exception:
                pass
        self._atomic_write_data_file_sync(self.data)

    def _write_data_snapshot_sync(self, data: dict[str, Any]) -> None:
        changed = self._sanitize_store_control_tags_inplace(data)
        if changed:
            logger.warning("[PrivateCompanion] 保存快照前清理非标准控制标签: fields=%s", changed)
        manager = getattr(self, "store_manager", None)
        if manager is not None:
            manager.save_snapshot(data)
            if getattr(self, "storage_backend", "json") == "sqlite":
                try:
                    manager.export_current_to_json(data)
                except Exception as exc:
                    logger.debug("[PrivateCompanion] SQLite 快照镜像 JSON 写出失败: %s", _single_line(exc, 160))
            return
        self._atomic_write_data_file_sync(data)

    def _atomic_write_data_file_sync(self, data: dict[str, Any]) -> None:
        base = self.data_file
        tmp_file = f"{base}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            last_exc: Exception | None = None
            for attempt in range(6):
                try:
                    os.replace(tmp_file, base)
                    return
                except PermissionError as exc:
                    last_exc = exc
                    time.sleep(0.05 * (attempt + 1))
                except OSError as exc:
                    last_exc = exc
                    if getattr(exc, "winerror", 0) not in {32, 33, 5}:
                        raise
                    time.sleep(0.05 * (attempt + 1))
            if last_exc:
                raise last_exc
        finally:
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass

    def _schedule_data_save(self, delay: float = 1.5) -> None:
        self._data_save_dirty = True
        task = getattr(self, "_data_save_task", None)
        if isinstance(task, asyncio.Task) and not task.done():
            return

        async def _runner() -> None:
            should_retry = False
            try:
                while bool(getattr(self, "_data_save_dirty", False)):
                    self._data_save_dirty = False
                    await asyncio.sleep(max(0.0, float(delay)))
                    snapshot = deepcopy(self.data)
                    await asyncio.to_thread(self._write_data_snapshot_sync, snapshot)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._data_save_dirty = True
                should_retry = True
                logger.warning("[PrivateCompanion] 延迟保存数据失败: %s", exc)
            finally:
                self._data_save_task = None
                if should_retry and bool(getattr(self, "_data_save_dirty", False)):
                    retry_delay = max(3.0, min(30.0, float(delay) * 2.0 if delay else 3.0))
                    try:
                        self._schedule_data_save(delay=retry_delay)
                    except Exception as retry_exc:
                        logger.warning("[PrivateCompanion] 延迟保存重试调度失败: %s", retry_exc)

        try:
            self._data_save_task = asyncio.create_task(_runner())
        except RuntimeError:
            self._save_data_sync()

    async def _reset_plugin_store(self) -> None:
        async with self._data_lock:
            self.data = self._new_store()
            if self.default_enable_configured_targets:
                self._sync_configured_targets()
            self._save_data_sync()

    async def _rebuild_today_after_reset(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        state = await self._ensure_daily_state(force=True)
        plan = await self._generate_daily_plan()
        async with self._data_lock:
            self.data["daily_plan"] = plan
            self._save_data_sync()

        diary = None
        if self.enable_daily_diary:
            diary = await self._generate_daily_diary()
            async with self._data_lock:
                diaries = self.data.setdefault("bot_diaries", [])
                if not isinstance(diaries, list):
                    diaries = []
                    self.data["bot_diaries"] = diaries
                diaries.append(diary)
                del diaries[:-self.max_diary_entries]
                self.data["diary_generated_day"] = _today_key()
                try:
                    self.data["dream_fragments"] = self._merge_dream_fragment_pool(
                        diary.get("dream_fragments", []) if isinstance(diary, dict) else []
                    )
                    self.data["daily_diary_postprocess_error"] = ""
                except Exception as exc:
                    self.data["daily_diary_postprocess_error"] = _single_line(exc, 180)
                    logger.warning(
                        "[PrivateCompanion] 重建今日日记已保存,但梦境碎片合并失败: %s",
                        _single_line(exc, 180),
                    )
                story_plan = diary.get("story_plan") if isinstance(diary, dict) else None
                if isinstance(story_plan, dict):
                    self.data["daily_story_plan"] = story_plan
                self._save_data_sync()
            outfit_generator = getattr(self, "_ensure_daily_outfit_photo", None)
            if callable(outfit_generator):
                try:
                    await outfit_generator(diary)
                except Exception as exc:
                    logger.warning(
                        "[PrivateCompanion] 重建今日日记已保存,但每日穿搭照片生成失败: %s",
                        _single_line(exc, 180),
                    )
        return state, plan, diary

    def _parse_private_user_aliases(self, raw: Any) -> dict[str, str]:
        aliases: dict[str, str] = {}
        if isinstance(raw, dict):
            items = raw.items()
        elif isinstance(raw, list):
            items = []
            for item in raw:
                if isinstance(item, dict):
                    alias = str(item.get("alias") or item.get("from") or item.get("source") or "").strip()
                    canonical = str(item.get("canonical") or item.get("to") or item.get("target") or "").strip()
                    if alias and canonical:
                        aliases[alias] = canonical
                    continue
                text = str(item or "").strip()
                if text:
                    items.append((text, ""))
        else:
            text = str(raw or "").strip()
            items = [(line.strip(), "") for line in text.splitlines() if line.strip()]
        for key, value in items:
            alias = str(key or "").strip()
            canonical = str(value or "").strip()
            if not canonical:
                for sep in ("=>", "=", ":", "：", "->"):
                    if sep in alias:
                        left, right = alias.split(sep, 1)
                        alias = left.strip()
                        canonical = right.strip()
                        break
            if alias and canonical and alias != canonical:
                aliases[alias] = canonical
        return aliases

    def _canonical_private_user_id(self, user_id: str) -> str:
        current = str(user_id or "").strip()
        aliases = getattr(self, "private_user_aliases", {}) or {}
        seen: set[str] = set()
        while current and current in aliases and current not in seen:
            seen.add(current)
            current = str(aliases.get(current) or "").strip()
        return current or str(user_id or "").strip()

    def _merge_user_record_values(self, target: dict[str, Any], source: dict[str, Any], alias_id: str) -> None:
        additive_keys = {
            "inbound_count",
            "private_inbound_count",
            "reply_count",
            "proactive_sent_count",
            "relationship_score",
            "sent_today",
            "ignored_streak",
            "poke_count",
        }
        max_keys = {
            "last_seen",
            "last_sent",
            "last_active_at",
            "last_user_message_at",
            "last_memory_refresh_at",
            "last_episode_refresh_at",
        }
        for key, value in source.items():
            if key == "user_id":
                continue
            if key in additive_keys:
                target[key] = _safe_int(target.get(key), 0) + _safe_int(value, 0)
            elif key in max_keys or key.endswith("_at") or key.endswith("_ts"):
                target[key] = max(_safe_float(target.get(key), 0), _safe_float(value, 0))
            elif isinstance(value, list):
                existing = target.get(key)
                if not isinstance(existing, list):
                    existing = []
                    target[key] = existing
                for item in value:
                    if item not in existing:
                        existing.append(deepcopy(item))
            elif isinstance(value, dict):
                existing = target.get(key)
                if not isinstance(existing, dict):
                    existing = {}
                    target[key] = existing
                for sub_key, sub_value in value.items():
                    if sub_key not in existing or existing.get(sub_key) in (None, "", [], {}):
                        existing[sub_key] = deepcopy(sub_value)
            elif target.get(key) in (None, "", [], {}):
                target[key] = deepcopy(value)
        aliases = target.setdefault("alias_user_ids", [])
        if not isinstance(aliases, list):
            aliases = []
            target["alias_user_ids"] = aliases
        for alias in [alias_id, *(source.get("alias_user_ids") if isinstance(source.get("alias_user_ids"), list) else [])]:
            alias_text = str(alias or "").strip()
            if alias_text and alias_text not in aliases:
                aliases.append(alias_text)

    def _merge_private_user_alias_records(self) -> bool:
        aliases = getattr(self, "private_user_aliases", {}) or {}
        if not aliases:
            return False
        users = self.data.setdefault("users", {})
        changed = False
        for alias_id, canonical_id in list(aliases.items()):
            alias_id = str(alias_id or "").strip()
            canonical_id = self._canonical_private_user_id(canonical_id)
            if not alias_id or not canonical_id or alias_id == canonical_id:
                continue
            source = users.get(alias_id)
            if not isinstance(source, dict):
                continue
            target = users.setdefault(canonical_id, deepcopy(_DEFAULT_USER_TEMPLATE))
            target["user_id"] = canonical_id
            self._merge_user_record_values(target, source, alias_id)
            users.pop(alias_id, None)
            changed = True
        return changed

    @staticmethod
    def _normalize_private_user_role(value: Any) -> str:
        text = str(value or "").strip().lower()
        mapping = {
            "owner": "owner",
            "master": "owner",
            "main": "owner",
            "target": "owner",
            "主人": "owner",
            "主用户": "owner",
            "目标用户": "owner",
            "friend": "friend",
            "social": "friend",
            "guest": "friend",
            "朋友": "friend",
            "好友": "friend",
            "普通朋友": "friend",
        }
        return mapping.get(text, "")

    @staticmethod
    def _private_user_role_label(role: str) -> str:
        return "主人" if role == "owner" else "朋友"

    def _protected_owner_nickname_tokens(self) -> set[str]:
        tokens: set[str] = set()
        generic = {
            "你",
            "妳",
            "您",
            "我",
            "他",
            "她",
            "它",
            "大家",
            "群友",
            "朋友",
            "主人",
            "主用户",
            "目标用户",
        }

        def add(value: Any) -> None:
            text = _single_line(value, 24)
            text = text.strip("「」『』“”\"'`[]()（）<>《》:：,，.。!！?？")
            if not text or text.isdigit() or text in generic:
                return
            if len(text) < 2 or len(text) > 12:
                return
            tokens.add(text)
            compact = re.sub(r"\s+", "", text)
            if compact and compact != text and 2 <= len(compact) <= 12 and compact not in generic:
                tokens.add(compact)

        add(getattr(self, "default_nickname", ""))
        target_ids = set()
        try:
            target_ids = set(self._configured_target_ids())
        except Exception:
            target_ids = set()
        users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
        if isinstance(users, dict):
            for user_id, user in users.items():
                if not isinstance(user, dict):
                    continue
                role = self._private_user_role(user, str(user_id or ""))
                if role != "owner" and str(user_id or "") not in target_ids:
                    continue
                add(user.get("nickname"))
                add(user.get("name"))
        profiles = self.data.get("worldbook_member_profiles", {}) if isinstance(getattr(self, "data", None), dict) else {}
        if isinstance(profiles, dict):
            for user_id in target_ids:
                profile = profiles.get(str(user_id))
                if not isinstance(profile, dict):
                    continue
                add(profile.get("name"))
                for key in ("aliases", "observed_names"):
                    raw = profile.get(key)
                    if isinstance(raw, list):
                        for item in raw:
                            add(item)
        return tokens

    def _private_user_default_role(self, user_id: str, user: dict[str, Any] | None = None) -> str:
        clean_id = self._canonical_private_user_id(str(user_id or "").strip())
        if clean_id and clean_id in set(self._configured_target_ids()):
            return "owner"
        return "friend"

    def _ensure_private_user_role(self, user_id: str, user: dict[str, Any]) -> str:
        role = self._normalize_private_user_role(user.get("relationship_role"))
        if not role:
            role = self._private_user_default_role(user_id, user)
            user["relationship_role"] = role
        return role

    def _get_user(self, user_id: str) -> dict[str, Any]:
        original_user_id = str(user_id or "").strip()
        user_id = self._canonical_private_user_id(original_user_id)
        users = self.data.setdefault("users", {})
        if original_user_id and original_user_id != user_id and original_user_id in users:
            target = users.setdefault(user_id, deepcopy(_DEFAULT_USER_TEMPLATE))
            target["user_id"] = user_id
            source = users.pop(original_user_id)
            if isinstance(source, dict):
                self._merge_user_record_values(target, source, original_user_id)
        created = user_id not in users
        user = users.setdefault(user_id, deepcopy(_DEFAULT_USER_TEMPLATE))
        user["user_id"] = user_id
        if original_user_id and original_user_id != user_id:
            aliases = user.setdefault("alias_user_ids", [])
            if isinstance(aliases, list) and original_user_id not in aliases:
                aliases.append(original_user_id)
        for key, default_value in _DEFAULT_USER_TEMPLATE.items():
            if key not in user:
                user[key] = deepcopy(default_value)
        self._ensure_private_user_role(user_id, user)
        user.setdefault("manual_enabled", False)
        user.setdefault("manual_disabled", False)
        if (
            not created
            and str(user_id).isdigit()
            and str(user_id) in set(self._configured_target_ids())
            and user.get("enabled") is False
            and not user.get("manual_enabled")
        ):
            user["manual_disabled"] = True
        if created:
            user["enabled"] = self._is_target_private_user(user_id, user)
        elif user.get("manual_disabled"):
            user["enabled"] = False
        elif not self._is_target_private_user(user_id, user):
            user["enabled"] = False
        if not user.get("nickname"):
            user["nickname"] = self.default_nickname
        if not user.get("style"):
            user["style"] = self.default_style
        return user

    def _latest_user_activity_ts(self, user: dict[str, Any] | None) -> float:
        if not isinstance(user, dict):
            return 0.0
        return max(
            _safe_float(user.get("last_activity_at"), 0),
            _safe_float(user.get("last_seen"), 0),
            _safe_float(user.get("last_user_message_at"), 0),
            _safe_float(user.get("last_reply_at"), 0),
        )

    def _latest_private_user_activity_ts(self, user: dict[str, Any] | None) -> float:
        if not isinstance(user, dict):
            return 0.0
        private_seen = _safe_float(user.get("last_private_seen"), 0)
        return max(
            private_seen,
            _safe_float(user.get("last_private_activity_at"), private_seen),
            _safe_float(user.get("last_private_reply_at"), 0),
        )

    def _note_private_inbound_activity(self, user: dict[str, Any], ts: float, *, text: str = "") -> None:
        if not isinstance(user, dict):
            return
        user["last_private_seen"] = ts
        user["last_private_activity_at"] = ts
        if text:
            user["private_inbound_count"] = _safe_int(user.get("private_inbound_count"), 0) + 1

    def _is_target_private_user(self, user_id: str, user: dict[str, Any] | None = None) -> bool:
        user_id = self._canonical_private_user_id(str(user_id or "").strip())
        if self._is_bot_self_user_id(user_id):
            return False
        if isinstance(user, dict) and user.get("manual_enabled"):
            return True
        if not user_id or not user_id.isdigit():
            return False
        if user_id in set(self._configured_target_ids()):
            return True
        return False

    def _is_bot_self_user_id(self, user_id: str) -> bool:
        user_id = str(user_id or "").strip()
        return bool(user_id and user_id in self._known_bot_self_ids())

    def _known_bot_self_ids(self) -> set[str]:
        ids: set[str] = set()
        for attr in ("bot_self_id", "bot_user_id", "self_id"):
            value = str(getattr(self, attr, "") or "").strip()
            if value.isdigit():
                ids.add(value)
        raw_ids = getattr(self, "bot_self_ids", None)
        if isinstance(raw_ids, (list, tuple, set)):
            for item in raw_ids:
                value = str(item or "").strip()
                if value.isdigit():
                    ids.add(value)
        platform_manager = getattr(getattr(self, "context", None), "platform_manager", None)
        for inst in list(getattr(platform_manager, "platform_insts", []) or []):
            for attr in ("client_self_id", "self_id", "bot_self_id", "bot_user_id"):
                value = str(getattr(inst, attr, "") or "").strip()
                if value.isdigit():
                    ids.add(value)
        return ids

    def _get_group(self, group_id: str) -> dict[str, Any]:
        groups = self.data.setdefault("groups", {})
        group = groups.setdefault(group_id, dict(_DEFAULT_GROUP_TEMPLATE))
        group["group_id"] = group_id
        for key, default_value in _DEFAULT_GROUP_TEMPLATE.items():
            if key not in group:
                group[key] = default_value.copy() if isinstance(default_value, (dict, list)) else default_value
        group["enabled"] = bool(group.get("enabled", True))
        return group

    def _parse_group_id_list(self, raw: Any) -> list[str]:
        if isinstance(raw, str):
            parts = re.split(r"[,\s,、;；]+", raw)
        elif isinstance(raw, list):
            parts = raw
        else:
            parts = []
        ids = []
        for part in parts:
            group_id = str(part).strip()
            if group_id and group_id.isdigit() and group_id not in ids:
                ids.append(group_id)
        return ids

    @staticmethod
    def _parse_text_list_config(raw: Any, *, limit: int = 120) -> list[str]:
        if isinstance(raw, str):
            parts = re.split(r"[\n,，、;；]+", raw)
        elif isinstance(raw, list):
            parts = raw
        else:
            parts = []
        values: list[str] = []
        seen: set[str] = set()
        for part in parts:
            value = _single_line(part, 60)
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            values.append(value)
            if len(values) >= limit:
                break
        return values

    def _configured_group_ids(self) -> list[str]:
        # Backward compatibility: old target_group_ids is now treated as whitelist.
        whitelist = self._parse_group_id_list(self.group_whitelist_ids)
        legacy = self._parse_group_id_list(self.target_group_ids)
        for group_id in legacy:
            if group_id not in whitelist:
                whitelist.append(group_id)
        return whitelist

    def _configured_group_blacklist_ids(self) -> list[str]:
        return self._parse_group_id_list(self.group_blacklist_ids)

    def _group_enabled_for_event(self, group_id: str) -> bool:
        if not self.enable_group_companion:
            return False
        if not self._group_allowed_by_access_mode(group_id):
            return False
        group = self._get_group(group_id)
        return bool(group.get("enabled", True))

    def _group_allowed_by_access_mode(self, group_id: str) -> bool:
        if self.group_access_mode == "blacklist":
            if group_id in self._configured_group_blacklist_ids():
                return False
        else:
            configured = self._configured_group_ids()
            if group_id not in configured:
                return False
        return True

