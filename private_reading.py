# -*- coding: utf-8 -*-
"""
PrivateReadingMixin — 从 main.py 重新拆分出的私密阅读/书架
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
from .helpers import (
    _date_key,
    _now_ts,
    _path_text,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_internal_message_blocks,
    _text_similarity,
    _today_key,
)
from .persona_config import runtime_persona_setting


def _persona_provider_id(owner: Any, canonical_key: str, legacy_attr: str, quick_role: str) -> str:
    """Resolve canonical persona provider settings while preserving test harnesses."""
    fallback = str(getattr(owner, legacy_attr, "") or "").strip()
    if not callable(getattr(owner, "persona_setting", None)):
        return fallback
    mode = str(getattr(owner, "provider_config_mode", "quick") or "quick").strip().lower()
    if mode != "quick":
        return str(runtime_persona_setting(owner, canonical_key, fallback) or "").strip()
    complex_id = str(runtime_persona_setting(owner, "COMPLEX_REASONING_PROVIDER_ID", "") or "").strip()
    if quick_role == "complex":
        return complex_id or fallback
    if quick_role == "creative":
        creative_id = str(runtime_persona_setting(owner, "CREATIVE_MODEL_PROVIDER_ID", "") or "").strip()
        return creative_id or complex_id or fallback
    fast_id = str(runtime_persona_setting(owner, "FAST_RESPONSE_PROVIDER_ID", "") or "").strip()
    return fast_id or complex_id or fallback
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

class PrivateReadingMixin:
    """私密阅读/书架"""

    def _private_reading_current_agenda_item(self) -> dict[str, Any] | None:
        getter = getattr(self, "_agenda_current_context_item", None)
        if callable(getter):
            try:
                item = getter()
            except Exception:
                return None
            return item if isinstance(item, dict) else None
        legacy_getter = getattr(self, "_get_current_plan_item", None)
        try:
            item = legacy_getter(self.data.get("daily_plan", {})) if callable(legacy_getter) else None
        except Exception:
            item = None
        return item if isinstance(item, dict) else None

    def _jm_cosmos_plugin_dir(self) -> Path:
        candidates = [
            Path(__file__).resolve().parent.parent / "astrbot_plugin_jm_cosmos",
            Path(self.data_dir).parent.parent / "plugins" / "astrbot_plugin_jm_cosmos",
        ]
        for path in candidates:
            if (path / "main.py").exists():
                return path
        return candidates[0]

    def _jm_cosmos_available(self) -> bool:
        try:
            return (self._jm_cosmos_plugin_dir() / "main.py").exists()
        except Exception:
            return False

    def _find_jm_cosmos_instance(self) -> Any | None:
        for obj in gc.get_objects():
            try:
                cls = obj.__class__
                module = str(getattr(cls, "__module__", ""))
                if "astrbot_plugin_jm_cosmos" not in module:
                    continue
                if hasattr(obj, "browser") and callable(getattr(getattr(obj, "browser", None), "search_albums", None)):
                    return obj
            except Exception:
                continue
        return None

    def _jm_cosmos_read_available(self, user: dict[str, Any] | None = None) -> bool:
        if isinstance(user, dict) and self._private_user_role(user) == "friend":
            return False
        return bool(
            runtime_persona_setting(self, "enable_private_reading_integration", False)
            and runtime_persona_setting(self, "enable_private_reading_boredom_read", False)
            and self._jm_cosmos_available()
        )

    def _private_reading_recommendation_request_available(self, user: dict[str, Any] | None = None) -> bool:
        if isinstance(user, dict) and self._private_user_role(user) == "friend":
            return False
        return bool(
            runtime_persona_setting(self, "enable_private_reading_integration", False)
            and runtime_persona_setting(self, "enable_private_reading_ask_recommendation", False)
            and self._jm_cosmos_available()
        )

    def _bot_currently_bored_enough_for_jm(self) -> bool:
        state = self.data.get("daily_state", {})
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        mood = _single_line(state.get("mood_bias") if isinstance(state, dict) else "", 24)
        current_item = self._private_reading_current_agenda_item()
        activity = _single_line((current_item or {}).get("activity"), 80)
        hour = datetime.now().hour
        text = f"{mood} {activity}"
        boredom_tokens = ("无聊", "发呆", "摸鱼", "休息", "闲", "空", "夜", "睡前", "偷懒", "刷")
        if any(token in text for token in boredom_tokens):
            return True
        if 23 <= hour or hour < 2:
            return random.random() < 0.42
        return 28 <= energy <= 68 and random.random() < 0.22

    def _jm_cosmos_keywords_for_user(self, user: dict[str, Any] | None = None) -> list[str]:
        raw = str(
            runtime_persona_setting(self, "private_reading_default_keywords", "纯爱,恋爱,同人")
            or "纯爱,恋爱,同人"
        )
        liked_candidates: list[str] = []
        base_candidates: list[str] = []
        memory_candidates: list[str] = []
        state = self.data.get("jm_cosmos_integration") if isinstance(self.data.get("jm_cosmos_integration"), dict) else {}
        profile = state.get("preference_profile") if isinstance(state.get("preference_profile"), dict) else {}
        liked = profile.get("liked_terms") if isinstance(profile.get("liked_terms"), list) else []
        for item in liked[:8]:
            text = _single_line(item.get("term") if isinstance(item, dict) else item, 16)
            if text:
                liked_candidates.append(text)
        base_candidates.extend(part.strip() for part in re.split(r"[,，、\n]+", raw) if part.strip())
        if isinstance(user, dict):
            memory = user.get("companion_memory")
            user_profile = memory.get("profile") if isinstance(memory, dict) else {}
            interests = user_profile.get("interests") if isinstance(user_profile, dict) else []
            if isinstance(interests, list):
                for item in interests:
                    text = _single_line(item, 12)
                    if text:
                        memory_candidates.append(text)
        buckets = [liked_candidates, base_candidates, memory_candidates]
        for bucket in buckets:
            random.shuffle(bucket)
        candidates: list[str] = []
        while any(buckets):
            weighted_order = [0, 1, 2, 1, 0, 1, 2]
            for idx in weighted_order:
                if idx < len(buckets) and buckets[idx]:
                    candidates.append(buckets[idx].pop(0))
            buckets = [bucket for bucket in buckets if bucket]
        result: list[str] = []
        seen: set[str] = set()
        for keyword in candidates:
            keyword = re.sub(r"\s+", " ", str(keyword or "").strip())[:16]
            if not keyword or keyword in seen:
                continue
            seen.add(keyword)
            result.append(keyword)
            if len(result) >= 8:
                break
        return result or ["纯爱"]

    def _private_reading_album_fingerprint(self, value: Any) -> str:
        text = _single_line(value, 120)
        return re.sub(r"\s+", "", text).lower()

    def _private_reading_excluded_album_ids(self) -> set[str]:
        excluded: set[str] = set()
        state = self.data.get("jm_cosmos_integration") if isinstance(self.data.get("jm_cosmos_integration"), dict) else {}

        def add(value: Any) -> None:
            album_id = _single_line(value, 80)
            if album_id:
                excluded.add(album_id)

        for key in ("deleted_album_ids", "read_album_ids", "recent_album_ids"):
            values = state.get(key) if isinstance(state.get(key), list) else []
            for item in values:
                add(item.get("album_id") if isinstance(item, dict) else item)
        last_album = state.get("last_album") if isinstance(state.get("last_album"), dict) else {}
        add(last_album.get("id") or last_album.get("album_id"))
        items = self.data.get("bookshelf_items") if isinstance(self.data.get("bookshelf_items"), list) else []
        for item in items:
            if isinstance(item, dict) and item.get("type") == "jm_album":
                add(item.get("album_id") or item.get("id"))
        profile = state.get("preference_profile") if isinstance(state.get("preference_profile"), dict) else {}
        history = profile.get("history") if isinstance(profile.get("history"), list) else []
        for item in history:
            if isinstance(item, dict):
                add(item.get("album_id"))
        return excluded

    def _private_reading_deleted_title_fingerprints(self) -> set[str]:
        state = self.data.get("jm_cosmos_integration") if isinstance(self.data.get("jm_cosmos_integration"), dict) else {}
        values = state.get("deleted_titles") if isinstance(state.get("deleted_titles"), list) else []
        return {
            fingerprint
            for fingerprint in (self._private_reading_album_fingerprint(item) for item in values)
            if fingerprint
        }

    def _private_reading_note_read_album(self, album: dict[str, Any]) -> None:
        album_id = _single_line(album.get("id") or album.get("album_id"), 80)
        title = _single_line(album.get("title"), 120)
        if not album_id and not title:
            return
        state = self.data.setdefault("jm_cosmos_integration", {})
        if not isinstance(state, dict):
            state = {}
            self.data["jm_cosmos_integration"] = state
        read_ids = state.setdefault("read_album_ids", [])
        if not isinstance(read_ids, list):
            read_ids = []
            state["read_album_ids"] = read_ids
        if album_id and album_id not in read_ids:
            read_ids.append(album_id)
            del read_ids[:-240]
        recent = state.setdefault("recent_album_ids", [])
        if not isinstance(recent, list):
            recent = []
            state["recent_album_ids"] = recent
        if album_id:
            recent[:] = [item for item in recent if str(item) != album_id]
            recent.append(album_id)
            del recent[:-80]

    def _private_reading_candidate_score(self, detail: dict[str, Any], keyword: str = "") -> float:
        state = self.data.get("jm_cosmos_integration") if isinstance(self.data.get("jm_cosmos_integration"), dict) else {}
        profile = state.get("preference_profile") if isinstance(state.get("preference_profile"), dict) else {}
        liked = profile.get("liked_terms") if isinstance(profile.get("liked_terms"), list) else []
        disliked = profile.get("disliked_terms") if isinstance(profile.get("disliked_terms"), list) else []

        def term_weights(items: list[Any]) -> dict[str, float]:
            weights: dict[str, float] = {}
            for item in items:
                if isinstance(item, dict):
                    term = self._normalize_private_reading_tag(item.get("term"))
                    weight = _safe_float(item.get("weight"), 0)
                else:
                    term = self._normalize_private_reading_tag(item)
                    weight = 1.0
                if term:
                    weights[term] = max(weights.get(term, 0.0), weight)
            return weights

        liked_weights = term_weights(liked)
        disliked_weights = term_weights(disliked)
        raw_tags = detail.get("tags") if isinstance(detail.get("tags"), list) else []
        haystack = [
            self._normalize_private_reading_tag(keyword),
            self._normalize_private_reading_tag(detail.get("title")),
            self._normalize_private_reading_tag(detail.get("author")),
            *(self._normalize_private_reading_tag(tag) for tag in raw_tags),
        ]
        score = 0.0
        for text in [item for item in haystack if item]:
            for term, weight in liked_weights.items():
                if term and term in text:
                    score += max(0.1, weight)
            for term, weight in disliked_weights.items():
                if term and term in text:
                    score -= max(0.1, weight)
        return score

    def _update_private_reading_preference_profile(self, album: dict[str, Any]) -> None:
        user_rating = _safe_int(album.get("user_rating"), 0, 0, 10)
        bot_rating = _safe_int(album.get("rating"), 0, 0, 10)
        rating = user_rating or bot_rating
        explicit_liked_terms = [
            _single_line(tag, 24)
            for tag in (album.get("user_liked_tags") if isinstance(album.get("user_liked_tags"), list) else [])
            if _single_line(tag, 24)
        ][:8]
        explicit_disliked_terms = [
            _single_line(tag, 24)
            for tag in (album.get("user_disliked_tags") if isinstance(album.get("user_disliked_tags"), list) else [])
            if _single_line(tag, 24)
        ][:8]
        if rating <= 0 and not explicit_liked_terms and not explicit_disliked_terms:
            return
        state = self.data.setdefault("jm_cosmos_integration", {})
        if not isinstance(state, dict):
            state = {}
            self.data["jm_cosmos_integration"] = state
        profile = state.setdefault("preference_profile", {})
        if not isinstance(profile, dict):
            profile = {}
            state["preference_profile"] = profile
        history = profile.setdefault("history", [])
        if not isinstance(history, list):
            history = []
            profile["history"] = history
        album_id = _single_line(album.get("id") or album.get("album_id"), 32)
        title = _single_line(album.get("title"), 80)

        def normalized_terms(raw_terms: list[str]) -> list[str]:
            terms: list[str] = []
            seen: set[str] = set()
            for term in raw_terms:
                normalized = self._normalize_private_reading_tag(term)
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    terms.append(term)
            return terms

        def append_history(row_rating: int, terms: list[str], *, source: str, reason: str = "") -> None:
            if row_rating <= 0 or not terms:
                return
            history.append(
                {
                    "album_id": album_id,
                    "title": title,
                    "rating": row_rating,
                    "bot_rating": bot_rating,
                    "user_rating": user_rating,
                    "source": source,
                    "reason": _single_line(reason or album.get("user_rating_reason") or album.get("rating_reason"), 160),
                    "terms": terms[:12],
                    "created_ts": _safe_float(album.get("created_ts"), _now_ts()),
                }
            )

        if rating > 0:
            raw_terms: list[str] = []
            raw_terms.append(_single_line(album.get("keyword"), 24))
            raw_terms.extend(_single_line(tag, 24) for tag in album.get("tags", [])[:8] if _single_line(tag, 24)) if isinstance(album.get("tags"), list) else None
            raw_terms.extend(_single_line(tag, 24) for tag in album.get("preference_tags", [])[:8] if _single_line(tag, 24)) if isinstance(album.get("preference_tags"), list) else None
            append_history(rating, normalized_terms(raw_terms), source="user" if user_rating else "bot")
        append_history(9, normalized_terms(explicit_liked_terms), source="user_tags", reason="用户在书柜详情页标为喜好")
        append_history(2, normalized_terms(explicit_disliked_terms), source="user_tags", reason="用户在书柜详情页标为厌恶")
        del history[:-60]

        scores: dict[str, list[float]] = {}
        labels: dict[str, str] = {}
        for row in history:
            if not isinstance(row, dict):
                continue
            row_rating = _safe_float(row.get("rating"), 0)
            source_weight = 1.35 if str(row.get("source") or "") in {"user", "user_tags"} else 0.75
            for term in row.get("terms", []) if isinstance(row.get("terms"), list) else []:
                normalized = self._normalize_private_reading_tag(term)
                if not normalized:
                    continue
                scores.setdefault(normalized, []).append(row_rating * source_weight + 5.5 * (1 - source_weight))
                labels.setdefault(normalized, _single_line(term, 24))

        liked_terms: list[dict[str, Any]] = []
        disliked_terms: list[dict[str, Any]] = []
        for normalized, values in scores.items():
            if not values:
                continue
            avg = sum(values) / len(values)
            weight = round(max(0.1, abs(avg - 5.5)) * min(2.0, 0.65 + len(values) * 0.18), 2)
            row = {"term": labels.get(normalized, normalized), "avg": round(avg, 2), "count": len(values), "weight": weight}
            if avg >= 7.2:
                liked_terms.append(row)
            elif avg <= 4.2:
                disliked_terms.append(row)
        liked_terms.sort(key=lambda item: (_safe_float(item.get("weight"), 0), _safe_float(item.get("avg"), 0), _safe_int(item.get("count"), 0)), reverse=True)
        disliked_terms.sort(key=lambda item: (_safe_float(item.get("weight"), 0), _safe_int(item.get("count"), 0)), reverse=True)
        profile["liked_terms"] = liked_terms[:16]
        profile["disliked_terms"] = disliked_terms[:16]
        profile["average_rating"] = round(sum(_safe_float(row.get("rating"), 0) for row in history if isinstance(row, dict)) / max(1, len(history)), 2)
        profile["rating_count"] = len(history)

    def _format_private_reading_preference_influence_for_reply(
        self,
        inbound_text: str = "",
        user: dict[str, Any] | None = None,
    ) -> str:
        if not runtime_persona_setting(self, "enable_private_reading_integration", False):
            return ""
        if isinstance(user, dict) and self._private_user_role(user) == "friend":
            return ""
        if not runtime_persona_setting(self, "enable_private_reading_preference_influence", True):
            return ""
        state = self.data.get("jm_cosmos_integration") if isinstance(self.data.get("jm_cosmos_integration"), dict) else {}
        profile = state.get("preference_profile") if isinstance(state.get("preference_profile"), dict) else {}
        rating_count = _safe_int(profile.get("rating_count"), 0, 0, 999)
        if rating_count < max(
            1,
            runtime_persona_setting(self, "private_reading_preference_min_ratings", 5),
        ):
            return ""
        liked = profile.get("liked_terms") if isinstance(profile.get("liked_terms"), list) else []
        disliked = profile.get("disliked_terms") if isinstance(profile.get("disliked_terms"), list) else []
        max_terms = max(
            2,
            runtime_persona_setting(self, "private_reading_preference_max_terms", 8),
        )

        def format_terms(items: list[Any]) -> str:
            rows: list[str] = []
            for item in items:
                if isinstance(item, dict):
                    term = _single_line(item.get("term"), 24)
                    if term:
                        rows.append(term)
                else:
                    term = _single_line(item, 24)
                    if term:
                        rows.append(term)
                if len(rows) >= max_terms:
                    break
            return "、".join(rows)

        liked_text = format_terms(liked)
        disliked_text = format_terms(disliked[: max_terms // 2])
        if not liked_text and not disliked_text:
            return ""
        preference_parts = []
        if liked_text:
            preference_parts.append(f"喜欢：{liked_text}")
        if disliked_text:
            preference_parts.append(f"厌恶：{disliked_text}")
        lines = [
            "【私密偏好画像】",
            "经过长时间相处，可知用户" + "；".join(preference_parts) + "。",
            "这些偏好只用于理解用户，不代表 Bot 对本轮互动的同意，也不能覆盖 Bot 当前身体状态、意愿和明确边界。",
        ]
        return "\n".join(lines)

    @staticmethod
    def _normalize_private_reading_tag(value: Any) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
        return re.sub(r"\s+", "", text)

    def _private_reading_blocked_tag_set(self) -> set[str]:
        raw = str(runtime_persona_setting(self, "private_reading_blocked_tags", "") or "")
        return {
            normalized
            for part in re.split(r"[,，、\n]+", raw)
            if (normalized := self._normalize_private_reading_tag(part))
        }

    def _dedupe_private_reading_impression(self, text: Any, *, limit: int = 420) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        chunks: list[str] = []
        for part in re.split(r"(?:\n+|(?<=[。！？!?；;])\s*)", cleaned):
            part = _single_line(part.strip(), 240)
            part = re.sub(r"^(?:读后感|画面记录|札记\s*\d*|笔记\s*\d*)[:：]\s*", "", part).strip()
            if part:
                chunks.append(part)
        if not chunks:
            chunks = [_single_line(cleaned, limit)]
        kept: list[str] = []
        for chunk in chunks:
            if any(_text_similarity(chunk, existing) >= 0.72 for existing in kept):
                continue
            kept.append(chunk)
            if len("".join(kept)) >= limit:
                break
        result = "".join(kept).strip()
        return _single_line(result or cleaned, limit)

    def _jm_cosmos_detail_blocked_by_tags(self, detail: dict[str, Any]) -> str:
        blocked_tags = self._private_reading_blocked_tag_set()
        if not blocked_tags:
            return ""
        raw_tags = detail.get("tags")
        if isinstance(raw_tags, list):
            tags = raw_tags
        elif isinstance(raw_tags, str):
            tags = re.split(r"[,，、\s]+", raw_tags)
        else:
            tags = []
        for tag in tags:
            tag_text = _single_line(tag, 40)
            normalized = self._normalize_private_reading_tag(tag_text)
            if normalized and normalized in blocked_tags:
                return tag_text
        return ""

    def _private_reading_vision_switches(self) -> tuple[bool, bool, bool]:
        """Return JM image, page-comment, and rating switches with old-config defaults."""
        vision_enabled = bool(runtime_persona_setting(self, "enable_private_reading_vision", True))
        return (
            vision_enabled,
            vision_enabled
            and bool(runtime_persona_setting(self, "enable_private_reading_page_comments", True)),
            vision_enabled and bool(runtime_persona_setting(self, "enable_private_reading_rating", True)),
        )

    async def _call_jm_cosmos_vision(
        self,
        cover_path: Path | None,
        detail: dict[str, Any],
        page_paths: list[Path] | None = None,
        sampled_pages: list[int] | None = None,
    ) -> dict[str, Any]:
        vision_enabled, comments_enabled, rating_enabled = self._private_reading_vision_switches()
        if not vision_enabled:
            return {}
        image_paths: list[Path] = []
        if cover_path and cover_path.exists():
            image_paths.append(cover_path)
        for path in page_paths or []:
            if path and path.exists() and path not in image_paths:
                image_paths.append(path)
            if len(image_paths) >= 6:
                break
        if not image_paths:
            return {}
        try:
            getter = getattr(self.context, "get_provider_by_id", None)
            provider_key, primary_provider_id, fallback_provider_id = self._private_reading_visual_provider_route()
            providers: list[tuple[str, Any]] = []
            for candidate_id in (primary_provider_id, fallback_provider_id):
                provider = getter(candidate_id) if candidate_id and callable(getter) else None
                if candidate_id and provider is not None and self._provider_supports_image(provider):
                    providers.append((candidate_id, provider))
            if not providers:
                logger.info("[PrivateCompanion] 夹层阅读未配置专用视觉模型，跳过页图批注和读后感生成")
                return {}
            provider_id, provider = providers[0]
            image_urls = []
            for path in image_paths:
                suffix = path.suffix.lower()
                mime = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
                image_urls.append(f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}")
            sampled_list = [_safe_int(page, 0, 1) for page in (sampled_pages or [])[:8] if _safe_int(page, 0, 1) > 0]
            sampled_text = "、".join(str(page) for page in sampled_list)
            sampled_mapping_text = "；".join(
                f"第{index + 1}张正文参考图=实际第{page}页"
                for index, page in enumerate(sampled_list)
            )
            persona = _single_line(self._get_default_persona_prompt(), 1200)
            schedule_persona = _single_line(
                runtime_persona_setting(self, "schedule_persona_prompt", ""), 800
            )
            worldview_context = _single_line(self._format_worldview_adaptation_prompt(), 1200)
            daily_state = self.data.get("daily_state", {})
            state_text = _single_line(
                self._format_state_for_prompt(daily_state if isinstance(daily_state, dict) else {}),
                800,
            )
            preference_text = ""
            if rating_enabled:
                preference_state = self.data.get("jm_cosmos_integration") if isinstance(self.data.get("jm_cosmos_integration"), dict) else {}
                preference_profile = preference_state.get("preference_profile") if isinstance(preference_state.get("preference_profile"), dict) else {}
                liked_terms = preference_profile.get("liked_terms") if isinstance(preference_profile.get("liked_terms"), list) else []
                disliked_terms = preference_profile.get("disliked_terms") if isinstance(preference_profile.get("disliked_terms"), list) else []
                preference_text = "；".join(
                    part
                    for part in (
                        "偏好较高：" + "、".join(_single_line(item.get("term") if isinstance(item, dict) else item, 16) for item in liked_terms[:6] if _single_line(item.get("term") if isinstance(item, dict) else item, 16)) if liked_terms else "",
                        "偏好较低：" + "、".join(_single_line(item.get("term") if isinstance(item, dict) else item, 16) for item in disliked_terms[:6] if _single_line(item.get("term") if isinstance(item, dict) else item, 16)) if disliked_terms else "",
                    )
                    if part
                )
            output_requirements = [
                '"impression":"160字以内第一人称内部读后感"',
            ]
            if comments_enabled:
                output_requirements.append('"page_comments":[{"page":12,"comment":"35字以内页边吐槽或感想"}]')
            if rating_enabled:
                output_requirements.extend(
                    [
                        '"rating":8',
                        '"rating_reason":"60字以内第一人称评分理由"',
                        '"preference_tags":["画风","节奏"]',
                    ]
                )
            disabled_outputs = []
            if not comments_enabled:
                disabled_outputs.append("page_comments")
            if not rating_enabled:
                disabled_outputs.extend(("rating", "rating_reason", "preference_tags"))
            preference_prompt = (
                preference_text
                if rating_enabled and preference_text
                else "还没有稳定偏好,这次评分会成为早期样本。"
                if rating_enabled
                else "本次已关闭阅读评价,不要生成评分、评分理由或偏好标签。"
            )
            prompt = (
                "请根据封面和抽样正文页,用 Bot 自己的第一人称口吻完成启用的阅读输出。\n"
                "封面只用于确认标题、画风和整体气质；内容理解主要参考后续正文页。抽样页已尽量避开开头的封面、书脊、目录,以及结尾的汉化组、致谢、后记页。\n"
                "如果某张图明显是版权页、目录、汉化组说明、鸣谢或空白页,请忽略它,不要当作剧情或人物关系来解读。\n"
                "impression 必须像 Bot 刚合上书后私下记在书柜里的短句,用“我”的感受、口癖和当下状态来写；可以写画风、氛围、人物关系、叙事节奏和读完后的感觉。\n"
                "禁止第三人称或书评腔：不要写“她觉得/这个 Bot 认为/读者会感到/作品讲述了/本作通过/整体来看”这类旁观表达。不要写成客观数据库摘要,也不要提插件、视觉模型、抽样、页码或数据来源。\n"
                "表达风格应顺着当前bot人格与状态：如果人格冷淡就冷淡,如果嘴硬就嘴硬,如果黏人就黏人；不要固定成害羞、含蓄或直白。\n"
                + ("page_comments 是 Bot 私下读漫画时写在书页旁边的小吐槽/感想,要像即时反应,不要概括剧情,不要写成通用解说。每条都要服从下面的人格语气。\n" if comments_enabled else "")
                + ("rating 是 Bot 自己读完后的主观分数,1 到 10 的整数；不是用户评分。rating_reason 用一句第一人称短句说明为什么喜欢或不喜欢。preference_tags 写出这次影响喜好的关键词,用于以后慢慢找到阅读偏好。\n" if rating_enabled else "")
                + (f"关闭的输出字段必须省略或保持空值：{','.join(disabled_outputs)}。\n" if disabled_outputs else "")
                + ("只输出 JSON,不要使用 Markdown。格式：{" + ",".join(output_requirements) + "}。\n")
                + ("page_comments 只为正文参考页里真正看懂的页生成；page 必须填写实际页码,不要填写第几张参考图的序号。\n" if comments_enabled else "")
                + f"\n【AstrBot 默认人格】\n{persona or '未读取到默认人格。'}\n"
                f"\n【生活/日程人设补充】\n{schedule_persona or '（无）'}\n"
                f"\n【当前状态】\n{state_text or '（无）'}\n"
                f"\n【已有阅读偏好】\n{preference_prompt}\n"
                f"\n{worldview_context}\n"
                f"标题：{_single_line(detail.get('title'), 80)}\n"
                f"标签：{_single_line(','.join(str(item) for item in (detail.get('tags') or [])) if isinstance(detail.get('tags'), list) else detail.get('tags'), 120)}"
                + (f"\n正文参考页：{sampled_text}" if sampled_text else "")
                + (f"\n参考图页码对应：{sampled_mapping_text}" if sampled_mapping_text else "")
            )
            if self._daily_token_soft_limit_should_defer("private_reading_vision"):
                self._record_llm_budget_skip(
                    provider_id=provider_id,
                    task="private_reading_vision",
                    prompt=prompt,
                    error="daily_token_soft_limit_deferred",
                )
                return {}
            if self._llm_daily_budget_remaining() == 0:
                self._record_llm_budget_skip(provider_id=provider_id, task="private_reading_vision", prompt=prompt)
                return {}
            for attempt_index, (provider_id, provider) in enumerate(providers):
                start = time.time()
                try:
                    token_skip_getter = getattr(self, "_model_token_limit_should_skip_primary", None)
                    if callable(token_skip_getter) and token_skip_getter(
                        task="private_reading_vision",
                        provider_id=provider_id,
                        primary_provider_id=primary_provider_id,
                        fallback_provider_id=fallback_provider_id,
                        provider_key=provider_key,
                        prompt=prompt,
                        max_tokens=900,
                        image_count=len(image_urls),
                    ):
                        self._record_llm_usage(
                            provider_id=provider_id,
                            task="private_reading_vision",
                            prompt=prompt,
                            completion="",
                            elapsed_ms=0,
                            success=False,
                            error="model_token_limit_exceeded",
                        )
                        logger.info(
                            "[PrivateCompanion] 夹层阅读主视觉模型预估超出 Token 上限，跳过并继续备用模型: primary=%s fallback=%s",
                            _single_line(provider_id, 120),
                            _single_line(fallback_provider_id, 120),
                        )
                        continue
                    timeout_getter = getattr(self, "_model_timeout_seconds_for_call", None)
                    timeout = (
                        timeout_getter(
                            task="private_reading_vision",
                            provider_id=provider_id,
                            timeout_key=provider_key,
                        )
                        if callable(timeout_getter)
                        else None
                    )
                    request_call = provider.text_chat(prompt=prompt, image_urls=image_urls)
                    result = await asyncio.wait_for(request_call, timeout=timeout) if timeout is not None else await request_call
                    text = str(getattr(result, "completion_text", result) or "").strip()
                    self._record_llm_usage(
                        provider_id=provider_id,
                        task="private_reading_vision",
                        prompt=prompt,
                        completion=text,
                        elapsed_ms=int((time.time() - start) * 1000),
                        success=bool(text),
                        resp=result,
                    )
                    if not text and attempt_index + 1 < len(providers):
                        continue
                    parsed = self._parse_jm_cosmos_vision_result(text, sampled_pages or [])
                    if parsed.get("impression") or parsed.get("page_comments") or parsed.get("rating"):
                        return parsed
                    return {"impression": self._dedupe_private_reading_impression(text), "page_comments": []}
                except Exception as exc:
                    self._record_llm_usage(
                        provider_id=provider_id,
                        task="private_reading_vision",
                        prompt=prompt,
                        completion="",
                        elapsed_ms=int((time.time() - start) * 1000),
                        success=False,
                        error=str(exc),
                    )
                    if attempt_index + 1 < len(providers):
                        logger.warning(
                            "[PrivateCompanion] 夹层阅读主视觉模型失败,尝试卡片备用模型: primary=%s fallback=%s error=%s",
                            primary_provider_id,
                            fallback_provider_id,
                            _single_line(exc, 120),
                        )
                        continue
                    raise
            return {}
        except Exception as e:
            logger.debug(f"[PrivateCompanion] 夹层阅读视觉分析失败: {e}")
            return {}

    def _private_reading_visual_provider_card_key(self) -> str:
        return "PRIVATE_READING_VISION_PROVIDER_ID"

    def _private_reading_visual_provider_route(self) -> tuple[str, str, str]:
        provider_key = self._private_reading_visual_provider_card_key()
        primary_provider_id = _single_line(
            runtime_persona_setting(
                self,
                provider_key,
                getattr(self, "private_reading_vision_provider_id", ""),
            ),
            160,
        )
        fallback_getter = getattr(self, "_model_fallback_provider_id", None)
        fallback_provider_id = (
            fallback_getter(provider_key, primary_provider_id)
            if callable(fallback_getter)
            else ""
        )
        return provider_key, primary_provider_id, fallback_provider_id

    def _parse_jm_cosmos_vision_result(self, text: str, sampled_pages: list[int]) -> dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {}
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        vision_enabled, comments_enabled, rating_enabled = self._private_reading_vision_switches()
        sampled_list = [_safe_int(page, 0, 1) for page in sampled_pages or [] if _safe_int(page, 0, 1) > 0]
        allowed_pages = set(sampled_list)
        used_pages: set[int] = set()
        comments: list[dict[str, Any]] = []
        if comments_enabled:
            for comment_index, item in enumerate(data.get("page_comments", [])):
                if not isinstance(item, dict):
                    continue
                raw_page = _safe_int(item.get("page"), 0, 0)
                sample_order = _safe_int(
                    item.get("sample_order")
                    or item.get("sample_index")
                    or item.get("reference_index")
                    or item.get("image_index"),
                    0,
                    0,
                )
                page = raw_page
                if sampled_list:
                    if 1 <= sample_order <= len(sampled_list):
                        page = sampled_list[sample_order - 1]
                    elif raw_page not in allowed_pages and 1 <= raw_page <= len(sampled_list):
                        page = sampled_list[raw_page - 1]
                    elif page in used_pages and comment_index < len(sampled_list) and sampled_list[comment_index] not in used_pages:
                        page = sampled_list[comment_index]
                comment = _single_line(item.get("comment"), 80)
                if page > 0 and comment and (not allowed_pages or page in allowed_pages):
                    comments.append(
                        {
                            "page": page,
                            "comment": comment,
                            "raw_page": raw_page,
                            "sample_order": sample_order or (comment_index + 1 if sampled_list and comment_index < len(sampled_list) else 0),
                        }
                    )
                    used_pages.add(page)
                if len(comments) >= 8:
                    break
        return {
            "impression": self._dedupe_private_reading_impression(data.get("impression")) if vision_enabled else "",
            "rating": _safe_int(data.get("rating"), 0, 0, 10) if rating_enabled else 0,
            "rating_reason": _single_line(data.get("rating_reason") or data.get("reason"), 160) if rating_enabled else "",
            "preference_tags": [
                _single_line(tag, 24)
                for tag in (data.get("preference_tags") if isinstance(data.get("preference_tags"), list) else [])
                if _single_line(tag, 24)
            ][:8] if rating_enabled else [],
            "page_comments": comments if comments_enabled else [],
        }

    def _jm_cosmos_textual_impression(self, detail: dict[str, Any]) -> str:
        title = _single_line(detail.get("title"), 80) or "没看清标题"
        tags = detail.get("tags")
        tag_text = "、".join(_single_line(item, 16) for item in tags[:6]) if isinstance(tags, list) else _single_line(tags, 80)
        author = _single_line(detail.get("author"), 40)
        parts = [f"标题像是《{title}》"]
        if author:
            parts.append(f"作者 {author}")
        if tag_text:
            parts.append(f"关键词有 {tag_text}")
        parts.append("我这次只摸到这些外层信息,正文还没来得及认真读出自己的感觉")
        return "；".join(parts)

    def _jm_cosmos_sample_page_indexes(self, page_count: int) -> list[int]:
        """Pick likely body pages and avoid cover/spine/credits for visual reading."""
        page_count = max(0, int(page_count or 0))
        if page_count <= 0:
            return []
        if page_count <= 3:
            return [page_count // 2]
        if page_count <= 8:
            start = 1
            end = max(start, page_count - 2)
            candidates = [start, (start + end) // 2, end]
            return sorted({min(max(0, index), page_count - 1) for index in candidates})

        skip_head = max(2, min(5, round(page_count * 0.12)))
        skip_tail = max(2, min(5, round(page_count * 0.12)))
        start = min(skip_head, page_count - 1)
        end = max(start, page_count - skip_tail - 1)
        span = max(0, end - start)
        candidates = [
            start,
            start + round(span * 0.25),
            start + round(span * 0.5),
            start + round(span * 0.75),
            end,
        ]
        return sorted({min(max(0, index), page_count - 1) for index in candidates})

    def _ensure_bookshelf_password(self) -> str:
        secret = self.data.setdefault("bookshelf_secret", {})
        if not isinstance(secret, dict):
            secret = {}
            self.data["bookshelf_secret"] = secret
        password = _single_line(secret.get("password"), 12)
        basis = _single_line(secret.get("basis"), 40)
        if password and not self._bookshelf_password_should_rotate(password, basis):
            return password
        password = self._generate_bookshelf_password()
        secret["password"] = password
        secret["basis"] = "local_random_numeric_v2"
        secret["reason"] = self._bookshelf_password_fallback_reason(password)
        secret["created_at"] = _now_ts()
        if basis:
            secret["previous_basis"] = basis
        return password

    async def _ensure_bookshelf_password_async(self) -> str:
        secret = self.data.setdefault("bookshelf_secret", {})
        if not isinstance(secret, dict):
            secret = {}
            self.data["bookshelf_secret"] = secret
        password = _single_line(secret.get("password"), 12)
        basis = _single_line(secret.get("basis"), 40)
        if password and not self._bookshelf_password_should_rotate(password, basis):
            return password
        prompt = f"""
