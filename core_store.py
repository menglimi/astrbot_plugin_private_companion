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
    _strip_persisted_chat_control_tags,
    _today_key,
    normalize_photo_generation_scopes,
)
from .companion_interaction_expression import current_interaction_projection, normalize_normal_interaction_band_cap
from .config_migration import _ensure_config_parent_dir
from .relationship_ledger import (
    apply_natural_relationship_decay,
    apply_relationship_event,
    clamp_relationship_positive_stage_cap,
    migrate_legacy_relationship_score,
    normalize_relationship_mode,
    normalize_relationship_positive_stage_cap_key,
)
from .storage.store_manager import StoreManager
from .person_context_contract import empty_person_store, ensure_person_store
from .photo_generation_scope import (
    PHOTO_GENERATION_SCOPE_LABELS,
    PHOTO_GENERATION_SCOPE_LIMIT_KEYS,
    PHOTO_GENERATION_SCOPES,
    normalize_photo_generation_scope_limit,
)
from .unified_profile_service import (
    DEFAULT_CLOSED_REPAIR_OPERATION_ID,
    ensure_legacy_profile_capabilities,
    ensure_new_profile_capabilities,
    migrate_legacy_capabilities,
    repair_default_closed_capabilities,
)
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
        default_sqlite_path = os.path.join(self.data_dir, "companions.db")
        configured_sqlite_path = str(getattr(self, "storage_sqlite_path", "") or "").strip()
        sqlite_path = configured_sqlite_path or default_sqlite_path
        if backend == "sqlite" and configured_sqlite_path:
            configured_path = Path(configured_sqlite_path)
            invalid_reason = ""
            try:
                if configured_path.exists() and configured_path.is_dir():
                    invalid_reason = "配置路径是目录，不是 SQLite 数据文件"
                elif configured_path.parent.exists() and not configured_path.parent.is_dir():
                    invalid_reason = "配置路径的父级不是目录"
                elif not configured_path.parent.exists():
                    configured_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                invalid_reason = f"配置路径父级不可创建: {_single_line(exc, 160)}"
            if invalid_reason:
                sqlite_path = default_sqlite_path
                logger.warning(
                    "[PrivateCompanion] SQLite 数据文件路径无效，已回退默认路径: configured=%s reason=%s fallback=%s",
                    configured_sqlite_path,
                    invalid_reason,
                    default_sqlite_path,
                )
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

    async def _save_config_if_possible(self) -> bool:
        for method_name in ("save_config", "save", "save_conf"):
            save = getattr(self.config, method_name, None)
            if not callable(save):
                continue
            try:
                _ensure_config_parent_dir(self.config, logger=logger)
                result = save()
                if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                    await result
                return True
            except TypeError:
                continue
            except FileNotFoundError as exc:
                if _ensure_config_parent_dir(self.config, error=exc, logger=logger):
                    try:
                        result = save()
                        if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                            await result
                        return True
                    except Exception as retry_exc:
                        logger.warning("[PrivateCompanion] 自动保存配置重试失败: %s", _single_line(retry_exc, 120))
                        return False
                logger.warning("[PrivateCompanion] 自动保存配置失败: %s", _single_line(exc, 120))
                return False
            except Exception as exc:
                logger.warning("[PrivateCompanion] 自动保存配置失败: %s", _single_line(exc, 120))
                return False
        logger.warning("[PrivateCompanion] 当前配置对象没有可用保存方法，本次修改未落盘")
        return False

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
            if bool(getattr(self, "enable_multi_persona_mode", False)):
                persona_ids = getattr(self, "_configured_multi_persona_ids", lambda: [])()
                if not persona_ids:
                    persona_ids = [getattr(self, "multi_persona_primary_id", "")]
                for persona_id in persona_ids:
                    token = getattr(self, "_activate_persona_id", lambda _pid: None)(persona_id)
                    try:
                        try:
                            await self._ensure_daily_state()
                            await self._ensure_daily_plan()
                            await self._ensure_daily_diary()
                            await self._maybe_settle_skill_growth()
                        except Exception as exc:
                            logger.warning(
                                "[PrivateCompanion] 启动初始化人格失败，继续处理其他人格: persona=%s error=%s",
                                _single_line(persona_id, 96),
                                _single_line(exc, 160),
                            )
                    finally:
                        if token is not None:
                            getattr(self, "_deactivate_persona_for_event", lambda _token: None)(token)
                if persona_ids and any(str(item or "").strip() for item in persona_ids):
                    return
            await self._ensure_daily_state()
            await self._ensure_daily_plan()
            # 启动只做一次普通维护检查；是否到达配置的日记时间由统一入口判断。
            # 手动“刷新日记”仍可显式传 force=True，不应让重启变成隐式强制生成。
            await self._ensure_daily_diary()
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
            "private_user_alias_merge_backups": {},
            "groups": {},
            "daily_plan": {},
            "daily_plan_history": [],
            "agenda_version": 1,
            "observed_activities": [],
            "place_cognitive_maps": {},
            "reality_touch_outputs": {},
            "window_snapshots": [],
            "agenda_reconciliation_history": [],
            "daily_state": {},
            "state_conditions": [],
            "state_generated_day": "",
            "body_cycle_state": {},
            "bot_diaries": [],
            "dream_fragments": [],
            "daily_dream": {},
            "diary_generated_day": "",
            "daily_diary_deleted_days": [],
            "daily_diary_delete_revision": 0,
            "daily_diary_failed_day": "",
            "daily_diary_failed_at": 0,
            "daily_diary_last_error": "",
            "daily_diary_postprocess_error": "",
            "daily_outfit_photo": {},
            "daily_outfit_history": [],
            "recent_photo_generations": [],
            "recent_photo_continuity": {},
            "daily_story_plan": {},
            "daily_story_plan_history": [],
            "bot_personal_outbox": [],
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
            "bookshelf_store_revision": 0,
            "memo_notes": [],
            "creative_projects": [],
            "creative_memory_pool": [],
            "proactive_candidate_pool": [],
            "external_proactive_abilities": {},
            "boundary_feedback_reports": [],
            "boundary_feedback_vent_history": [],
            "worldbook_entries": [],
            "worldbook_member_profiles": {},
            "worldbook_group_profiles": {},
            # Visual references are kept separate from the legacy 24-item catalog.
            "photo_reference_assets": [],
            "worldbook_import_state": {},
            "runtime_settings": {},
            "inbound_debounce_stats": {},
            "group_llm_reply_blocks": {},
            "reaction_expression_group_states": {},
            "cache_metrics": {},
            "_req041_memory_scope_state": {},
            "persona_lifecycle": {
                "generation": 1,
                "reset_at": 0,
                "previous_backup": "",
            },
            "balance_awareness": {},
            "qweather_location": {},
            "weather_alerts": {},
            "weather_alert_awareness": {},
            "body_monitor_integration": {},
            "environment_change_awareness": {},
            "personal_goal_state": {},
            "personal_goals": [],
            "food_menu": {},
            "daily_review_reports": [],
            "daily_review_active_guidance": {},
            "daily_review_last_attempt": {},
            "daily_review_completed_day": "",
            "daily_review_case_audit": [],
            "unified_person": empty_person_store(),
        }

    @staticmethod
    def _ensure_store_defaults(data: dict[str, Any]) -> dict[str, Any]:
        data.setdefault("version", DATA_VERSION)
        data.setdefault("users", {})
        data.setdefault("private_user_alias_merge_backups", {})
        data.setdefault("groups", {})
        data.setdefault("daily_plan", {})
        data.setdefault("daily_plan_history", [])
        data.setdefault("agenda_version", 1)
        data.setdefault("observed_activities", [])
        data.setdefault("place_cognitive_maps", {})
        data.setdefault("reality_touch_outputs", {})
        data.setdefault("window_snapshots", [])
        data.setdefault("agenda_reconciliation_history", [])
        data.setdefault("daily_state", {})
        data.setdefault("state_conditions", [])
        data.setdefault("state_generated_day", "")
        data.setdefault("body_cycle_state", {})
        data.setdefault("bot_diaries", [])
        data.setdefault("dream_fragments", [])
        data.setdefault("daily_dream", {})
        data.setdefault("diary_generated_day", "")
        data.setdefault("daily_diary_deleted_days", [])
        data.setdefault("daily_diary_delete_revision", 0)
        data.setdefault("daily_diary_failed_day", "")
        data.setdefault("daily_diary_failed_at", 0)
        data.setdefault("daily_diary_last_error", "")
        data.setdefault("daily_diary_postprocess_error", "")
        data.setdefault("daily_outfit_photo", {})
        data.setdefault("daily_outfit_history", [])
        data.setdefault("recent_photo_generations", [])
        data.setdefault("recent_photo_continuity", {})
        data.setdefault("daily_story_plan", {})
        data.setdefault("daily_story_plan_history", [])
        data.setdefault("bot_personal_outbox", [])
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
        data.setdefault("bookshelf_store_revision", 0)
        data.setdefault("memo_notes", [])
        data.setdefault("creative_projects", [])
        data.setdefault("creative_memory_pool", [])
        data.setdefault("proactive_candidate_pool", [])
        data.setdefault("external_proactive_abilities", {})
        data.setdefault("boundary_feedback_reports", [])
        data.setdefault("boundary_feedback_vent_history", [])
        data.setdefault("worldbook_entries", [])
        data.setdefault("worldbook_member_profiles", {})
        data.setdefault("worldbook_group_profiles", {})
        data.setdefault("photo_reference_assets", [])
        data.setdefault("worldbook_deleted_member_ids", [])
        data.setdefault("worldbook_deleted_group_ids", [])
        data.setdefault("worldbook_import_state", {})
        data.setdefault("runtime_settings", {})
        data.setdefault("atrelay_send_log", [])
        data.setdefault("inbound_debounce_stats", {})
        data.setdefault("group_llm_reply_blocks", {})
        data.setdefault("reaction_expression_group_states", {})
        data.setdefault("cache_metrics", {})
        data.setdefault("_req041_memory_scope_state", {})
        lifecycle = data.setdefault("persona_lifecycle", {})
        if not isinstance(lifecycle, dict):
            lifecycle = {}
            data["persona_lifecycle"] = lifecycle
        lifecycle.setdefault("generation", 1)
        lifecycle.setdefault("reset_at", 0)
        lifecycle.setdefault("previous_backup", "")
        data.setdefault("balance_awareness", {})
        data.setdefault("qweather_location", {})
        data.setdefault("weather_alerts", {})
        data.setdefault("weather_alert_awareness", {})
        data.setdefault("body_monitor_integration", {})
        data.setdefault("environment_change_awareness", {})
        data.setdefault("personal_goal_state", {})
        data.setdefault("personal_goals", [])
        data.setdefault("food_menu", {})
        data.setdefault("daily_review_reports", [])
        data.setdefault("daily_review_active_guidance", {})
        data.setdefault("daily_review_last_attempt", {})
        data.setdefault("daily_review_completed_day", "")
        data.setdefault("daily_review_case_audit", [])
        ensure_person_store(data)
        # Legacy profiles retain their effective durable permissions once.
        # Creation paths below install a separate default-closed state for new
        # identities, so a user starting a DM cannot manufacture authority.
        migrate_legacy_capabilities(
            data,
            operation_id="req036-capability-v1",
            dry_run=False,
        )
        # PR #110 could recreate an already observed legacy identity with a
        # schema-v1 default-closed document.  Reconcile only durable legacy
        # enable evidence; new/automatic and explicitly disabled profiles
        # remain closed.  The operation id is versioned so installs that ran
        # the previous compatibility pass still receive this pass once.
        repair_default_closed_capabilities(
            data,
            operation_id=DEFAULT_CLOSED_REPAIR_OPERATION_ID,
            dry_run=False,
        )
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

    @staticmethod
    def _store_path_is_raw_user_text(path: tuple[Any, ...]) -> bool:
        """Raw observations are evidence and must not be rewritten during persistence."""
        if "recent_phrases" in path:
            return True
        if len(path) >= 3 and path[0] == "memo_notes" and path[-1] in {"title", "content"}:
            return True
        return bool(
            len(path) >= 5
            and path[0] == "groups"
            and path[2] == "recent_messages"
            and path[-1] == "text"
        )

    def _sanitize_store_control_tags_inplace(self, value: Any, _path: tuple[Any, ...] = ()) -> int:
        """Remove leaked pseudo-control tags from persisted companion data."""
        if not bool(getattr(self, "enable_store_control_tag_sanitization", True)):
            return 0
        changed = 0
        if isinstance(value, dict):
            for key, item in list(value.items()):
                item_path = (*_path, key)
                if isinstance(item, str):
                    if self._store_path_is_raw_user_text(item_path):
                        continue
                    cleaned = _strip_persisted_chat_control_tags(item)
                    if cleaned != item:
                        value[key] = cleaned
                        changed += 1
                elif isinstance(item, (dict, list)):
                    changed += self._sanitize_store_control_tags_inplace(item, item_path)
            return changed
        if isinstance(value, list):
            for idx, item in enumerate(list(value)):
                item_path = (*_path, idx)
                if isinstance(item, str):
                    if self._store_path_is_raw_user_text(item_path):
                        continue
                    cleaned = _strip_persisted_chat_control_tags(item)
                    if cleaned != item:
                        value[idx] = cleaned
                        changed += 1
                elif isinstance(item, (dict, list)):
                    changed += self._sanitize_store_control_tags_inplace(item, item_path)
            return changed
        return 0

    def _log_store_control_cleanup(self, stage: str, changed: int, *, cooldown_seconds: float = 600.0) -> bool:
        if changed <= 0:
            return False
        now = time.monotonic()
        states = getattr(self, "_store_control_cleanup_log_states", None)
        if not isinstance(states, dict):
            states = {}
            self._store_control_cleanup_log_states = states
        key = _single_line(stage, 40) or "unknown"
        state = states.setdefault(key, {"last_at": 0.0, "suppressed_events": 0, "suppressed_fields": 0})
        last_at = _safe_float(state.get("last_at"), 0.0, 0.0)
        if last_at and now - last_at < max(1.0, float(cooldown_seconds)):
            state["suppressed_events"] = _safe_int(state.get("suppressed_events"), 0, 0) + 1
            state["suppressed_fields"] = _safe_int(state.get("suppressed_fields"), 0, 0) + changed
            return False
        suppressed_events = _safe_int(state.get("suppressed_events"), 0, 0)
        suppressed_fields = _safe_int(state.get("suppressed_fields"), 0, 0)
        state.update({"last_at": now, "suppressed_events": 0, "suppressed_fields": 0})
        suffix = (
            f" suppressed_events={suppressed_events} suppressed_fields={suppressed_fields}"
            if suppressed_events
            else ""
        )
        logger.info(
            "[PrivateCompanion] Store safety cleanup: stage=%s fields=%s%s",
            key,
            changed,
            suffix,
        )
        return True

    @staticmethod
    def _proactive_candidate_repeat_limit_for_status(status: Any) -> int:
        normalized = str(status or "").strip().lower()
        if normalized in {"accepted", "deferred", "queued", "pending", "unknown", ""}:
            return 12
        if normalized == "sent":
            return 8
        return 6

    def _sanitize_proactive_candidate_repeat_counts_inplace(self, data: Any) -> int:
        if not isinstance(data, dict):
            return 0
        pool = data.get("proactive_candidate_pool")
        if not isinstance(pool, list):
            return 0
        changed = 0
        for item in pool:
            if not isinstance(item, dict):
                continue
            limit = self._proactive_candidate_repeat_limit_for_status(item.get("status"))
            current = _safe_int(item.get("repeat_count"), 1, 1)
            normalized = max(1, min(limit, current))
            if current != normalized:
                item["repeat_count"] = normalized
                item["repeat_count_capped"] = True
                changed += 1
        if changed:
            data["proactive_candidate_repeat_sanitized_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return changed

    @staticmethod
    def _store_history_item_timestamp(item: dict[str, Any]) -> float:
        return max(
            _safe_float(item.get("updated_ts"), 0),
            _safe_float(item.get("created_ts"), 0),
            _safe_float(item.get("scheduled_ts"), 0),
            _safe_float(item.get("last_seen_ts"), 0),
            _safe_float(item.get("ts"), 0),
        )

    @staticmethod
    def _compact_external_history_items(items: Any, *, limit: int) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        text_limits = {
            "key": 120,
            "id": 120,
            "title": 300,
            "headline": 300,
            "topic": 200,
            "source": 100,
            "source_title": 300,
            "summary": 1200,
            "note": 1200,
            "impression": 1200,
            "reason": 400,
            "link": 800,
            "url": 800,
            "source_url": 800,
            "published_at": 80,
            "published": 80,
            "date": 80,
        }
        compacted: list[dict[str, Any]] = []
        for raw in items[: max(0, limit)]:
            if not isinstance(raw, dict):
                continue
            item: dict[str, Any] = {}
            for key, max_len in text_limits.items():
                value = raw.get(key)
                if value is not None and str(value).strip():
                    item[key] = _single_line(value, max_len)
            for key in ("created_ts", "published_ts", "score", "rank", "text_readable", "video_subtitle_readable"):
                value = raw.get(key)
                if isinstance(value, (int, float, bool)):
                    item[key] = value
            if item:
                compacted.append(item)
        return compacted

    def _compact_store_history_inplace(self, data: Any) -> dict[str, int]:
        if not isinstance(data, dict):
            return {}
        changed: dict[str, int] = {}

        pool = data.get("proactive_candidate_pool")
        if isinstance(pool, list) and len(pool) > 600:
            candidates = [item for item in pool if isinstance(item, dict)]
            users = data.get("users") if isinstance(data.get("users"), dict) else {}
            planned_ids = {
                _single_line(user.get("planned_candidate_id"), 40)
                for user in users.values()
                if isinstance(user, dict) and _single_line(user.get("planned_candidate_id"), 40)
            }
            active_statuses = {"", "accepted", "deferred", "queued", "pending", "unknown"}
            protected = [item for item in candidates if _single_line(item.get("id"), 40) in planned_ids]
            protected_ids = {_single_line(item.get("id"), 40) for item in protected}
            active = [
                item
                for item in candidates
                if _single_line(item.get("id"), 40) not in protected_ids
                and _single_line(item.get("status"), 24).lower() in active_statuses
            ]
            completed = [
                item
                for item in candidates
                if _single_line(item.get("id"), 40) not in protected_ids
                and _single_line(item.get("status"), 24).lower() not in active_statuses
            ]
            active.sort(key=self._store_history_item_timestamp, reverse=True)
            completed.sort(key=self._store_history_item_timestamp, reverse=True)
            remaining = max(0, 600 - len(protected))
            kept_active = active[:remaining]
            kept_completed = completed[: max(0, remaining - len(kept_active))]
            kept = protected + kept_active + kept_completed
            kept.sort(key=self._store_history_item_timestamp)
            data["proactive_candidate_pool"] = kept[-600:]
            changed["proactive_candidate_pool"] = len(pool) - len(data["proactive_candidate_pool"])

        news = data.get("news_integration")
        if isinstance(news, dict):
            digests = news.get("digests")
            if isinstance(digests, list):
                compacted_digests: list[dict[str, Any]] = []
                removed_payloads = 0
                for raw in digests[-32:]:
                    if not isinstance(raw, dict):
                        continue
                    digest = dict(raw)
                    for key in ("items", "results", "raw_items", "articles"):
                        if key in digest:
                            digest.pop(key, None)
                            removed_payloads += 1
                    compacted_digests.append(digest)
                if len(compacted_digests) != len(digests) or removed_payloads:
                    news["digests"] = compacted_digests
                    changed["news_digests"] = max(0, len(digests) - len(compacted_digests)) + removed_payloads
            latest_items = news.get("latest_items")
            if isinstance(latest_items, list):
                compacted_latest = self._compact_external_history_items(latest_items, limit=12)
                if compacted_latest != latest_items:
                    news["latest_items"] = compacted_latest
                    changed["news_latest_items"] = len(latest_items)
            last_digest = news.get("last_digest")
            if isinstance(last_digest, dict) and isinstance(last_digest.get("items"), list):
                compacted_items = self._compact_external_history_items(last_digest.get("items"), limit=8)
                if compacted_items != last_digest.get("items"):
                    last_digest["items"] = compacted_items
                    changed["news_last_digest_items"] = len(compacted_items)

        web = data.get("web_exploration")
        if isinstance(web, dict):
            notes = web.get("notes")
            if isinstance(notes, list):
                compacted_notes: list[dict[str, Any]] = []
                removed_payloads = 0
                for raw in notes[-40:]:
                    if not isinstance(raw, dict):
                        continue
                    note = dict(raw)
                    for key in ("results", "raw_results", "pages"):
                        if key in note:
                            note.pop(key, None)
                            removed_payloads += 1
                    compacted_notes.append(note)
                if len(compacted_notes) != len(notes) or removed_payloads:
                    web["notes"] = compacted_notes
                    changed["web_notes"] = max(0, len(notes) - len(compacted_notes)) + removed_payloads
            latest_results = web.get("latest_results")
            if isinstance(latest_results, list):
                compacted_results = self._compact_external_history_items(latest_results, limit=8)
                if compacted_results != latest_results:
                    web["latest_results"] = compacted_results
                    changed["web_latest_results"] = len(latest_results)
            last_digest = web.get("last_digest")
            if isinstance(last_digest, dict) and isinstance(last_digest.get("results"), list):
                compacted_results = self._compact_external_history_items(last_digest.get("results"), limit=6)
                if compacted_results != last_digest.get("results"):
                    last_digest["results"] = compacted_results
                    changed["web_last_digest_results"] = len(compacted_results)
        return changed

    @staticmethod
    def _strip_ephemeral_group_transcripts_inplace(data: Any) -> dict[str, int]:
        """Project group chat to a bounded restart-safe context window for snapshots."""
        if not isinstance(data, dict):
            return {}
        groups = data.get("groups")
        if not isinstance(groups, dict):
            return {}
        removed_messages = 0
        removed_phrases = 0
        for group in groups.values():
            if not isinstance(group, dict):
                continue
            recent = group.get("recent_messages")
            if isinstance(recent, list) and recent:
                # Keep only a small restart-safe tail. The live store still
                # retains its configured window for in-process reasoning.
                keep = [item for item in recent[-12:] if isinstance(item, dict)]
                for item in keep:
                    item["text"] = _single_line(item.get("text"), 180)
                    item.pop("image_vision", None)
                removed_messages += max(0, len(recent) - len(keep))
                group["recent_messages"] = keep
            members = group.get("members")
            if not isinstance(members, dict):
                continue
            for member in members.values():
                if not isinstance(member, dict):
                    continue
                phrases = member.get("recent_phrases")
                if isinstance(phrases, list) and phrases:
                    keep_phrases = [_single_line(item, 80) for item in phrases[:4] if _single_line(item, 80)]
                    removed_phrases += max(0, len(phrases) - len(keep_phrases))
                    member["recent_phrases"] = keep_phrases
        changed: dict[str, int] = {}
        if removed_messages:
            changed["group_recent_messages"] = removed_messages
        if removed_phrases:
            changed["group_member_recent_phrases"] = removed_phrases
        return changed

    def _mark_bookshelf_store_changed(self, data: dict[str, Any] | None = None) -> int:
        target = data if isinstance(data, dict) else self.data
        try:
            current = max(0, int(target.get("bookshelf_store_revision") or 0))
        except (TypeError, ValueError, OverflowError):
            current = 0
        revision = max(current + 1, int(time.time() * 1000))
        target["bookshelf_store_revision"] = revision
        return revision

    def _recover_bookshelf_after_load(self, data: dict[str, Any]) -> int:
        recoverer = getattr(self, "_recover_bookshelf_items_from_local_pages_inplace", None)
        if not callable(recoverer):
            return 0
        try:
            recovered = max(0, int(recoverer(data) or 0))
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 启动恢复夹层本地书页失败，已保留现有存储: %s",
                _single_line(exc, 160),
            )
            return 0
        if recovered:
            logger.warning("[PrivateCompanion] 已根据本地书页和删除记录校准夹层书库: changed=%s", recovered)
        return recovered

    def _load_data_sync(self) -> dict[str, Any]:
        manager = getattr(self, "store_manager", None)
        if manager is not None:
            try:
                data = manager.load_initial_store()
                changed = self._sanitize_store_control_tags_inplace(data)
                repeat_changed = self._sanitize_proactive_candidate_repeat_counts_inplace(data)
                compacted = self._compact_store_history_inplace(data)
                bookshelf_recovered = self._recover_bookshelf_after_load(data)
                if changed:
                    logger.warning("[PrivateCompanion] 启动读取数据时清理非标准控制标签: fields=%s", changed)
                if repeat_changed:
                    logger.warning("[PrivateCompanion] 启动读取数据时压缩主动候选重复计数: items=%s", repeat_changed)
                if compacted:
                    logger.info("[PrivateCompanion] 启动读取数据时压缩历史存储: %s", compacted)
                if bookshelf_recovered:
                    manager.save_store(data)
                    if getattr(self, "storage_backend", "json") == "sqlite":
                        try:
                            mirror = deepcopy(data)
                            self._strip_ephemeral_group_transcripts_inplace(mirror)
                            manager.export_current_to_json(mirror, force=True)
                        except Exception as exc:
                            logger.debug("[PrivateCompanion] 夹层恢复后的 JSON 镜像写出失败: %s", _single_line(exc, 160))
                return data
            except Exception as exc:
                logger.error(
                    "[PrivateCompanion] StoreManager 读取失败，为避免用空数据覆盖原存储，已中止加载: %s",
                    _single_line(exc, 200),
                )
                raise
        if not os.path.exists(self.data_file):
            return self._new_store()
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("数据文件根节点必须是 JSON 对象")
            data = self._ensure_store_defaults(data)
            changed = self._sanitize_store_control_tags_inplace(data)
            repeat_changed = self._sanitize_proactive_candidate_repeat_counts_inplace(data)
            compacted = self._compact_store_history_inplace(data)
            self._recover_bookshelf_after_load(data)
            if changed:
                logger.warning("[PrivateCompanion] 启动读取 JSON 时清理非标准控制标签: fields=%s", changed)
            if repeat_changed:
                logger.warning("[PrivateCompanion] 启动读取 JSON 时压缩主动候选重复计数: items=%s", repeat_changed)
            if compacted:
                logger.info("[PrivateCompanion] 启动读取 JSON 时压缩历史存储: %s", compacted)
            return data
        except Exception as exc:
            logger.error(
                "[PrivateCompanion] 读取已有 JSON 数据失败，为避免覆盖原文件，已中止加载: %s",
                _single_line(exc, 200),
            )
            raise

    def _save_data_sync(self):
        scoped_scheduler = getattr(self, "_req041_schedule_scoped_sync", None)
        if callable(scoped_scheduler):
            scoped_scheduler()
        group_observation_save = bool(getattr(self, "_group_observation_dirty", False))
        if group_observation_save:
            self._group_observation_dirty = False
        active_persona = str(getattr(self, "_active_persona_scope", lambda: "")() or "")
        if bool(getattr(self, "enable_multi_persona_mode", False)) and active_persona:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                snapshot = deepcopy(self.data)
                self._write_persona_data_snapshot_sync(active_persona, snapshot)
                return
            self._schedule_data_save(delay=15.0 if group_observation_save else 0.35)
            return
        compacted = self._compact_store_history_inplace(self.data)
        if compacted:
            logger.info("[PrivateCompanion] 保存前压缩历史存储: %s", compacted)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._save_data_now_sync()
            return
        self._schedule_data_save(delay=15.0 if group_observation_save else 0.35)

    def _save_data_now_sync(self) -> None:
        changed = self._sanitize_store_control_tags_inplace(self.data)
        repeat_changed = self._sanitize_proactive_candidate_repeat_counts_inplace(self.data)
        if changed:
            self._log_store_control_cleanup("immediate_save", changed)
        if repeat_changed:
            logger.warning("[PrivateCompanion] 保存数据前压缩主动候选重复计数: items=%s", repeat_changed)
        manager = getattr(self, "store_manager", None)
        if manager is not None:
            manager.save_store(self.data)
            if getattr(self, "storage_backend", "json") == "sqlite":
                try:
                    mirror = deepcopy(self.data)
                    self._strip_ephemeral_group_transcripts_inplace(mirror)
                    manager.export_current_to_json(mirror)
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
        snapshot = deepcopy(self.data)
        self._strip_ephemeral_group_transcripts_inplace(snapshot)
        self._atomic_write_data_file_sync(snapshot)

    def _write_data_snapshot_sync(self, data: dict[str, Any]) -> int:
        changed = self._sanitize_store_control_tags_inplace(data)
        self._compact_store_history_inplace(data)
        manager = getattr(self, "store_manager", None)
        if manager is not None:
            manager.save_snapshot(data)
            if getattr(self, "storage_backend", "json") == "sqlite":
                try:
                    mirror = deepcopy(data)
                    self._strip_ephemeral_group_transcripts_inplace(mirror)
                    manager.export_current_to_json(mirror)
                except Exception as exc:
                    logger.debug("[PrivateCompanion] SQLite 快照镜像 JSON 写出失败: %s", _single_line(exc, 160))
            return changed
        mirror = deepcopy(data)
        self._strip_ephemeral_group_transcripts_inplace(mirror)
        self._atomic_write_data_file_sync(mirror)
        return changed

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

    def _persona_data_for_save(self, persona_id: str) -> dict[str, Any]:
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict):
            profile = profiles.get(persona_id)
            if isinstance(profile, dict):
                return profile
        ensure_profile = getattr(self, "_ensure_persona_profile", None)
        if callable(ensure_profile):
            profile = ensure_profile(persona_id)
            if isinstance(profile, dict):
                return profile
        return {}

    def _write_persona_data_snapshot_sync(self, persona_id: str, data: dict[str, Any]) -> int:
        changed = self._sanitize_store_control_tags_inplace(data)
        self._compact_store_history_inplace(data)
        saver = getattr(self, "_save_persona_profile_sync", None)
        if not callable(saver):
            raise RuntimeError("persona profile saver is unavailable")
        saver(persona_id, data)
        return changed

    def _schedule_persona_data_save(self, persona_id: str, delay: float) -> None:
        dirty = getattr(self, "_persona_data_save_dirty", None)
        if not isinstance(dirty, set):
            dirty = set()
            self._persona_data_save_dirty = dirty
        tasks = getattr(self, "_persona_data_save_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self._persona_data_save_tasks = tasks
        dirty.add(persona_id)
        stop_event = getattr(self, "_stop_event", None)
        if stop_event is not None and callable(getattr(stop_event, "is_set", None)) and stop_event.is_set():
            return
        task = tasks.get(persona_id)
        if isinstance(task, asyncio.Task) and not task.done():
            return

        async def _runner() -> None:
            should_retry = False
            try:
                while persona_id in dirty:
                    dirty.discard(persona_id)
                    await asyncio.sleep(max(0.0, float(delay)))
                    live_data = self._persona_data_for_save(persona_id)
                    snapshot = deepcopy(live_data)
                    try:
                        snapshot_changed = await asyncio.to_thread(
                            self._write_persona_data_snapshot_sync,
                            persona_id,
                            snapshot,
                        )
                    finally:
                        # Do not keep a full store copy alive while waiting for the
                        # next coalesced write; this matters for large persona stores.
                        snapshot = None
                    if snapshot_changed:
                        live_changed = self._sanitize_store_control_tags_inplace(live_data)
                        if live_changed:
                            dirty.add(persona_id)
                            self._log_store_control_cleanup(
                                "delayed_persona_save_live_sync",
                                live_changed,
                            )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                dirty.add(persona_id)
                should_retry = True
                logger.warning(
                    "[PrivateCompanion] 延迟保存人格数据失败: persona=%s error=%s",
                    persona_id,
                    _single_line(exc, 160),
                )
            finally:
                tasks.pop(persona_id, None)
                stop_event = getattr(self, "_stop_event", None)
                stopping = bool(
                    stop_event is not None
                    and callable(getattr(stop_event, "is_set", None))
                    and stop_event.is_set()
                )
                if not stopping and persona_id in dirty:
                    retry_delay = (
                        max(3.0, min(30.0, float(delay) * 2.0 if delay else 3.0))
                        if should_retry
                        else max(0.0, float(delay))
                    )
                    try:
                        self._schedule_persona_data_save(persona_id, retry_delay)
                    except Exception as retry_exc:
                        logger.warning(
                            "[PrivateCompanion] 延迟保存人格数据重试调度失败: persona=%s error=%s",
                            persona_id,
                            _single_line(retry_exc, 160),
                        )

        try:
            tasks[persona_id] = asyncio.create_task(_runner())
        except RuntimeError:
            snapshot = deepcopy(self._persona_data_for_save(persona_id))
            self._write_persona_data_snapshot_sync(persona_id, snapshot)
            dirty.discard(persona_id)

    def _schedule_default_data_save(self, delay: float) -> None:
        self._data_save_dirty = True
        stop_event = getattr(self, "_stop_event", None)
        if stop_event is not None and callable(getattr(stop_event, "is_set", None)) and stop_event.is_set():
            return
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
                    try:
                        snapshot_changed = await asyncio.to_thread(self._write_data_snapshot_sync, snapshot)
                    finally:
                        # A dirty burst can schedule another iteration immediately.
                        # Release the previous full copy before that iteration starts.
                        snapshot = None
                    if snapshot_changed:
                        live_changed = self._sanitize_store_control_tags_inplace(self.data)
                        if live_changed:
                            self._data_save_dirty = True
                            self._log_store_control_cleanup("delayed_save_live_sync", live_changed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._data_save_dirty = True
                should_retry = True
                logger.warning("[PrivateCompanion] 延迟保存数据失败: %s", exc)
            finally:
                self._data_save_task = None
                stop_event = getattr(self, "_stop_event", None)
                stopping = bool(
                    stop_event is not None
                    and callable(getattr(stop_event, "is_set", None))
                    and stop_event.is_set()
                )
                if not stopping and bool(getattr(self, "_data_save_dirty", False)):
                    retry_delay = (
                        max(3.0, min(30.0, float(delay) * 2.0 if delay else 3.0))
                        if should_retry
                        else max(0.0, float(delay))
                    )
                    try:
                        self._schedule_default_data_save(delay=retry_delay)
                    except Exception as retry_exc:
                        logger.warning("[PrivateCompanion] 延迟保存重试调度失败: %s", retry_exc)

        try:
            self._data_save_task = asyncio.create_task(_runner())
        except RuntimeError:
            self._save_data_sync()

    def _schedule_data_save(self, delay: float = 1.5) -> None:
        scoped_scheduler = getattr(self, "_req041_schedule_scoped_sync", None)
        if callable(scoped_scheduler):
            scoped_scheduler()
        if bool(getattr(self, "_group_observation_dirty", False)):
            delay = max(float(delay), 15.0)
            self._group_observation_dirty = False
        active_getter = getattr(self, "_active_persona_scope", None)
        persona_id = str(active_getter() if callable(active_getter) else "").strip()
        if bool(getattr(self, "enable_multi_persona_mode", False)) and persona_id:
            self._schedule_persona_data_save(persona_id, delay)
            return
        self._schedule_default_data_save(delay)

    def _schedule_group_observation_save(self, delay: float = 15.0) -> None:
        """Coalesce high-frequency group observations into a bounded save window."""
        self._schedule_data_save(delay=max(5.0, float(delay)))

    async def _flush_scheduled_data_save(self) -> None:
        """Wait until all coalesced writes, including writes queued while waiting, finish."""
        while True:
            pending: list[asyncio.Task] = []
            task = getattr(self, "_data_save_task", None)
            if isinstance(task, asyncio.Task) and not task.done():
                pending.append(task)
            persona_tasks = getattr(self, "_persona_data_save_tasks", {})
            if isinstance(persona_tasks, dict):
                pending.extend(
                    item
                    for item in persona_tasks.values()
                    if isinstance(item, asyncio.Task) and not item.done()
                )
            if pending:
                await asyncio.gather(*(asyncio.shield(item) for item in pending))
                continue
            legacy_dirty = bool(getattr(self, "_data_save_dirty", False))
            persona_dirty = set(getattr(self, "_persona_data_save_dirty", set()) or set())
            if legacy_dirty or persona_dirty:
                stop_event = getattr(self, "_stop_event", None)
                if (
                    stop_event is not None
                    and callable(getattr(stop_event, "is_set", None))
                    and stop_event.is_set()
                ):
                    return
                for persona_id in persona_dirty:
                    self._schedule_persona_data_save(persona_id, 0.0)
                if legacy_dirty:
                    self._schedule_default_data_save(delay=0.0)
                continue
            return

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

    def _private_event_identity_context(self, event: Any, subject_id: Any) -> dict[str, str]:
        """Build a bounded platform/account identity for a user record."""
        subject = self._normalize_private_identity_id(subject_id) or _single_line(subject_id, 128)
        platform_getter = getattr(self, "_platform_kind_for_event", None)
        platform = platform_getter(event) if callable(platform_getter) else "generic"
        platform = _single_line(platform, 40).lower() or "generic"
        adapter = _single_line(getattr(event, "adapter_instance_id", ""), 120)
        if not adapter:
            origin = _single_line(getattr(event, "unified_msg_origin", ""), 240)
            adapter = origin.split(":", 1)[0] if ":" in origin else origin
            adapter = _single_line(adapter, 80)
        self_getter = getattr(self, "_event_self_id", None)
        bot_id = ""
        if callable(self_getter):
            try:
                bot_id = self._normalize_private_identity_id(self_getter(event))
            except Exception:
                bot_id = ""
        return {
            "subject": subject,
            "platform": platform,
            "adapter": adapter,
            "bot_id": bot_id,
        }

    def _private_user_matches_event_identity(
        self,
        user: Any,
        context: dict[str, str],
    ) -> bool:
        if not isinstance(user, dict) or not isinstance(context, dict):
            return False
        subject = _single_line(context.get("subject"), 128)
        stored_subject = _single_line(user.get("identity_subject_id"), 128)
        if not stored_subject:
            stored_umo = _single_line(user.get("umo") or user.get("last_inbound_umo"), 240)
            parser = getattr(self, "_private_umo_session_id", None)
            if callable(parser) and stored_umo:
                try:
                    stored_subject = _single_line(parser(stored_umo), 128)
                except Exception:
                    stored_subject = ""
        if subject and stored_subject and subject != stored_subject:
            return False
        platform = _single_line(context.get("platform"), 40).lower()
        stored_platform = _single_line(user.get("identity_platform_kind"), 40).lower()
        if not stored_platform:
            umo = _single_line(user.get("umo") or user.get("last_inbound_umo"), 240)
            platform_parser = getattr(self, "_platform_kind_for_umo", None)
            if callable(platform_parser) and umo:
                try:
                    stored_platform = _single_line(platform_parser(umo), 40).lower()
                except Exception:
                    stored_platform = ""
        if not stored_platform or not platform or stored_platform != platform:
            return False
        for field, stored_field in (
            ("adapter", "identity_adapter_instance_id"),
            ("bot_id", "identity_bot_id"),
        ):
            expected = _single_line(context.get(field), 120)
            actual = _single_line(user.get(stored_field), 120)
            # A stamped profile with a missing account marker is not safe to
            # reuse for a concrete adapter/bot event.  Treat it as legacy and
            # let the resolver create an isolated scoped record instead of
            # silently sharing data between Bot accounts.
            if expected and (not actual or expected != actual):
                return False
        return True

    def _event_private_user_storage_id(self, event: Any, user_id: Any) -> str:
        """Resolve a private/group event to a platform-isolated users key."""
        raw = _single_line(user_id, 160)
        normalized = self._normalize_private_identity_id(raw)
        canonical = self._canonical_private_user_id(normalized or raw)
        if not canonical:
            return ""
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(users, dict):
            return canonical
        context = self._private_event_identity_context(event, raw)
        # Reuse a previously isolated record for the same exact platform/account.
        for stored_id, candidate in users.items():
            if self._canonical_private_user_id(str(stored_id or "")) == canonical and self._private_user_matches_event_identity(candidate, context):
                return str(stored_id)
            if isinstance(candidate, dict) and _single_line(candidate.get("identity_subject_id"), 128) == context.get("subject") and self._private_user_matches_event_identity(candidate, context):
                return str(stored_id)
        existing = users.get(canonical)
        if not isinstance(existing, dict) or self._private_user_matches_event_identity(existing, context):
            return canonical
        stored_subject = _single_line(existing.get("identity_subject_id"), 128)
        stored_platform = _single_line(existing.get("identity_platform_kind"), 40).lower()
        if not stored_platform:
            stored_umo = _single_line(existing.get("umo") or existing.get("last_inbound_umo"), 240)
            platform_parser = getattr(self, "_platform_kind_for_umo", None)
            if callable(platform_parser) and stored_umo:
                try:
                    inferred_platform = _single_line(platform_parser(stored_umo), 40).lower()
                except Exception:
                    inferred_platform = ""
                if inferred_platform and inferred_platform != "generic":
                    stored_platform = inferred_platform
        # Claim an unversioned legacy record on its first concrete event. The
        # normal profile path stamps the platform/account immediately, so a
        # later same-ID event from another platform is isolated below.
        if not stored_platform and (not stored_subject or stored_subject == context.get("subject")):
            return canonical
        # Older explicitly managed DM profiles may have a real inbound route
        # but no adapter/bot stamps. Claim them only for the same concrete
        # platform and subject; a different platform or any existing account
        # marker still takes the isolated path below.
        observed_platform = _single_line(context.get("platform"), 40).lower()
        stored_adapter = _single_line(existing.get("identity_adapter_instance_id"), 120)
        stored_bot_id = _single_line(existing.get("identity_bot_id"), 120)
        explicitly_managed = bool(
            existing.get("manual_enabled")
            or existing.get("manual_disabled")
            or existing.get("auto_profile_created")
            or _safe_int(existing.get("private_inbound_count") or 0, 0) > 0
            or _safe_float(existing.get("last_private_seen") or 0, 0.0) > 0
        )
        same_subject = not stored_subject or stored_subject == context.get("subject")
        same_platform = bool(
            stored_platform
            and observed_platform
            and stored_platform == observed_platform
        )
        if explicitly_managed and same_subject and same_platform and not stored_adapter and not stored_bot_id:
            return canonical
        # A configured target is allowed to roll over adapter-instance
        # metadata inside its configured platform. Reuse the canonical record
        # so passive and proactive paths do not split into a disabled shadow.
        # Unconfigured identities still take the isolated digest path below.
        try:
            configured_ids = {
                self._canonical_private_user_id(str(item or "").strip())
                for item in self._configured_target_ids()
                if str(item or "").strip()
            }
        except Exception:
            configured_ids = set()
        if canonical in configured_ids:
            configured_raw = _single_line(getattr(self, "target_platform", ""), 80).lower()
            configured_kind = self._normalize_platform_kind(configured_raw) if configured_raw else "generic"
            observed_kind = _single_line(context.get("platform"), 40).lower()
            compatible = True
            if configured_kind != "generic" and observed_kind not in {"", "generic", configured_kind}:
                compatible = False
            elif configured_kind != "generic" and observed_kind == "generic" and configured_raw:
                adapter = _single_line(context.get("adapter"), 120).lower()
                if adapter and configured_raw not in {adapter, adapter.split(":", 1)[0]}:
                    compatible = False
            elif configured_kind == "generic" and configured_raw:
                adapter = _single_line(context.get("adapter"), 120).lower()
                compatible = not adapter or configured_raw in {adapter, adapter.split(":", 1)[0]}
            if compatible:
                return canonical
        # A conflicting platform/account never inherits the existing record.
        digest = hashlib.sha256(
            f"{context.get('platform','generic')}|{context.get('adapter','')}|{context.get('bot_id','')}|{canonical}".encode("utf-8")
        ).hexdigest()[:16]
        return _single_line(f"{context.get('platform','generic')}:{canonical}:{digest}", 160)

    def _stamp_private_event_identity(self, user: dict[str, Any], event: Any, subject_id: Any) -> None:
        if not isinstance(user, dict):
            return
        context = self._private_event_identity_context(event, subject_id)
        user["identity_subject_id"] = context.get("subject", "")
        user["identity_platform_kind"] = context.get("platform", "generic")
        if context.get("adapter"):
            user["identity_adapter_instance_id"] = context["adapter"]
        if context.get("bot_id"):
            user["identity_bot_id"] = context["bot_id"]

    def _private_user_id_for_event(self, event: Any, user_id: Any = None) -> str:
        """Return the storage key for a raw sender in this event's identity scope."""
        raw = user_id
        if raw is None:
            try:
                raw = event.get_sender_id()
            except Exception:
                raw = ""
        raw_text = _single_line(raw, 160)
        normalizer = getattr(self, "_normalize_private_identity_id", None)
        normalized = normalizer(raw_text) if callable(normalizer) else raw_text
        normalized = normalized or raw_text
        resolver = getattr(self, "_event_private_user_storage_id", None)
        if callable(resolver):
            try:
                resolved = resolver(event, normalized)
            except Exception:
                resolved = ""
            if resolved:
                return _single_line(resolved, 160)
            # A resolver failure on a concrete platform/account must not fall
            # back to a bare sender ID, which would re-open the cross-adapter
            # collision this scoped resolver is meant to prevent.  Preserve a
            # deterministic namespace so the event can still be handled and
            # diagnosed without inheriting another profile.
            try:
                context = self._private_event_identity_context(event, normalized)
            except Exception:
                context = {}
            platform = _single_line(context.get("platform"), 40).lower() if isinstance(context, dict) else ""
            adapter = _single_line(context.get("adapter"), 120) if isinstance(context, dict) else ""
            bot_id = _single_line(context.get("bot_id"), 120) if isinstance(context, dict) else ""
            if platform and platform != "generic" and (adapter or bot_id):
                canonical = _single_line(self._canonical_private_user_id(normalized), 128)
                digest = hashlib.sha256(
                    f"{platform}|{adapter}|{bot_id}|{canonical}".encode("utf-8")
                ).hexdigest()[:16]
                return _single_line(f"{platform}:{canonical}:{digest}", 160)
        return _single_line(self._canonical_private_user_id(normalized), 160)

    @staticmethod
    def _normalize_private_identity_id(value: Any, limit: int = 128) -> str:
        if isinstance(value, (dict, list, tuple, set)):
            return ""
        # Parse the transport wrapper before applying the identity length
        # limit. Otherwise a long adapter/platform prefix can truncate the
        # opaque session ID and create a second, colliding user record.
        text = _single_line(value, max(512, limit + 256))
        if not text:
            return ""
        invalid_exact = {
            "default",
            "aiocqhttp",
            "qq_official",
            "weixin_official_account",
            "dingtalk",
            "friendmessage",
            "groupmessage",
            "friend_message",
            "group_message",
            "umo",
            "uid",
            "none",
            "null",
        }
        umo_match = re.search(r":friendmessage:", text, re.IGNORECASE)
        if umo_match:
            session_id = _single_line(text[umo_match.end():], limit)
            if not session_id or ":" in session_id:
                return ""
            session_lower = session_id.lower()
            if session_lower in invalid_exact or re.search(r"(friendmessage|groupmessage|unified_msg_origin)", session_lower):
                return ""
            return session_id
        text = _single_line(text, limit)
        lower = text.lower()
        if lower in invalid_exact:
            return ""
        if ":" in text:
            return ""
        if re.search(r"(friendmessage|groupmessage|unified_msg_origin)", lower):
            return ""
        return text

    @staticmethod
    def _normalize_group_identity_id(value: Any, limit: int = 160) -> str:
        """Normalize a numeric group ID, opaque platform ID, or GroupMessage UMO."""
        if isinstance(value, (dict, list, tuple, set)):
            return ""
        # The platform prefix is not part of the group identity and must not
        # consume the opaque session ID's length budget.
        text = _single_line(value, max(512, limit + 256))
        if not text:
            return ""
        invalid_exact = {
            "default",
            "aiocqhttp",
            "qq_official",
            "groupmessage",
            "group_message",
            "group",
            "friendmessage",
            "friend_message",
            "umo",
            "uid",
            "none",
            "null",
        }
        umo_match = re.search(r":groupmessage:", text, re.IGNORECASE)
        if umo_match:
            text = _single_line(text[umo_match.end():], limit)
            if not text or ":" in text:
                return ""
        else:
            text = _single_line(text, limit)
        lower = text.lower()
        if lower in invalid_exact or ":" in text:
            return ""
        if re.search(r"(friendmessage|groupmessage|unified_msg_origin)", lower):
            return ""
        return text

    @staticmethod
    def _group_merge_list_identity(field: str, item: Any) -> tuple[Any, ...] | None:
        """Return a stable identity for group history entries when one exists."""
        if not isinstance(item, dict):
            try:
                return ("value", json.dumps(item, ensure_ascii=False, sort_keys=True))
            except (TypeError, ValueError):
                return ("value", repr(item))

        if field == "group_episodes":
            episode_id = item.get("id")
            if episode_id not in (None, ""):
                return ("id", str(episode_id))
            return (
                "episode",
                str(item.get("created_ts") or ""),
                str(item.get("date") or ""),
                str(item.get("summary") or ""),
            )

        identity_fields = {
            "topic_threads": ("signature", "topic_id", "id"),
            "slang_terms": ("term", "text", "id"),
            "recent_messages": ("message_id", "id"),
            "pending_atrelay_tasks": ("task_id", "id"),
            "group_wakeup_logs": ("trace_id", "id"),
        }.get(field, ("id", "event_id", "trace_id"))
        for key in identity_fields:
            value = item.get(key)
            if value not in (None, ""):
                return (key, str(value))

        if field == "recent_messages":
            return (
                "message",
                str(item.get("ts") or ""),
                str(item.get("sender_id") or ""),
                str(item.get("text") or ""),
            )
        try:
            return ("value", json.dumps(item, ensure_ascii=False, sort_keys=True))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _group_merge_records_equal(target: dict[str, Any], source: dict[str, Any]) -> bool:
        """Detect copied alias records so counters are not added twice."""
        ignored = {"group_id", "umo", "alias_group_ids", "umo_aliases"}
        target_body = {key: value for key, value in target.items() if key not in ignored}
        source_body = {key: value for key, value in source.items() if key not in ignored}
        return target_body == source_body

    def _merge_group_list_values(self, target: list[Any], source: list[Any], field: str) -> None:
        identities: dict[tuple[Any, ...], int] = {}
        for index, item in enumerate(target):
            identity = self._group_merge_list_identity(field, item)
            if identity is not None:
                identities.setdefault(identity, index)

        for item in source:
            identity = self._group_merge_list_identity(field, item)
            matched_index = identities.get(identity) if identity is not None else None
            if matched_index is None:
                target.append(deepcopy(item))
                if identity is not None:
                    identities[identity] = len(target) - 1
                continue
            existing = target[matched_index]
            if isinstance(existing, dict) and isinstance(item, dict) and existing != item:
                self._merge_group_mapping_values(existing, item)

    def _merge_group_mapping_values(
        self,
        target: dict[str, Any],
        source: dict[str, Any],
        *,
        numeric_values_are_counts: bool = False,
    ) -> None:
        """Recursively merge independently accumulated group observations."""
        for key, value in source.items():
            if key == "group_id":
                continue
            existing = target.get(key)
            if isinstance(value, dict):
                if not isinstance(existing, dict):
                    if existing in (None, "", [], {}):
                        target[key] = deepcopy(value)
                    continue
                if existing != value:
                    self._merge_group_mapping_values(
                        existing,
                        value,
                        numeric_values_are_counts=(
                            key in {"tone", "counts", "counters", "feedback_counts", "reaction_counts"}
                            or key.endswith("_counts")
                        ),
                    )
                continue
            if isinstance(value, list):
                if not isinstance(existing, list):
                    if existing in (None, "", [], {}):
                        target[key] = deepcopy(value)
                    continue
                self._merge_group_list_values(existing, value, key)
                continue
            if isinstance(value, bool):
                if key not in target:
                    target[key] = value
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if numeric_values_are_counts or key == "count" or key.endswith("_count") or key.endswith("_today"):
                    target[key] = _safe_float(existing, 0.0, 0.0) + _safe_float(value, 0.0, 0.0)
                    if isinstance(existing, int) and isinstance(value, int):
                        target[key] = int(target[key])
                elif key == "last_seen" or key.endswith("_at") or key.endswith("_ts"):
                    target[key] = max(_safe_float(existing, 0.0, 0.0), _safe_float(value, 0.0, 0.0))
                elif existing in (None, "", 0):
                    target[key] = deepcopy(value)
                continue
            if existing in (None, "", [], {}):
                target[key] = deepcopy(value)

    def _merge_group_record_values(
        self,
        target: dict[str, Any],
        source: dict[str, Any],
        alias_id: Any,
    ) -> None:
        if target is source:
            return

        copied_alias = self._group_merge_records_equal(target, source)
        target_manual = _single_line(target.get("manual_group_name"), 80)
        source_manual = _single_line(source.get("manual_group_name"), 80)
        target_manual_updated = _safe_float(target.get("manual_group_name_updated_at"), 0.0, 0.0)
        source_manual_updated = _safe_float(source.get("manual_group_name_updated_at"), 0.0, 0.0)
        known_names: list[str] = []
        for candidate in (
            *(target.get("group_name_aliases") if isinstance(target.get("group_name_aliases"), list) else []),
            target_manual,
            source_manual,
            source.get("name"),
            source.get("group_name"),
        ):
            name = _single_line(candidate, 80)
            if name and name not in known_names:
                known_names.append(name)

        if not copied_alias:
            self._merge_group_mapping_values(target, source)

        if source_manual and (not target_manual or source_manual_updated > target_manual_updated):
            target_manual = source_manual
            target["manual_group_name"] = source_manual
            if source_manual_updated:
                target["manual_group_name_updated_at"] = source_manual_updated
        if known_names:
            target["group_name_aliases"] = known_names
        if target_manual:
            target["manual_group_name"] = target_manual
            target["name"] = target_manual
            target["group_name"] = target_manual
            target["group_name_source"] = "manual"

        aliases = target.setdefault("alias_group_ids", [])
        if not isinstance(aliases, list):
            aliases = []
            target["alias_group_ids"] = aliases
        source_aliases = source.get("alias_group_ids") if isinstance(source.get("alias_group_ids"), list) else []
        for candidate in (alias_id, *source_aliases):
            alias = _single_line(candidate, 512)
            if alias and alias not in aliases:
                aliases.append(alias)

        umo_aliases = target.setdefault("umo_aliases", [])
        if not isinstance(umo_aliases, list):
            umo_aliases = []
            target["umo_aliases"] = umo_aliases
        for candidate in (source.get("umo"), alias_id):
            umo = _single_line(candidate, 512)
            if ":groupmessage:" in umo.lower() and umo not in umo_aliases:
                umo_aliases.append(umo)

    def _canonicalize_group_records(self, canonical_id: str) -> dict[str, Any]:
        """Re-key and merge equivalent records in the active persona store only."""
        groups = self.data.setdefault("groups", {})
        if not isinstance(groups, dict):
            groups = {}
            self.data["groups"] = groups

        matches: list[tuple[Any, dict[str, Any]]] = []
        for raw_key, raw_group in list(groups.items()):
            if not isinstance(raw_group, dict):
                continue
            identities = {
                self._normalize_group_identity_id(raw_key),
                self._normalize_group_identity_id(raw_group.get("group_id")),
                self._normalize_group_identity_id(raw_group.get("umo")),
            }
            if canonical_id in identities:
                matches.append((raw_key, raw_group))

        canonical_group = groups.get(canonical_id)
        if not isinstance(canonical_group, dict):
            canonical_group = matches[0][1] if matches else deepcopy(_DEFAULT_GROUP_TEMPLATE)
            groups[canonical_id] = canonical_group

        for raw_key, source in matches:
            if source is not canonical_group:
                self._merge_group_record_values(canonical_group, source, raw_key)
            elif raw_key != canonical_id:
                aliases = canonical_group.setdefault("alias_group_ids", [])
                if not isinstance(aliases, list):
                    aliases = []
                    canonical_group["alias_group_ids"] = aliases
                alias = _single_line(raw_key, 512)
                if alias and alias not in aliases:
                    aliases.append(alias)
            if raw_key != canonical_id:
                groups.pop(raw_key, None)

        canonical_group["group_id"] = canonical_id
        return canonical_group

    def _merge_user_record_values(self, target: dict[str, Any], source: dict[str, Any], alias_id: str) -> None:
        # Alias records can span the v1/v2 score boundary. Normalize both sides
        # before additive fields are combined so their units never mix.
        migration_now = _now_ts()
        migrate_legacy_relationship_score(
            target,
            created=False,
            now=migration_now,
            record_id=target.get("user_id"),
        )
        migrate_legacy_relationship_score(
            source,
            created=False,
            now=migration_now,
            record_id=source.get("user_id") or alias_id,
        )
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
            elif key == "req041_relationship_source_revision":
                target[key] = max(_safe_int(target.get(key), 0), _safe_int(value, 0))
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
        users = self.data.setdefault("users", {})
        changed = False
        migration_now = _now_ts()

        def merge_transport_identity_records() -> bool:
            """Fold legacy full-UMO user keys into their stable private identity."""
            normalizer = getattr(self, "_normalize_private_identity_id", None)
            if not callable(normalizer):
                return False
            transport_changed = False
            for raw_user_id, source in list(users.items()):
                raw_id = str(raw_user_id or "").strip()
                if ":FriendMessage:" not in raw_id or not isinstance(source, dict):
                    continue
                normalized_id = normalizer(raw_id)
                canonical_id = self._canonical_private_user_id(normalized_id) if normalized_id else ""
                if not canonical_id or canonical_id == raw_id:
                    continue
                target = users.get(canonical_id)
                if isinstance(target, dict):
                    self._merge_user_record_values(target, source, raw_id)
                else:
                    target = source
                    users[canonical_id] = target
                target["user_id"] = canonical_id
                raw_aliases = target.get("alias_user_ids")
                if isinstance(raw_aliases, list):
                    target["alias_user_ids"] = [
                        item for item in raw_aliases if str(item or "").strip() != raw_id
                    ]
                users.pop(raw_user_id, None)
                transport_changed = True
                logger.info(
                    "[PrivateCompanion] 已归一旧私聊 UMO 用户键: old=%s user=%s",
                    _single_line(raw_id, 120),
                    _single_line(canonical_id, 80),
                )
            return transport_changed

        backups = self.data.setdefault("private_user_alias_merge_backups", {})
        if not isinstance(backups, dict):
            backups = {}
            self.data["private_user_alias_merge_backups"] = backups
            changed = True

        # Keep alias records recoverable when an operator removes a mapping later.
        # The merged canonical record is intentionally retained; only the pre-merge
        # alias snapshot is restored, so activity recorded after the merge is not lost.
        active_aliases = {str(alias or "").strip() for alias in aliases}
        for raw_alias_id, raw_backup in list(backups.items()):
            alias_id = str(raw_alias_id or "").strip()
            if not alias_id or alias_id in active_aliases or not isinstance(raw_backup, dict):
                continue
            source = raw_backup.get("source")
            if not isinstance(source, dict):
                source = deepcopy(_DEFAULT_USER_TEMPLATE)
            source = deepcopy(source)
            source["user_id"] = alias_id
            if alias_id not in users:
                users[alias_id] = source
                changed = True
            canonical_id = str(raw_backup.get("canonical_id") or "").strip()
            target = users.get(canonical_id)
            if isinstance(target, dict):
                target_aliases = target.get("alias_user_ids")
                if isinstance(target_aliases, list) and alias_id in target_aliases:
                    target["alias_user_ids"] = [
                        item for item in target_aliases if str(item or "").strip() != alias_id
                    ]
                    changed = True
            backups.pop(raw_alias_id, None)
            changed = True

        # Startup maintenance must cover every persisted user, even when no
        # alias mapping is configured for this installation.
        for raw_user_id, raw_user in list(users.items()):
            if not isinstance(raw_user, dict):
                continue
            migration = migrate_legacy_relationship_score(
                raw_user,
                created=False,
                now=migration_now,
                record_id=raw_user_id,
            )
            changed = changed or bool(migration.get("changed"))
        # Older versions removed alias records without retaining a snapshot.
        # Recreate a clean identity from the canonical record's alias list when
        # that alias is no longer configured, even if other mappings remain.
        for canonical_id, user in list(users.items()):
            if not isinstance(user, dict):
                continue
            raw_alias_ids = user.get("alias_user_ids")
            if not isinstance(raw_alias_ids, list):
                continue
            kept_alias_ids: list[Any] = []
            for raw_alias_id in raw_alias_ids:
                alias_id = str(raw_alias_id or "").strip()
                if not alias_id or alias_id == str(canonical_id or "").strip():
                    continue
                if alias_id in active_aliases:
                    kept_alias_ids.append(raw_alias_id)
                    continue
                if alias_id not in users:
                    restored = deepcopy(_DEFAULT_USER_TEMPLATE)
                    restored["user_id"] = alias_id
                    users[alias_id] = restored
                changed = True
            if kept_alias_ids != raw_alias_ids:
                user["alias_user_ids"] = kept_alias_ids
                changed = True
        if not aliases:
            return merge_transport_identity_records() or changed
        for alias_id, canonical_id in list(aliases.items()):
            alias_id = str(alias_id or "").strip()
            canonical_id = self._canonical_private_user_id(canonical_id)
            if not alias_id or not canonical_id or alias_id == canonical_id:
                continue
            backup = backups.get(alias_id)
            source = users.get(alias_id)
            previous_canonical = (
                str(backup.get("canonical_id") or "").strip()
                if isinstance(backup, dict)
                else ""
            )
            if (
                not isinstance(source, dict)
                and isinstance(backup, dict)
                and previous_canonical
                and previous_canonical != canonical_id
            ):
                restored_source = backup.get("source")
                if isinstance(restored_source, dict):
                    source = deepcopy(restored_source)
                    source["user_id"] = alias_id
                    users[alias_id] = source
                    changed = True
            if not isinstance(source, dict):
                continue
            if isinstance(backup, dict):
                if previous_canonical and previous_canonical != canonical_id:
                    previous_target = users.get(previous_canonical)
                    previous_target_aliases = (
                        previous_target.get("alias_user_ids")
                        if isinstance(previous_target, dict)
                        else None
                    )
                    if isinstance(previous_target_aliases, list) and alias_id in previous_target_aliases:
                        previous_target["alias_user_ids"] = [
                            item
                            for item in previous_target_aliases
                            if str(item or "").strip() != alias_id
                        ]
                    changed = True
                backup["canonical_id"] = canonical_id
            else:
                backups[alias_id] = {
                    "canonical_id": canonical_id,
                    "source": deepcopy(source),
                }
                changed = True
            target_created = canonical_id not in users
            target = users.setdefault(canonical_id, deepcopy(_DEFAULT_USER_TEMPLATE))
            target["user_id"] = canonical_id
            target_migration = migrate_legacy_relationship_score(
                target,
                created=target_created,
                now=migration_now,
                record_id=canonical_id,
            )
            source_migration = migrate_legacy_relationship_score(
                source,
                created=False,
                now=migration_now,
                record_id=alias_id,
            )
            changed = changed or bool(target_migration.get("changed")) or bool(source_migration.get("changed"))
            self._merge_user_record_values(target, source, alias_id)
            users.pop(alias_id, None)
            changed = True
        return merge_transport_identity_records() or changed

    def _private_user_has_group_observation_evidence(self, user_id: str, user: dict[str, Any]) -> bool:
        """Whether a disabled users row was materialized only from group observation."""
        if not user_id or not isinstance(user, dict):
            return False
        if bool(user.get("observation_only")):
            return True
        if _single_line(user.get("profile_origin"), 40).lower() == "group_observation":
            return True

        subject_id = _single_line(user.get("identity_subject_id"), 160)
        if not subject_id:
            subject_id = self._canonical_private_user_id(str(user_id or "").strip())
        profiles = self.data.get("worldbook_member_profiles") if isinstance(getattr(self, "data", None), dict) else {}
        observation = profiles.get(subject_id) if isinstance(profiles, dict) else None
        if isinstance(observation, dict) and bool(observation.get("observation_only")):
            return True

        person_id = _single_line(user.get("unified_person_id"), 80)
        root = self.data.get("unified_person") if isinstance(getattr(self, "data", None), dict) else {}
        if not person_id or not isinstance(root, dict):
            return False
        links = root.get("identity_links")
        checkpoints = root.get("binding_checkpoints")
        has_group_creation = any(
            isinstance(item, dict)
            and _single_line(item.get("person_id"), 80) == person_id
            and _single_line(item.get("last_operation_id"), 160).startswith("req036.group_observation:")
            for item in (links.values() if isinstance(links, dict) else [])
        )
        person_checkpoints = [
            item
            for item in (checkpoints.values() if isinstance(checkpoints, dict) else [])
            if isinstance(item, dict) and _single_line(item.get("person_id"), 80) == person_id
        ]
        has_private_source = any(
            _single_line(item.get("last_source_scope"), 160).lower() in {"private", "dm"}
            or _single_line(item.get("last_source_scope"), 160).lower().startswith(("private:", "dm:"))
            for item in person_checkpoints
        )
        return bool(has_group_creation and not has_private_source)

    def _private_user_is_reaction_only_shadow(self, user_id: str, user: dict[str, Any]) -> bool:
        """Whether a scoped row only mirrors a canonical user's reaction cache."""
        match = re.fullmatch(r"([a-z0-9_]+):([^:]+):([0-9a-f]{16})", str(user_id or "").strip().lower())
        if not match or not isinstance(user, dict):
            return False
        platform_kind, canonical_id, _digest = match.groups()
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        canonical = users.get(canonical_id) if isinstance(users, dict) else None
        if not isinstance(canonical, dict) or canonical is user:
            return False
        if _single_line(user.get("identity_platform_kind"), 40):
            return False
        if _single_line(user.get("last_inbound_umo"), 240):
            return False
        reaction = user.get("reaction_expression")
        scopes = reaction.get("scopes") if isinstance(reaction, dict) else None
        if not isinstance(scopes, dict) or not scopes:
            return False
        expected_marker = f":FriendMessage:{canonical_id}"
        if any(expected_marker not in _single_line(scope, 240) for scope in scopes):
            return False
        canonical_umo = _single_line(canonical.get("last_inbound_umo") or canonical.get("umo"), 240)
        if expected_marker not in canonical_umo:
            return False
        platform_parser = getattr(self, "_platform_kind_for_umo", None)
        if callable(platform_parser):
            try:
                canonical_platform = _single_line(platform_parser(canonical_umo), 40).lower()
            except Exception:
                canonical_platform = ""
            if canonical_platform not in {"", "generic", platform_kind}:
                return False
        return True

    def _private_user_has_private_footprint(self, user_id: str, user: dict[str, Any]) -> bool:
        """Whether a stored user has evidence that it belongs in private chat."""
        if not user_id or not isinstance(user, dict):
            return False
        try:
            configured_targets = {
                str(item).strip()
                for item in self._configured_target_ids()
                if str(item).strip()
            }
        except Exception:
            configured_targets = set()
        if user_id in configured_targets:
            return True
        if bool(user.get("enabled")) or bool(user.get("manual_enabled")) or bool(user.get("manual_disabled")):
            return True
        if self._normalize_private_user_role(user.get("relationship_role")) == "owner":
            return True
        profile_origin = _single_line(user.get("profile_origin"), 40).lower()
        if profile_origin in {"manual", "administrator", "private", "private_auto"}:
            return True
        if bool(user.get("auto_profile_created")):
            return True

        # ``umo`` alone is not proof of a DM. Legacy group observation rows
        # were assigned a synthetic ``default:FriendMessage:<id>`` fallback
        # before any private event was received. Inbound and bound routes are
        # only written by real private delivery paths.
        for key in ("last_inbound_umo", "bound_delivery_umo", "preferred_delivery_umo"):
            route = _single_line(user.get(key), 300)
            if route and ":GroupMessage:" not in route:
                return True
        routes = user.get("private_delivery_routes")
        if isinstance(routes, (dict, list)) and routes:
            return True

        numeric_activity_keys = (
            "last_sent",
            "last_user_message_at",
            "last_companion_message_at",
            "last_reply_at",
            "last_private_seen",
            "last_private_activity_at",
            "last_private_reply_at",
            "private_inbound_count",
            "reply_count",
            "proactive_sent_count",
        )
        if any(_safe_float(user.get(key), 0.0, 0.0) > 0 for key in numeric_activity_keys):
            return True

        text_activity_keys = (
            "last_user_message",
            "last_companion_message",
            "last_proactive_reason",
            "last_proactive_action",
            "last_proactive_behavior_summary",
            "last_proactive_motive",
        )
        if any(bool(_single_line(user.get(key), 240)) for key in text_activity_keys):
            return True

        structured_activity_keys = (
            "companion_memory",
            "expression_profile",
            "intent_profile",
            "relationship_state",
            "persona_relationship",
            "dialogue_episodes",
            "open_loops",
            "action_preferences",
            "action_consequences",
            "state_continuity",
            "pending_followup_event",
            "suspended_proactive",
            "simulation_mode",
            "llm_timer_event",
            "planned_event_chain",
            "greetings_sent",
            "behavior_habits",
        )

        def has_structured_activity(value: Any) -> bool:
            if isinstance(value, dict):
                return any(has_structured_activity(item) for item in value.values())
            if isinstance(value, list):
                return any(has_structured_activity(item) for item in value)
            if isinstance(value, str):
                return bool(value.strip())
            return value not in (None, False, 0)

        if any(has_structured_activity(user.get(key)) for key in structured_activity_keys):
            return True
        ledger = user.get("relationship_ledger")
        if isinstance(ledger, list) and any(
            isinstance(item, dict)
            and _single_line(item.get("reason_code"), 80).lower() not in {"", "group_inbound"}
            for item in ledger
        ):
            return True
        aliases = user.get("alias_user_ids")
        if isinstance(aliases, list) and any(
            ":FriendMessage:" in _single_line(item, 240)
            for item in aliases
        ):
            return True
        group_only_ledger = bool(ledger) and all(
            isinstance(item, dict)
            and _single_line(item.get("reason_code"), 80).lower() == "group_inbound"
            for item in ledger
        )
        if _safe_float(user.get("relationship_score"), 0.0) != 0 and not group_only_ledger:
            return True

        default_nickname = _single_line(getattr(self, "default_nickname", ""), 40)
        nickname = _single_line(user.get("nickname"), 40)
        if nickname and nickname != default_nickname:
            return True
        default_style = _single_line(getattr(self, "default_style", ""), 120)
        style = _single_line(user.get("style"), 120)
        return bool(style and style != default_style)

    def _cleanup_orphan_reaction_expression_users(self) -> bool:
        """Remove group-only placeholders from the private-user table.

        Old group observation and reaction paths could create a user record and
        then attach transient group caches to it.  Those caches are not private
        chat evidence; explicitly managed users and records with real private
        activity remain untouched.
        """
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else None
        if not isinstance(users, dict) or not users:
            return False
        removed: list[str] = []

        for raw_user_id, user in list(users.items()):
            user_id = self._canonical_private_user_id(str(raw_user_id or "").strip())
            if not user_id or not isinstance(user, dict):
                continue
            if self._is_bot_self_user_id(user_id):
                continue
            cleanup_evidence = self._private_user_has_group_observation_evidence(user_id, user)
            cleanup_evidence = cleanup_evidence or self._private_user_is_reaction_only_shadow(user_id, user)
            if not cleanup_evidence:
                continue
            if self._private_user_has_private_footprint(user_id, user):
                continue
            users.pop(raw_user_id, None)
            removed.append(user_id)

        if removed:
            logger.info(
                "[PrivateCompanion] 已清理群聊链路遗留的私聊占位记录: count=%s ids=%s",
                len(removed),
                ",".join(removed[:12]),
            )
            return True
        return False

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
            "主要用户": "owner",
            "目标用户": "owner",
            "friend": "friend",
            "social": "friend",
            "guest": "friend",
            "朋友": "friend",
            "好友": "friend",
            "普通朋友": "friend",
            "次要用户": "friend",
        }
        return mapping.get(text, "")

    @staticmethod
    def _private_user_role_label(role: str) -> str:
        return "主要用户" if role == "owner" else "次要用户"

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
            "主要用户",
            "次要用户",
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

    def _ensure_relationship_user_state(self, user: dict[str, Any], *, created: bool = False) -> bool:
        """Lazily normalize additive relationship fields without migrating user identity or data paths."""
        # The user-visible affinity master switch is intentionally a hard
        # runtime boundary: archived relationship data remains readable, but
        # it must not be normalized, decayed or otherwise changed while off.
        if not bool(getattr(self, "enable_custom_relationship_stage_policy", False)):
            return False
        before = {
            "relationship_mode": user.get("relationship_mode"),
            "relationship_score": user.get("relationship_score"),
            "relationship_score_schema_version": user.get("relationship_score_schema_version"),
            "relationship_positive_stage_cap_key": user.get("relationship_positive_stage_cap_key"),
            "normal_interaction_band_cap": user.get("normal_interaction_band_cap"),
            "current_interaction": deepcopy(user.get("current_interaction")),
            "relationship_decay_settled_day": user.get("relationship_decay_settled_day"),
            "relationship_last_decay_stage_drop_at": user.get("relationship_last_decay_stage_drop_at"),
        }
        score_migration = migrate_legacy_relationship_score(
            user,
            created=created,
            now=_now_ts(),
            record_id=user.get("user_id"),
        )
        user["relationship_mode"] = normalize_relationship_mode(
            user.get("relationship_mode"),
            user.get("relationship_role"),
        )
        positive_cap = normalize_relationship_positive_stage_cap_key(
            getattr(self, "relationship_positive_stage_cap_key", "close")
        )
        interaction_cap = normalize_normal_interaction_band_cap(
            getattr(self, "normal_interaction_band_cap", "warm")
        )
        user["relationship_positive_stage_cap_key"] = positive_cap
        user["normal_interaction_band_cap"] = interaction_cap
        clamp_relationship_positive_stage_cap(user, cap_key=positive_cap)
        raw_interaction = user.get("current_interaction")
        if created and not raw_interaction:
            raw_interaction = {
                "expression_band": str(getattr(self, "default_interaction_band", "relaxed") or "relaxed"),
                "source": "default_profile",
                "reason": "profile_created",
                "updated_at": _now_ts(),
                "expires_at": 0,
                "manual_override": False,
            }
        user["current_interaction"] = current_interaction_projection(
            raw_interaction,
            relationship_role=user.get("relationship_role"),
            relationship_mode=user.get("relationship_mode"),
            relationship_score=user.get("relationship_score"),
            normal_interaction_band_cap=interaction_cap,
            now=_now_ts(),
        )
        apply_natural_relationship_decay(
            user,
            grace_days=int(getattr(self, "relationship_decay_grace_days", 3)),
            early_rate=int(getattr(self, "relationship_decay_early_per_day", 2)),
            middle_rate=int(getattr(self, "relationship_decay_middle_per_day", 5)),
            late_rate=int(getattr(self, "relationship_decay_late_per_day", 8)),
            policy=(
                getattr(self, "relationship_stage_policy", None)
                if bool(getattr(self, "enable_custom_relationship_stage_policy", False))
                else None
            ),
            timezone_name=getattr(self, "environment_perception_timezone", None),
        )
        after = {
            "relationship_mode": user.get("relationship_mode"),
            "relationship_score": user.get("relationship_score"),
            "relationship_score_schema_version": user.get("relationship_score_schema_version"),
            "relationship_positive_stage_cap_key": user.get("relationship_positive_stage_cap_key"),
            "normal_interaction_band_cap": user.get("normal_interaction_band_cap"),
            "current_interaction": user.get("current_interaction"),
            "relationship_decay_settled_day": user.get("relationship_decay_settled_day"),
            "relationship_last_decay_stage_drop_at": user.get("relationship_last_decay_stage_drop_at"),
        }
        changed = before != after or bool(score_migration.get("changed"))
        if changed:
            snapshot_emitter = getattr(self, "_req041_emit_relationship_snapshot", None)
            if callable(snapshot_emitter):
                snapshot_emitter(user, reason_code="relationship_state_normalized")
        return changed

    def _apply_relationship_event(
        self,
        user: dict[str, Any],
        delta: int,
        *,
        reason_code: str,
        event_id: str = "",
        now: float | None = None,
        req041_group_admission_event_id: str = "",
    ) -> dict[str, Any]:
        if bool(getattr(self, "enable_p4_b_legacy_score_isolation", False)):
            return {
                "changed": False,
                "code": "p4_legacy_score_isolated",
                "score": user.get("relationship_score"),
            }
        if not bool(getattr(self, "enable_custom_relationship_stage_policy", False)):
            return {
                "changed": False,
                "code": "relationship_system_disabled",
                "score": user.get("relationship_score"),
            }
        score_migration = migrate_legacy_relationship_score(
            user,
            created=False,
            now=now,
            record_id=user.get("user_id"),
        )
        # A secondary user's ordinary positive events are paused while a
        # verified boundary violation is still unrecovered. Explicit apology
        # recovery remains available through its dedicated reason code.
        if delta > 0 and str(reason_code) not in {"relationship_violation_recovery"}:
            violation = user.get("relationship_violation")
            recovery_settler = getattr(self, "_settle_relationship_violation_recovery", None)
            if isinstance(violation, dict) and callable(recovery_settler):
                recovery_settler(user, now=_now_ts() if now is None else _safe_float(now, _now_ts(), 0))
                violation = user.get("relationship_violation")
            try:
                role = self._private_user_role(user, str(user.get("user_id") or ""))
            except Exception:
                role = str(user.get("relationship_role") or "friend")
            try:
                pending_points = int(violation.get("unrecovered_points") or 0) if isinstance(violation, dict) else 0
            except (TypeError, ValueError):
                pending_points = 0
            if str(role).strip().lower() != "owner" and pending_points > 0:
                if score_migration.get("changed"):
                    self._schedule_data_save()
                return {
                    "changed": False,
                    "code": "relationship_violation_recovery_pending",
                    "score": user.get("relationship_score"),
                    "delta": 0,
                }
        result = apply_relationship_event(
            user,
            delta,
            reason_code=reason_code,
            event_id=event_id or None,
            now=now,
            positive_daily_cap=int(getattr(self, "relationship_positive_daily_cap", 12)),
            event_window_seconds=int(getattr(self, "relationship_event_window_minutes", 30)) * 60,
            positive_event_cap=int(getattr(self, "relationship_positive_event_cap", 4)),
            negative_event_cap=int(getattr(self, "relationship_negative_event_cap", 12)),
            positive_stage_cap_key=getattr(self, "relationship_positive_stage_cap_key", "close"),
            timezone_name=getattr(self, "environment_perception_timezone", None),
        )
        producer = getattr(self, "req041_dual_write_producer", None)
        if result.get("changed") and producer is not None:
            try:
                try:
                    source_revision = max(0, int(user.get("req041_relationship_source_revision") or 0)) + 1
                except (TypeError, ValueError, OverflowError):
                    source_revision = 1
                registry_getter = getattr(self, "_active_unified_person_registry", None)
                registry = registry_getter() if callable(registry_getter) else None
                if registry is None:
                    raise RuntimeError("dual_write_registry_unavailable")
                scope_getter = getattr(self, "_unified_persona_domain", None)
                source_scope = scope_getter() if callable(scope_getter) else ""
                dual_write = producer.emit_relationship(
                    registry=registry,
                    user=user,
                    requested_delta=delta,
                    reason_code=str(reason_code or ""),
                    result=result,
                    source_scope=source_scope or "default",
                    source_revision=source_revision,
                    group_admission_event_id=req041_group_admission_event_id,
                )
                if int(dual_write.get("source_revision") or 0) > 0:
                    user["req041_relationship_source_revision"] = int(dual_write["source_revision"])
                result["req041_dual_write"] = str(dual_write.get("status") or "unknown")
                result["req041_dual_write_code"] = str(dual_write.get("code") or "")
            except Exception as exc:
                producer.fail_closed("relationship_dual_write_failed")
                result["req041_dual_write"] = "failed"
                result["req041_dual_write_code"] = "relationship_dual_write_failed"
                migration_status = getattr(self, "req041_migration_status", None)
                if isinstance(migration_status, dict):
                    migration_status.update({
                        "state": "paused",
                        "code": "relationship_dual_write_failed",
                        "dual_write": "failed",
                    })
                logger.warning(
                    "[PrivateCompanion] REQ-041 关系双写失败，已暂停新读切换并保留 legacy 写入: %s",
                    _single_line(exc, 160),
                )
        if result.get("changed") or score_migration.get("changed"):
            self._schedule_data_save()
        return result

    def _get_user(self, user_id: str) -> dict[str, Any]:
        original_user_id = str(user_id or "").strip()
        # Some platform adapters expose the complete private UMO as sender_id.
        # Keep the conversation route in ``umo`` but use its stable session ID
        # as the private-user record key, so the identity page never treats a
        # transport origin as a QQ user.
        normalized_identity = self._normalize_private_identity_id(original_user_id)
        if normalized_identity:
            original_user_id = normalized_identity
        user_id = self._canonical_private_user_id(original_user_id)
        users = self.data.setdefault("users", {})
        alias_migration_changed = False
        if original_user_id and original_user_id != user_id and original_user_id in users:
            target_created = user_id not in users
            target = users.setdefault(user_id, {})
            target["user_id"] = user_id
            source = users.pop(original_user_id)
            if isinstance(source, dict):
                migration_now = _now_ts()
                target_migration = migrate_legacy_relationship_score(
                    target,
                    created=target_created,
                    now=migration_now,
                    record_id=user_id,
                )
                source_migration = migrate_legacy_relationship_score(
                    source,
                    created=False,
                    now=migration_now,
                    record_id=original_user_id,
                )
                alias_migration_changed = bool(
                    target_migration.get("changed") or source_migration.get("changed")
                )
                self._merge_user_record_values(target, source, original_user_id)
        created = user_id not in users
        user = users.setdefault(user_id, deepcopy(_DEFAULT_USER_TEMPLATE))
        user["user_id"] = user_id
        if original_user_id and original_user_id != user_id:
            aliases = user.setdefault("alias_user_ids", [])
            if isinstance(aliases, list) and original_user_id not in aliases:
                aliases.append(original_user_id)
        if not created:
            # Capture legacy permission while the record still contains only
            # persisted evidence. The default template intentionally supports
            # old installs and must not manufacture an enabled signal here.
            # This also repairs a late-imported default-closed document when
            # its manual/automatic legacy grant remains present.
            ensure_legacy_profile_capabilities(user)
        for key, default_value in _DEFAULT_USER_TEMPLATE.items():
            if key not in user:
                user[key] = deepcopy(default_value)
        self._ensure_private_user_role(user_id, user)
        relationship_changed = self._ensure_relationship_user_state(user, created=created)
        user.setdefault("manual_enabled", False)
        user.setdefault("manual_disabled", False)
        # Compatibility mirror only: passive private chat is always available.
        user["enabled"] = True
        if not user.get("nickname"):
            user["nickname"] = self.default_nickname
        if not user.get("style"):
            user["style"] = self.default_style
        if relationship_changed or alias_migration_changed:
            self._schedule_data_save()
        return user

    def _auto_profile_platform_set(self) -> set[str]:
        raw = getattr(self, "auto_profile_platforms", None)
        if isinstance(raw, str):
            items = re.split(r"[\s,，、;；]+", raw)
        elif isinstance(raw, (list, tuple, set)):
            items = list(raw)
        else:
            items = []
        normalized = {
            self._normalize_platform_kind(item)
            for item in items
            if str(item or "").strip()
        }
        return normalized or {"onebot", "qq_official", "telegram", "webchat", "generic"}

    def _auto_profile_nickname(self, user_id: str, sender_display_name: str) -> str:
        strategy = str(getattr(self, "default_nickname_strategy", "platform_display_name") or "").strip()
        fixed = _single_line(getattr(self, "default_nickname", "你"), 24) or "你"
        observed = _single_line(sender_display_name, 24)
        generic = {"用户", "主人", "主要用户", "默认用户", "unknown", "未知"}
        if strategy == "fixed":
            return fixed
        if strategy == "user_id":
            return _single_line(user_id, 24) or fixed
        if observed and observed.lower() not in generic:
            return observed
        return fixed or _single_line(user_id, 24)

    def _ensure_auto_private_user_profile(
        self,
        event: Any,
        *,
        user_id: str,
        sender_display_name: str = "",
        now: float | None = None,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Create a minimal private profile using the configured permission defaults."""
        raw_user_id = str(user_id or "").strip()
        identity_normalizer = getattr(self, "_normalize_private_identity_id", None)
        normalized_user_id = identity_normalizer(raw_user_id) if callable(identity_normalizer) else ""
        resolver = getattr(self, "_event_private_user_storage_id", None)
        canonical_user_id = (
            resolver(event, normalized_user_id or raw_user_id)
            if callable(resolver)
            else self._canonical_private_user_id(normalized_user_id or raw_user_id)
        )
        if not canonical_user_id or self._is_bot_self_user_id(canonical_user_id):
            return None, False
        platform_kind = self._platform_kind_for_event(event)
        if platform_kind not in self._auto_profile_platform_set():
            return None, False
        users = self.data.setdefault("users", {})
        existing = users.get(canonical_user_id) if isinstance(users, dict) else None
        if isinstance(existing, dict):
            stamper = getattr(self, "_stamp_private_event_identity", None)
            if callable(stamper):
                stamper(existing, event, normalized_user_id or raw_user_id)
            # A legacy/migrated profile remains addressable even when automatic
            # creation is disabled.  Its REQ-036 capability state, not a DM,
            # decides whether the conversation may proceed.
            ensure_legacy_profile_capabilities(existing)
            return existing, False
        # Configured targets are an administrator-owned permission source.  A
        # platform/adapter identity rollover may produce a new scoped storage
        # key, but it must still materialize that target record even when
        # automatic profiles for ordinary users are disabled.  The capability
        # migrator below decides whether this scoped record is actually open;
        # this branch only makes the exact target addressable.
        target_checker = getattr(self, "_is_target_private_user", None)
        is_configured_target = False
        if callable(target_checker):
            for candidate in (normalized_user_id, raw_user_id, canonical_user_id):
                try:
                    if candidate and bool(target_checker(candidate, None)):
                        is_configured_target = True
                        break
                except Exception:
                    continue
        if is_configured_target:
            user = self._get_user(canonical_user_id)
            stamper = getattr(self, "_stamp_private_event_identity", None)
            if callable(stamper):
                stamper(user, event, normalized_user_id or raw_user_id)
            return user, False
        if not bool(getattr(self, "enable_auto_user_profile_creation", False)):
            return None, False

        user = self._get_user(canonical_user_id)
        stamper = getattr(self, "_stamp_private_event_identity", None)
        if callable(stamper):
            stamper(user, event, normalized_user_id or raw_user_id)
        created_at = float(now if now is not None else _now_ts())
        user["auto_profile_created"] = True
        user["auto_profile_created_at"] = created_at
        user["profile_origin"] = "private_auto"
        # `_get_user()` owns relationship initialization.  An automatic profile
        # must not bypass the ledger or overwrite an explicitly configured role.
        user["nickname"] = self._auto_profile_nickname(canonical_user_id, sender_display_name)
        user["style"] = _single_line(getattr(self, "default_style", "温柔"), 24) or "温柔"
        default_proactive_enabled = bool(getattr(self, "default_proactive_enabled", False))
        user["auto_enabled"] = True
        user["manual_enabled"] = False
        user["manual_disabled"] = False
        user["enabled"] = True
        user["private_memory_enabled"] = False
        user["cross_group_memory_enabled"] = False
        user["proactive_daily_limit"] = (
            max(0, min(30, _safe_int(getattr(self, "default_proactive_daily_limit", 0), 0)))
            if default_proactive_enabled
            else 0
        )
        user["proactive_boundary_note"] = (
            "自动建档按配置允许主动触达"
            if default_proactive_enabled
            else "自动建档默认不主动触达"
        )
        ensure_new_profile_capabilities(
            user,
            proactive_private_enabled=default_proactive_enabled,
            grant_source="private_auto_default",
        )
        user["last_seen"] = max(_safe_float(user.get("last_seen"), 0), created_at)
        user["last_activity_at"] = max(_safe_float(user.get("last_activity_at"), 0), created_at)
        self._note_private_user_umo(canonical_user_id, user, getattr(event, "unified_msg_origin", ""))
        self._schedule_data_save()
        return user, True

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
        previous_message_at = _safe_float(user.get("last_user_message_at"), 0)
        if text and previous_message_at > 0 and ts > previous_message_at:
            user["last_inbound_gap_seconds"] = min(
                365 * 24 * 3600,
                max(0.0, ts - previous_message_at),
            )
            user["last_inbound_gap_observed_at"] = ts
        user["last_private_seen"] = ts
        user["last_private_activity_at"] = ts
        if text:
            user["private_inbound_count"] = _safe_int(user.get("private_inbound_count"), 0) + 1

    def _is_target_private_user(self, user_id: str, user: dict[str, Any] | None = None) -> bool:
        user_id = self._canonical_private_user_id(str(user_id or "").strip())
        if self._is_bot_self_user_id(user_id):
            return False
        if isinstance(user, dict):
            if user.get("manual_enabled") or user.get("auto_enabled"):
                return True
            capabilities = user.get("unified_profile_capabilities")
            if (
                user.get("proactive_private_enabled") is True
                or (
                    isinstance(capabilities, dict)
                    and capabilities.get("proactive_private_enabled") is True
                )
            ):
                return True
        if not user_id:
            return False
        if user_id in set(self._configured_target_ids()):
            return True
        return False

    def _private_passive_profile_available(
        self,
        user_id: str,
        user: dict[str, Any] | None = None,
    ) -> bool:
        """Return whether a real private profile may use passive chat enhancements.

        Passive private chat is intentionally independent from the historical
        target/``enabled`` permission flags.  Those flags remain meaningful to
        proactive delivery and relationship policy, but must not suppress normal
        per-turn enhancements for an existing private profile.
        """
        canonical = self._canonical_private_user_id(str(user_id or "").strip())
        if not canonical or self._is_bot_self_user_id(canonical):
            return False
        return isinstance(user, dict)

    def _photo_generation_scope(self, event: Any = None, *, proactive: bool = False, user: dict[str, Any] | None = None, user_id: str = "") -> str:
        """Return the configured permission bucket for a photo request."""
        proactive = proactive or bool(getattr(event, "private_companion_proactive_framework", False))
        if proactive:
            return "proactive"
        group_getter = getattr(self, "_extract_group_id_from_event", None)
        if event is not None and callable(group_getter):
            try:
                if str(group_getter(event) or "").strip():
                    return "group"
            except Exception:
                pass
        resolved_id = str(user_id or "").strip()
        if not resolved_id and event is not None:
            try:
                resolved_id = str(event.get_sender_id() or "").strip()
            except Exception:
                resolved_id = ""
        resolver = getattr(self, "_private_user_id_for_event", None)
        if event is not None and resolved_id and callable(resolver):
            try:
                resolved_id = str(resolver(event, resolved_id) or resolved_id)
            except Exception:
                pass
        if user is None and resolved_id:
            getter = getattr(self, "_get_user", None)
            if callable(getter):
                try:
                    user = getter(resolved_id)
                except Exception:
                    user = None
        role_getter = getattr(self, "_private_user_role", None)
        role = role_getter(user, resolved_id) if callable(role_getter) else str((user or {}).get("relationship_role") or "friend")
        return "private_owner" if role == "owner" else "private_friend"

    def _photo_generation_scope_daily_limit(self, scope: str) -> int:
        scope = str(scope or "").strip().lower()
        key = PHOTO_GENERATION_SCOPE_LIMIT_KEYS.get(scope)
        if key and hasattr(self, key):
            return normalize_photo_generation_scope_limit(getattr(self, key, -1))

        legacy = getattr(self, "photo_generation_allowed_scopes", None)
        if isinstance(legacy, dict):
            return normalize_photo_generation_scope_limit(legacy.get(scope, -1))
        allowed = normalize_photo_generation_scopes(
            legacy,
            default_if_missing=True,
        )
        return -1 if scope in allowed else 0

    def _photo_generation_scope_requester_id(
        self,
        event: Any = None,
        *,
        user: dict[str, Any] | None = None,
        user_id: str = "",
    ) -> str:
        resolved_id = str(user_id or (user or {}).get("user_id") or "").strip()
        if not resolved_id and event is not None:
            try:
                resolved_id = str(event.get_sender_id() or "").strip()
            except Exception:
                resolved_id = ""
        resolver = getattr(self, "_private_user_id_for_event", None)
        if event is not None and resolved_id and callable(resolver):
            try:
                resolved_id = str(resolver(event, resolved_id) or resolved_id).strip()
            except Exception:
                pass
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        if resolved_id and callable(canonicalizer):
            try:
                resolved_id = str(canonicalizer(resolved_id) or resolved_id).strip()
            except Exception:
                pass
        return resolved_id

    def _photo_generation_scope_today_key(self) -> str:
        today_getter = getattr(self, "_environment_today_key", None)
        if callable(today_getter):
            try:
                today = str(today_getter() or "").strip()
                if today:
                    return today
            except Exception:
                pass
        return _today_key()

    def _photo_generation_scope_quota_left(
        self,
        event: Any = None,
        *,
        proactive: bool = False,
        user: dict[str, Any] | None = None,
        user_id: str = "",
        scope: str = "",
    ) -> int | None:
        resolved_scope = str(scope or "").strip().lower() or self._photo_generation_scope(
            event,
            proactive=proactive,
            user=user,
            user_id=user_id,
        )
        limit = self._photo_generation_scope_daily_limit(resolved_scope)
        if limit < 0:
            return None
        if limit == 0:
            return 0
        requester_id = self._photo_generation_scope_requester_id(
            event,
            user=user,
            user_id=user_id,
        )
        # Shared jobs such as the cached daily outfit have no requester and keep
        # their existing independent quota; a zero scope limit still blocks them.
        if not requester_id:
            return limit
        today = self._photo_generation_scope_today_key()
        data = getattr(self, "data", None)
        usage = data.get("photo_generation_scope_attempts") if isinstance(data, dict) else None
        if not isinstance(usage, dict) or str(usage.get("day") or "") != today:
            return limit
        counts = usage.get("counts")
        scope_counts = counts.get(resolved_scope) if isinstance(counts, dict) else None
        used = _safe_int(scope_counts.get(requester_id), 0, 0) if isinstance(scope_counts, dict) else 0
        return max(0, limit - used)

    def _note_photo_generation_scope_attempt(
        self,
        event: Any = None,
        *,
        proactive: bool = False,
        user: dict[str, Any] | None = None,
        user_id: str = "",
        scope: str = "",
    ) -> None:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return
        resolved_scope = str(scope or "").strip().lower() or self._photo_generation_scope(
            event,
            proactive=proactive,
            user=user,
            user_id=user_id,
        )
        if resolved_scope not in PHOTO_GENERATION_SCOPES:
            return
        requester_id = self._photo_generation_scope_requester_id(
            event,
            user=user,
            user_id=user_id,
        )
        if not requester_id:
            return
        today = self._photo_generation_scope_today_key()
        usage = data.get("photo_generation_scope_attempts")
        if not isinstance(usage, dict) or str(usage.get("day") or "") != today:
            usage = {"day": today, "counts": {}}
            data["photo_generation_scope_attempts"] = usage
        counts = usage.setdefault("counts", {})
        if not isinstance(counts, dict):
            counts = {}
            usage["counts"] = counts
        scope_counts = counts.setdefault(resolved_scope, {})
        if not isinstance(scope_counts, dict):
            scope_counts = {}
            counts[resolved_scope] = scope_counts
        scope_counts[requester_id] = _safe_int(scope_counts.get(requester_id), 0, 0) + 1

    def _photo_generation_scope_quota_block_message(
        self,
        event: Any = None,
        *,
        proactive: bool = False,
        user: dict[str, Any] | None = None,
        user_id: str = "",
        scope: str = "",
    ) -> str:
        resolved_scope = str(scope or "").strip().lower() or self._photo_generation_scope(
            event,
            proactive=proactive,
            user=user,
            user_id=user_id,
        )
        label = PHOTO_GENERATION_SCOPE_LABELS.get(resolved_scope, "当前范围")
        if self._photo_generation_scope_daily_limit(resolved_scope) == 0:
            return f"管理员已关闭{label}生图/改图（对应每日上限为 0）。"
        return f"今天{label}生图/改图额度用完了；管理员可调高对应每日上限，或设为 -1 取消限制。"

    def _photo_generation_scope_allowed(self, event: Any = None, *, proactive: bool = False, user: dict[str, Any] | None = None, user_id: str = "") -> bool:
        quota_left = self._photo_generation_scope_quota_left(
            event,
            proactive=proactive,
            user=user,
            user_id=user_id,
        )
        return quota_left is None or quota_left > 0

    def _is_bot_self_user_id(self, user_id: str) -> bool:
        user_id = str(user_id or "").strip()
        return bool(user_id and user_id in self._known_bot_self_ids())

    def _known_bot_self_ids(self) -> set[str]:
        ids: set[str] = set()
        for attr in ("bot_self_id", "bot_user_id", "self_id"):
            value = self._normalize_private_identity_id(getattr(self, attr, ""))
            if value:
                ids.add(value)
        raw_ids = getattr(self, "bot_self_ids", None)
        if isinstance(raw_ids, (list, tuple, set)):
            for item in raw_ids:
                value = self._normalize_private_identity_id(item)
                if value:
                    ids.add(value)
        platform_manager = getattr(getattr(self, "context", None), "platform_manager", None)
        for inst in list(getattr(platform_manager, "platform_insts", []) or []):
            for attr in ("self_id", "bot_self_id", "bot_user_id"):
                value = self._normalize_private_identity_id(getattr(inst, attr, ""))
                if value:
                    ids.add(value)
            bot = getattr(inst, "bot", None)
            api_clients = getattr(bot, "_wsr_api_clients", None)
            if isinstance(api_clients, dict):
                for item in api_clients:
                    value = self._normalize_private_identity_id(item)
                    if value and re.fullmatch(r"[1-9]\d{4,14}", value):
                        ids.add(value)
        return ids

    def _configured_bot_scope_ids(self) -> set[str]:
        """Return normalized Bot self IDs or adapter instance IDs."""
        raw = getattr(self, "bot_scope_ids", [])
        if isinstance(raw, str):
            values = re.split(r"[,\s,、;；]+", raw)
        elif isinstance(raw, (list, tuple, set)):
            values = list(raw)
        else:
            values = []
        return {
            _single_line(value, 160).casefold()
            for value in values
            if _single_line(value, 160)
        }

    def _bot_scope_allows_candidates(self, candidates: set[str]) -> bool:
        mode = _single_line(getattr(self, "bot_scope_mode", "all"), 20).casefold() or "all"
        if mode not in {"all", "allowlist", "denylist"}:
            mode = "all"
        if mode == "all":
            return True
        configured = self._configured_bot_scope_ids()
        if not configured:
            return mode != "allowlist"
        normalized = {
            _single_line(value, 160).casefold()
            for value in candidates
            if _single_line(value, 160)
        }
        matched = bool(normalized.intersection(configured))
        return matched if mode == "allowlist" else not matched

    def _bot_scope_platform_instance_candidates(self, instance_id: str) -> set[str]:
        prefix = _single_line(instance_id, 160).casefold()
        candidates = {prefix} if prefix else set()
        manager = getattr(getattr(self, "context", None), "platform_manager", None)
        if manager is None or not prefix:
            return candidates
        try:
            platforms = list(manager.get_insts())
        except Exception:
            platforms = list(getattr(manager, "platform_insts", []) or [])
        for platform in platforms:
            try:
                meta = platform.meta()
            except Exception:
                meta = None
            instance_ids = {
                _single_line(getattr(meta, attr, ""), 160).casefold()
                for attr in ("id", "name")
                if _single_line(getattr(meta, attr, ""), 160)
            }
            if prefix not in instance_ids:
                continue
            candidates.update(instance_ids)
            for owner in (platform, getattr(platform, "bot", None)):
                if owner is None:
                    continue
                for attr in ("self_id", "bot_self_id", "bot_user_id"):
                    value = _single_line(getattr(owner, attr, ""), 160).casefold()
                    if value:
                        candidates.add(value)
            api_clients = getattr(getattr(platform, "bot", None), "_wsr_api_clients", None)
            if isinstance(api_clients, dict):
                candidates.update(
                    _single_line(value, 160).casefold()
                    for value in api_clients
                    if _single_line(value, 160)
                )
            break
        return candidates

    def _bot_scope_allows_umo(self, umo: Any) -> bool:
        """Apply Bot scope to an eventless background delivery route."""
        origin = _single_line(umo, 240)
        prefix = origin.split(":", 1)[0] if ":" in origin else origin
        return self._bot_scope_allows_candidates(
            self._bot_scope_platform_instance_candidates(prefix)
        )

    def _bot_scope_allows_event(self, event: Any | None) -> bool:
        """Check whether the configured Bot scope accepts this event."""
        candidates: set[str] = set()
        if event is not None:
            self_getter = getattr(self, "_event_self_id", None)
            if callable(self_getter):
                try:
                    value = _single_line(self_getter(event), 160)
                except Exception:
                    value = ""
                if value:
                    candidates.add(value.casefold())
            for owner in (event, getattr(event, "message_obj", None)):
                if owner is None:
                    continue
                for attr in ("adapter_instance_id", "platform_instance_id", "platform_id"):
                    value = _single_line(getattr(owner, attr, ""), 160)
                    if value:
                        candidates.add(value.casefold())
            origin = _single_line(getattr(event, "unified_msg_origin", ""), 240)
            if ":" in origin:
                candidates.update(
                    self._bot_scope_platform_instance_candidates(origin.split(":", 1)[0])
                )
        return self._bot_scope_allows_candidates(candidates)

    def _get_group(self, group_id: str) -> dict[str, Any]:
        canonical_id = self._normalize_group_identity_id(group_id)
        if not canonical_id:
            canonical_id = _single_line(group_id, 160)
        group = self._canonicalize_group_records(canonical_id)
        for key, default_value in _DEFAULT_GROUP_TEMPLATE.items():
            if key not in group:
                group[key] = deepcopy(default_value)
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
            group_id = self._normalize_group_identity_id(part)
            if group_id and group_id not in ids:
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
            if not configured:
                if not bool(getattr(self, "_empty_group_whitelist_warning_logged", False)):
                    self._empty_group_whitelist_warning_logged = True
                    logger.warning(
                        "[PrivateCompanion] 群聊观察已开启但白名单为空,当前不会观察任何群；"
                        "请在群聊观测页把目标群加入白名单,或改用黑名单模式: first_group=%s",
                        _single_line(group_id, 80) or "-",
                    )
                return False
            self._empty_group_whitelist_warning_logged = False
            if group_id not in configured:
                return False
        return True

    def _group_llm_reply_block_store(self) -> dict[str, Any]:
        store = self.data.setdefault("group_llm_reply_blocks", {})
        if not isinstance(store, dict):
            store = {}
            self.data["group_llm_reply_blocks"] = store
        return store

    def _group_llm_reply_block_item(self, group_id: str) -> dict[str, Any]:
        group_id = _single_line(group_id, 80)
        if not group_id:
            return {}
        item = self._group_llm_reply_block_store().get(group_id)
        return item if isinstance(item, dict) else {}

    def _group_llm_reply_blocked(self, group_id: str) -> bool:
        item = self._group_llm_reply_block_item(group_id)
        return bool(item.get("enabled"))

    def _set_group_llm_reply_block(
        self,
        group_id: str,
        enabled: bool,
        *,
        operator_id: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        group_id = _single_line(group_id, 80)
        if not group_id:
            return {}
        store = self._group_llm_reply_block_store()
        if enabled:
            item = {
                "group_id": group_id,
                "enabled": True,
                "updated_at": _now_ts(),
                "operator_id": _single_line(operator_id, 80),
                "reason": _single_line(reason, 160),
            }
            store[group_id] = item
            return item
        previous = store.get(group_id)
        if isinstance(previous, dict):
            previous["enabled"] = False
            previous["cleared_at"] = _now_ts()
            previous["cleared_by"] = _single_line(operator_id, 80)
            previous["clear_reason"] = _single_line(reason, 160)
            store.pop(group_id, None)
            return previous
        return {"group_id": group_id, "enabled": False}

    def _active_group_llm_reply_blocks(self) -> list[dict[str, Any]]:
        store = self._group_llm_reply_block_store()
        items: list[dict[str, Any]] = []
        for group_id, item in list(store.items()):
            if not isinstance(item, dict) or not bool(item.get("enabled")):
                continue
            normalized = dict(item)
            normalized["group_id"] = _single_line(normalized.get("group_id") or group_id, 80)
            items.append(normalized)
        return sorted(items, key=lambda item: _safe_float(item.get("updated_at"), 0.0), reverse=True)