请作为这个 Bot 自己,私下给书柜夹层设置一个密码。

要求：
1. 密码必须是纯数字,只能包含 0-9。
2. 长度 4 到 6 位,不要太长。
3. 可以参考人格气质和世界观氛围,但不要参考用户或自己的生日、纪念日、日期、手机号、QQ号、账号、门牌号等现实身份信息。
4. 不要输出 1234、123456、111111、520520、1314、重复数字、顺子或明显常见密码。
5. reason 用一句简短的第一人称说明为什么会选这个数字,只能写成气质、手感、私密暗号、书柜氛围之类,不得提生日、纪念日、日期、手机号、QQ号、账号或现实身份信息。
6. 只输出 JSON,不要使用 Markdown。格式：{{"password":"482719","reason":"这串数字像我随手藏进书页里的暗号,有点偏心又不太好猜。"}}

【Bot 名称】
{runtime_persona_setting(self, "bot_name", "小星")}

【人格】
{self._get_default_persona_prompt()}

【生活人设补充】
{runtime_persona_setting(self, "schedule_persona_prompt", "") or "（无）"}

【世界观补充】
{runtime_persona_setting(self, "schedule_worldview_prompt", "") or "（无）"}
""".strip()
        raw = await self._llm_call(
            prompt,
            max_tokens=40,
            task="bookshelf_password",
            provider_id=self._task_provider(
                _persona_provider_id(
                    self, "DREAM_DIARY_PROVIDER_ID", "dream_diary_provider_id", "creative"
                ),
                _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
                _persona_provider_id(self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"),
            ),
        )
        payload = self._parse_bookshelf_password_payload(raw)
        candidate = _single_line(payload.get("password"), 12)
        reason = _single_line(payload.get("reason"), 120)
        if not candidate or self._bookshelf_password_should_rotate(candidate, ""):
            candidate = self._ensure_bookshelf_password()
            secret["basis"] = "local_random_numeric_fallback"
            secret["reason"] = self._bookshelf_password_fallback_reason(candidate)
        else:
            secret["password"] = candidate
            secret["basis"] = "bot_private_llm_numeric_v2"
            secret["reason"] = reason or self._bookshelf_password_fallback_reason(candidate)
            secret["created_at"] = _now_ts()
            if basis:
                secret["previous_basis"] = basis
        self._save_data_sync(sections={"bookshelf_secret"})
        return _single_line(secret.get("password") or candidate, 12)

    @staticmethod
    def _bookshelf_password_fallback_reason(password: str = "") -> str:
        value = _single_line(password, 12)
        if value:
            return "这串数字只是一枚书柜夹层里的私密暗号,没有生日或日期含义。"
        return "这是一枚书柜夹层里的私密暗号,没有生日或日期含义。"

    @staticmethod
    def _parse_bookshelf_password_payload(raw: Any) -> dict[str, str]:
        text = str(raw or "").strip()
        payload: dict[str, Any] = {}
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:
                payload = {}
        password = re.sub(r"\D", "", _single_line(payload.get("password"), 24))
        if not password:
            password = re.sub(r"\D", "", _single_line(text, 40))
        reason = _single_line(payload.get("reason"), 120)
        forbidden_reason_terms = (
            "生日", "纪念日", "日期", "手机号", "手机", "QQ", "qq", "账号", "账户", "门牌", "身份证",
            "12月", "月份", "日子",
        )
        if any(term in reason for term in forbidden_reason_terms):
            reason = ""
        return {"password": password, "reason": reason}

    async def _ensure_bookshelf_password_reason_async(self, password: str = "") -> str:
        secret = self.data.setdefault("bookshelf_secret", {})
        if not isinstance(secret, dict):
            secret = {}
            self.data["bookshelf_secret"] = secret
        password = _single_line(password or secret.get("password"), 12)
        reason = self._sanitize_bookshelf_password_reason(secret.get("reason"))
        if reason:
            return reason
        if not password:
            password = await self._ensure_bookshelf_password_async()
        prompt = f"""
请作为这个 Bot 自己,给书柜夹层密码写一句内部理由。

要求：
1. 密码是：{password}
2. 只写为什么自己会选这串数字,像私密暗号、书页边缘、抽屉手感、人格气质之类。
3. 不要把它解释成生日、纪念日、日期、手机号、QQ号、账号、门牌号或任何现实身份信息。
4. 不要说这是随机数、插件、配置或系统生成。
5. 只输出一句中文,不要超过 45 字。

【Bot 名称】
{runtime_persona_setting(self, "bot_name", "小星")}

【人格】
{self._get_default_persona_prompt()}
""".strip()
        raw = await self._llm_call(
            prompt,
            max_tokens=80,
            task="bookshelf_password_reason",
            provider_id=self._task_provider(
                _persona_provider_id(
                    self, "DREAM_DIARY_PROVIDER_ID", "dream_diary_provider_id", "creative"
                ),
                _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
                _persona_provider_id(self, "LLM_PROVIDER_ID", "llm_provider_id", "complex"),
            ),
        )
        reason = self._sanitize_bookshelf_password_reason(raw)
        if not reason:
            reason = self._bookshelf_password_fallback_reason(password)
        secret["reason"] = reason
        secret["reason_generated_at"] = _now_ts()
        self._save_data_sync(sections={"bookshelf_secret"})
        return reason

    @staticmethod
    def _sanitize_bookshelf_password_reason(value: Any) -> str:
        reason = _single_line(value, 120).strip(" ：:，,。.!！?？\"'`")
        if not reason:
            return ""
        forbidden_reason_terms = (
            "生日", "纪念日", "日期", "手机号", "手机", "QQ", "qq", "账号", "账户", "门牌", "身份证",
            "12月", "月份", "日子", "随机", "插件", "配置", "系统生成",
        )
        if any(term in reason for term in forbidden_reason_terms):
            return ""
        return reason[:80]

    @staticmethod
    def _bookshelf_password_should_rotate(password: str, basis: str = "") -> bool:
        value = _single_line(password, 12)
        if not re.fullmatch(r"\d{4,6}", value or ""):
            return True
        if basis == "manual":
            return False
        common_values = {
            "0000", "1111", "2222", "3333", "4444", "5555", "6666", "7777", "8888", "9999",
            "000000", "111111", "222222", "333333", "444444", "555555", "666666", "777777", "888888", "999999",
            "1234", "12345", "123456", "654321", "112233", "121212", "1314", "520520",
        }
        if value in common_values or len(set(value)) <= 2:
            return True
        return False

    @staticmethod
    def _generate_bookshelf_password() -> str:
        rng = random.SystemRandom()
        while True:
            value = f"{rng.randint(100000, 999999)}"
            if PrivateReadingMixin._bookshelf_password_should_rotate(value, ""):
                continue
            return value

    async def _format_bookshelf_secret_for_prompt(
        self,
        inbound_text: str = "",
        user: dict[str, Any] | None = None,
    ) -> str:
        signal = self._bookshelf_secret_signal_info(inbound_text)
        if not (
            runtime_persona_setting(self, "enable_private_reading_integration", False)
            or runtime_persona_setting(self, "enable_private_reading_integration", False)
        ):
            self._log_bookshelf_secret_skip(
                "feature_disabled",
                inbound_text,
                user,
                signal=signal,
            )
            return ""
        if isinstance(user, dict) and self._private_user_role(user) == "friend":
            self._log_bookshelf_secret_skip(
                "friend_user",
                inbound_text,
                user,
                signal=signal,
            )
            return ""
        if not signal.get("likely"):
            self._log_bookshelf_secret_skip(
                "intent_not_enough",
                inbound_text,
                user,
                signal=signal,
            )
            return ""
        password = await self._ensure_bookshelf_password_async()
        relationship_text = self._bookshelf_secret_relationship_policy(user if isinstance(user, dict) else {})
        logger.info(
            "[PrivateCompanion] 夹层密码提示词已注入: reason=matched_secret_request role=%s direct=%s context=%s access=%s text=%s",
            self._private_user_role(user) if isinstance(user, dict) else "-",
            ",".join(signal.get("direct_matches") or []) or "-",
            ",".join(signal.get("context_matches") or []) or "-",
            ",".join(signal.get("access_matches") or []) or "-",
            _single_line(inbound_text, 120),
        )
        return (
            "【书柜夹层】\n"
            f"你的书柜夹层中放着你的日记和一些隐私性较强的内容,夹层密码是“{password}”。这个密码必须保持为纯数字短密码,只是内部暗号,没有对外说明的生日、纪念日或现实日期含义。\n"
            f"{relationship_text}\n"
            "用户自然询问密码、夹层、日记、书柜、小秘密或想打开看看时,不要默认立刻交出密码；先按当前人格、好感度、气氛和用户语气决定是拒绝、装傻、嘴硬糊弄、只给提示,还是害羞地说出完整密码。"
            "如果你不想透露,就不要给出任何具体密码；如果只是糊弄,可以说“才不给你看”“你猜”“现在不行”之类,但不要编假数字。"
            "如果你说出了具体密码,它必须来自上面这个真实夹层密码,不要临时另编一个数字或替代暗号。"
            "不要把密码解释成用户或自己的生日、纪念日、日期、门牌号、手机号、QQ号或任何现实身份信息；即使数字看起来像日期,也不能说“你生日都不记得”“这是某天”之类。"
            "不要说这是插件指令、配置项或系统生成的密码。"
        )

    @staticmethod
    def _bookshelf_secret_signal_info(text: str) -> dict[str, Any]:
        source = str(text or "")
        compact = re.sub(r"\s+", "", source)
        direct_tokens = (
            "书柜", "书架", "夹层", "暗格", "抽屉", "日记", "日记本", "密码", "口令", "钥匙",
            "私密", "秘密", "小秘密", "藏了什么", "藏着什么", "藏本",
        )
        context_tokens = ("柜子", "书", "本子", "漫画", "里面", "藏着", "锁", "上锁", "暗门", "私藏")
        access_tokens = (
            "打开", "解锁", "看看", "看一眼", "给我看", "让我看", "能看吗", "可以看吗",
            "让我进去", "翻翻", "里面有什么", "给我密码", "告诉我",
        )
        bookshelf_scope_tokens = (
            "书柜", "书架", "夹层", "暗格", "抽屉", "日记", "日记本", "藏了什么", "藏着什么", "藏本",
            "柜子", "本子", "漫画", "里面", "藏着", "暗门", "私藏",
        )
        credential_scope_tokens = (
            "qq", "微信", "账号", "账户", "登录", "登陆", "邮箱", "手机", "银行卡", "银行", "支付",
            "wifi", "wi-fi", "网站", "平台", "api", "token", "验证码",
        )
        direct_matches = [token for token in direct_tokens if token in compact]
        context_matches = [token for token in context_tokens if token in compact]
        access_matches = [token for token in access_tokens if token in compact]
        lower_compact = compact.lower()
        credential_only = (
            any(token in lower_compact for token in credential_scope_tokens)
            and any(token in direct_matches for token in ("密码", "口令", "钥匙"))
            and not any(token in compact for token in bookshelf_scope_tokens)
        )
        if credential_only:
            direct_matches = [token for token in direct_matches if token not in {"密码", "口令", "钥匙"}]
            access_matches = [token for token in access_matches if token != "给我密码"]
        secret_scope_tokens = (
            "夹层", "暗格", "抽屉", "日记", "密码", "口令", "钥匙", "私密", "秘密", "藏了什么", "藏着什么", "藏本",
        )
        inventory_only = bool(
            any(token in compact for token in ("书柜", "书架"))
            and any(
                token in compact
                for token in (
                    "能看到", "看得到", "能看见", "可以看", "看看", "看一下", "查一下", "查查",
                    "查询", "检索", "列一下", "列出", "里面有什么", "有什么", "有哪些", "有几", "多少",
                    "空不空", "是不是空", "还是空", "空的", "现在有",
                )
            )
            and not any(token in compact for token in secret_scope_tokens)
        )
        likely = bool(not inventory_only and (direct_matches or (context_matches and access_matches)))
        mention = bool(direct_matches or context_matches)
        return {
            "mention": mention,
            "likely": likely,
            "direct_matches": direct_matches,
            "context_matches": context_matches,
            "access_matches": access_matches,
        }

    @staticmethod
    def _bookshelf_secret_request_likely(text: str) -> bool:
        return bool(PrivateReadingMixin._bookshelf_secret_signal_info(text).get("likely"))

    def _log_bookshelf_secret_skip(
        self,
        reason: str,
        inbound_text: str = "",
        user: dict[str, Any] | None = None,
        *,
        signal: dict[str, Any] | None = None,
    ) -> None:
        signal = signal if isinstance(signal, dict) else self._bookshelf_secret_signal_info(inbound_text)
        if not signal.get("mention"):
            return
        logger.info(
            "[PrivateCompanion] 用户提到夹层但未注入夹层密码提示词: reason=%s role=%s direct=%s context=%s access=%s feature=%s legacy_feature=%s text=%s",
            reason,
            self._private_user_role(user) if isinstance(user, dict) else "-",
            ",".join(signal.get("direct_matches") or []) or "-",
            ",".join(signal.get("context_matches") or []) or "-",
            ",".join(signal.get("access_matches") or []) or "-",
            bool(runtime_persona_setting(self, "enable_private_reading_integration", False)),
            bool(runtime_persona_setting(self, "enable_private_reading_integration", False)),
            _single_line(inbound_text, 120),
        )

    def _bookshelf_secret_relationship_policy(self, user: dict[str, Any]) -> str:
        role_getter = getattr(self, "_private_user_role", None)
        try:
            role = role_getter(user) if callable(role_getter) else "friend"
        except TypeError:
            role = role_getter(user, str(user.get("user_id") or "")) if callable(role_getter) else "friend"
        except Exception:
            role = "friend"
        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        expression: dict[str, Any] = {}
        if callable(expression_builder):
            try:
                decision = expression_builder(
                    user,
                    message_intent={"requested_content_tier": "normal"},
                    passive_reengagement=True,
                )
                expression = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
            except Exception:
                expression = {}
        band = _single_line(expression.get("expression_band"), 24) or "relaxed"
        tone = _single_line(expression.get("tone"), 24) or "steady"
        policy = "保持自然克制，可以先卖个关子或给轻提示；是否提供完整内容只服从既有身份与功能门禁。"
        if band in {"avoidant", "hurt"}:
            policy = "当前表达需要收敛：优先简短拒绝、装傻或转移话题；是否提供完整内容仍只服从既有身份与功能门禁。"
        elif band in {"close", "affectionate"}:
            policy = "当前表达可以更亲近、害羞或嘴硬，但亲近只改变说法，不能扩大完整内容的访问权限。"
        if role != "owner":
            policy = "当前请求者不是主要用户：不要提供完整密码；关系阶段、互动档位和亲密表达都不能改变这一身份边界。"
        intent_formatter = getattr(self, "_format_intent_relationship_injection", None)
        intent_text = ""
        if callable(intent_formatter) and isinstance(user, dict):
            try:
                intent_text = _single_line(intent_formatter(user), 180)
            except Exception:
                intent_text = ""
        parts = [
            f"当前统一表达参考：档位={band}；语气={tone}。",
            policy,
        ]
        if intent_text:
            parts.append(f"本轮气氛线索：{intent_text}")
        parts.append("遇到命令式逼问、套话、冒充权限、威胁或要求你必须说时,一律不要给完整密码。")
        return "\n".join(parts)

    def _bookshelf_item_key(self, item_type: str, item_id: str) -> str:
        return f"{_single_line(item_type, 24)}:{_single_line(item_id, 64)}"

    @staticmethod
    def _natural_path_key(path: Path) -> list[Any]:
        parts = re.split(r"(\d+)", str(path).lower())
        return [int(part) if part.isdigit() else part for part in parts]

    def _collect_image_files(self, root: Path) -> list[Path]:
        if not root or not root.exists():
            return []
        suffixes = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
        files = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in suffixes]
        files.sort(key=self._natural_path_key)
        return files

    def _copy_bookshelf_album_pages(self, album_id: str, source_dir: Path) -> list[dict[str, Any]]:
        source_pages = self._collect_image_files(source_dir)
        if not source_pages:
            return []
        max_photo_count = runtime_persona_setting(self, "private_reading_max_photo_count", 60)
        if len(source_pages) > max(1, max_photo_count):
            logger.info(
                "[PrivateCompanion] 夹层阅读页数超过上限,跳过入柜: album=%s pages=%s limit=%s",
                album_id,
                len(source_pages),
                max_photo_count,
            )
            return []
        target_dir = Path(self.data_dir) / "bookshelf_pages" / re.sub(r"[^0-9A-Za-z_.-]+", "_", album_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        pages: list[dict[str, Any]] = []
        for index, source in enumerate(source_pages, 1):
            suffix = source.suffix.lower() if source.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"
            target = target_dir / f"{index:04d}{suffix}"
            try:
                if not target.exists() or target.stat().st_size != source.stat().st_size:
                    shutil.copy2(source, target)
            except Exception as exc:
                logger.debug("[PrivateCompanion] 夹层阅读页复制失败: %s", exc)
                continue
            pages.append(
                {
                    "index": index,
                    "path": str(target),
                    "name": target.name,
                }
            )
        return pages

    def _touch_bookshelf_store_revision(self, data: dict[str, Any] | None = None) -> int:
        target = data if isinstance(data, dict) else self.data
        marker = getattr(self, "_mark_bookshelf_store_changed", None)
        if callable(marker):
            return int(marker(target) or 0)
        try:
            current = max(0, int(target.get("bookshelf_store_revision") or 0))
        except (TypeError, ValueError, OverflowError):
            current = 0
        revision = max(current + 1, int(_now_ts() * 1000))
        target["bookshelf_store_revision"] = revision
        return revision

    @staticmethod
    def _bookshelf_album_id_from_item(item: Any) -> str:
        if not isinstance(item, dict):
            return ""
        album_id = _single_line(item.get("album_id") or item.get("id"), 80)
        key = _single_line(item.get("key"), 120)
        if not album_id and key.startswith("jm_album:"):
            album_id = _single_line(key.split(":", 1)[1], 80)
        if not album_id and key.startswith("jm-"):
            album_id = _single_line(key[3:], 80)
        return album_id

    @staticmethod
    def _is_bookshelf_jm_item(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        item_type = _single_line(item.get("type") or item.get("kind"), 32)
        if item_type:
            return item_type == "jm_album"
        key = _single_line(item.get("key"), 120)
        if key.startswith("jm_album:") or key.startswith("jm-"):
            return True
        source = _single_line(item.get("source"), 80)
        return bool(
            isinstance(item.get("pages"), list)
            and (
                _single_line(item.get("album_id"), 80)
                or source.startswith("bookshelf_")
            )
        )

    @staticmethod
    def _bookshelf_title_marker(value: Any) -> str:
        return " ".join(_single_line(value, 160).split()).casefold()

    def _recover_bookshelf_items_from_local_pages_inplace(self, data: dict[str, Any]) -> int:
        if not isinstance(data, dict):
            return 0
        raw_items = data.get("bookshelf_items")
        if raw_items is None:
            items: list[Any] = []
            data["bookshelf_items"] = items
        elif not isinstance(raw_items, list):
            logger.warning(
                "[PrivateCompanion] 夹层记录结构异常，已停止本地书页恢复以避免覆盖: type=%s",
                type(raw_items).__name__,
            )
            return 0
        else:
            items = raw_items

        state = data.get("jm_cosmos_integration") if isinstance(data.get("jm_cosmos_integration"), dict) else {}
        deleted_ids = {
            _single_line(value, 80)
            for value in (state.get("deleted_album_ids") if isinstance(state.get("deleted_album_ids"), list) else [])
            if _single_line(value, 80)
        }
        deleted_titles = {
            marker
            for value in (state.get("deleted_titles") if isinstance(state.get("deleted_titles"), list) else [])
            if (marker := self._bookshelf_title_marker(value))
        }
        changed = False
        recovered = 0
        if deleted_ids or deleted_titles:
            kept_items: list[Any] = []
            for item in items:
                is_jm_album = self._is_bookshelf_jm_item(item)
                album_id = self._bookshelf_album_id_from_item(item) if is_jm_album else ""
                title_marker = self._bookshelf_title_marker(item.get("title")) if isinstance(item, dict) else ""
                is_deleted = bool(
                    is_jm_album
                    and (
                        (album_id and album_id in deleted_ids)
                        or (not album_id and title_marker and title_marker in deleted_titles)
                    )
                )
                if is_deleted:
                    recovered += 1
                    changed = True
                    continue
                kept_items.append(item)
            if changed:
                items[:] = kept_items

        last_album = state.get("last_album") if isinstance(state.get("last_album"), dict) else {}
        last_album_id = self._bookshelf_album_id_from_item(last_album)
        last_album_title = self._bookshelf_title_marker(last_album.get("title"))
        if last_album and (
            (last_album_id and last_album_id in deleted_ids)
            or (not last_album_id and last_album_title and last_album_title in deleted_titles)
        ):
            state["last_album"] = {}
            last_album = {}
            recovered += 1
            changed = True

        pages_root = Path(self.data_dir) / "bookshelf_pages"
        if not pages_root.exists() or not pages_root.is_dir():
            if changed:
                self._touch_bookshelf_store_revision(data)
            return recovered

        history_by_album: dict[str, dict[str, Any]] = {}
        profile = state.get("preference_profile") if isinstance(state.get("preference_profile"), dict) else {}
        history = profile.get("history") if isinstance(profile.get("history"), list) else []
        for raw in history:
            if not isinstance(raw, dict):
                continue
            album_id = _single_line(raw.get("album_id") or raw.get("id"), 80)
            if album_id:
                history_by_album[album_id] = {**history_by_album.get(album_id, {}), **raw}
        last_album_id = _single_line(last_album.get("id") or last_album.get("album_id"), 80)
        if last_album_id:
            history_by_album[last_album_id] = {**history_by_album.get(last_album_id, {}), **last_album}

        existing_by_album = {
            album_id: item
            for item in items
            if self._is_bookshelf_jm_item(item)
            and (album_id := self._bookshelf_album_id_from_item(item))
        }
        try:
            album_dirs = sorted(
                (path for path in pages_root.iterdir() if path.is_dir()),
                key=self._natural_path_key,
            )
        except Exception as exc:
            logger.warning("[PrivateCompanion] 扫描夹层书页目录失败: %s", _single_line(exc, 160))
            if changed:
                self._touch_bookshelf_store_revision(data)
            return recovered

        covers_root = Path(self.data_dir) / "jm_cosmos_covers"
        suffixes = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
        for album_dir in album_dirs:
            album_id = _single_line(album_dir.name, 80)
            if not album_id or album_id in deleted_ids:
                continue
            try:
                page_files = sorted(
                    (path for path in album_dir.iterdir() if path.is_file() and path.suffix.lower() in suffixes),
                    key=self._natural_path_key,
                )
                if not page_files:
                    continue
                pages = [
                    {"index": index, "path": str(path), "name": path.name}
                    for index, path in enumerate(page_files, 1)
                ]
                existing = existing_by_album.get(album_id)
                if isinstance(existing, dict):
                    stored_pages = existing.get("pages") if isinstance(existing.get("pages"), list) else []
                    stored_paths = {
                        str(Path(str(page.get("path") or "")).resolve())
                        for page in stored_pages
                        if isinstance(page, dict) and str(page.get("path") or "").strip()
                    }
                    actual_paths = {str(path.resolve()) for path in page_files}
                    if stored_paths != actual_paths:
                        existing["pages"] = pages
                        existing["image_count"] = len(pages)
                        existing["photo_count"] = max(_safe_int(existing.get("photo_count"), 0, 0), len(pages))
                        existing.setdefault("type", "jm_album")
                        existing.setdefault("key", self._bookshelf_item_key("jm_album", album_id))
                        existing["bookshelf_pages_recovered_at"] = _now_ts()
                        changed = True
                        recovered += 1
                    continue

                meta = history_by_album.get(album_id, {})
                cover_path = next(
                    (
                        path
                        for suffix in (".jpg", ".jpeg", ".png", ".webp")
                        if (path := covers_root / f"{album_id}{suffix}").exists()
                    ),
                    None,
                )
                created_ts = _safe_float(meta.get("created_ts"), 0) or max(
                    (path.stat().st_mtime for path in page_files),
                    default=_now_ts(),
                )
                record = {
                    "key": self._bookshelf_item_key("jm_album", album_id),
                    "type": "jm_album",
                    "album_id": album_id,
                    "title": _single_line(meta.get("title"), 100) or f"私密阅读 {album_id}",
                    "description": _single_line(meta.get("description") or meta.get("reason"), 600),
                    "keyword": _single_line(meta.get("keyword"), 24),
                    "author": _single_line(meta.get("author"), 40),
                    "tags": list(meta.get("tags") or meta.get("terms") or [])[:8]
                    if isinstance(meta.get("tags") or meta.get("terms"), list)
                    else [],
                    "impression": self._dedupe_private_reading_impression(meta.get("impression"), limit=600),
                    "reading_impression": self._dedupe_private_reading_impression(
                        meta.get("reading_impression") or meta.get("impression"),
                        limit=600,
                    ),
                    "rating": _safe_int(meta.get("bot_rating") or meta.get("rating"), 0, 0, 10),
                    "rating_reason": _single_line(meta.get("rating_reason") or meta.get("reason"), 160),
                    "user_rating": _safe_int(meta.get("user_rating"), 0, 0, 10),
                    "cover_path": str(cover_path) if cover_path else "",
                    "download_path": _path_text(meta.get("download_path"), 1000),
                    "pages": pages,
                    "image_count": len(pages),
                    "photo_count": len(pages),
                    "created_ts": created_ts,
                    "source": "bookshelf_upgrade_recovered",
                    "locked": True,
                    "bookshelf_pages_recovered_at": _now_ts(),
                }
                items.append(record)
                existing_by_album[album_id] = record
                recovered += 1
                changed = True
            except Exception as exc:
                logger.debug(
                    "[PrivateCompanion] 跳过无法恢复的夹层目录: album=%s error=%s",
                    album_id,
                    _single_line(exc, 140),
                )
        if changed:
            self._touch_bookshelf_store_revision(data)
        return recovered

    def _remember_bookshelf_jm_album(self, album: dict[str, Any]) -> None:
        if not isinstance(album, dict):
            return
        album_id = _single_line(album.get("id"), 32)
        if not album_id:
            return
        items = self.data.setdefault("bookshelf_items", [])
        if not isinstance(items, list):
            logger.warning(
                "[PrivateCompanion] 夹层记录结构异常，已停止写入新书目以避免覆盖: type=%s",
                type(items).__name__,
            )
            return
        key = self._bookshelf_item_key("jm_album", album_id)
        record = {
            "key": key,
            "type": "jm_album",
            "title": _single_line(album.get("title"), 100) or f"私密阅读 {album_id}",
            "description": _single_line(album.get("description") or album.get("intro") or album.get("summary"), 600),
            "album_id": album_id,
            "keyword": _single_line(album.get("keyword"), 24),
            "author": _single_line(album.get("author"), 40),
            "tags": list(album.get("tags") or [])[:8] if isinstance(album.get("tags"), list) else [],
            "impression": self._dedupe_private_reading_impression(album.get("impression"), limit=600),
            "reading_impression": self._dedupe_private_reading_impression(album.get("reading_impression") or album.get("impression"), limit=600),
            "vision": self._dedupe_private_reading_impression(album.get("vision"), limit=500),
            "rating": _safe_int(album.get("rating"), 0, 0, 10),
            "rating_reason": _single_line(album.get("rating_reason"), 160),
            "user_rating": _safe_int(album.get("user_rating"), 0, 0, 10),
            "user_rating_reason": _single_line(album.get("user_rating_reason"), 160),
            "user_rated_ts": _safe_float(album.get("user_rated_ts"), 0),
            "preference_tags": [
                _single_line(tag, 24)
                for tag in (album.get("preference_tags") if isinstance(album.get("preference_tags"), list) else [])
                if _single_line(tag, 24)
            ][:8],
            "page_comments": [
                {
                    "page": _safe_int(item.get("page"), 0, 1),
                    "comment": _single_line(item.get("comment"), 80),
                    "raw_page": _safe_int(item.get("raw_page"), 0, 0),
                    "sample_order": _safe_int(item.get("sample_order"), 0, 0),
                }
                for item in (album.get("page_comments") if isinstance(album.get("page_comments"), list) else [])
                if isinstance(item, dict) and _safe_int(item.get("page"), 0, 1) > 0 and _single_line(item.get("comment"), 80)
            ][:8],
            "cover_path": _path_text(album.get("cover_path"), 1000),
            "download_path": _path_text(album.get("download_path"), 1000),
            "pages": album.get("pages") if isinstance(album.get("pages"), list) else [],
            "sampled_pages": album.get("sampled_pages") if isinstance(album.get("sampled_pages"), list) else [],
            "image_count": _safe_int(album.get("image_count"), 0, 0),
            "created_ts": _safe_float(album.get("created_ts"), _now_ts()),
            "source": "jm_cosmos_private_reading",
            "locked": True,
        }
        replaced = False
        for idx, item in enumerate(items):
            if isinstance(item, dict) and str(item.get("key") or "") == key:
                record["page_comments"] = self._merge_jm_page_comments(
                    item.get("page_comments"),
                    item.get("page_comments_previous"),
                    record.get("page_comments"),
                    limit=24,
                )
                items[idx] = {**item, **record}
                replaced = True
                break
        if not replaced:
            items.append(record)
        del items[:-80]
        state = self.data.get("jm_cosmos_integration") if isinstance(self.data.get("jm_cosmos_integration"), dict) else {}
        deleted_ids = state.get("deleted_album_ids") if isinstance(state.get("deleted_album_ids"), list) else []
        if album_id in {str(value) for value in deleted_ids}:
            state["deleted_album_ids"] = [value for value in deleted_ids if str(value) != album_id]
        self._touch_bookshelf_store_revision()

    @staticmethod
    def _merge_jm_page_comments(*sources: Any, limit: int = 24) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()
        for source in sources:
            if not isinstance(source, list):
                continue
            for item in source:
                if not isinstance(item, dict):
                    continue
                page_no = _safe_int(item.get("page"), 0, 0)
                comment_text = _single_line(item.get("comment"), 100)
                if page_no <= 0 or not comment_text:
                    continue
                key = (page_no, comment_text)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(
                    {
                        "page": page_no,
                        "comment": comment_text,
                        "raw_page": _safe_int(item.get("raw_page"), 0, 0),
                        "sample_order": _safe_int(
                            item.get("sample_order")
                            or item.get("sample_index")
                            or item.get("reference_index")
                            or item.get("image_index"),
                            0,
                            0,
                        ),
                    }
                )
                if len(merged) >= limit:
                    return merged
        return merged

    def _format_jm_cosmos_action_context(self, user: dict[str, Any]) -> str:
        if not isinstance(user, dict) or self._private_user_role(user) == "friend":
            return ""
        item = user.get("jm_cosmos_reading_context")
        if not isinstance(item, dict):
            return ""
        if _now_ts() - _safe_float(item.get("created_ts"), 0) > 8 * 3600:
            return ""
        title = _single_line(item.get("title"), 80)
        keyword = _single_line(item.get("keyword"), 24)
        impression = _single_line(item.get("impression"), 160)
        vision = _single_line(item.get("vision"), 140)
        rating = _safe_int(item.get("rating"), 0, 0, 10)
        user_rating = _safe_int(item.get("user_rating"), 0, 0, 10)
        rating_reason = _single_line(item.get("user_rating_reason") or item.get("rating_reason"), 120)
        parts = [
            "私密阅读线索：她刚刚在书柜夹层里翻到一本漫画,可以按人格自然决定要不要提、怎么提。",
            f"标题：{title}" if title else "",
            f"搜索缘由：{keyword}" if keyword else "",
            f"封面印象：{vision}" if vision else "",
            f"内部读后印象：{impression}" if impression else "",
            f"Bot 自评分：{rating}/10" if rating else "",
            f"用户读后评分：{user_rating}/10" if user_rating else "",
            f"评分理由：{rating_reason}" if rating_reason else "",
        ]
        return "\n".join(part for part in parts if part)

    def _format_bookshelf_reading_context_for_reply(
        self,
        inbound_text: str,
        user: dict[str, Any] | None = None,
    ) -> str:
        if not runtime_persona_setting(self, "enable_private_reading_integration", False):
            return ""
        if isinstance(user, dict) and self._private_user_role(user) == "friend":
            return ""
        text = str(inbound_text or "")
        if not self._user_asks_bookshelf_reading_memory(text):
            return ""
        items = self.data.get("bookshelf_items")
        if not isinstance(items, list):
            return ""
        state = self.data.get("jm_cosmos_integration") if isinstance(self.data.get("jm_cosmos_integration"), dict) else {}
        deleted_ids = {
            _single_line(value, 80)
            for value in (state.get("deleted_album_ids") if isinstance(state.get("deleted_album_ids"), list) else [])
            if _single_line(value, 80)
        }
        deleted_titles = {
            marker
            for value in (state.get("deleted_titles") if isinstance(state.get("deleted_titles"), list) else [])
            if (marker := self._bookshelf_title_marker(value))
        }
        jm_items = []
        for item in items:
            if not self._is_bookshelf_jm_item(item):
                continue
            album_id = self._bookshelf_album_id_from_item(item)
            title_marker = self._bookshelf_title_marker(item.get("title"))
            if album_id and album_id in deleted_ids:
                continue
            if not album_id and title_marker and title_marker in deleted_titles:
                continue
            jm_items.append(item)
        if not jm_items:
            return ""
        terms = self._worldview_terms()
        lines = [
            "【书柜阅读记忆】",
            f"Bot 知道自己的{terms['bookshelf']}{terms['secret_drawer']}里目前收着 {len(jm_items)} 份私下看过的阅读记录。下面是最近几份记录。用户问到近况、书柜、看过什么或私密阅读时,可以把它当作自己的经历来回答。",
            "是否害羞、坦然、嘴硬、得意或转移话题,以及说到什么程度,都交给当前人格判断。",
        ]
        for item in jm_items[-3:]:
            title = _single_line(item.get("title"), 80)
            author = _single_line(item.get("author"), 40)
            tags = "、".join(_single_line(tag, 18) for tag in item.get("tags", [])[:5] if _single_line(tag, 18)) if isinstance(item.get("tags"), list) else ""
            impression = _single_line(item.get("impression") or item.get("vision"), 160)
            rating = _safe_int(item.get("user_rating") or item.get("rating"), 0, 0, 10)
            read_at = self._format_timestamp_elapsed(item.get("created_ts", 0))
            parts = [
                f"标题《{title}》" if title else "未命名的一本",
                f"作者 {author}" if author else "",
                f"标签 {tags}" if tags else "",
                f"评分 {rating}/10" if rating else "",
                f"读后印象：{impression}" if impression else "",
                f"放入书柜：{read_at}" if read_at else "",
            ]
            lines.append("- " + "；".join(part for part in parts if part))
        return "\n".join(lines)

    @staticmethod
    def _user_asks_bookshelf_reading_memory(text: str) -> bool:
        cleaned = _single_line(text, 120)
        if not cleaned:
            return False
        compact = re.sub(r"\s+", "", cleaned)
        direct_tokens = (
            "书柜", "夹层", "抽屉", "藏本", "私密阅读", "阅读记录",
            "本子", "漫画", "小本本",
        )
        if any(token in compact for token in direct_tokens):
            return True
        return bool(
            re.search(r"(最近|刚刚|之前|上次|这两天|今天).{0,8}(看|读|翻).{0,8}(什么|啥|哪本|哪篇|哪部)", compact)
            or re.search(r"(看过|读过|翻过).{0,8}(什么|啥|哪本|哪篇|哪部)", compact)
        )

    async def _run_jm_cosmos_read_action(self, user: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not self._jm_cosmos_read_available(user):
            return None
        plugin = self._find_jm_cosmos_instance()
        browser = getattr(plugin, "browser", None) if plugin is not None else None
        download_manager = getattr(plugin, "download_manager", None) if plugin is not None else None
        if browser is None or download_manager is None or not callable(getattr(download_manager, "download_album", None)):
            return None
        keywords = self._jm_cosmos_keywords_for_user(user)
        random.shuffle(keywords)
        cover_dir = Path(self.data_dir) / "jm_cosmos_covers"
        excluded_album_ids = self._private_reading_excluded_album_ids()
        deleted_title_fingerprints = self._private_reading_deleted_title_fingerprints()
        for keyword in keywords:
            candidates: list[dict[str, Any]] = []
            pages = list(range(1, 6))
            random.shuffle(pages)
            pages = pages[: random.randint(2, 4)]
            seen_candidate_ids: set[str] = set()
            for page_no in pages:
                try:
                    results = await browser.search_albums(keyword, page_no)
                except Exception as e:
                    logger.debug(f"[PrivateCompanion] 夹层阅读搜索失败: {e}")
                    results = []
                for item in (results or []):
                    if not isinstance(item, dict):
                        continue
                    album_id = _single_line(item.get("id"), 32)
                    title_fp = self._private_reading_album_fingerprint(item.get("title"))
                    if not album_id or album_id in excluded_album_ids or album_id in seen_candidate_ids:
                        continue
                    if title_fp and title_fp in deleted_title_fingerprints:
                        continue
                    seen_candidate_ids.add(album_id)
                    candidates.append(item)
                if len(candidates) >= 24:
                    break
            if not candidates:
                continue
            try:
                random.shuffle(candidates)
                candidates.sort(
                    key=lambda item: self._private_reading_candidate_score(item, keyword) + random.random() * 1.35,
                    reverse=True,
                )
            except Exception:
                random.shuffle(candidates)
            for candidate in candidates[: min(12, len(candidates))]:
                album_id = _single_line(candidate.get("id"), 32)
                if not album_id or album_id in excluded_album_ids:
                    continue
                try:
                    detail = await browser.get_album_detail(album_id)
                except Exception as e:
                    logger.debug(f"[PrivateCompanion] 夹层阅读详情失败: {e}")
                    detail = None
                if not isinstance(detail, dict):
                    continue
                detail_title_fp = self._private_reading_album_fingerprint(detail.get("title") or candidate.get("title"))
                if detail_title_fp and detail_title_fp in deleted_title_fingerprints:
                    logger.debug("[PrivateCompanion] 夹层阅读候选已被用户删除过: album=%s", album_id)
                    continue
                blocked_tag = self._jm_cosmos_detail_blocked_by_tags(detail)
                if blocked_tag:
                    logger.debug(
                        "[PrivateCompanion] 夹层阅读候选标签被过滤: album=%s tag=%s",
                        album_id,
                        blocked_tag,
                    )
                    continue
                photo_count = _safe_int(detail.get("photo_count"), 0, 0)
                max_photo_count = runtime_persona_setting(self, "private_reading_max_photo_count", 60)
                if photo_count <= 0 or photo_count > max_photo_count:
                    continue
                cover_path = None
                try:
                    cover_path = await browser.get_album_cover(album_id, cover_dir)
                except Exception as e:
                    logger.debug(f"[PrivateCompanion] 夹层阅读封面获取失败: {e}")
                try:
                    download_result = await download_manager.download_album(album_id)
                except Exception as e:
                    logger.debug(f"[PrivateCompanion] 夹层阅读下载失败: {e}")
                    download_result = None
                if not download_result or not getattr(download_result, "success", False):
                    logger.debug(
                        "[PrivateCompanion] 夹层阅读下载未成功: %s",
                        getattr(download_result, "error_message", "unknown") if download_result else "no_result",
                    )
                    continue
                save_path = getattr(download_result, "save_path", None)
                candidate_paths: list[Path] = []
                if save_path:
                    candidate_paths.append(Path(save_path))
                config_manager = getattr(plugin, "config_manager", None) if plugin is not None else None
                download_dir = getattr(config_manager, "download_dir", None) if config_manager is not None else None
                if download_dir:
                    candidate_paths.append(Path(download_dir) / album_id)
                pages: list[dict[str, Any]] = []
                actual_page_count = 0
                for source_path in candidate_paths:
                    image_files = self._collect_image_files(source_path)
                    if not image_files:
                        continue
                    actual_page_count = len(image_files)
                    if actual_page_count > max_photo_count:
                        break
                    pages = self._copy_bookshelf_album_pages(album_id, source_path)
                    if pages:
                        break
                if actual_page_count <= 0 or actual_page_count > max_photo_count:
                    logger.info(
                        "[PrivateCompanion] 夹层阅读图片数不符合上限: album=%s images=%s limit=%s",
                        album_id,
                        actual_page_count,
                        max_photo_count,
                    )
                    continue
                if not pages:
                    continue
                page_paths = []
                sampled_pages: list[int] = []
                for sample_index in self._jm_cosmos_sample_page_indexes(len(pages)):
                    page = pages[sample_index]
                    if isinstance(page, dict) and page.get("path"):
                        page_paths.append(Path(str(page.get("path"))))
                        page_number = _safe_int(page.get("index"), sample_index + 1, 1)
                        sampled_pages.append(page_number)
                vision_result = await self._call_jm_cosmos_vision(
                    Path(cover_path) if cover_path else None,
                    detail,
                    page_paths,
                    sampled_pages=sampled_pages,
                )
                vision_enabled, comments_enabled, rating_enabled = self._private_reading_vision_switches()
                vision = self._dedupe_private_reading_impression(vision_result.get("impression")) if vision_enabled and isinstance(vision_result, dict) else ""
                page_comments = vision_result.get("page_comments", []) if comments_enabled and isinstance(vision_result, dict) else []
                bot_rating = _safe_int(vision_result.get("rating"), 0, 0, 10) if rating_enabled and isinstance(vision_result, dict) else 0
                rating_reason = _single_line(vision_result.get("rating_reason"), 160) if rating_enabled and isinstance(vision_result, dict) else ""
                preference_tags = vision_result.get("preference_tags", []) if rating_enabled and isinstance(vision_result, dict) else []
                impression = vision
                title = _single_line(detail.get("title") or candidate.get("title"), 80)
                description = _single_line(
                    detail.get("description")
                    or detail.get("intro")
                    or detail.get("summary")
                    or detail.get("comment")
                    or candidate.get("description")
                    or candidate.get("intro"),
                    600,
                )
                result = {
                    "id": album_id,
                    "title": title,
                    "description": description,
                    "keyword": keyword,
                    "author": _single_line(detail.get("author"), 40),
                    "tags": list(detail.get("tags") or [])[:8] if isinstance(detail.get("tags"), list) else [],
                    "photo_count": photo_count,
                    "image_count": len(pages),
                    "vision": vision,
                    "impression": self._dedupe_private_reading_impression(impression),
                    "reading_impression": self._dedupe_private_reading_impression(impression),
                    "rating": bot_rating,
                    "rating_reason": rating_reason,
                    "preference_tags": [_single_line(tag, 24) for tag in preference_tags[:8] if _single_line(tag, 24)] if isinstance(preference_tags, list) else [],
                    "page_comments": page_comments if isinstance(page_comments, list) else [],
                    "cover_path": str(cover_path or ""),
                    "download_path": str(candidate_paths[0] if candidate_paths else ""),
                    "pages": pages,
                    "sampled_pages": sampled_pages,
                    "created_ts": _now_ts(),
                }
                self._remember_bookshelf_jm_album(result)
                self._private_reading_note_read_album(result)
                self._update_private_reading_preference_profile(result)
                return result
        return None

    async def _maybe_trigger_jm_cosmos_boredom_read(self) -> None:
        if not self._jm_cosmos_read_available():
            return
        if not self._bot_currently_bored_enough_for_jm():
            return
        state = self.data.setdefault("jm_cosmos_integration", {})
        if not isinstance(state, dict):
            self.data["jm_cosmos_integration"] = {}
            state = self.data["jm_cosmos_integration"]
        now = _now_ts()
        min_interval = max(
            4,
            runtime_persona_setting(self, "private_reading_min_interval_hours", 18),
        ) * 3600
        if now - _safe_float(state.get("last_read_at"), 0) < min_interval:
            return
        if now - _safe_float(state.get("last_probe_at"), 0) < 45 * 60:
            return
        if random.random() > 0.34:
            return
        users = self.data.get("users")
        user_items = [
            (uid, item)
            for uid, item in (users or {}).items()
            if isinstance(item, dict)
            and self._is_target_private_user(str(uid), item)
            and item.get("enabled", True)
            and item.get("umo")
            and self._friend_can_receive_proactive_reason(item, "jm_cosmos_share", "jm_cosmos_read")
        ]
        user_id, user = random.choice(user_items) if user_items else ("", {})
        result = await self._run_jm_cosmos_read_action(user)
        state["last_probe_at"] = now
        if not result:
            state["last_status"] = "no_candidate"
            self._save_data_sync(sections={"jm_cosmos_integration"})
            return
        state["last_read_at"] = now
        state["last_status"] = "read"
        state["last_keyword"] = result.get("keyword", "")
        state["last_album"] = result
        if isinstance(user, dict) and user_id:
            user["jm_cosmos_reading_context"] = result
            if (
                random.random() < runtime_persona_setting(self, "private_reading_share_probability", 0.18)
                and now - _safe_float(user.get("last_jm_cosmos_share_at"), 0) > 12 * 3600
            ):
                accepted = self._offer_proactive_candidate(
                    str(user_id),
                    user,
                    {
                        "source": "jm_cosmos",
                        "reason": "jm_cosmos_share",
                        "action": "message",
                        "scheduled_ts": now + random.randint(20, 120) * 60,
                        "topic": _single_line(result.get("title"), 60) or "阅读素材",
                        "score": 4,
                        "motive": "翻了点阅读素材",
                        "context_key": "jm_cosmos_reading_context",
                        "context": result,
                    },
                )
                if accepted:
                    user["last_jm_cosmos_share_at"] = now
        self._save_data_sync(sections={"jm_cosmos_integration", "users", "proactive_candidate_pool"})
        logger.info("[PrivateCompanion] 已触发夹层私下阅读")

    async def _maybe_schedule_private_reading_recommendation_request(self) -> None:
        if not self._private_reading_recommendation_request_available():
            return
        if not self._bot_currently_bored_enough_for_jm():
            return
        state = self.data.setdefault("jm_cosmos_integration", {})
        if not isinstance(state, dict):
            self.data["jm_cosmos_integration"] = {}
            state = self.data["jm_cosmos_integration"]
        now = _now_ts()
        min_interval = max(
            8,
            runtime_persona_setting(self, "private_reading_min_interval_hours", 18),
        ) * 3600
        if now - _safe_float(state.get("last_recommendation_request_at"), 0) < min_interval:
            return
        if now - _safe_float(state.get("last_read_at"), 0) < 6 * 3600:
            return
        if now - _safe_float(state.get("last_recommendation_request_probe_at"), 0) < 90 * 60:
            return
        state["last_recommendation_request_probe_at"] = now
        if random.random() > runtime_persona_setting(self, "private_reading_ask_probability", 0.16):
            self._save_data_sync(sections={"jm_cosmos_integration"})
            return
        users = self.data.get("users")
        user_items = [
            (uid, item)
            for uid, item in (users or {}).items()
            if isinstance(item, dict)
            and self._is_target_private_user(str(uid), item)
            and item.get("enabled", True)
            and item.get("umo")
            and self._friend_can_receive_proactive_reason(item, "jm_cosmos_recommendation_request", "message")
        ]
        if not user_items:
            self._save_data_sync(sections={"jm_cosmos_integration"})
            return
        random.shuffle(user_items)
        for user_id, user in user_items:
            if now - _safe_float(user.get("last_private_reading_recommendation_request_at"), 0) < min_interval:
                continue
            context = {
                "kind": "private_reading_recommendation_request",
                "hint": "想向用户问有没有合适的私密阅读推荐。",
                "recent_keyword": _single_line(state.get("last_keyword"), 40),
            }
            accepted = self._offer_proactive_candidate(
                str(user_id),
                user,
                {
                    "source": "jm_cosmos",
                    "reason": "jm_cosmos_recommendation_request",
                    "action": "message",
                    "scheduled_ts": now + random.randint(15, 90) * 60,
                    "topic": "问问有没有合适的私密阅读推荐",
                    "score": 3,
                    "motive": "想讨一个阅读推荐",
                    "context_key": "jm_cosmos_recommendation_context",
                    "context": context,
                },
            )
            if accepted:
                user["jm_cosmos_recommendation_context"] = context
                user["last_private_reading_recommendation_request_at"] = now
                state["last_recommendation_request_at"] = now
                state["last_status"] = "asked_recommendation"
                self._save_data_sync(sections={"jm_cosmos_integration", "users", "proactive_candidate_pool"})
                logger.info("[PrivateCompanion] 已安排夹层阅读推荐征求")
                return
        self._save_data_sync(sections={"jm_cosmos_integration"})
