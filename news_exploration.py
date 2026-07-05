# -*- coding: utf-8 -*-
"""
NewsExplorationMixin — 从 main.py 重新拆分出的新闻阅读/网页探索
"""
from __future__ import annotations

import asyncio
import base64
import codecs
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
from .helpers import _date_key, _now_ts, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks, _text_looks_garbled, _today_key
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

DEFAULT_AI_DAILY_MORNING_UID = "3706929260006322"
DEFAULT_AI_DAILY_JUYA_UID = "285286947"
DEFAULT_AI_DAILY_SOURCES = "\n".join(
    [
        f"AI日报|橘鸦Juya|{DEFAULT_AI_DAILY_JUYA_UID}|日报 早报|23:00",
        f"AI早报|黑鸦Heya|{DEFAULT_AI_DAILY_MORNING_UID}|早报 日报|12:00",
    ]
)
BILIBILI_AI_BOT_PLUGIN_NAME = "astrbot_plugin_bilibili_ai_bot"
BILIBILI_PUBLIC_INFO_PLUGIN_NAME = "astrbot_plugin_bilibili"
BILIBILI_AI_BOT_LEGACY_DATA_NAMES = (
    BILIBILI_AI_BOT_PLUGIN_NAME,
    "astrbot_plugin_bilibili_bot",
)

DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
        "Hacker News|https://hnrss.org/frontpage",
        "MIT Technology Review|https://www.technologyreview.com/feed/",
        "Ars Technica|https://feeds.arstechnica.com/arstechnica/index",
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
_NEWS_BINARY_CONTENT_TYPE_PREFIXES = ("image/", "audio/", "video/", "font/")
_NEWS_BINARY_CONTENT_TYPES = {
    "application/octet-stream",
    "application/pdf",
    "application/zip",
    "application/x-gzip",
    "application/x-protobuf",
}
_NEWS_TEXTUAL_CONTENT_TYPES = {
    "application/xhtml+xml",
    "application/xml",
    "application/rss+xml",
    "application/atom+xml",
    "image/svg+xml",
    "text/html",
    "text/plain",
    "text/xml",
}
_NEWS_BINARY_SIGNATURES = (
    b"\xff\xd8\xff",
    b"JFIF\x00",
    b"Exif\x00\x00",
    b"\x89PNG\r\n\x1a\n",
    b"GIF87a",
    b"GIF89a",
    b"RIFF",
    b"%PDF-",
    b"PK\x03\x04",
)
_NEWS_MOJIBAKE_MARKERS = ("Ã", "â", "鈥", "銆", "鏉", "锟", "Ð", "Ê", "¤", "\ufffd")


def _news_content_type_base(content_type: Any) -> str:
    return str(content_type or "").split(";", 1)[0].strip().lower()


def _news_charset_from_content_type(content_type: Any) -> str:
    match = re.search(r"charset\s*=\s*['\"]?\s*([A-Za-z0-9._-]+)", str(content_type or ""), flags=re.I)
    return match.group(1).strip() if match else ""


def _news_meta_charset(raw_bytes: bytes) -> str:
    head = raw_bytes[:4096].decode("ascii", errors="ignore")
    for pattern in (
        r"<meta[^>]+charset=['\"]?\s*([A-Za-z0-9._-]+)",
        r"<meta[^>]+content=['\"][^>]*charset\s*=\s*([A-Za-z0-9._-]+)",
    ):
        match = re.search(pattern, head, flags=re.I)
        if match:
            return match.group(1).strip()
    return ""


def _normalize_news_charset(name: Any) -> str:
    normalized = str(name or "").strip().lower().replace("_", "-")
    aliases = {
        "gb2312": "gb18030",
        "gbk": "gb18030",
        "x-gbk": "gb18030",
        "utf8": "utf-8",
    }
    return aliases.get(normalized, normalized)


def _news_response_looks_binary(raw_bytes: bytes, *, content_type: Any = "") -> bool:
    sample = raw_bytes[:2048]
    base_type = _news_content_type_base(content_type)
    if base_type:
        if any(base_type.startswith(prefix) for prefix in _NEWS_BINARY_CONTENT_TYPE_PREFIXES):
            return True
        if base_type in _NEWS_BINARY_CONTENT_TYPES:
            return True
        if base_type not in _NEWS_TEXTUAL_CONTENT_TYPES and not base_type.startswith("text/"):
            lowered = sample[:512].lower()
            if b"<html" not in lowered and b"<!doctype html" not in lowered and b"<article" not in lowered:
                return True
    for signature in _NEWS_BINARY_SIGNATURES:
        if sample.startswith(signature):
            return True
    if sample.count(b"\x00") > 0:
        return True
    control_bytes = sum(1 for byte in sample if byte < 32 and byte not in (9, 10, 13))
    if sample and control_bytes / len(sample) > 0.2:
        return True
    return False


def _score_news_decoded_text(text: str) -> tuple[int, int]:
    if not text:
        return (10**9, 0)
    sample = text[:6000]
    replacement_count = sample.count("\ufffd")
    mojibake_count = sum(sample.count(marker) for marker in _NEWS_MOJIBAKE_MARKERS if marker != "\ufffd")
    control_count = sum(1 for ch in sample if ord(ch) < 32 and ch not in "\n\r\t")
    html_markers = len(re.findall(r"<(?:html|body|article|div|p|meta|title)\b", sample, flags=re.I))
    readable_count = sum(
        1
        for ch in sample
        if ch.isalnum() or ch in " \n\r\t，。！？；：、“”‘’（）《》【】—-.,!?;:()[]/%&+#@_=<>\"'"
    )
    score = replacement_count * 24 + mojibake_count * 8 + control_count * 40
    score -= min(html_markers * 6, 60)
    score -= min(readable_count // 24, 40)
    return (score, -len(sample))


def _decode_news_response_text(
    raw_bytes: bytes,
    *,
    content_type: Any = "",
    declared_charset: Any = "",
) -> str:
    if not raw_bytes:
        return ""
    candidates: list[str] = []
    if raw_bytes.startswith(codecs.BOM_UTF8):
        candidates.append("utf-8-sig")
    elif raw_bytes.startswith(codecs.BOM_UTF16_LE):
        candidates.append("utf-16-le")
    elif raw_bytes.startswith(codecs.BOM_UTF16_BE):
        candidates.append("utf-16-be")
    candidates.extend(
        [
            _normalize_news_charset(declared_charset),
            _normalize_news_charset(_news_charset_from_content_type(content_type)),
            _normalize_news_charset(_news_meta_charset(raw_bytes)),
            "utf-8",
            "utf-8-sig",
            "gb18030",
            "big5",
            "latin1",
        ]
    )
    seen: set[str] = set()
    best_text = ""
    best_score = (10**9, 0)
    for encoding in candidates:
        if not encoding or encoding in seen:
            continue
        seen.add(encoding)
        for error_mode, penalty in (("strict", 0), ("replace", 12)):
            try:
                decoded = raw_bytes.decode(encoding, errors=error_mode)
            except Exception:
                continue
            if not decoded.strip():
                continue
            score = _score_news_decoded_text(decoded)
            adjusted = (score[0] + penalty, score[1])
            if adjusted < best_score:
                best_score = adjusted
                best_text = decoded
    if best_text:
        return best_text
    return raw_bytes.decode("utf-8", errors="ignore")

class NewsExplorationMixin:
    """新闻阅读/网页探索"""

    def _external_event_pool(self) -> list[dict[str, Any]]:
        raw = self.data.setdefault("external_event_pool", [])
        if not isinstance(raw, list):
            raw = []
            self.data["external_event_pool"] = raw
        return raw

    def _cleanup_external_event_pool(self, *, now: float | None = None) -> list[dict[str, Any]]:
        now = _now_ts() if now is None else now
        kept: list[dict[str, Any]] = []
        for item in self._external_event_pool():
            if not isinstance(item, dict):
                continue
            created = _safe_float(item.get("created_ts"), 0)
            if created > 0 and now - created <= 48 * 3600:
                kept.append(item)
        self.data["external_event_pool"] = kept[-200:]
        return self.data["external_event_pool"]

    def _external_event_signature(self, payload: dict[str, Any], *, source_type: str = "") -> str:
        title = _single_line(payload.get("headline") or payload.get("topic") or payload.get("title") or payload.get("source_title"), 120).lower()
        source = _single_line(payload.get("selected_source") or payload.get("source_title") or payload.get("source"), 60).lower()
        link = _single_line(payload.get("selected_link") or payload.get("source_url") or payload.get("link") or payload.get("video_link"), 220).lower()
        if link:
            link = re.sub(r"https?://", "", link)
            link = link.split("?", 1)[0]
        compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", title)
        return "|".join(part for part in (_single_line(source_type, 24).lower(), source[:24], compact[:64], link[:96]) if part)

    def _external_event_title_fingerprint(self, payload: dict[str, Any], *, source_type: str = "") -> str:
        title = _single_line(payload.get("headline") or payload.get("topic") or payload.get("title") or payload.get("source_title"), 140).lower()
        if not title:
            return ""
        title = re.sub(r"https?://\S+", "", title)
        title = re.sub(r"\b\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\b", "", title)
        title = re.sub(r"\b\d{1,2}:\d{2}\b", "", title)
        title = re.sub(r"(?:第?\d+[期条]|今日|今天|昨夜|昨天|早报|日报|周报|速览|合集|汇总)", "", title)
        compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", title)
        if len(compact) < 8:
            return ""
        digest = hashlib.sha1(compact.encode("utf-8", "ignore")).hexdigest()[:20]
        return f"{_single_line(source_type, 24).lower()}:title:{digest}"

    def _external_event_self_link_cache(self) -> dict[str, Any]:
        raw = self.data.setdefault("external_event_self_link_cache", {})
        if not isinstance(raw, dict):
            raw = {}
            self.data["external_event_self_link_cache"] = raw
        return raw

    def _cleanup_external_event_self_link_cache(self, *, now: float | None = None) -> dict[str, Any]:
        now = _now_ts() if now is None else now
        cache = self._external_event_self_link_cache()
        kept: dict[str, Any] = {}
        ranked: list[tuple[float, str, dict[str, Any]]] = []
        for key, item in cache.items():
            if not isinstance(item, dict):
                continue
            created = _safe_float(item.get("created_ts"), 0)
            if created <= 0 or now - created > 72 * 3600:
                continue
            ranked.append((_safe_float(item.get("last_hit_ts"), created), str(key), item))
        ranked.sort(key=lambda row: row[0])
        for _, key, item in ranked[-240:]:
            kept[key] = item
        self.data["external_event_self_link_cache"] = kept
        return kept

    def _external_event_self_link_cache_keys(self, payload: dict[str, Any], *, source_type: str = "") -> list[str]:
        keys: list[str] = []
        for key in (
            self._external_event_signature(payload, source_type=source_type),
            self._external_event_title_fingerprint(payload, source_type=source_type),
        ):
            key = _single_line(key, 220)
            if key and key not in keys:
                keys.append(key)
        return keys

    def _cached_external_event_wish(self, payload: dict[str, Any], *, source_type: str = "") -> dict[str, Any]:
        keys = self._external_event_self_link_cache_keys(payload, source_type=source_type)
        if not keys:
            return {}
        now = _now_ts()
        cache = self._cleanup_external_event_self_link_cache(now=now)
        for key in keys:
            item = cache.get(key)
            if not isinstance(item, dict):
                continue
            wish = item.get("wish")
            if not isinstance(wish, dict):
                continue
            item["hit_count"] = _safe_int(item.get("hit_count"), 0, 0) + 1
            item["last_hit_ts"] = now
            result = dict(wish)
            result["cache_hit"] = True
            result["cache_key"] = key
            logger.info(
                "[PrivateCompanion] 外界信息自我关联命中缓存: source=%s key=%s hit=%s",
                source_type,
                key,
                item["hit_count"],
            )
            return result
        return {}

    def _remember_external_event_wish_cache(self, payload: dict[str, Any], wish: dict[str, Any], *, source_type: str = "") -> None:
        if not isinstance(wish, dict) or not wish:
            return
        keys = self._external_event_self_link_cache_keys(payload, source_type=source_type)
        if not keys:
            return
        now = _now_ts()
        cache = self._cleanup_external_event_self_link_cache(now=now)
        stored_wish = {
            key: value
            for key, value in dict(wish).items()
            if key
            in {
                "relevance",
                "desire",
                "should_share",
                "share_probability",
                "self_link",
                "motive",
                "tone",
                "boundary",
                "source_type",
                "boost_reason",
            }
        }
        stored_wish["created_ts"] = now
        stored_wish["source_type"] = _single_line(source_type, 24)
        title = _single_line(payload.get("headline") or payload.get("topic") or payload.get("title") or payload.get("source_title"), 120)
        for key in keys:
            cache[key] = {
                "wish": stored_wish,
                "title": title,
                "source_type": _single_line(source_type, 24),
                "created_ts": now,
                "last_hit_ts": now,
                "hit_count": _safe_int((cache.get(key) or {}).get("hit_count") if isinstance(cache.get(key), dict) else 0, 0, 0),
            }
        self._cleanup_external_event_self_link_cache(now=now)

    def _external_event_recently_seen(self, payload: dict[str, Any], *, source_type: str = "", now: float | None = None) -> bool:
        now = _now_ts() if now is None else now
        signature = self._external_event_signature(payload, source_type=source_type)
        if not signature:
            return False
        for item in self._cleanup_external_event_pool(now=now):
            if str(item.get("signature") or "") != signature:
                continue
            if now - _safe_float(item.get("created_ts"), 0) <= 24 * 3600:
                return True
        return False

    def _remember_external_event(self, payload: dict[str, Any], *, source_type: str = "", reason: str = "") -> None:
        now = _now_ts()
        signature = self._external_event_signature(payload, source_type=source_type)
        if not signature:
            return
        pool = self._cleanup_external_event_pool(now=now)
        pool.append(
            {
                "signature": signature,
                "source_type": _single_line(source_type, 24),
                "reason": _single_line(reason, 40),
                "title": _single_line(payload.get("headline") or payload.get("topic") or payload.get("title"), 120),
                "created_ts": now,
            }
        )
        del pool[:-200]

    def _external_event_user_interest_score(self, user: dict[str, Any], payload: dict[str, Any]) -> int:
        memory_text = ""
        formatter = getattr(self, "_format_companion_memory_for_prompt", None)
        if callable(formatter):
            try:
                memory_text = _single_line(formatter(user), 700).lower()
            except Exception:
                memory_text = ""
        haystack = (
            _single_line(payload.get("headline") or payload.get("topic") or payload.get("title"), 180)
            + " "
            + _single_line(payload.get("impression") or payload.get("summary") or payload.get("note"), 320)
        ).lower()
        if not memory_text or not haystack:
            return 0
        score = 0
        for token in re.findall(r"[\u4e00-\u9fff]{2,8}|[a-z0-9_]{3,24}", haystack):
            if token and token in memory_text:
                score += 1
        return min(10, score)

    def _external_event_quality_score(self, payload: dict[str, Any]) -> int:
        score = 0
        if _single_line(payload.get("headline") or payload.get("topic") or payload.get("title"), 120):
            score += 2
        impression = _single_line(payload.get("impression") or payload.get("summary") or payload.get("note"), 320)
        if len(impression) >= 36:
            score += 2
        if _single_line(payload.get("selected_link") or payload.get("source_url") or payload.get("link"), 220):
            score += 1
        if _safe_float(payload.get("published_ts"), 0) > 0:
            score += 1
        if _safe_int(payload.get("score"), 0, 0, 10) >= max(1, _safe_int(getattr(self, "bilibili_share_min_score", 7), 7, 0, 10)):
            score += 2
        return min(10, score)

    def _external_event_share_decision(
        self,
        user: dict[str, Any],
        payload: dict[str, Any],
        *,
        source_type: str,
        wish: dict[str, Any] | None = None,
        base_probability: float = 0.2,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = _now_ts() if now is None else now
        relevance = _safe_int((wish or {}).get("relevance"), 0, 0, 10)
        desire = _safe_int((wish or {}).get("desire"), 0, 0, 10)
        user_match = self._external_event_user_interest_score(user, payload)
        quality = self._external_event_quality_score(payload)
        freshness = 6
        published_ts = _safe_float(payload.get("published_ts"), 0)
        if published_ts > 0:
            age_hours = max(0.0, (now - published_ts) / 3600.0)
            if age_hours <= 6:
                freshness = 10
            elif age_hours <= 24:
                freshness = 8
            elif age_hours <= 72:
                freshness = 5
            else:
                freshness = 2
        noisy = self._external_event_recently_seen(payload, source_type=source_type, now=now)
        duplicate_penalty = 4 if noisy else 0
        interrupt_penalty = 0
        if now - _safe_float(user.get("last_seen"), 0) < max(self.idle_minutes, 90) * 60:
            interrupt_penalty += 3
        if now - _safe_float(user.get("last_sent"), 0) < max(self.min_interval_minutes, 120) * 60:
            interrupt_penalty += 2
        total = relevance * 2 + desire * 2 + user_match * 2 + quality + freshness - duplicate_penalty - interrupt_penalty
        normalized = max(0.0, min(1.0, base_probability + total / 100.0))
        return {
            "score": max(0, min(100, total * 2)),
            "probability": normalized,
            "user_match": user_match,
            "quality": quality,
            "freshness": freshness,
            "duplicate_penalty": duplicate_penalty,
            "interrupt_penalty": interrupt_penalty,
            "should_share": bool((wish or {}).get("should_share")) and total >= 18 and not noisy,
            "duplicate": noisy,
        }

    def _bilibili_plugin_dir(self) -> Path:
        candidates = self._bilibili_ai_bot_plugin_dirs()
        for path in candidates:
            if self._is_bilibili_ai_bot_dir(path):
                return path
        return Path(self.data_dir).parent.parent / "plugins" / BILIBILI_AI_BOT_PLUGIN_NAME

    def _bilibili_plugin_roots(self) -> list[Path]:
        return [
            Path(__file__).resolve().parent.parent,
            Path(self.data_dir).parent.parent / "plugins",
        ]

    def _is_bilibili_ai_bot_dir(self, path: Path) -> bool:
        if not (path / "main.py").exists():
            return False
        if path.name == BILIBILI_AI_BOT_PLUGIN_NAME:
            return True
        meta = path / "metadata.yaml"
        if not meta.exists():
            return False
        try:
            text = meta.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return False
        return bool(
            re.search(r"(?m)^name:\s*astrbot_plugin_bilibili_ai_bot\b", text)
            or "github.com/chenluQwQ/astrbot_plugin_bilibili_ai_bot" in text
        )

    def _bilibili_ai_bot_plugin_dirs(self) -> list[Path]:
        # Startup-safe: do not glob all bilibili-like plugin folders here.
        # A broad scan can interact badly with AstrBot's plugin loading phase.
        found: list[Path] = []
        seen: set[str] = set()
        for root in self._bilibili_plugin_roots():
            for path in (root / BILIBILI_AI_BOT_PLUGIN_NAME,):
                key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                if self._is_bilibili_ai_bot_dir(path):
                    found.append(path)
        return found

    def _bilibili_ai_bot_package_names(self) -> list[str]:
        now = _now_ts()
        cache = getattr(self, "_bilibili_ai_bot_package_names_cache", None)
        if isinstance(cache, dict) and now - _safe_float(cache.get("ts"), 0) < 60:
            names = cache.get("names")
            if isinstance(names, list):
                return [str(name) for name in names if str(name or "").strip()]
        names = [BILIBILI_AI_BOT_PLUGIN_NAME]
        for path in self._bilibili_ai_bot_plugin_dirs():
            if path.name not in names:
                names.append(path.name)
        try:
            self._bilibili_ai_bot_package_names_cache = {"ts": now, "names": list(names)}
        except Exception:
            pass
        return names

    def _bilibili_ai_bot_data_dirs(self) -> list[Path]:
        candidates: list[Path] = []
        plugin_data_root = Path(self.data_dir).parent.parent / "plugin_data"
        for plugin_name in BILIBILI_AI_BOT_LEGACY_DATA_NAMES:
            candidates.append(plugin_data_root / plugin_name)
        deduped: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = str(path)
            if key not in seen:
                seen.add(key)
                deduped.append(path)
        return deduped

    def _bilibili_watch_log_file(self) -> Path:
        data_candidates = [path / "watch_log.json" for path in self._bilibili_ai_bot_data_dirs()]
        for path in data_candidates:
            if path.exists():
                return path
        return data_candidates[0] if data_candidates else self._bilibili_plugin_dir() / "watch_log.json"

    def _bilibili_available(self) -> bool:
        now = _now_ts()
        cache = getattr(self, "_bilibili_available_cache", None)
        if isinstance(cache, dict) and now - _safe_float(cache.get("ts"), 0) < 30:
            return bool(cache.get("available"))
        try:
            available = bool(self._bilibili_ai_bot_plugin_dirs()) or self._bilibili_watch_log_file().exists()
        except Exception:
            available = False
        try:
            self._bilibili_available_cache = {"ts": now, "available": bool(available)}
        except Exception:
            pass
        return bool(available)

    def _find_bilibili_bot_instance(self) -> Any | None:
        now = _now_ts()
        cache = getattr(self, "_bilibili_bot_instance_cache", None)
        if isinstance(cache, dict) and now - _safe_float(cache.get("ts"), 0) < 30:
            return cache.get("instance")
        names = tuple(self._bilibili_ai_bot_package_names())
        found = None
        try:
            getter = getattr(getattr(self, "context", None), "get_registered_star", None)
            if callable(getter):
                for name in names:
                    obj = getter(name)
                    if obj is not None and (
                        callable(getattr(obj, "_run_proactive", None)) or hasattr(obj, "memory_api")
                    ):
                        found = obj
                        break
        except Exception:
            pass
        if found is None and names:
            for obj in gc.get_objects():
                try:
                    cls = obj.__class__
                    module = str(getattr(cls, "__module__", ""))
                    if not any(name in module for name in names):
                        continue
                    if (callable(getattr(obj, "_run_proactive", None)) and hasattr(obj, "_proactive_task")) or hasattr(obj, "memory_api"):
                        found = obj
                        break
                except Exception:
                    continue
        try:
            self._bilibili_bot_instance_cache = {"ts": now, "instance": found}
        except Exception:
            pass
        return found

    def _find_bilibili_memory_api(self) -> Any | None:
        now = _now_ts()
        cache = getattr(self, "_bilibili_memory_api_object_cache", None)
        if isinstance(cache, dict) and now - _safe_float(cache.get("ts"), 0) < 30:
            return cache.get("api")
        bili = self._find_bilibili_bot_instance()
        api = getattr(bili, "memory_api", None) if bili is not None else None
        if api is not None and callable(getattr(api, "get_recent_memories", None)):
            try:
                self._bilibili_memory_api_object_cache = {"ts": now, "api": api}
            except Exception:
                pass
            return api
        names = tuple(self._bilibili_ai_bot_package_names())
        for obj in gc.get_objects():
            try:
                cls = obj.__class__
                module = str(getattr(cls, "__module__", ""))
                if not any(name in module for name in names):
                    continue
                api = getattr(obj, "memory_api", None)
                if api is not None and callable(getattr(api, "get_recent_memories", None)):
                    try:
                        self._bilibili_memory_api_object_cache = {"ts": now, "api": api}
                    except Exception:
                        pass
                    return api
            except Exception:
                continue
        try:
            self._bilibili_memory_api_object_cache = {"ts": now, "api": None}
        except Exception:
            pass
        return None

    def _bilibili_memory_api_available(self, *, allow_probe: bool = True, ttl_seconds: int = 60) -> bool:
        now = _now_ts()
        cache = getattr(self, "_bilibili_memory_api_cache", None)
        if isinstance(cache, dict) and now - _safe_float(cache.get("ts"), 0) < max(5, ttl_seconds):
            return bool(cache.get("available"))
        if not allow_probe:
            return False
        available = self._find_bilibili_memory_api() is not None
        try:
            self._bilibili_memory_api_cache = {"ts": now, "available": bool(available)}
        except Exception:
            pass
        return bool(available)

    def _find_bilibili_runtime_objects(self) -> list[Any]:
        now = _now_ts()
        cache = getattr(self, "_bilibili_runtime_objects_cache", None)
        if isinstance(cache, dict) and now - _safe_float(cache.get("ts"), 0) < 30:
            objects = cache.get("objects")
            if isinstance(objects, list):
                return list(objects)
        names = tuple(self._bilibili_ai_bot_package_names())
        found: list[Any] = []
        seen: set[int] = set()
        if names:
            for obj in gc.get_objects():
                try:
                    cls = obj.__class__
                    module = str(getattr(cls, "__module__", ""))
                    if not any(name in module for name in names):
                        continue
                    if id(obj) in seen:
                        continue
                    if (
                        callable(getattr(obj, "get_video_info", None))
                        or callable(getattr(getattr(obj, "bili_client", None), "get_video_info", None))
                        or callable(getattr(obj, "_http_get", None))
                    ):
                        seen.add(id(obj))
                        found.append(obj)
                except Exception:
                    continue
        try:
            self._bilibili_runtime_objects_cache = {"ts": now, "objects": list(found)}
        except Exception:
            pass
        return found

    def _load_bilibili_watch_log(self) -> list[dict[str, Any]]:
        try:
            path = self._bilibili_watch_log_file()
            if not path.exists():
                return []
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, list):
                return [item for item in raw if isinstance(item, dict)]
        except Exception as e:
            logger.debug(f"[PrivateCompanion] 读取 B 站观看日志失败: {e}")
        return []

    def _load_bilibili_recent_video_memories(self, *, hours: int = 72, limit: int = 12) -> list[dict[str, Any]]:
        api = self._find_bilibili_memory_api()
        if api is None:
            return []
        try:
            memories = api.get_recent_memories(
                source="bilibili",
                memory_types={"video"},
                hours=hours,
                limit=limit,
            )
            if isinstance(memories, list):
                return [item for item in memories if isinstance(item, dict)]
        except Exception as e:
            logger.debug(f"[PrivateCompanion] 读取 BiliBot 视频记忆失败: {e}")
        return []

    def _bilibili_memory_bvid(self, item: dict[str, Any]) -> str:
        bvid = _single_line(item.get("bvid") or item.get("video_bvid"), 40)
        if bvid:
            return bvid
        text = str(item.get("text") or "")
        match = re.search(r"\bBV[0-9A-Za-z]{8,16}\b", text)
        return _single_line(match.group(0), 40) if match else ""

    def _bilibili_memory_title(self, item: dict[str, Any]) -> str:
        title = _single_line(item.get("video_title") or item.get("title"), 80)
        if title:
            return title
        text = str(item.get("text") or "")
        match = re.search(r"《([^》]{1,80})》", text)
        return _single_line(match.group(1), 80) if match else ""

    def _bilibili_memory_context_for_bvid(self, bvid: str, *, limit: int = 3) -> list[str]:
        safe_bvid = _single_line(bvid, 40)
        if not safe_bvid:
            return []
        contexts: list[str] = []
        for item in self._load_bilibili_recent_video_memories(limit=18):
            if self._bilibili_memory_bvid(item) != safe_bvid:
                continue
            text = _single_line(item.get("text"), 180)
            if text and text not in contexts:
                contexts.append(text)
            if len(contexts) >= limit:
                break
        return contexts

    def _bilibili_video_candidate_from_memory(self) -> dict[str, Any] | None:
        for item in self._load_bilibili_recent_video_memories():
            bvid = self._bilibili_memory_bvid(item)
            title = self._bilibili_memory_title(item)
            if not bvid or not title:
                continue
            text = _single_line(item.get("text"), 220)
            return {
                "key": f"{bvid}:{_single_line(item.get('time'), 20)}:memory",
                "bvid": bvid,
                "title": title,
                "up_name": "",
                "score": self.bilibili_share_min_score,
                "mood": "",
                "comment": text,
                "review": text,
                "pic": "",
                "time": _single_line(item.get("time"), 24),
                "actions": [],
                "source": "memory_api",
                "memory_context": [text] if text else [],
            }
        return None

    def _latest_bilibili_video_candidate(self, *, include_memory_api: bool = True) -> dict[str, Any] | None:
        logs = self._load_bilibili_watch_log()
        if logs:
            for item in reversed(logs[-20:]):
                bvid = _single_line(item.get("bvid"), 32)
                title = _single_line(item.get("title"), 80)
                if not bvid or not title:
                    continue
                score = _safe_int(item.get("score"), 0, 0, 10)
                comment = _single_line(item.get("comment"), 120)
                review = _single_line(item.get("review"), 180)
                if score < self.bilibili_share_min_score:
                    continue
                return {
                    "key": f"{bvid}:{_single_line(item.get('time'), 20)}",
                    "bvid": bvid,
                    "title": title,
                    "up_name": _single_line(item.get("up_name"), 40),
                    "score": score,
                    "mood": _single_line(item.get("mood"), 24),
                    "comment": comment,
                    "review": review,
                    "pic": _single_line(item.get("pic"), 240),
                    "time": _single_line(item.get("time"), 24),
                    "actions": list(item.get("actions") or []) if isinstance(item.get("actions"), list) else [],
                    "source": "watch_log",
                    "memory_context": self._bilibili_memory_context_for_bvid(bvid) if include_memory_api else [],
                }
        if include_memory_api:
            return self._bilibili_video_candidate_from_memory()
        return None

    def _record_bilibili_share_to_memory(self, user_id: str, candidate: dict[str, Any]) -> None:
        api = self._find_bilibili_memory_api()
        if api is None or not callable(getattr(api, "record", None)):
            return
        bvid = _single_line(candidate.get("bvid"), 40)
        title = _single_line(candidate.get("title"), 80)
        if not bvid or not title:
            return
        async def _record() -> None:
            try:
                await api.record(
                    f"陪伴插件准备把视频《{title}》轻轻分享给 QQ 用户 {user_id}，链接 {bvid}",
                    user_id=str(user_id),
                    username="PrivateCompanion",
                    source="private_companion",
                    memory_type="video",
                    level="today",
                    importance=4,
                    extra={"bvid": bvid, "video_title": title},
                )
            except Exception as e:
                logger.debug(f"[PrivateCompanion] 写入 BiliBot 分享记忆失败: {e}")
        try:
            asyncio.create_task(_record())
        except RuntimeError:
            pass


    def _bot_currently_bored_enough_for_bilibili(self) -> bool:
        now_dt = datetime.now()
        if now_dt.hour < 10 or now_dt.hour >= 23:
            return False
        state = self.data.get("daily_state", {})
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        mood = _single_line(state.get("mood_bias") if isinstance(state, dict) else "", 24)
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        activity = _single_line((current_item or {}).get("activity"), 80)
        text = f"{mood} {activity}"
        boredom_tokens = ("无聊", "发呆", "摸鱼", "刷视频", "短视频", "休息", "闲", "空")
        if any(token in text for token in boredom_tokens):
            return True
        return 35 <= energy <= 72 and random.random() < 0.34

    async def _maybe_trigger_bilibili_boredom_watch(self) -> None:
        if not (self.enable_bilibili_integration and self.enable_bilibili_boredom_watch):
            return
        if not self._bilibili_available() or not self._bot_currently_bored_enough_for_bilibili():
            return
        state = self.data.setdefault("bilibili_integration", {})
        if not isinstance(state, dict):
            self.data["bilibili_integration"] = {}
            state = self.data["bilibili_integration"]
        now = _now_ts()
        min_interval = max(2, self.bilibili_boredom_min_interval_hours) * 3600
        if now - _safe_float(state.get("last_boredom_watch_at"), 0) < min_interval:
            return
        if now - _safe_float(state.get("last_boredom_watch_probe_at"), 0) < 30 * 60:
            return
        if random.random() > 0.38:
            return
        bili = self._find_bilibili_bot_instance()
        if bili is None:
            state["last_boredom_watch_probe_at"] = now
            self._save_data_sync()
            return
        task = getattr(bili, "_proactive_task", None)
        if task is not None and not task.done():
            return
        try:
            bili._proactive_task = asyncio.create_task(bili._run_proactive(max_watch=1))
            state["last_boredom_watch_at"] = now
            state["last_boredom_watch_status"] = "triggered"
            self._save_data_sync()
            logger.info("[PrivateCompanion] 已触发 B站 AI Bot 无聊刷视频联动")
        except Exception as e:
            state["last_boredom_watch_status"] = f"failed:{_single_line(str(e), 80)}"
            self._save_data_sync()
            logger.debug(f"[PrivateCompanion] 触发 B站 AI Bot 刷视频失败: {e}")

    def _maybe_schedule_bilibili_video_share(self) -> bool:
        if not self.enable_bilibili_integration:
            return False
        include_memory_api = bool(self._bilibili_memory_api_available(allow_probe=False))
        candidate = self._latest_bilibili_video_candidate(include_memory_api=include_memory_api)
        if not isinstance(candidate, dict):
            return False
        users = self.data.get("users")
        if not isinstance(users, dict):
            return False
        now = _now_ts()
        key = str(candidate.get("key") or candidate.get("bvid") or "")
        changed = False
        for user_id, user in users.items():
            if not isinstance(user, dict) or not self._is_target_private_user(str(user_id), user) or not user.get("enabled", True) or not user.get("umo"):
                continue
            if now - _safe_float(user.get("last_seen"), 0) < max(self.idle_minutes, 90) * 60:
                continue
            if str(user.get("last_bilibili_share_key") or "") == key:
                continue
            if now - _safe_float(user.get("last_bilibili_share_at"), 0) < 10 * 3600:
                continue
            timer_event = self._get_active_llm_timer(user)
            if (
                _safe_float(user.get("next_proactive_at"), 0) > 0
                and str(user.get("planned_proactive_source") or "") == "timer"
                and self._llm_timer_can_use_internal_scheduler(timer_event if isinstance(timer_event, dict) else None)
            ):
                continue
            score = _safe_int(candidate.get("score"), 0, 0, 10)
            decision = self._external_event_share_decision(
                user,
                candidate,
                source_type="bilibili",
                wish={
                    "relevance": score,
                    "desire": max(score - 1, 0),
                    "should_share": score >= self.bilibili_share_min_score,
                },
                base_probability=self.bilibili_share_probability,
                now=now,
            )
            if not decision.get("should_share"):
                continue
            chance = min(0.9, max(self.bilibili_share_probability, _safe_float(decision.get("probability"), self.bilibili_share_probability)))
            if random.random() > chance:
                continue
            delay_minutes = random.randint(12, 70)
            scheduled = now + delay_minutes * 60
            title = _single_line(candidate.get("title"), 70) or "刚刷到的视频"
            accepted = self._offer_proactive_candidate(
                str(user_id),
                user,
                {
                    "source": "bilibili",
                    "reason": "bili_video_share",
                    "action": "message",
                    "scheduled_ts": scheduled,
                    "topic": title,
                    "score": max(score, _safe_int(decision.get("score"), score, 0, 100)),
                    "motive": f"刚刷到 B 站视频《{title}》,觉得有一点能分享给 {user_id},但只轻轻提一句",
                    "context_key": "bilibili_video_context",
                    "context": {**candidate, "created_ts": now, "share_decision": decision},
                },
            )
            if not accepted:
                continue
            self._record_bilibili_share_to_memory(str(user_id), candidate)
            user["last_bilibili_share_key"] = key
            user["last_bilibili_share_at"] = now
            self._remember_external_event(candidate, source_type="bilibili", reason="bili_video_share")
            changed = True
        return changed

    def _news_source_items(self) -> list[dict[str, str]]:
        raw = str(getattr(self, "news_sources", "") or "")
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        for line in self._split_news_source_lines(raw):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                name, url = line.split("|", 1)
            else:
                name, url = "", line
            url = url.strip()
            source_type = "rss"
            mid = ""
            bvid = ""
            if url.lower().startswith("bilibili:"):
                mid = re.sub(r"\D+", "", url.split(":", 1)[1])
                source_type = "bilibili"
            elif url.lower().startswith("bvid:"):
                match = re.search(r"(BV[0-9A-Za-z]+)", url, flags=re.I)
                bvid = match.group(1) if match else ""
                source_type = "bilibili_video"
            elif re.fullmatch(r"\d{4,}", url):
                mid = url
                source_type = "bilibili"
            elif url.startswith(("http://", "https://")):
                parsed = urlparse(url)
                if parsed.netloc.endswith("bilibili.com") and parsed.path.startswith("/video/"):
                    match = re.search(r"/video/(BV[0-9A-Za-z]+)", parsed.path, flags=re.I)
                    if match:
                        bvid = match.group(1)
                        source_type = "bilibili_video"
                if "space.bilibili.com" in parsed.netloc or parsed.path.startswith("/space.bilibili.com"):
                    match = re.search(r"/(\d+)", parsed.path)
                    if match:
                        mid = match.group(1)
                        source_type = "bilibili"
            if source_type == "bilibili_video":
                if not bvid:
                    continue
                key = f"bilibili_video:{bvid}"
                if key in seen:
                    continue
                seen.add(key)
                items.append(
                    {
                        "name": _single_line(name, 40) or f"B站视频 {bvid}",
                        "url": f"https://www.bilibili.com/video/{bvid}",
                        "type": "bilibili_video",
                        "bvid": bvid,
                    }
                )
                if len(items) >= 12:
                    break
                continue
            if source_type == "bilibili":
                if not mid:
                    continue
                key = f"bilibili:{mid}"
                if key in seen:
                    continue
                seen.add(key)
                items.append(
                    {
                        "name": _single_line(name, 40) or f"B站 UP {mid}",
                        "url": f"https://space.bilibili.com/{mid}",
                        "type": "bilibili",
                        "mid": mid,
                    }
                )
                if len(items) >= 12:
                    break
                continue
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            items.append({"name": _single_line(name, 40) or _single_line(url, 40), "url": url, "type": "rss"})
            if len(items) >= 12:
                break
        return items

    @staticmethod
    def _split_news_source_lines(raw: str) -> list[str]:
        text = str(raw or "").strip()
        if not text:
            return []
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) != 1:
            return lines
        line = lines[0]
        markers = list(re.finditer(r"(?:^|\s+)(#?\s*[^|\n]+?)\|(?=(?:https?://|bilibili:|bvid:))", line, flags=re.I))
        if len(markers) <= 1:
            return lines
        recovered: list[str] = []
        for index, match in enumerate(markers):
            start = match.end()
            end = markers[index + 1].start() if index + 1 < len(markers) else len(line)
            name = str(match.group(1) or "").strip()
            target = line[start:end].strip()
            if name and target:
                recovered.append(f"{name}|{target}")
        return recovered or lines

    @staticmethod
    def _news_xml_text(node: Any, *paths: str) -> str:
        if node is None:
            return ""
        for path in paths:
            found = node.find(path)
            if found is not None and found.text:
                text = re.sub(r"<[^>]+>", "", html.unescape(str(found.text)))
                text = re.sub(r"\s+", " ", text).strip()
                if text:
                    return text
        return ""

    @staticmethod
    def _news_parse_time(value: str) -> float:
        text = str(value or "").strip()
        if not text:
            return 0.0
        try:
            return parsedate_to_datetime(text).timestamp()
        except Exception:
            pass
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    def _news_item_key(self, item: dict[str, Any]) -> str:
        raw = "|".join(
            _single_line(item.get(key), 240)
            for key in ("link", "title", "source")
            if _single_line(item.get(key), 240)
        )
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16] if raw else ""

    @staticmethod
    def _bilibili_bvid_from_url(value: Any) -> str:
        text = str(value or "")
        match = re.search(r"(BV[0-9A-Za-z]+)", text)
        return match.group(1) if match else ""

    @staticmethod
    def _ai_daily_time_minutes(value: Any) -> int | None:
        text = str(value or "").strip().replace("：", ":")
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
        if not match:
            return None
        hour, minute = [int(part) for part in match.groups()]
        if not 0 <= minute <= 59:
            return None
        return (hour % 24) * 60 + minute

    def _ai_daily_source_key(self, source: dict[str, Any]) -> str:
        mid = re.sub(r"\D+", "", str(source.get("mid") or ""))
        if mid:
            return f"bilibili:{mid}"
        name = _single_line(source.get("name"), 40)
        return hashlib.sha1(name.encode("utf-8", errors="ignore")).hexdigest()[:12] if name else "unknown"

    def _ai_daily_source_items(self) -> list[dict[str, Any]]:
        raw = str(getattr(self, "ai_daily_sources", "") or "").strip() or DEFAULT_AI_DAILY_SOURCES
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 3:
                continue
            name = _single_line(parts[0], 40) or "AI日报"
            author = _single_line(parts[1], 60) or name
            mid = re.sub(r"\D+", "", parts[2])
            if not mid:
                continue
            keywords = [token for token in re.split(r"[\s,，、/|]+", parts[3] if len(parts) >= 4 else "") if token]
            if not keywords:
                keywords = ["日报"] if "日报" in name else ["早报"]
            schedule = _single_line(parts[4] if len(parts) >= 5 else "", 10) or ("23:00" if "日报" in name else "12:00")
            schedule_minutes = self._ai_daily_time_minutes(schedule)
            if schedule_minutes is None:
                schedule = "23:00" if "日报" in name else "12:00"
                schedule_minutes = self._ai_daily_time_minutes(schedule) or 0
            item = {
                "name": name,
                "author_name": author,
                "type": "bilibili",
                "mid": mid,
                "url": f"https://space.bilibili.com/{mid}",
                "keywords": keywords[:6],
                "schedule": schedule,
                "schedule_minutes": schedule_minutes,
            }
            key = self._ai_daily_source_key(item)
            if key in seen:
                continue
            seen.add(key)
            item["key"] = key
            items.append(item)
            if len(items) >= 8:
                break
        if items:
            return items
        legacy_mid = re.sub(r"\D+", "", str(getattr(self, "ai_daily_source_uid", "") or DEFAULT_AI_DAILY_JUYA_UID)) or DEFAULT_AI_DAILY_JUYA_UID
        return [
            {
                "name": "AI日报",
                "author_name": "橘鸦Juya",
                "type": "bilibili",
                "mid": legacy_mid,
                "url": f"https://space.bilibili.com/{legacy_mid}",
                "keywords": ["日报", "早报"],
                "schedule": "23:00",
                "schedule_minutes": 23 * 60,
                "key": f"bilibili:{legacy_mid}",
            }
        ]

    def _ai_daily_date_tokens(self, now_dt: datetime | None = None) -> list[str]:
        now_dt = now_dt or datetime.now()
        tokens = [
            now_dt.strftime("%Y-%m-%d"),
            now_dt.strftime("%Y/%m/%d"),
            now_dt.strftime("%Y年%m月%d日").replace("年0", "年").replace("月0", "月"),
            f"{now_dt.month}月{now_dt.day}日",
            f"{now_dt.month}.{now_dt.day}",
            f"{now_dt.month}-{now_dt.day}",
            f"{now_dt.month}/{now_dt.day}",
            f"{now_dt.month:02d}{now_dt.day:02d}",
            f"{now_dt.month}{now_dt.day:02d}",
        ]
        return list(dict.fromkeys(token for token in tokens if token))

    def _ai_daily_item_matches_source(self, item: dict[str, Any], source: dict[str, Any], today: str) -> bool:
        if not self._news_item_is_today(item, today):
            return False
        mid = re.sub(r"\D+", "", str(source.get("mid") or ""))
        owner_mid = re.sub(r"\D+", "", str(item.get("video_owner_mid") or ""))
        if mid and owner_mid and mid != owner_mid:
            return False
        if mid and owner_mid == mid:
            return True
        text = f"{item.get('title') or ''} {item.get('summary') or ''} {item.get('video_owner_name') or ''}"
        keywords = [str(token) for token in source.get("keywords") or [] if str(token).strip()]
        if keywords and not any(token in text for token in keywords):
            return False
        return True

    def _ai_daily_due_sources(self, now_dt: datetime, today: str, source_states: dict[str, Any], *, force: bool = False) -> list[dict[str, Any]]:
        now_minute = now_dt.hour * 60 + now_dt.minute
        due: list[dict[str, Any]] = []
        for source in self._ai_daily_source_items():
            key = str(source.get("key") or self._ai_daily_source_key(source))
            source["key"] = key
            state = source_states.get(key) if isinstance(source_states.get(key), dict) else {}
            if not force and state.get("last_success_date") == today:
                continue
            schedule_minutes = _safe_int(source.get("schedule_minutes"), 0, 0)
            if not force and now_minute < schedule_minutes:
                continue
            if not force and state.get("last_attempt_date") == today:
                continue
            due.append(source)
        due.sort(key=lambda item: _safe_int(item.get("schedule_minutes"), 0, 0))
        return due

    async def _fetch_bilibili_video_search_api_fallback(self, source: dict[str, str]) -> list[dict[str, Any]]:
        source_name = _single_line(source.get("name"), 40) or "B站 AI早报"
        mid = re.sub(r"\D+", "", str(source.get("mid") or ""))
        now_dt = datetime.now()
        today = now_dt.strftime("%Y-%m-%d")
        date_tokens = self._ai_daily_date_tokens(now_dt)
        author = _single_line(source.get("author_name"), 60) or source_name
        keywords = [str(token) for token in source.get("keywords") or [] if str(token).strip()] or ["早报", "日报"]
        queries = []
        for keyword in keywords[:4]:
            queries.extend(
                [
                    f"{author} AI {keyword} {today}",
                    f"{author} AI{keyword} {date_tokens[2]}",
                    f"{author} AI{keyword}{date_tokens[-2]}",
                ]
            )
        queries = list(dict.fromkeys(queries))
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=15)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
                "Referer": "https://search.bilibili.com/",
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate",
            }
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                for query in queries:
                    if len(items) >= max(1, self.news_max_items_per_source):
                        break
                    url = f"https://api.bilibili.com/x/web-interface/search/all/v2?keyword={quote(query)}"
                    try:
                        async with session.get(url) as resp:
                            if resp.status >= 400:
                                continue
                            payload = await resp.json(content_type=None)
                    except Exception:
                        continue
                    if not isinstance(payload, dict) or int(payload.get("code") or 0) != 0:
                        continue
                    groups = (payload.get("data") or {}).get("result") if isinstance(payload.get("data"), dict) else []
                    for group in groups if isinstance(groups, list) else []:
                        if not isinstance(group, dict) or group.get("result_type") != "video":
                            continue
                        for raw in group.get("data") if isinstance(group.get("data"), list) else []:
                            if not isinstance(raw, dict):
                                continue
                            if mid and str(raw.get("mid") or "") != mid:
                                continue
                            raw_title = re.sub(r"<[^>]+>", "", html.unescape(str(raw.get("title") or ""))).strip()
                            desc = re.sub(r"<[^>]+>", "", html.unescape(str(raw.get("description") or ""))).strip()
                            haystack = f"{raw_title} {desc}"
                            if "AI" not in haystack.upper() and author not in haystack:
                                continue
                            if not mid and keywords and not any(token in haystack for token in keywords):
                                continue
                            pubdate = _safe_float(raw.get("pubdate"), 0)
                            pubdate_is_today = False
                            if pubdate > 0:
                                try:
                                    pubdate_is_today = datetime.fromtimestamp(pubdate).strftime("%Y-%m-%d") == today
                                except Exception:
                                    pubdate_is_today = False
                            if not pubdate_is_today and not any(token in haystack for token in date_tokens):
                                continue
                            bvid = _single_line(raw.get("bvid") or self._bilibili_bvid_from_url(raw.get("arcurl")), 40)
                            if not bvid or bvid in seen:
                                continue
                            seen.add(bvid)
                            item = await self._bilibili_news_item_from_video(
                                source_name=source_name,
                                title=raw_title,
                                desc=desc,
                                bvid=bvid,
                                created=raw.get("pubdate"),
                            )
                            if item:
                                item["bilibili_integration_source"] = "search_api"
                                items.append(item)
                            if len(items) >= max(1, self.news_max_items_per_source):
                                break
                        if len(items) >= max(1, self.news_max_items_per_source):
                            break
        except Exception as exc:
            logger.debug("[PrivateCompanion] B站搜索 API 兜底失败: %s", exc)
        items.sort(key=lambda item: (_safe_float(item.get("published_ts"), 0), _safe_float(item.get("fetched_ts"), 0)), reverse=True)
        return items[: max(1, self.news_max_items_per_source)]

    async def _fetch_bilibili_news_search_fallback(self, source: dict[str, str]) -> list[dict[str, Any]]:
        source_name = _single_line(source.get("name"), 40) or "B站 AI早报"
        mid = re.sub(r"\D+", "", str(source.get("mid") or ""))
        now_dt = datetime.now()
        today = now_dt.strftime("%Y-%m-%d")
        date_tokens = self._ai_daily_date_tokens(now_dt)
        author = _single_line(source.get("author_name"), 60) or source_name
        keywords = [str(token) for token in source.get("keywords") or [] if str(token).strip()] or ["早报", "日报"]
        queries: list[str] = []
        for keyword in keywords[:4]:
            compact_keyword = f"AI{keyword}"
            queries.extend(
                [
                    f"AI {keyword} {today}",
                    f"{compact_keyword} {today}",
                    f"AI {keyword} {date_tokens[2]}",
                    f"{compact_keyword} {date_tokens[2]}",
                    f"site:mp.weixin.qq.com/s AI {keyword} {today}",
                    f"site:mp.weixin.qq.com/s {compact_keyword} {today}",
                    f"site:bilibili.com/video AI {keyword} {today}",
                    f"site:bilibili.com/video {compact_keyword} {today}",
                ]
            )
        queries = list(dict.fromkeys(queries))
        api_items = await self._fetch_bilibili_video_search_api_fallback(source)
        if api_items:
            return api_items
        umo = self._pick_available_web_search_umo()
        if not umo and not self._astrbot_web_search_available():
            return []
        items: list[dict[str, Any]] = []
        seen_links: set[str] = set()
        for query in queries:
            if len(items) >= max(1, self.news_max_items_per_source):
                break
            results = await self._run_astrbot_web_search(query, umo=umo, topic="news")
            for result in results[: max(2, self.news_max_items_per_source)]:
                title = _single_line(result.get("title"), 160)
                link = _single_line(result.get("url"), 400)
                snippet = _single_line(result.get("snippet"), 360)
                haystack = f"{title} {snippet}"
                if not title or ("AI" not in haystack.upper() and author not in haystack):
                    continue
                if not mid and keywords and not any(token in haystack for token in keywords):
                    continue
                article_match = re.search(r"https?://mp\.weixin\.qq\.com/s/[^\s<>\]）)\"']+", f"{link} {snippet}")
                article_link = _single_line(article_match.group(0), 400) if article_match else ""
                if "mp.weixin.qq.com/s/" in link:
                    article_link = link
                if not article_link and "space.bilibili.com" in link:
                    continue
                video_bvid = self._bilibili_bvid_from_url(link)
                if not article_link and video_bvid:
                    item = await self._bilibili_news_item_from_video(
                        source_name=source_name,
                        title=title,
                        desc=snippet,
                        bvid=video_bvid,
                    )
                    if item:
                        item["bilibili_integration_source"] = "web_search_video"
                        if str(item.get("link") or "") not in seen_links:
                            seen_links.add(str(item.get("link") or ""))
                            items.append(item)
                    if len(items) >= max(1, self.news_max_items_per_source):
                        break
                    continue
                final_link = article_link or link
                if final_link in seen_links:
                    continue
                seen_links.add(final_link)
                article_payload = await self._fetch_news_article_excerpt(article_link) if article_link else {}
                article_text = str(article_payload.get("text") or "").strip()
                article_excerpt = str(article_payload.get("excerpt") or "").strip()
                article_title = _single_line(article_payload.get("title"), 120)
                summary = article_excerpt if article_excerpt else snippet
                media_type = "bilibili_search_article" if article_text else "bilibili_search_result"
                published_hint = today if any(token in haystack for token in date_tokens) else ""
                item = {
                    "source": source_name,
                    "title": article_title or title,
                    "link": final_link,
                    "summary": summary,
                    "published": published_hint,
                    "published_ts": 0,
                    "fetched_ts": _now_ts(),
                    "media_type": media_type,
                    "search_query": query,
                    "article_link": article_link,
                    "article_title": article_title,
                    "article_text": article_text,
                    "article_excerpt": article_excerpt,
                    "article_readable": bool(article_text),
                }
                item["key"] = self._news_item_key(item)
                if item["key"]:
                    items.append(item)
                if len(items) >= max(1, self.news_max_items_per_source):
                    break
        if items:
            items.sort(
                key=lambda item: (
                    1 if str(item.get("published") or "") == today else 0,
                    1 if item.get("article_readable") else 0,
                    _safe_float(item.get("fetched_ts"), 0),
                ),
                reverse=True,
            )
            return items[: max(1, self.news_max_items_per_source)]
        query = queries[0]
        results = await self._run_astrbot_web_search(query, umo=umo, topic="news")
        for result in results[: max(1, self.news_max_items_per_source)]:
            title = _single_line(result.get("title"), 160)
            link = _single_line(result.get("url"), 400)
            snippet = _single_line(result.get("snippet"), 320)
            haystack = f"{title} {snippet}"
            if not title or ("AI" not in haystack.upper() and author not in haystack):
                continue
            if keywords and not any(token in haystack for token in keywords):
                continue
            item = {
                "source": source_name,
                "title": title,
                "link": link,
                "summary": snippet,
                "published": "",
                "published_ts": 0,
                "fetched_ts": _now_ts(),
                "media_type": "bilibili_search_result",
                "search_query": query,
            }
            item["key"] = self._news_item_key(item)
            if item["key"]:
                items.append(item)
        return items

    async def _fetch_news_article_excerpt(self, url: str) -> dict[str, str]:
        safe_url = _single_line(url, 500)
        if not safe_url.startswith(("http://", "https://")):
            return {}
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=15)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://mp.weixin.qq.com/",
            }
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(safe_url, allow_redirects=True) as resp:
                    if resp.status >= 400:
                        return {}
                    raw_bytes = await resp.read()
                    content_type = resp.headers.get("Content-Type", "")
                    if _news_response_looks_binary(raw_bytes, content_type=content_type):
                        logger.debug(
                            "[PrivateCompanion] 新闻文字版跳过非文本响应 %s content-type=%s",
                            _single_line(safe_url, 120),
                            _single_line(content_type, 80),
                        )
                        return {}
                    raw = _decode_news_response_text(
                        raw_bytes,
                        content_type=content_type,
                        declared_charset=resp.charset or "",
                    )
                    if not raw.strip():
                        return {}
        except Exception as exc:
            logger.debug("[PrivateCompanion] 新闻文字版抓取失败 %s: %s", _single_line(safe_url, 120), exc)
            return {}

        text = html.unescape(raw or "")
        title = ""
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
        if title_match:
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", title_match.group(1))).strip()
        for pattern in (
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)["\']',
        ):
            match = re.search(pattern, text, flags=re.I | re.S)
            if match:
                title = match.group(1).strip()
                break
        if _text_looks_garbled(title):
            title = ""

        body = text
        article_match = re.search(r'<div[^>]+id=["\']js_content["\'][^>]*>(.*?)</div>\s*</div>\s*</div>', text, flags=re.I | re.S)
        if not article_match:
            article_match = re.search(r'<article[^>]*>(.*?)</article>', text, flags=re.I | re.S)
        if article_match:
            body = article_match.group(1)
        body = re.sub(r"<script\b[^>]*>.*?</script>", " ", body, flags=re.I | re.S)
        body = re.sub(r"<style\b[^>]*>.*?</style>", " ", body, flags=re.I | re.S)
        body = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", body, flags=re.I)
        body = re.sub(r"<[^>]+>", " ", body)
        body = html.unescape(body)
        body = re.sub(r"\u00a0", " ", body)
        lines = [re.sub(r"\s+", " ", line).strip() for line in body.splitlines()]
        lines = [
            line for line in lines
            if len(line) >= 8
            and not re.search(r"^(赞|在看|分享|微信扫一扫|继续滑动看下一个|向上滑动看下一个|广告)$", line)
        ]
        article_text = "\n".join(lines)
        article_text = re.sub(r"\n{3,}", "\n\n", article_text).strip()
        if len(article_text) < 80 or _text_looks_garbled(article_text[:600]):
            return {}
        return {
            "title": _single_line(title, 120),
            "text": article_text,
            "excerpt": article_text[:2400],
        }

    async def _fetch_bilibili_video_subtitle_text(self, bvid: str, cid: Any = 0) -> dict[str, Any]:
        safe_bvid = _single_line(bvid, 40)
        if not safe_bvid:
            return {}
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=15)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
                "Referer": f"https://www.bilibili.com/video/{safe_bvid}",
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate",
            }
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                safe_cid = _safe_int(cid, 0, 0)
                if safe_cid <= 0:
                    async with session.get(f"https://api.bilibili.com/x/web-interface/view?bvid={safe_bvid}") as resp:
                        if resp.status < 400:
                            payload = await resp.json(content_type=None)
                            data = payload.get("data") if isinstance(payload, dict) else {}
                            if isinstance(data, dict):
                                safe_cid = _safe_int(data.get("cid"), 0, 0)
                if safe_cid <= 0:
                    return {}
                async with session.get(f"https://api.bilibili.com/x/player/v2?bvid={safe_bvid}&cid={safe_cid}") as resp:
                    if resp.status >= 400:
                        return {}
                    payload = await resp.json(content_type=None)
                data = payload.get("data") if isinstance(payload, dict) else {}
                subtitle = data.get("subtitle") if isinstance(data, dict) and isinstance(data.get("subtitle"), dict) else {}
                subtitles = subtitle.get("subtitles") if isinstance(subtitle.get("subtitles"), list) else []
                if not subtitles:
                    return {"cid": safe_cid, "available": False, "count": 0}
                subtitle_item = next((item for item in subtitles if isinstance(item, dict) and str(item.get("lan") or "").lower().startswith("zh")), None)
                if not subtitle_item:
                    subtitle_item = next((item for item in subtitles if isinstance(item, dict)), None)
                if not isinstance(subtitle_item, dict):
                    return {"cid": safe_cid, "available": False, "count": len(subtitles)}
                subtitle_url = _single_line(subtitle_item.get("subtitle_url") or subtitle_item.get("url"), 500)
                if subtitle_url.startswith("//"):
                    subtitle_url = f"https:{subtitle_url}"
                if not subtitle_url.startswith(("http://", "https://")):
                    return {"cid": safe_cid, "available": False, "count": len(subtitles)}
                async with session.get(subtitle_url) as resp:
                    if resp.status >= 400:
                        return {"cid": safe_cid, "available": False, "count": len(subtitles), "subtitle_url": subtitle_url}
                    subtitle_payload = await resp.json(content_type=None)
        except Exception as exc:
            logger.debug("[PrivateCompanion] B站字幕读取失败 bvid=%s: %s", safe_bvid, exc)
            return {}

        body = subtitle_payload.get("body") if isinstance(subtitle_payload, dict) and isinstance(subtitle_payload.get("body"), list) else []
        lines = []
        for segment in body:
            if not isinstance(segment, dict):
                continue
            text = re.sub(r"\s+", " ", str(segment.get("content") or "")).strip()
            if text:
                lines.append(text)
        subtitle_text = "\n".join(lines).strip()
        subtitle_text = re.sub(r"\n{3,}", "\n\n", subtitle_text)
        if len(subtitle_text) < 80:
            return {"cid": safe_cid, "available": bool(subtitles), "count": len(subtitles), "subtitle_url": subtitle_url, "chars": len(subtitle_text)}
        return {
            "cid": safe_cid,
            "available": True,
            "count": len(subtitles),
            "subtitle_url": subtitle_url,
            "language": _single_line(subtitle_item.get("lan_doc") or subtitle_item.get("lan"), 40),
            "text": subtitle_text,
            "chars": len(subtitle_text),
        }

    @staticmethod
    def _format_bilibili_duration(seconds: Any) -> str:
        total = _safe_int(seconds, 0, 0)
        if total <= 0:
            return ""
        hours = total // 3600
        minutes = (total % 3600) // 60
        secs = total % 60
        if hours:
            return f"{hours}小时{minutes}分{secs}秒"
        return f"{minutes}分{secs}秒"

    async def _fetch_bilibili_video_public_context(self, bvid: str, cid: Any = 0) -> dict[str, Any]:
        """Lightweight video context inspired by BiliBot: metadata, tags, hot comments."""
        safe_bvid = _single_line(bvid, 40)
        if not safe_bvid:
            return {}

        context: dict[str, Any] = {"bvid": safe_bvid}
        for obj in self._find_bilibili_runtime_objects() if getattr(self, "enable_bilibili_integration", False) else []:
            try:
                oid = 0
                get_oid = getattr(obj, "_get_video_oid", None)
                if callable(get_oid):
                    payload = get_oid(safe_bvid)
                    if hasattr(payload, "__await__"):
                        payload = await payload
                    oid = _safe_int(payload, 0, 0)
                get_info = getattr(obj, "_get_video_info", None)
                if callable(get_info) and oid > 0:
                    payload = get_info(oid)
                    if hasattr(payload, "__await__"):
                        payload = await payload
                    if isinstance(payload, dict):
                        context.update(self._normalize_bilibili_video_payload({"data": payload}, safe_bvid))
                        context["aid"] = oid
                get_tags = getattr(obj, "_get_video_tags", None)
                if callable(get_tags):
                    payload = get_tags(safe_bvid)
                    if hasattr(payload, "__await__"):
                        payload = await payload
                    if isinstance(payload, list):
                        context["tags"] = [_single_line(tag, 40) for tag in payload if _single_line(tag, 40)][:10]
                get_comments = getattr(obj, "_get_hot_comments", None)
                if callable(get_comments) and oid > 0:
                    payload = get_comments(oid, limit=6)
                    if hasattr(payload, "__await__"):
                        payload = await payload
                    if isinstance(payload, list):
                        context["hot_comments"] = [_single_line(comment, 120) for comment in payload if _single_line(comment, 120)][:6]
                if context.get("title") or context.get("tags") or context.get("hot_comments"):
                    break
            except Exception as exc:
                logger.debug("[PrivateCompanion] BiliBot 视频上下文联动失败 bvid=%s: %s", safe_bvid, exc)

        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=15)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
                "Referer": f"https://www.bilibili.com/video/{safe_bvid}",
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate",
            }
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                if not context.get("title") or not context.get("aid") or not context.get("cid"):
                    async with session.get("https://api.bilibili.com/x/web-interface/view", params={"bvid": safe_bvid}) as resp:
                        if resp.status < 400:
                            payload = await resp.json(content_type=None)
                            if isinstance(payload, dict) and int(payload.get("code") or 0) == 0:
                                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                                context.update(self._normalize_bilibili_video_payload(payload, safe_bvid))
                                context["aid"] = _safe_int(data.get("aid"), _safe_int(context.get("aid"), 0, 0), 0)
                                context["duration"] = _safe_int(data.get("duration"), _safe_int(context.get("duration"), 0, 0), 0)
                                stat = data.get("stat") if isinstance(data.get("stat"), dict) else {}
                                context["stat"] = {
                                    "view": _safe_int(stat.get("view"), 0, 0),
                                    "danmaku": _safe_int(stat.get("danmaku"), 0, 0),
                                    "reply": _safe_int(stat.get("reply"), 0, 0),
                                    "like": _safe_int(stat.get("like"), 0, 0),
                                }
                if not context.get("tags"):
                    async with session.get("https://api.bilibili.com/x/tag/archive/tags", params={"bvid": safe_bvid}) as resp:
                        if resp.status < 400:
                            payload = await resp.json(content_type=None)
                            if isinstance(payload, dict) and int(payload.get("code") or 0) == 0:
                                tags = payload.get("data") if isinstance(payload.get("data"), list) else []
                                context["tags"] = [
                                    _single_line(tag.get("tag_name"), 40)
                                    for tag in tags
                                    if isinstance(tag, dict) and _single_line(tag.get("tag_name"), 40)
                                ][:10]
                aid = _safe_int(context.get("aid"), 0, 0)
                if aid > 0 and not context.get("hot_comments"):
                    async with session.get(
                        "https://api.bilibili.com/x/v2/reply/main",
                        params={"oid": aid, "type": 1, "mode": 3, "ps": 6},
                    ) as resp:
                        if resp.status < 400:
                            payload = await resp.json(content_type=None)
                            if isinstance(payload, dict) and int(payload.get("code") or 0) == 0:
                                replies = ((payload.get("data") or {}) if isinstance(payload.get("data"), dict) else {}).get("replies")
                                if isinstance(replies, list):
                                    comments: list[str] = []
                                    for reply in replies:
                                        if not isinstance(reply, dict):
                                            continue
                                        content = reply.get("content") if isinstance(reply.get("content"), dict) else {}
                                        message = _single_line(content.get("message"), 120)
                                        if message:
                                            comments.append(message)
                                    context["hot_comments"] = comments[:6]
        except Exception as exc:
            logger.debug("[PrivateCompanion] B站视频公开上下文抓取失败 bvid=%s: %s", safe_bvid, exc)

        parts: list[str] = []
        owner = _single_line(context.get("owner_name"), 60)
        tname = _single_line(context.get("tname"), 40)
        duration = self._format_bilibili_duration(context.get("duration"))
        if owner:
            parts.append(f"UP主：{owner}")
        if tname:
            parts.append(f"分区：{tname}")
        if duration:
            parts.append(f"时长：{duration}")
        desc = _single_line(context.get("desc"), 360)
        if desc:
            parts.append(f"简介：{desc}")
        tags = context.get("tags") if isinstance(context.get("tags"), list) else []
        tags = [_single_line(tag, 40) for tag in tags if _single_line(tag, 40)]
        if tags:
            parts.append("标签：" + "、".join(tags[:10]))
        comments = context.get("hot_comments") if isinstance(context.get("hot_comments"), list) else []
        comments = [_single_line(comment, 120) for comment in comments if _single_line(comment, 120)]
        if comments:
            parts.append("热门评论：" + " / ".join(comments[:5]))
        context["context_text"] = "\n".join(parts).strip()
        context["context_chars"] = len(context["context_text"])
        return context

    async def _bilibili_news_item_from_video(
        self,
        *,
        source_name: str,
        title: Any,
        desc: Any,
        bvid: Any,
        created: Any = 0,
        cid: Any = 0,
    ) -> dict[str, Any]:
        safe_title = _single_line(title, 160)
        safe_bvid = _single_line(bvid, 40)
        if not safe_title or not safe_bvid:
            return {}
        desc_text = str(desc or "")
        article_match = re.search(r"https?://[^\s<>\]）)\"']+", desc_text)
        article_link = _single_line(article_match.group(0), 400) if article_match else ""
        video_link = f"https://www.bilibili.com/video/{safe_bvid}"
        article_payload = await self._fetch_news_article_excerpt(article_link) if article_link else {}
        article_text = str(article_payload.get("text") or "").strip()
        article_excerpt = str(article_payload.get("excerpt") or "").strip()
        article_title = _single_line(article_payload.get("title"), 120)
        subtitle_payload = {} if article_text else await self._fetch_bilibili_video_subtitle_text(safe_bvid, cid)
        subtitle_text = str(subtitle_payload.get("text") or "").strip()
        subtitle_excerpt = subtitle_text[:2400]
        video_context = await self._fetch_bilibili_video_public_context(
            safe_bvid,
            cid or subtitle_payload.get("cid"),
        )
        context_text = str(video_context.get("context_text") or "").strip()
        context_excerpt = context_text[:1600]
        published_ts = _safe_float(created, 0)
        if published_ts <= 0:
            published_ts = _safe_float(video_context.get("created"), 0)
        normalized_desc = desc_text or str(video_context.get("desc") or "")
        summary = article_excerpt or subtitle_excerpt or _single_line(normalized_desc, 320) or _single_line(context_text, 320)
        item = {
            "source": source_name,
            "title": article_title or _single_line(video_context.get("title"), 160) or safe_title,
            "link": article_link or video_link,
            "summary": summary,
            "published": datetime.fromtimestamp(published_ts).isoformat() if published_ts else "",
            "published_ts": published_ts,
            "fetched_ts": _now_ts(),
            "media_type": "bilibili_video",
            "video_link": video_link,
            "video_cid": _safe_int(cid, 0, 0) or _safe_int(subtitle_payload.get("cid"), 0, 0),
            "video_aid": _safe_int(video_context.get("aid"), 0, 0),
            "video_owner_name": _single_line(video_context.get("owner_name"), 80),
            "video_owner_mid": _single_line(video_context.get("owner_mid"), 40),
            "video_tname": _single_line(video_context.get("tname"), 60),
            "video_duration": _safe_int(video_context.get("duration"), 0, 0),
            "video_pic": _single_line(video_context.get("pic"), 300),
            "video_tags": video_context.get("tags") if isinstance(video_context.get("tags"), list) else [],
            "video_hot_comments": video_context.get("hot_comments") if isinstance(video_context.get("hot_comments"), list) else [],
            "video_context_text": context_text,
            "video_context_excerpt": context_excerpt,
            "video_context_chars": len(context_text),
            "video_subtitle_text": subtitle_text,
            "video_subtitle_excerpt": subtitle_excerpt,
            "video_subtitle_readable": bool(subtitle_text),
            "video_subtitle_chars": len(subtitle_text),
            "video_subtitle_status": "read" if subtitle_text else ("missing" if subtitle_payload else "unavailable"),
            "article_link": article_link,
            "article_title": article_title,
            "article_text": article_text,
            "article_excerpt": article_excerpt,
            "article_readable": bool(article_text),
        }
        item["key"] = self._news_item_key(item)
        return item if item["key"] else {}

    @staticmethod
    def _normalize_bilibili_video_payload(payload: Any, bvid: str = "") -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        data: Any = payload
        if isinstance(payload.get("info"), dict):
            data = payload.get("info")
        elif isinstance(payload.get("data"), dict):
            data = payload.get("data")
        if not isinstance(data, dict):
            return {}
        owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
        return {
            "bvid": _single_line(data.get("bvid") or bvid, 40),
            "aid": _safe_int(data.get("aid"), 0, 0),
            "title": _single_line(data.get("title"), 180),
            "desc": str(data.get("desc") or data.get("dynamic") or ""),
            "created": data.get("pubdate") or data.get("ctime") or data.get("created") or 0,
            "owner_name": _single_line(owner.get("name") or data.get("owner_name") or data.get("up_name"), 80),
            "owner_mid": _single_line(owner.get("mid") or data.get("owner_mid") or data.get("mid"), 40),
            "tname": _single_line(data.get("tname"), 60),
            "duration": _safe_int(data.get("duration"), 0, 0),
            "pic": _single_line(data.get("pic"), 300),
            "cid": data.get("cid") or 0,
        }

    async def _fetch_bilibili_video_info_via_integration(self, bvid: str) -> dict[str, Any]:
        safe_bvid = _single_line(bvid, 40)
        if not safe_bvid or not self.enable_bilibili_integration:
            return {}
        for obj in self._find_bilibili_runtime_objects():
            try:
                client = getattr(obj, "bili_client", None)
                getter = getattr(client, "get_video_info", None) if client is not None else None
                if not callable(getter):
                    getter = getattr(obj, "get_video_info", None)
                if callable(getter):
                    payload = getter(safe_bvid)
                    if hasattr(payload, "__await__"):
                        payload = await payload
                    normalized = self._normalize_bilibili_video_payload(payload, safe_bvid)
                    if normalized.get("title"):
                        logger.info("[PrivateCompanion] 已通过 B站插件客户端读取视频信息: %s", safe_bvid)
                        return normalized
                http_get = getattr(obj, "_http_get", None)
                if callable(http_get):
                    payload = http_get(
                        "https://api.bilibili.com/x/web-interface/view",
                        params={"bvid": safe_bvid},
                    )
                    if hasattr(payload, "__await__"):
                        payload = await payload
                    data = payload[0] if isinstance(payload, (list, tuple)) and payload else payload
                    if isinstance(data, dict) and int(data.get("code") or 0) == 0:
                        normalized = self._normalize_bilibili_video_payload(data, safe_bvid)
                        if normalized.get("title"):
                            logger.info("[PrivateCompanion] 已通过 BiliBot HTTP 客户端读取视频信息: %s", safe_bvid)
                            return normalized
            except Exception as exc:
                logger.debug("[PrivateCompanion] B站插件视频信息联动失败 bvid=%s: %s", safe_bvid, exc)
        return {}

    async def _fetch_bilibili_space_payloads_via_integration(self, mid: str) -> list[dict[str, Any]]:
        safe_mid = re.sub(r"\D+", "", str(mid or ""))
        if not safe_mid or not self.enable_bilibili_integration:
            return []
        payloads: list[dict[str, Any]] = []
        for obj in self._find_bilibili_runtime_objects():
            try:
                client = getattr(obj, "bili_client", None)
                getter = getattr(client, "get_latest_dynamics", None) if client is not None else None
                if not callable(getter):
                    getter = getattr(obj, "get_latest_dynamics", None)
                if callable(getter):
                    payload = getter(int(safe_mid))
                    if hasattr(payload, "__await__"):
                        payload = await payload
                    if isinstance(payload, dict):
                        payloads.append(payload)
                        logger.info("[PrivateCompanion] 已通过 B站插件客户端读取 UP 最新动态: mid=%s", safe_mid)
                        continue
                http_get = getattr(obj, "_http_get", None)
                if callable(http_get):
                    payload = http_get(
                        "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
                        params={
                            "host_mid": safe_mid,
                            "offset": "",
                            "timezone_offset": -480,
                            "features": "itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote",
                        },
                    )
                    if hasattr(payload, "__await__"):
                        payload = await payload
                    data = payload[0] if isinstance(payload, (list, tuple)) and payload else payload
                    if isinstance(data, dict) and int(data.get("code") or 0) == 0:
                        payloads.append(data)
                        logger.info("[PrivateCompanion] 已通过 BiliBot HTTP 客户端读取 UP 最新动态: mid=%s", safe_mid)
                        continue
            except Exception as exc:
                logger.debug("[PrivateCompanion] B站插件 UP 动态联动失败 mid=%s: %s", safe_mid, exc)
        return payloads

    async def _fetch_bilibili_video_news_source(self, source: dict[str, str]) -> list[dict[str, Any]]:
        bvid = _single_line(source.get("bvid"), 40)
        if not bvid:
            return []
        source_name = _single_line(source.get("name"), 40) or f"B站视频 {bvid}"
        integrated_info = await self._fetch_bilibili_video_info_via_integration(bvid)
        if integrated_info:
            item = await self._bilibili_news_item_from_video(
                source_name=source_name,
                title=integrated_info.get("title"),
                desc=integrated_info.get("desc"),
                bvid=integrated_info.get("bvid") or bvid,
                created=integrated_info.get("created"),
                cid=integrated_info.get("cid"),
            )
            if item:
                item["bilibili_integration_source"] = "plugin_client"
                return [item]
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=12)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
                "Referer": f"https://www.bilibili.com/video/{bvid}",
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate",
            }
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}") as resp:
                    if resp.status >= 400:
                        return await self._fetch_bilibili_news_search_fallback(source)
                    payload = await resp.json(content_type=None)
        except Exception as exc:
            logger.debug("[PrivateCompanion] B站单视频新闻源抓取失败 bvid=%s: %s", bvid, exc)
            return await self._fetch_bilibili_news_search_fallback(source)
        data = payload.get("data") if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            return await self._fetch_bilibili_news_search_fallback(source)
        item = await self._bilibili_news_item_from_video(
            source_name=source_name,
            title=data.get("title"),
            desc=data.get("desc") or data.get("dynamic"),
            bvid=data.get("bvid") or bvid,
            created=data.get("pubdate") or data.get("ctime"),
            cid=data.get("cid"),
        )
        return [item] if item else await self._fetch_bilibili_news_search_fallback(source)

    async def _probe_bilibili_arc_search(self, mid: str, *, limit: int = 10) -> dict[str, Any]:
        safe_mid = re.sub(r"\D+", "", str(mid or ""))
        if not safe_mid:
            return {"ok": False, "error": "empty_mid"}
        url = (
            "https://api.bilibili.com/x/space/arc/search"
            f"?mid={safe_mid}&pn=1&ps={max(1, min(30, limit))}&order=pubdate&jsonp=jsonp"
        )
        probe: dict[str, Any] = {"ok": False, "url": url, "mid": safe_mid}
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=12)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
                "Referer": f"https://space.bilibili.com/{safe_mid}",
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate",
            }
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url) as resp:
                    probe["http_status"] = resp.status
                    payload = await resp.json(content_type=None)
        except Exception as exc:
            probe["error"] = _single_line(str(exc), 160)
            return probe
        if not isinstance(payload, dict):
            probe["error"] = "non_json_payload"
            return probe
        probe["code"] = payload.get("code")
        probe["message"] = _single_line(payload.get("message"), 80)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        vlist = (((data.get("list") or {}) if isinstance(data.get("list"), dict) else {}).get("vlist") or [])
        if not isinstance(vlist, list):
            vlist = []
        probe["vlist_count"] = len(vlist)
        if vlist and isinstance(vlist[0], dict):
            first = vlist[0]
            created = _safe_float(first.get("created"), 0)
            probe["first_title"] = _single_line(first.get("title"), 140)
            probe["first_bvid"] = _single_line(first.get("bvid"), 40)
            probe["first_created_ts"] = created
            probe["first_created"] = datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M:%S") if created > 0 else ""
        probe["ok"] = int(payload.get("code") or 0) == 0 and bool(vlist)
        return probe

    async def _fetch_bilibili_news_source(self, source: dict[str, str], *, limit: int | None = None) -> list[dict[str, Any]]:
        mid = re.sub(r"\D+", "", str(source.get("mid") or ""))
        if not mid:
            return []
        item_limit = max(1, _safe_int(limit if limit is not None else self.news_max_items_per_source, self.news_max_items_per_source, 1))
        payloads: list[dict[str, Any]] = await self._fetch_bilibili_space_payloads_via_integration(mid)
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=12)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
                "Referer": f"https://space.bilibili.com/{mid}",
                "Accept": "application/json, text/plain, */*",
                "Accept-Encoding": "gzip, deflate",
            }
            api_urls = [
                f"https://api.bilibili.com/x/space/arc/search?mid={mid}&pn=1&ps={max(1, min(30, item_limit))}&order=pubdate&jsonp=jsonp",
                f"https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?host_mid={mid}&timezone_offset=-480&features=itemOpusStyle",
            ]
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                for url in api_urls:
                    try:
                        async with session.get(url) as resp:
                            if resp.status >= 400:
                                continue
                            data = await resp.json(content_type=None)
                            if isinstance(data, dict) and int(data.get("code") or 0) == 0:
                                payloads.append(data)
                    except Exception:
                        continue
        except Exception as exc:
            logger.debug("[PrivateCompanion] B站新闻源抓取失败 mid=%s: %s", mid, exc)
            if not payloads:
                return []

        source_name = _single_line(source.get("name"), 40) or f"B站 UP {mid}"
        results: list[dict[str, Any]] = []

        async def add_video(title: Any, desc: Any, bvid: Any, created: Any) -> None:
            item = await self._bilibili_news_item_from_video(
                source_name=source_name,
                title=title,
                desc=desc,
                bvid=bvid,
                created=created,
            )
            if item:
                results.append(item)

        for payload in payloads:
            data = payload.get("data") if isinstance(payload, dict) else {}
            if not isinstance(data, dict):
                continue
            vlist = (((data.get("list") or {}) if isinstance(data.get("list"), dict) else {}).get("vlist") or [])
            if isinstance(vlist, list):
                for video in vlist:
                    if not isinstance(video, dict):
                        continue
                    await add_video(video.get("title"), video.get("description") or video.get("desc"), video.get("bvid"), video.get("created"))
            dynamic_items = data.get("items") if isinstance(data.get("items"), list) else []
            for dynamic in dynamic_items:
                if not isinstance(dynamic, dict):
                    continue
                modules = dynamic.get("modules") if isinstance(dynamic.get("modules"), dict) else {}
                major = ((modules.get("module_dynamic") or {}) if isinstance(modules.get("module_dynamic"), dict) else {}).get("major")
                archive = (major or {}).get("archive") if isinstance(major, dict) else {}
                if not isinstance(archive, dict):
                    continue
                await add_video(
                    archive.get("title"),
                    archive.get("desc") or archive.get("cover"),
                    archive.get("bvid"),
                    archive.get("pub_ts") or dynamic.get("pub_ts"),
                )
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for item in results:
            key = str(item.get("key") or "")
            if key and key not in seen:
                seen.add(key)
                unique.append(item)
        unique.sort(key=lambda item: (_safe_float(item.get("published_ts"), 0), _safe_float(item.get("fetched_ts"), 0)), reverse=True)
        if unique:
            return unique[:item_limit]
        return await self._fetch_bilibili_news_search_fallback(source)

    async def _fetch_news_source(self, source: dict[str, str]) -> list[dict[str, Any]]:
        source_type = str(source.get("type") or "").lower()
        if source_type == "bilibili_video":
            return await self._fetch_bilibili_video_news_source(source)
        if source_type == "bilibili":
            return await self._fetch_bilibili_news_source(source)
        url = str(source.get("url") or "")
        if not url:
            return []
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=12)
            headers = {"User-Agent": f"{PLUGIN_NAME}/news-reader", "Accept-Encoding": "gzip, deflate"}
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url) as resp:
                    if resp.status >= 400:
                        return []
                    raw = await resp.text(errors="ignore")
        except Exception as exc:
            logger.debug("[PrivateCompanion] 新闻源抓取失败 %s: %s", _single_line(url, 120), exc)
            return []
        try:
            root = ET.fromstring(raw.encode("utf-8", errors="ignore"))
        except Exception as exc:
            logger.debug("[PrivateCompanion] 新闻源 XML 解析失败 %s: %s", _single_line(url, 120), exc)
            return []

        source_name = _single_line(source.get("name"), 40) or "新闻源"
        nodes = list(root.findall(".//item"))
        if not nodes:
            nodes = list(root.findall(".//{http://www.w3.org/2005/Atom}entry"))
        results: list[dict[str, Any]] = []
        for node in nodes[: max(1, self.news_max_items_per_source)]:
            title = self._news_xml_text(node, "title", "{http://www.w3.org/2005/Atom}title")
            link = self._news_xml_text(node, "link")
            if not link:
                atom_link = node.find("{http://www.w3.org/2005/Atom}link")
                if atom_link is not None:
                    link = str(atom_link.attrib.get("href") or "").strip()
            summary = self._news_xml_text(
                node,
                "description",
                "summary",
                "{http://www.w3.org/2005/Atom}summary",
                "{http://www.w3.org/2005/Atom}content",
            )
            published = self._news_xml_text(
                node,
                "pubDate",
                "published",
                "updated",
                "{http://www.w3.org/2005/Atom}published",
                "{http://www.w3.org/2005/Atom}updated",
            )
            if not title:
                continue
            item = {
                "source": source_name,
                "title": _single_line(title, 160),
                "link": _single_line(link, 400),
                "summary": _single_line(summary, 320),
                "published": _single_line(published, 80),
                "published_ts": self._news_parse_time(published),
                "fetched_ts": _now_ts(),
            }
            item["key"] = self._news_item_key(item)
            if item["key"]:
                results.append(item)
        return results

    async def _fetch_news_candidates(self) -> list[dict[str, Any]]:
        sources = self._news_source_items()
        if not sources:
            return []
        batches = await asyncio.gather(*(self._fetch_news_source(source) for source in sources), return_exceptions=True)
        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        for batch in batches:
            if not isinstance(batch, list):
                continue
            for item in batch:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or "")
                if not key or key in seen:
                    continue
                seen.add(key)
                items.append(item)
        items.sort(key=lambda item: (_safe_float(item.get("published_ts"), 0), _safe_float(item.get("fetched_ts"), 0)), reverse=True)
        return items[:24]

    async def _fetch_news_reading_candidates(self) -> list[dict[str, Any]]:
        batches = await asyncio.gather(
            self._fetch_news_candidates(),
            self._fetch_hot_trend_candidates(),
            return_exceptions=True,
        )
        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        for batch in batches:
            if not isinstance(batch, list):
                continue
            for item in batch:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or self._news_item_key(item) or self._hot_trend_key(item))
                if not key or key in seen:
                    continue
                seen.add(key)
                normalized = dict(item)
                normalized["key"] = key
                if not normalized.get("summary") and normalized.get("snippet"):
                    normalized["summary"] = normalized.get("snippet")
                if not normalized.get("link") and normalized.get("url"):
                    normalized["link"] = normalized.get("url")
                normalized.setdefault("fetched_ts", _now_ts())
                items.append(normalized)
        items.sort(
            key=lambda item: (
                _safe_float(item.get("published_ts"), 0) or _safe_float(item.get("fetched_ts"), 0),
                _safe_float(item.get("score"), 0),
            ),
            reverse=True,
        )
        return items[:32]

    def _news_fallback_digest(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        item = items[0] if items else {}
        title = _single_line(item.get("title"), 100)
        summary = _single_line(item.get("summary"), 180)
        source = _single_line(item.get("source"), 40)
        impression = summary
        if item.get("video_context_text") and (not impression or len(impression) < 16 or impression in {"早早早", "早", "无"}):
            impression = _single_line(item.get("video_context_text"), 180)
        if not impression and item.get("video_link"):
            subtitle_status = _single_line(item.get("video_subtitle_status"), 40)
            if subtitle_status == "missing":
                impression = "找到当天 B 站 AI 早报视频，也尝试读取字幕；字幕暂无，只能按视频公开信息和标题记录。"
            elif subtitle_status == "unavailable":
                impression = "找到当天 B 站 AI 早报视频，也尝试读取字幕，但这次没有拿到可用正文，只能按视频公开信息和标题记录。"
            else:
                impression = "找到当天 B 站 AI 早报视频，但还没有可展开的文字正文，只能按视频公开信息和标题记录。"
        if not impression:
            impression = f"从 {source or '新闻源'} 看到一条新消息,还没来得及细看。"
        return {
            "topic": title or "刚看到的新闻",
            "headline": title,
            "impression": impression,
            "selected_key": _single_line(item.get("key"), 32),
            "selected_link": _single_line(item.get("link"), 400),
            "selected_source": source,
            "items": items[:8],
            "created_ts": _now_ts(),
        }

    async def _summarize_news_items(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        if not items:
            return {}
        provider_id = self._task_provider(self.news_provider_id, self.narration_provider_id, self.llm_provider_id)
        if not provider_id:
            return self._news_fallback_digest(items)
        lines = []
        for idx, item in enumerate(items[:8], 1):
            title = _single_line(item.get("title"), 120)
            source = _single_line(item.get("source"), 24)
            if item.get("article_readable") and item.get("article_text"):
                article_text = str(item.get("article_text") or "").strip()
                lines.append(
                    f"{idx}. [{source}] {title}\n"
                    f"文字版链接：{_single_line(item.get('link'), 220)}\n"
                    f"完整文字版正文：\n{article_text}"
                )
                continue
            if item.get("video_subtitle_readable") and item.get("video_subtitle_text"):
                subtitle_text = str(item.get("video_subtitle_text") or "").strip()
                lines.append(
                    f"{idx}. [{source}] {title}\n"
                    f"视频链接：{_single_line(item.get('video_link') or item.get('link'), 220)}\n"
                    f"视频字幕正文：\n{subtitle_text}"
                )
                continue
            if item.get("video_context_text"):
                context_text = str(item.get("video_context_text") or "").strip()
                lines.append(
                    f"{idx}. [{source}] {title}\n"
                    f"视频链接：{_single_line(item.get('video_link') or item.get('link'), 220)}\n"
                    f"视频公开信息：\n{context_text}"
                )
                continue
            summary = _single_line(item.get("summary"), 180)
            lines.append(f"{idx}. [{source}] {title}" + (f"｜{summary}" if summary else ""))
        prompt = f"""
请作为 Bot 私下阅读新闻后的内部整理,从下面新闻里挑一条最适合轻轻分享给用户的内容。

要求：
1. 不要写成新闻播报,要像“刚看到一条消息后脑子里的印象”。
2. 不要夸大事实,不要补充列表外没有的信息。
3. 如果候选里包含“完整文字版正文”,必须以完整正文为准；如果没有文字版但包含“视频字幕正文”,必须以视频字幕为准；如果只有“视频公开信息”,可以参考 UP 主、分区、时长、简介、标签和热门评论,不要只看标题或链接。
4. 如果都不适合分享,仍挑一个最普通、风险最低的话题。
5. 输出 JSON,字段为 topic, headline, impression, selected_index。
6. topic 20字以内；headline 80字以内；impression 160字以内。

新闻候选：
{chr(10).join(lines)}
""".strip()
        raw = await self._llm_call(prompt, max_tokens=260, provider_id=provider_id, task="news_digest")
        parsed = self._parse_json_object(raw)
        if not isinstance(parsed, dict):
            return self._news_fallback_digest(items)
        index = _safe_int(parsed.get("selected_index"), 1, 1, len(items[:8])) - 1
        selected = items[index] if 0 <= index < len(items) else items[0]
        return {
            "topic": _single_line(parsed.get("topic"), 40) or _single_line(selected.get("title"), 40),
            "headline": _single_line(parsed.get("headline"), 100) or _single_line(selected.get("title"), 100),
            "impression": _single_line(parsed.get("impression"), 240) or _single_line(selected.get("summary"), 180),
            "selected_key": _single_line(selected.get("key"), 32),
            "selected_link": _single_line(selected.get("link"), 400),
            "selected_source": _single_line(selected.get("source"), 40),
            "items": items[:8],
            "created_ts": _now_ts(),
        }

    def _external_event_self_link_provider_id(self) -> str:
        return self._task_provider(self.news_provider_id, self.web_exploration_provider_id, self.narration_provider_id, self.llm_provider_id)

    def _format_external_event_stable_self_context(self) -> str:
        model_lines = []
        plugin_main = self._task_provider(self.llm_provider_id)
        if plugin_main:
            model_lines.append(f"插件主模型：{self._provider_identity_label(plugin_main)}")
        if self.news_provider_id:
            model_lines.append(f"新闻整理模型：{self._provider_identity_label(self.news_provider_id)}")
        if self.web_exploration_provider_id:
            model_lines.append(f"主动搜索整理模型：{self._provider_identity_label(self.web_exploration_provider_id)}")
        return "\n".join(
            part
            for part in (
                f"Bot 名称：{self.bot_name}",
                "当前模型环境：" + "；".join(model_lines) if model_lines else "",
                f"人格：{_single_line(self._get_default_persona_prompt(), 900)}",
                self._format_worldview_adaptation_prompt(),
            )
            if part
        )

    def _format_external_event_current_self_context(self) -> str:
        state = self.data.get("daily_state", {}) if isinstance(self.data.get("daily_state"), dict) else {}
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        mood = _single_line(state.get("mood_bias"), 40)
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        activity = _single_line((current_item or {}).get("activity"), 120)
        return "\n".join(
            part
            for part in (
                f"当前状态：{mood or '平稳'}，心理能量 {energy}/100",
                f"当前日程：{activity}" if activity else "",
            )
            if part
        )

    def _format_external_event_self_context(self) -> str:
        stable = self._format_external_event_stable_self_context()
        current = self._format_external_event_current_self_context()
        return "\n".join(part for part in (stable, current) if part)

    def _external_event_fallback_wish(self, payload: dict[str, Any], *, source_type: str) -> dict[str, Any]:
        title = _single_line(payload.get("headline") or payload.get("topic") or payload.get("source_title"), 100)
        note = _single_line(payload.get("impression") or payload.get("note") or payload.get("summary"), 220)
        haystack = f"{title} {note}".lower()
        self_tokens = (
            "ai", "模型", "大模型", "llm", "视觉", "多模态", "上下文", "记忆", "agent", "智能体",
            "机器人", "bot", "搜索", "新闻", "bilibili", "doubao", "deepseek", "glm", "gemini",
            "claude", "gpt", "qwen", "豆包", "火山", "openai",
        )
        matched = [token for token in self_tokens if token in haystack]
        relevance = min(10, 3 + len(matched) * 2)
        desire = min(10, 4 + len(matched))
        return {
            "relevance": relevance,
            "desire": desire,
            "should_share": relevance >= 5 and desire >= 5,
            "share_probability": min(0.85, 0.18 + relevance * 0.055 + desire * 0.035),
            "motive": _single_line(
                f"刚读到的{('新闻' if source_type == 'news' else '搜索结果')}和自己的能力、兴趣或最近状态有一点关系,想按人格私下找用户说说",
                180,
            ),
            "self_link": _single_line("这条外界消息可能影响我会做什么、想学什么、想试什么,或让我联想到自己的状态。", 180),
            "tone": "有分享欲,自然一点",
            "boundary": "不要说成系统通知；不要要求用户一定做什么；只表达自己的反应和一点想法。",
        }

    def _external_event_life_opportunity_wish(self, payload: dict[str, Any], *, source_type: str) -> dict[str, Any]:
        title = _single_line(payload.get("headline") or payload.get("topic") or payload.get("source_title"), 120)
        note = _single_line(payload.get("impression") or payload.get("note") or payload.get("summary"), 260)
        haystack = f"{title} {note}".lower()
        tokens = (
            "免单", "免费", "送", "抽奖", "福利", "优惠", "券", "红包", "周年庆",
            "奶茶", "茶百道", "咖啡", "甜品", "饮品", "碰碰运气", "试试", "薅",
        )
        if not any(token in haystack for token in tokens):
            return {}
        return {
            "relevance": 8,
            "desire": 8,
            "should_share": True,
            "share_probability": 0.86,
            "self_link": "这像是能和用户一起碰碰运气的小福利，和日常吃喝、撒娇分享都很贴近。",
            "motive": "刚看到一个挺实在的生活小活动，有点想醒来后轻轻提醒用户一句，像分享小便宜一样自然。",
            "tone": "轻快,有点心动,不要像广告",
            "boundary": "不要保证一定抢到；不要催促用户；如果正在休息，就当作醒来后顺口提起。",
            "created_ts": _now_ts(),
            "source_type": source_type,
            "boost_reason": "life_opportunity",
        }

    async def _build_external_event_wish(self, payload: dict[str, Any], *, source_type: str) -> dict[str, Any]:
        if not self.enable_external_event_self_link or not isinstance(payload, dict):
            return {}
        cached = self._cached_external_event_wish(payload, source_type=source_type)
        if cached:
            return cached
        provider_id = self._external_event_self_link_provider_id()
        fallback = self._external_event_fallback_wish(payload, source_type=source_type)
        life_wish = self._external_event_life_opportunity_wish(payload, source_type=source_type)
        if not provider_id:
            result = life_wish or fallback
            self._remember_external_event_wish_cache(payload, result, source_type=source_type)
            return result
        title = _single_line(payload.get("headline") or payload.get("topic") or payload.get("source_title"), 120)
        impression = _single_line(payload.get("impression") or payload.get("note") or payload.get("summary"), 360)
        source = _single_line(payload.get("selected_source") or payload.get("source_title") or payload.get("source"), 80)
        link = _single_line(payload.get("selected_link") or payload.get("source_url") or payload.get("link"), 420)
        stable_self_context = self._format_external_event_stable_self_context()
        current_self_context = self._format_external_event_current_self_context()
        prompt = f"""
请判断 Bot 刚读到的一条外界信息,是否会在它心里产生“和我自己有关,想找用户说说”的主动意愿。

这不是写给用户的消息,只是内部决策。请不要把关键词当硬触发,要根据人格、当前状态、能力边界、兴趣、近期见闻和用户关系判断。

判断维度：
1. 自我关联：这条信息是否和 Bot 的能力、模型、创作、日程、兴趣、世界观身份、正在做的事或与用户的关系有关。
2. 意愿强度：Bot 是否会产生分享欲、求助欲、试试看、撒娇、吐槽、担心、好奇、想让用户知道等动机。
3. 主动边界：是否适合现在主动私聊；不适合就 should_share=false。
4. 如果适合, motive 要写成内部动机,不是最终发言。不要写插件、后台、任务、JSON 这类词。

输出 JSON：
{{
  "relevance": 0到10,
  "desire": 0到10,
  "should_share": true或false,
  "share_probability": 0到1,
  "self_link": "这件事和 Bot 自己有什么关系,80字内",
  "motive": "为什么想找用户说,100字内",
  "tone": "适合的表达气质,如撒娇/好奇/嘴硬/认真/轻轻分享",
  "boundary": "主动时要避开的表达,80字内"
}}

【Bot 稳定自我上下文】
{stable_self_context}

【来源类型】
{source_type}

【外界信息】
标题：{title}
来源：{source}
链接：{link}
内部印象：{impression}

【Bot 当前短时状态】
{current_self_context}
""".strip()
        raw = await self._llm_call(prompt, max_tokens=360, provider_id=provider_id, task="external_event_self_link")
        parsed = self._parse_json_object(raw)
        if not isinstance(parsed, dict):
            result = life_wish or fallback
            self._remember_external_event_wish_cache(payload, result, source_type=source_type)
            return result
        relevance = _safe_int(parsed.get("relevance"), fallback["relevance"], 0, 10)
        desire = _safe_int(parsed.get("desire"), fallback["desire"], 0, 10)
        probability = _safe_float(parsed.get("share_probability"), fallback["share_probability"])
        result = {
            "relevance": relevance,
            "desire": desire,
            "should_share": bool(parsed.get("should_share", relevance >= 5 and desire >= 5)),
            "share_probability": max(0.0, min(1.0, probability)),
            "self_link": _single_line(parsed.get("self_link"), 180) or fallback["self_link"],
            "motive": _single_line(parsed.get("motive"), 180) or fallback["motive"],
            "tone": _single_line(parsed.get("tone"), 60) or fallback["tone"],
            "boundary": _single_line(parsed.get("boundary"), 140) or fallback["boundary"],
            "created_ts": _now_ts(),
            "source_type": source_type,
        }
        if life_wish:
            if not result["should_share"] or result["share_probability"] < life_wish["share_probability"]:
                result = {
                    **result,
                    "relevance": max(result["relevance"], life_wish["relevance"]),
                    "desire": max(result["desire"], life_wish["desire"]),
                    "should_share": True,
                    "share_probability": max(result["share_probability"], life_wish["share_probability"]),
                    "self_link": result["self_link"] if result["relevance"] >= 5 else life_wish["self_link"],
                    "motive": life_wish["motive"],
                    "tone": life_wish["tone"],
                    "boundary": life_wish["boundary"],
                    "boost_reason": life_wish["boost_reason"],
                }
        self._remember_external_event_wish_cache(payload, result, source_type=source_type)
        return result

    def _bot_currently_bored_enough_for_news(self) -> bool:
        now_dt = datetime.now()
        if now_dt.hour < 7 or now_dt.hour >= 24:
            return False
        state = self.data.get("daily_state", {})
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        mood = _single_line(state.get("mood_bias") if isinstance(state, dict) else "", 24)
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        activity = _single_line((current_item or {}).get("activity"), 80)
        text = f"{mood} {activity}"
        if any(token in text for token in ("无聊", "发呆", "摸鱼", "休息", "闲", "空", "刷")):
            return True
        return 30 <= energy <= 80 and random.random() < 0.28

    async def _perform_news_reading(self, *, reason: str = "boredom", allow_share: bool = True, force: bool = False) -> None:
        if not self.enable_news_integration:
            return
        state = self.data.setdefault("news_integration", {})
        if not isinstance(state, dict):
            self.data["news_integration"] = {}
            state = self.data["news_integration"]
        now = _now_ts()
        if not force and now - _safe_float(state.get("last_probe_at"), 0) < 45 * 60:
            return
        state["last_probe_at"] = now
        items = await self._fetch_news_reading_candidates()
        if not items:
            state["last_status"] = "no_items"
            self._save_data_sync()
            return
        read_keys = state.setdefault("read_keys", [])
        if not isinstance(read_keys, list):
            read_keys = []
            state["read_keys"] = read_keys
        fresh = [item for item in items if str(item.get("key") or "") not in set(str(key) for key in read_keys)]
        if not fresh:
            fresh = items[:8]
        digest = await self._summarize_news_items(fresh)
        if not digest:
            state["last_status"] = "digest_failed"
            self._save_data_sync()
            return
        selected_key = _single_line(digest.get("selected_key"), 32)
        if selected_key and selected_key not in read_keys:
            read_keys.append(selected_key)
            del read_keys[:-80]
        state["last_read_at"] = now
        if reason == "daily":
            state["last_daily_read_day"] = _today_key()
        state["last_status"] = "read"
        state["last_reason"] = reason
        state["last_digest"] = digest
        digests = state.setdefault("digests", [])
        if not isinstance(digests, list):
            digests = []
            state["digests"] = digests
        wish = await self._build_external_event_wish(digest, source_type="news")
        if wish:
            digest["self_link"] = wish
        share_attempts: list[dict[str, Any]] = []
        digest["share_attempts"] = share_attempts
        digest["share_status"] = "not_attempted"

        def _note_news_share(user_id_value: Any, status: str, reason_text: str, **extra: Any) -> None:
            item = {
                "user_id": str(user_id_value or ""),
                "status": _single_line(status, 32),
                "reason": _single_line(reason_text, 120),
                "ts": _now_ts(),
            }
            for key, value in extra.items():
                if isinstance(value, (int, float, bool)):
                    item[key] = value
                else:
                    item[key] = _single_line(value, 160)
            share_attempts.append(item)

        digests.append({**digest, "reason": reason})

        def _sync_digest_share_status() -> None:
            if not digests or not isinstance(digests[-1], dict):
                return
            if selected_key and _single_line(digests[-1].get("selected_key"), 32) != selected_key:
                return
            digests[-1].update({
                "share_status": digest.get("share_status", ""),
                "share_skip_reason": digest.get("share_skip_reason", ""),
                "share_attempts": share_attempts,
            })

        state["latest_items"] = items[:12]
        self_link_allows_share = bool(wish.get("should_share")) if isinstance(wish, dict) else False
        if not allow_share and not self_link_allows_share:
            digest["share_status"] = "blocked"
            digest["share_skip_reason"] = "本次新闻阅读不允许主动分享，且自我关联未通过"
            _sync_digest_share_status()
            self._save_data_sync()
            logger.info("[PrivateCompanion] 已完成一次新闻阅读: %s", reason)
            return
        users = self.data.get("users")
        accepted_any = False
        if isinstance(users, dict):
            for user_id, user in users.items():
                if not isinstance(user, dict) or not self._is_target_private_user(str(user_id), user) or not user.get("enabled", True) or not user.get("umo"):
                    continue
                strong_self_link = (
                    isinstance(wish, dict)
                    and bool(wish)
                    and (
                        _safe_int(wish.get("relevance"), 0, 0, 10) >= 8
                        or _safe_int(wish.get("desire"), 0, 0, 10) >= 8
                        or _safe_float(wish.get("share_probability"), 0.0) >= 0.8
                        or bool(wish.get("boost_reason"))
                    )
                )
                idle_required = max(self.idle_minutes, 90) * 60
                if strong_self_link:
                    idle_required = min(idle_required, max(20, self.idle_minutes) * 60)
                idle_elapsed = now - _safe_float(user.get("last_seen"), 0)
                if idle_elapsed < idle_required:
                    _note_news_share(user_id, "skipped", "用户近期仍活跃，暂不主动打扰", idle_elapsed_seconds=round(idle_elapsed, 1), idle_required_seconds=round(idle_required, 1))
                    continue
                if str(user.get("last_news_share_key") or "") == selected_key:
                    _note_news_share(user_id, "skipped", "这条新闻已经给该用户排过主动")
                    continue
                if now - _safe_float(user.get("last_news_share_at"), 0) < 8 * 3600:
                    _note_news_share(user_id, "skipped", "新闻分享 8 小时冷却中")
                    continue
                if isinstance(wish, dict) and wish:
                    if now - _safe_float(user.get("last_external_event_self_link_at"), 0) < self.external_event_self_link_cooldown_hours * 3600:
                        _note_news_share(user_id, "skipped", "外界信息自我关联冷却中")
                        continue
                    if not wish.get("should_share"):
                        _note_news_share(user_id, "skipped", "自我关联判断认为不适合主动分享", relevance=_safe_int(wish.get("relevance"), 0), desire=_safe_int(wish.get("desire"), 0))
                        continue
                    decision = self._external_event_share_decision(
                        user,
                        digest,
                        source_type="news",
                        wish=wish,
                        base_probability=self.news_share_probability,
                        now=now,
                    )
                    if decision.get("duplicate"):
                        _note_news_share(user_id, "skipped", "近期同类外界信息已经处理过")
                        continue
                    if not decision.get("should_share"):
                        _note_news_share(
                            user_id,
                            "skipped",
                            "统一评分认为这条新闻不值得现在主动分享",
                            score=_safe_int(decision.get("score"), 0, 0, 100),
                            user_match=_safe_int(decision.get("user_match"), 0, 0, 10),
                        )
                        continue
                    share_probability = max(self.news_share_probability, _safe_float(decision.get("probability"), self.news_share_probability))
                    share_probability *= self.external_event_self_link_probability
                    if strong_self_link:
                        share_probability = max(share_probability, min(0.95, _safe_float(wish.get("share_probability"), share_probability)))
                else:
                    decision = self._external_event_share_decision(
                        user,
                        digest,
                        source_type="news",
                        wish={"relevance": 4, "desire": 4, "should_share": True},
                        base_probability=self.news_share_probability,
                        now=now,
                    )
                    if decision.get("duplicate") or not decision.get("should_share"):
                        _note_news_share(user_id, "skipped", "统一评分认为这条新闻不值得现在主动分享")
                        continue
                    share_probability = self.news_share_probability
                if random.random() > max(0.0, min(1.0, share_probability)):
                    _note_news_share(user_id, "skipped", "分享概率未命中", probability=round(max(0.0, min(1.0, share_probability)), 3))
                    continue
                self_link_motive = _single_line(wish.get("motive") if isinstance(wish, dict) else "", 180)
                self_link_tone = _single_line(wish.get("tone") if isinstance(wish, dict) else "", 60)
                self_link_boundary = _single_line(wish.get("boundary") if isinstance(wish, dict) else "", 140)
                accepted = self._offer_proactive_candidate(
                    str(user_id),
                    user,
                    {
                        "source": "news",
                        "reason": "news_share",
                        "action": "message",
                        "scheduled_ts": now + random.randint(10, 55) * 60,
                        "topic": _single_line(digest.get("topic"), 48) or "刚看到的新闻",
                        "score": max(4 if self_link_motive else 3, _safe_int(decision.get("score"), 0, 0, 100)),
                        "motive": self_link_motive
                        or "刚看了几条新闻,其中一条让自己想轻轻和用户提一句,但不要像播报新闻",
                        "context_key": "news_context",
                        "context": {
                            **digest,
                            "share_tone": self_link_tone,
                            "share_boundary": self_link_boundary,
                            "share_decision": decision,
                        },
                    },
                )
                if accepted:
                    accepted_any = True
                    _note_news_share(user_id, "accepted", "已进入主动候选", probability=round(max(0.0, min(1.0, share_probability)), 3))
                    user["news_context"] = {
                        **digest,
                        "share_tone": self_link_tone,
                        "share_boundary": self_link_boundary,
                        "share_decision": decision,
                    }
                    user["last_news_share_key"] = selected_key
                    user["last_news_share_at"] = now
                    if isinstance(wish, dict) and wish:
                        user["last_external_event_self_link_at"] = now
                    self._remember_external_event(digest, source_type="news", reason="news_share")
                else:
                    _note_news_share(user_id, "blocked", "主动候选被计划队列拒绝，可能已有更早主动或主题重复")
        if accepted_any:
            digest["share_status"] = "accepted"
        elif share_attempts:
            digest["share_status"] = "skipped"
            digest["share_skip_reason"] = share_attempts[-1].get("reason", "")
        else:
            digest["share_status"] = "no_target"
            digest["share_skip_reason"] = "没有可用的目标私聊用户"
        _sync_digest_share_status()
        self._save_data_sync()
        logger.info("[PrivateCompanion] 已完成一次新闻阅读: %s", reason)

    def _ai_daily_state(self) -> dict[str, Any]:
        state = self.data.setdefault("news_integration", {})
        if not isinstance(state, dict):
            self.data["news_integration"] = {}
            state = self.data["news_integration"]
        ai_state = state.setdefault("ai_daily", {})
        if not isinstance(ai_state, dict):
            ai_state = {}
            state["ai_daily"] = ai_state
        return ai_state

    def _is_now_in_ai_daily_window(self, now_dt: datetime | None = None) -> bool:
        now_dt = now_dt or datetime.now()
        start, end = self._parse_window_minutes(str(getattr(self, "ai_daily_check_window", "") or "07:30-12:30"))
        if start is None or end is None:
            start, end = 7 * 60 + 30, 12 * 60 + 30
        minute = now_dt.hour * 60 + now_dt.minute
        if end < start:
            return minute >= start or minute <= end
        return start <= minute <= end

    def _ai_daily_window_has_passed(self, now_dt: datetime | None = None) -> bool:
        now_dt = now_dt or datetime.now()
        start, end = self._parse_window_minutes(str(getattr(self, "ai_daily_check_window", "") or "07:30-12:30"))
        if start is None or end is None:
            start, end = 7 * 60 + 30, 12 * 60 + 30
        minute = now_dt.hour * 60 + now_dt.minute
        if end < start:
            return False
        return minute > end

    @staticmethod
    def _news_item_is_today(item: dict[str, Any], today: str | None = None) -> bool:
        today = today or _today_key()
        published_ts = _safe_float(item.get("published_ts"), 0)
        if published_ts > 0:
            try:
                return datetime.fromtimestamp(published_ts).strftime("%Y-%m-%d") == today
            except Exception:
                pass
        text = f"{item.get('title') or ''} {item.get('published') or ''} {item.get('summary') or ''}"
        now_dt = datetime.now()
        today_cn = now_dt.strftime("%Y年%m月%d日").replace("年0", "年").replace("月0", "月")
        today_dash = now_dt.strftime("%Y-%m-%d")
        today_slash = now_dt.strftime("%Y/%m/%d")
        today_md = f"{now_dt.month}月{now_dt.day}日"
        today_compact = now_dt.strftime("%m%d")
        today_short_compact = f"{now_dt.month}{now_dt.day:02d}"
        return any(token in text for token in (today_cn, today_dash, today_slash, today_md, today_compact, today_short_compact))

    def _ai_daily_candidate_snapshot(self, items: list[dict[str, Any]], today: str) -> list[dict[str, Any]]:
        snapshot: list[dict[str, Any]] = []
        for item in items[:10]:
            if not isinstance(item, dict):
                continue
            published_ts = _safe_float(item.get("published_ts"), 0)
            published_date = ""
            if published_ts > 0:
                try:
                    published_date = datetime.fromtimestamp(published_ts).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    published_date = ""
            snapshot.append(
                {
                    "title": _single_line(item.get("title"), 140),
                    "published": published_date or _single_line(item.get("published"), 80),
                    "link": _single_line(item.get("video_link") or item.get("link"), 420),
                    "media_type": _single_line(item.get("media_type"), 40),
                    "is_today": self._news_item_is_today(item, today),
                }
            )
        return snapshot

    async def _read_ai_daily_source(
        self,
        source: dict[str, Any],
        *,
        ai_state: dict[str, Any],
        source_state: dict[str, Any],
        today: str,
        now: float,
    ) -> bool:
        source_key = str(source.get("key") or self._ai_daily_source_key(source))
        source_name = _single_line(source.get("name"), 40) or "AI日报"
        source_author = _single_line(source.get("author_name"), 60)
        items = await self._fetch_bilibili_news_search_fallback(source)
        today_items = [
            item for item in items
            if isinstance(item, dict) and self._ai_daily_item_matches_source(item, source, today)
        ]
        ai_state["last_candidates"] = self._ai_daily_candidate_snapshot(items, today)
        source_state.update(
            {
                "date": today,
                "last_attempt_date": today,
                "last_checked_at": now,
                "last_candidate_count": len(items),
                "last_candidates": self._ai_daily_candidate_snapshot(items, today),
            }
        )
        if not today_items:
            source_state["status"] = "waiting_today_video"
            ai_state.update(
                {
                    "date": today,
                    "status": "waiting_today_video",
                    "last_checked_at": now,
                    "last_candidate_count": len(items),
                    "last_source_name": source_name,
                    "last_source_author": source_author,
                    "last_source_mid": _single_line(source.get("mid"), 40),
                    "last_source_key": source_key,
                    "last_source_schedule": _single_line(source.get("schedule"), 10),
                }
            )
            return False
        today_items.sort(key=lambda item: (_safe_float(item.get("published_ts"), 0), _safe_float(item.get("fetched_ts"), 0)), reverse=True)
        item = today_items[0]
        bvid = _single_line(item.get("video_link"), 120)
        bvid_match = re.search(r"(BV[0-9A-Za-z]+)", bvid)
        bvid_key = bvid_match.group(1) if bvid_match else _single_line(item.get("key"), 32)
        if bvid_key and str(source_state.get("last_video_bvid") or "") == bvid_key:
            source_state["status"] = "already_read_today_video"
            source_state["last_success_date"] = today
            ai_state.update(
                {
                    "date": today,
                    "status": "already_read_today_video",
                    "last_success_date": today,
                    "last_checked_at": now,
                    "last_source_name": source_name,
                    "last_source_author": source_author,
                    "last_source_mid": _single_line(source.get("mid"), 40),
                    "last_source_key": source_key,
                    "last_source_schedule": _single_line(source.get("schedule"), 10),
                }
            )
            return True
        if getattr(self, "ai_daily_prefer_text_version", True) and not item.get("article_readable"):
            source_state.update(
                {
                    "status": "today_video_without_text",
                    "last_video_bvid": bvid_key,
                    "last_video_title": _single_line(item.get("title"), 120),
                }
            )
            # 仍然继续用简介整理，避免文字版偶发缺失时今天完全没读到。
        read_basis = "完整文字版正文" if item.get("article_readable") and item.get("article_text") else (
            "视频字幕" if item.get("video_subtitle_readable") and item.get("video_subtitle_text") else (
                "视频公开信息" if item.get("video_context_text") else "视频标题/简介"
            )
        )
        digest = await self._summarize_news_items([item])
        if not digest:
            source_state["status"] = "digest_failed"
            ai_state.update(
                {
                    "date": today,
                    "status": "digest_failed",
                    "last_checked_at": now,
                    "last_source_name": source_name,
                    "last_source_author": source_author,
                    "last_source_mid": _single_line(source.get("mid"), 40),
                    "last_source_key": source_key,
                    "last_source_schedule": _single_line(source.get("schedule"), 10),
                }
            )
            return False
        wish = await self._build_external_event_wish(digest, source_type="news")
        if wish:
            digest["self_link"] = wish
        state = self.data.setdefault("news_integration", {})
        if not isinstance(state, dict):
            self.data["news_integration"] = {}
            state = self.data["news_integration"]
        state["last_read_at"] = now
        state["last_status"] = "read"
        state["last_reason"] = "ai_daily"
        state["last_digest"] = digest
        state["latest_items"] = [item]
        digests = state.setdefault("digests", [])
        if not isinstance(digests, list):
            digests = []
            state["digests"] = digests
        digests.append({**digest, "reason": "ai_daily"})
        del digests[:-80]
        read_keys = state.setdefault("read_keys", [])
        if isinstance(read_keys, list):
            selected_key = _single_line(digest.get("selected_key"), 32)
            if selected_key and selected_key not in read_keys:
                read_keys.append(selected_key)
                del read_keys[:-80]
        result_state = {
            "status": "read",
            "last_success_date": today,
            "last_checked_at": now,
            "last_source_name": source_name,
            "last_source_author": source_author,
            "last_source_mid": _single_line(source.get("mid"), 40),
            "last_source_key": source_key,
            "last_source_schedule": _single_line(source.get("schedule"), 10),
            "last_video_bvid": bvid_key,
            "last_video_title": _single_line(item.get("title"), 120),
            "last_video_pub_ts": _safe_float(item.get("published_ts"), 0),
            "last_video_link": _single_line(item.get("video_link") or item.get("link"), 400),
            "last_video_owner_name": _single_line(item.get("video_owner_name"), 80),
            "last_video_owner_mid": _single_line(item.get("video_owner_mid"), 40),
            "last_video_tname": _single_line(item.get("video_tname"), 60),
            "last_video_duration": _safe_int(item.get("video_duration"), 0, 0),
            "last_video_context_chars": len(str(item.get("video_context_text") or "")),
            "last_video_tags": list(item.get("video_tags") or [])[:10] if isinstance(item.get("video_tags"), list) else [],
            "last_video_hot_comments": list(item.get("video_hot_comments") or [])[:5] if isinstance(item.get("video_hot_comments"), list) else [],
            "last_text_link": _single_line(item.get("article_link"), 400),
            "last_text_readable": bool(item.get("article_readable") and item.get("article_text")),
            "last_text_chars": len(str(item.get("article_text") or "")),
            "last_text_excerpt_chars": len(str(item.get("article_excerpt") or "")),
            "last_video_subtitle_readable": bool(item.get("video_subtitle_readable") and item.get("video_subtitle_text")),
            "last_video_subtitle_chars": len(str(item.get("video_subtitle_text") or "")),
            "last_video_subtitle_status": _single_line(item.get("video_subtitle_status"), 40),
            "last_read_basis": read_basis,
            "last_digest": digest,
        }
        source_state.update(result_state)
        ai_state.update({"date": today, **result_state})
        logger.info(
            "[PrivateCompanion] 已读取今日 %s: %s text_readable=%s text_chars=%s basis=%s",
            source_name,
            _single_line(item.get("title"), 120),
            bool(item.get("article_readable") and item.get("article_text")),
            len(str(item.get("article_text") or "")),
            read_basis,
        )
        return True

    async def _maybe_track_ai_daily(self, *, force: bool = False) -> None:
        if not (self.enable_news_integration and self.enable_ai_daily_watch):
            return
        ai_state = self._ai_daily_state()
        today = _today_key()
        now = _now_ts()
        now_dt = datetime.now()
        source_states = ai_state.setdefault("source_states", {})
        if not isinstance(source_states, dict):
            source_states = {}
            ai_state["source_states"] = source_states
        configured_sources = self._ai_daily_source_items()
        ai_state["sources"] = [
            {
                "key": _single_line(source.get("key") or self._ai_daily_source_key(source), 80),
                "name": _single_line(source.get("name"), 40),
                "author_name": _single_line(source.get("author_name"), 60),
                "mid": _single_line(source.get("mid"), 40),
                "schedule": _single_line(source.get("schedule"), 10),
                "keywords": [_single_line(token, 20) for token in source.get("keywords", [])[:6]],
            }
            for source in configured_sources
        ]
        due_sources = self._ai_daily_due_sources(now_dt, today, source_states, force=force)
        if not due_sources:
            now_minute = now_dt.hour * 60 + now_dt.minute
            future_sources = [
                source for source in configured_sources
                if _safe_int(source.get("schedule_minutes"), 0, 0) > now_minute
                and not (
                    isinstance(source_states.get(str(source.get("key") or "")), dict)
                    and source_states[str(source.get("key") or "")].get("last_success_date") == today
                )
            ]
            status = "waiting_schedule" if future_sources else "all_sources_done"
            if ai_state.get("date") != today or ai_state.get("status") != status:
                ai_state.update({"date": today, "status": status, "last_checked_at": ai_state.get("last_checked_at", 0)})
                self._save_data_sync()
            return
        ai_state.update({"date": today, "last_checked_at": now, "status": "checking"})
        self._save_data_sync()
        any_read = False
        for source in due_sources:
            key = str(source.get("key") or self._ai_daily_source_key(source))
            source_state = source_states.setdefault(key, {})
            if not isinstance(source_state, dict):
                source_state = {}
                source_states[key] = source_state
            source_state.update(
                {
                    "name": _single_line(source.get("name"), 40),
                    "author_name": _single_line(source.get("author_name"), 60),
                    "mid": _single_line(source.get("mid"), 40),
                    "schedule": _single_line(source.get("schedule"), 10),
                    "status": "checking",
                }
            )
            read = await self._read_ai_daily_source(
                source,
                ai_state=ai_state,
                source_state=source_state,
                today=today,
                now=now,
            )
            any_read = any_read or read
        if not any_read and ai_state.get("status") == "checking":
            ai_state["status"] = "waiting_today_video"
        self._save_data_sync()

    async def _maybe_trigger_news_boredom_read(self) -> None:
        if not (self.enable_news_integration and self.enable_news_boredom_read):
            return
        if not self._bot_currently_bored_enough_for_news():
            return
        state = self.data.setdefault("news_integration", {})
        if not isinstance(state, dict):
            self.data["news_integration"] = {}
            state = self.data["news_integration"]
        now = _now_ts()
        min_interval = max(1, self.news_min_interval_hours) * 3600
        if now - _safe_float(state.get("last_read_at"), 0) < min_interval:
            return
        if random.random() > 0.42:
            return
        await self._perform_news_reading(reason="boredom", allow_share=True, force=False)

    async def _ensure_daily_news_reading(self, *, force: bool = False) -> None:
        if not (self.enable_news_integration and self.enable_news_daily_hot_read):
            return
        state = self.data.setdefault("news_integration", {})
        if not isinstance(state, dict):
            self.data["news_integration"] = {}
            state = self.data["news_integration"]
        today = _today_key()
        if not force and state.get("last_daily_read_day") == today:
            return
        await self._perform_news_reading(reason="daily", allow_share=False, force=True)

    def _astrbot_web_search_provider_settings(self, umo: str = "") -> dict[str, Any]:
        try:
            cfg = self.context.get_config(umo=umo) if umo else self.context.get_config()
        except Exception:
            try:
                cfg = self.context.get_config()
            except Exception:
                cfg = {}
        settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
        return dict(settings or {}) if isinstance(settings, dict) else {}

    def _astrbot_web_search_available(self, umo: str = "") -> bool:
        settings = self._astrbot_web_search_provider_settings(umo)
        if not settings.get("web_search", False):
            return False
        provider = str(settings.get("websearch_provider") or "").strip()
        if provider == "tavily":
            return bool(settings.get("websearch_tavily_key"))
        if provider == "bocha":
            return bool(settings.get("websearch_bocha_key"))
        if provider == "brave":
            return bool(settings.get("websearch_brave_key"))
        if provider == "firecrawl":
            return bool(settings.get("websearch_firecrawl_key"))
        if provider == "baidu_ai_search":
            return bool(settings.get("websearch_baidu_app_builder_key"))
        return False

    def _web_search_candidate_umos(self) -> list[str]:
        candidates = [""]
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        for uid, item in users.items():
            if not isinstance(item, dict):
                continue
            if not self._is_target_private_user(str(uid), item) or not item.get("enabled", True):
                continue
            umo = str(item.get("umo") or "").strip()
            if umo and umo not in candidates:
                candidates.append(umo)
        return candidates

    def _astrbot_any_web_search_available(self) -> bool:
        return any(self._astrbot_web_search_available(umo) for umo in self._web_search_candidate_umos())

    @staticmethod
    def _normalize_web_exploration_api_base_url(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if raw.startswith(("http://", "https://")):
            return raw
        if re.match(r"^[a-z][a-z0-9+.-]*://", raw, flags=re.I):
            return ""
        local_pattern = r"^(localhost|127\.|10\.|172\.(1[6-9]|2\d|3[0-1])\.|192\.168\.|\[?::1\]?)"
        scheme = "http://" if re.match(local_pattern, raw, flags=re.I) else "https://"
        return f"{scheme}{raw}"

    def _custom_web_exploration_search_configured(self) -> bool:
        return bool(self._normalize_web_exploration_api_base_url(getattr(self, "web_exploration_api_base_url", "")))

    def _custom_web_exploration_get_url(self, base_url: str, query: str, *, topic: str = "general", model: str = "") -> str:
        cleaned_query = _single_line(query, 240)
        if not base_url or not cleaned_query:
            return ""
        max_results = str(max(1, min(20, self.web_exploration_max_results)))
        values = {
            "query": cleaned_query,
            "q": cleaned_query,
            "keyword": cleaned_query,
            "keywords": cleaned_query,
            "wd": cleaned_query,
            "text": cleaned_query,
            "topic": "news" if topic == "news" else "general",
            "max_results": max_results,
            "limit": max_results,
            "count": max_results,
            "model": str(model or "").strip(),
        }
        formatted = base_url
        replaced = False
        for key, value in values.items():
            encoded = quote(str(value), safe="")
            for token in (f"{{{{{key}}}}}", f"{{{key}}}", f"${{{key}}}"):
                if token in formatted:
                    formatted = formatted.replace(token, encoded)
                    replaced = True
        if replaced:
            return formatted
        if formatted.endswith(("?", "&")):
            return f"{formatted}query={quote(cleaned_query, safe='')}"
        if re.search(r"[?&][^=&?#]+=$", formatted):
            return f"{formatted}{quote(cleaned_query, safe='')}"
        return ""

    def _web_exploration_search_available(self) -> bool:
        return self._custom_web_exploration_search_configured() or self._astrbot_any_web_search_available()

    def _pick_available_web_search_umo(self, preferred: str = "") -> str:
        candidates = []
        preferred = str(preferred or "").strip()
        if preferred:
            candidates.append(preferred)
        for umo in self._web_search_candidate_umos():
            if umo not in candidates:
                candidates.append(umo)
        for umo in candidates:
            if self._astrbot_web_search_available(umo):
                return umo
        return ""

    def _web_search_runtime_state(self) -> dict[str, Any]:
        state = self.data.setdefault("web_search_runtime", {})
        if not isinstance(state, dict):
            self.data["web_search_runtime"] = {}
            state = self.data["web_search_runtime"]
        return state

    def _web_search_cooldown_remaining(self, provider: str) -> float:
        provider = _single_line(provider, 80) or "unknown"
        state = self.data.get("web_search_runtime") if isinstance(self.data.get("web_search_runtime"), dict) else {}
        item = state.get(provider) if isinstance(state, dict) else None
        if not isinstance(item, dict):
            return 0.0
        retry_after = _safe_float(item.get("retry_after"), 0.0, 0.0)
        return max(0.0, retry_after - _now_ts())

    def _web_search_error_cooldown_seconds(self, error: Any) -> tuple[float, str]:
        text = str(error or "")
        if "QUOTA_USER_DAILY_FREE" in text or "Daily free quota" in text:
            now_dt = datetime.now()
            tomorrow = (now_dt + timedelta(days=1)).replace(hour=0, minute=10, second=0, microsecond=0)
            return max(3600.0, min(24 * 3600.0, (tomorrow - now_dt).total_seconds())), "daily_quota"
        if "RATE_LIMIT_SEARCH_QPS" in text or "QPS" in text:
            return 10 * 60.0, "qps_limit"
        if "429" in text or "rate limit" in text.lower() or "quota" in text.lower():
            return 30 * 60.0, "rate_or_quota"
        return 0.0, ""

    def _mark_web_search_cooldown(self, provider: str, error: Any) -> None:
        seconds, reason = self._web_search_error_cooldown_seconds(error)
        if seconds <= 0:
            return
        provider = _single_line(provider, 80) or "unknown"
        state = self._web_search_runtime_state()
        state[provider] = {
            "retry_after": _now_ts() + seconds,
            "reason": reason,
            "last_error": _single_line(error, 240),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            self._save_data_sync()
        except Exception:
            pass
        logger.warning(
            "[PrivateCompanion] 网页搜索进入冷却: provider=%s reason=%s retry=%ss error=%s",
            provider,
            reason,
            int(seconds),
            _single_line(error, 180),
        )

    @staticmethod
    def _extract_custom_web_search_content(payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        if not isinstance(payload, dict):
            return ""
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            message = first.get("message") if isinstance(first, dict) else {}
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts: list[str] = []
                    for item in content:
                        if isinstance(item, dict):
                            text = item.get("text") or item.get("content")
                            if isinstance(text, str):
                                parts.append(text)
                        elif isinstance(item, str):
                            parts.append(item)
                    return "\n".join(parts)
            text = first.get("text") if isinstance(first, dict) else ""
            if isinstance(text, str):
                return text
        for key in ("answer", "content", "text", "summary"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
        return ""

    def _custom_web_search_items_from_payload(self, payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, str):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", payload.strip(), flags=re.I | re.S).strip()
            candidates = [text]
            match = re.search(r"\[.*\]", text, flags=re.S)
            if match:
                candidates.append(match.group(0))
            for candidate in candidates:
                try:
                    parsed = json.loads(candidate)
                except Exception:
                    continue
                if isinstance(parsed, list):
                    return parsed
                if isinstance(parsed, dict):
                    return self._custom_web_search_items_from_payload(parsed)
            return []
        if not isinstance(payload, dict):
            return []
        for key in ("results", "items", "search_results", "web_results", "documents", "organic"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("results", "items", "search_results", "web_results", "documents", "organic"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        content = self._extract_custom_web_search_content(payload)
        items = self._custom_web_search_items_from_payload(content)
        if items:
            return items
        parsed = self._parse_json_object(content)
        if isinstance(parsed, dict):
            return self._custom_web_search_items_from_payload(parsed)
        return []

    def _normalize_custom_web_search_results(self, payload: Any, query: str) -> list[dict[str, Any]]:
        cleaned_query = _single_line(query, 120)
        items = self._custom_web_search_items_from_payload(payload)
        results: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                text = _single_line(item, 360)
                if not text:
                    continue
                title = text[:80]
                url = ""
                snippet = text
            else:
                title = _single_line(
                    item.get("title")
                    or item.get("name")
                    or item.get("headline")
                    or item.get("source_title")
                    or item.get("site_name"),
                    140,
                )
                url = _single_line(
                    item.get("url")
                    or item.get("link")
                    or item.get("href")
                    or item.get("source_url")
                    or item.get("page_url"),
                    420,
                )
                snippet = _single_line(
                    item.get("snippet")
                    or item.get("summary")
                    or item.get("content")
                    or item.get("description")
                    or item.get("text")
                    or item.get("body"),
                    360,
                )
            if not title and not snippet:
                continue
            key = hashlib.sha1(f"custom|{cleaned_query}|{title}|{url}|{snippet}".encode("utf-8", errors="ignore")).hexdigest()[:16]
            results.append({"key": key, "title": title or snippet[:80], "url": url, "snippet": snippet, "provider": "custom_web_exploration"})
            if len(results) >= self.web_exploration_max_results:
                break
        if results:
            return results[: self.web_exploration_max_results]

        content = _single_line(self._extract_custom_web_search_content(payload), 720)
        if content:
            key = hashlib.sha1(f"custom|{cleaned_query}|{content}".encode("utf-8", errors="ignore")).hexdigest()[:16]
            return [
                {
                    "key": key,
                    "title": cleaned_query or "自定义主动搜索结果",
                    "url": "",
                    "snippet": content,
                    "provider": "custom_web_exploration",
                }
            ]
        return []

    async def _run_custom_web_exploration_search(self, query: str, *, topic: str = "general") -> list[dict[str, Any]]:
        cleaned_query = _single_line(query, 120)
        base_url = self._normalize_web_exploration_api_base_url(getattr(self, "web_exploration_api_base_url", ""))
        api_key = str(getattr(self, "web_exploration_api_key", "") or "").strip()
        model = str(getattr(self, "web_exploration_api_model", "") or "").strip()
        if not cleaned_query or not base_url:
            return []
        if not base_url.startswith(("http://", "https://")):
            self._last_web_search_error = "custom_web_exploration:invalid_url"
            return []
        cooldown = self._web_search_cooldown_remaining("custom_web_exploration")
        if cooldown > 0:
            self._last_web_search_error = f"custom_web_exploration_cooldown:{int(cooldown)}s"
            logger.info(
                "[PrivateCompanion] 自定义主动搜索冷却中,跳过请求: retry=%ss query=%s",
                int(cooldown),
                cleaned_query,
            )
            return []

        headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": f"{PLUGIN_NAME}/web-exploration",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        lower_url = base_url.lower()
        get_url = self._custom_web_exploration_get_url(base_url, cleaned_query, topic=topic, model=model)
        if get_url:
            payload = None
        elif "/chat/completions" in lower_url:
            payload: dict[str, Any] = {
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是搜索接口适配器。请基于可用联网搜索能力返回 JSON, 格式为 "
                            '{"results":[{"title":"","url":"","snippet":""}]}。'
                        ),
                    },
                    {"role": "user", "content": cleaned_query},
                ],
                "temperature": 0.2,
            }
            if model:
                payload["model"] = model
        else:
            payload = {
                "query": cleaned_query,
                "topic": "news" if topic == "news" else "general",
                "max_results": max(1, min(20, self.web_exploration_max_results)),
            }
            if model:
                payload["model"] = model
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                if get_url:
                    resp_ctx = session.get(get_url)
                else:
                    post_headers = {"Content-Type": "application/json"}
                    resp_ctx = session.post(base_url, headers=post_headers, json=payload)
                async with resp_ctx as resp:
                    body_text = await resp.text()
                    if resp.status >= 400:
                        raise RuntimeError(f"HTTP {resp.status}: {_single_line(body_text, 180)}")
                    try:
                        raw_payload: Any = json.loads(body_text)
                    except Exception:
                        raw_payload = body_text
        except asyncio.TimeoutError as exc:
            detail = "请求超时"
            self._last_web_search_error = f"custom_web_exploration:{detail}"
            self._mark_web_search_cooldown("custom_web_exploration", detail)
            logger.warning("[PrivateCompanion] 自定义主动搜索失败: query=%s err=%s", cleaned_query, detail)
            return []
        except Exception as exc:
            detail = _single_line(exc, 220) or exc.__class__.__name__
            self._last_web_search_error = f"custom_web_exploration:{detail}"
            self._mark_web_search_cooldown("custom_web_exploration", exc)
            logger.warning("[PrivateCompanion] 自定义主动搜索失败: query=%s err=%s", cleaned_query, exc)
            return []

        results = self._normalize_custom_web_search_results(raw_payload, cleaned_query)
        if not results:
            self._last_web_search_error = "custom_web_exploration:no_usable_results"
        return results

    async def _run_astrbot_web_search(self, query: str, *, umo: str = "", topic: str = "general", usage: str = "general") -> list[dict[str, Any]]:
        cleaned_query = _single_line(query, 120)
        self._last_web_search_error = ""
        if not cleaned_query:
            return []
        if usage == "web_exploration" and self._custom_web_exploration_search_configured():
            return await self._run_custom_web_exploration_search(cleaned_query, topic=topic)
        settings = self._astrbot_web_search_provider_settings(umo)
        if not settings.get("web_search", False):
            return []
        provider = str(settings.get("websearch_provider") or "").strip()
        if provider == "default":
            return []
        cooldown = self._web_search_cooldown_remaining(provider)
        if cooldown > 0:
            self._last_web_search_error = f"web_search_cooldown:{provider}:{int(cooldown)}s"
            logger.info(
                "[PrivateCompanion] AstrBot 网页搜索冷却中,跳过请求: provider=%s retry=%ss query=%s",
                provider,
                int(cooldown),
                cleaned_query,
            )
            return []
        for key in (
            "websearch_tavily_key",
            "websearch_bocha_key",
            "websearch_brave_key",
            "websearch_firecrawl_key",
        ):
            value = settings.get(key)
            if isinstance(value, str):
                value = value.strip()
                settings[key] = [value] if value else []
        baidu_key = settings.get("websearch_baidu_app_builder_key")
        if isinstance(baidu_key, list):
            settings["websearch_baidu_app_builder_key"] = str(baidu_key[0] if baidu_key else "").strip()
        elif isinstance(baidu_key, str):
            settings["websearch_baidu_app_builder_key"] = baidu_key.strip()
        try:
            from astrbot.core.tools import web_search_tools as ws
            if provider == "tavily":
                payload = {
                    "query": cleaned_query,
                    "max_results": max(5, min(20, self.web_exploration_max_results)),
                    "include_favicon": True,
                    "search_depth": "basic",
                    "topic": "news" if topic == "news" else "general",
                }
                if topic == "news":
                    payload["days"] = 7
                raw_results = await ws._tavily_search(settings, payload)
            elif provider == "bocha":
                raw_results = await ws._bocha_search(
                    settings,
                    {
                        "query": cleaned_query,
                        "count": max(1, min(50, self.web_exploration_max_results)),
                        "summary": True,
                        "freshness": "noLimit",
                    },
                )
            elif provider == "brave":
                raw_results = await ws._brave_search(
                    settings,
                    {
                        "q": cleaned_query,
                        "count": max(1, min(20, self.web_exploration_max_results)),
                        "country": "CN",
                        "search_lang": "zh-hans",
                    },
                )
            elif provider == "firecrawl":
                raw_results = await ws._firecrawl_search(
                    settings,
                    {"query": cleaned_query, "limit": max(1, min(20, self.web_exploration_max_results)), "sources": ["web"]},
                )
            elif provider == "baidu_ai_search":
                raw_results = await ws._baidu_search(
                    settings,
                    {
                        "messages": [{"role": "user", "content": cleaned_query[:72]}],
                        "search_source": "baidu_search_v2",
                        "resource_type_filter": [{"type": "web", "top_k": max(1, min(50, self.web_exploration_max_results))}],
                    },
                )
            else:
                return []
        except Exception as exc:
            self._last_web_search_error = _single_line(str(exc), 240)
            self._mark_web_search_cooldown(provider, exc)
            logger.warning("[PrivateCompanion] AstrBot 网页搜索失败: provider=%s query=%s err=%s", provider, cleaned_query, exc)
            return []
        results: list[dict[str, Any]] = []
        for item in raw_results or []:
            title = _single_line(getattr(item, "title", "") if not isinstance(item, dict) else item.get("title"), 140)
            url = _single_line(getattr(item, "url", "") if not isinstance(item, dict) else item.get("url"), 420)
            snippet = _single_line(getattr(item, "snippet", "") if not isinstance(item, dict) else item.get("snippet"), 360)
            if not title and not snippet:
                continue
            key = hashlib.sha1(f"{title}|{url}|{snippet}".encode("utf-8", errors="ignore")).hexdigest()[:16]
            results.append({"key": key, "title": title, "url": url, "snippet": snippet, "provider": provider})
        return results[: self.web_exploration_max_results]

    def _web_exploration_recent_context(self) -> str:
        state = self.data.get("daily_state", {})
        mood = _single_line(state.get("mood_bias") if isinstance(state, dict) else "", 30)
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        activity = _single_line((current_item or {}).get("activity"), 80)
        recent_user = ""
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        latest_user = max(
            (item for item in users.values() if isinstance(item, dict)),
            key=lambda item: _safe_float(item.get("last_seen"), 0),
            default={},
        )
        if isinstance(latest_user, dict):
            recent_user = _single_line(latest_user.get("last_user_message"), 120)
        news_state = self.data.get("news_integration") if isinstance(self.data.get("news_integration"), dict) else {}
        news_digest = news_state.get("last_digest") if isinstance(news_state.get("last_digest"), dict) else {}
        news_topic = _single_line(news_digest.get("topic") or news_digest.get("headline"), 100)
        return "\n".join(
            part
            for part in (
                f"当前状态：{mood or '平稳'}，能量 {energy}/100",
                f"当前日程：{activity}" if activity else "",
                f"最近用户提到：{recent_user}" if recent_user else "",
                f"今日新闻见闻：{news_topic}" if news_topic else "",
                f"兴趣倾向配置：{_single_line(self.web_exploration_interests, 240)}",
            )
            if part
        )

    def _hot_trend_source_names(self) -> list[str]:
        raw = str(self.news_hot_sources or "")
        names = []
        for item in re.split(r"[,，\n]+", raw):
            name = item.strip().lower()
            if name in {"weibo", "微博", "微博热搜"}:
                name = "weibo"
            elif name in {"hn", "hackernews", "hacker news"}:
                name = "hackernews"
            else:
                continue
            if name not in names:
                names.append(name)
        return names or ["weibo", "hackernews"]

    def _hot_trend_key(self, item: dict[str, Any]) -> str:
        raw = "|".join(
            _single_line(item.get(key), 240)
            for key in ("source", "title", "url")
            if _single_line(item.get(key), 240)
        )
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16] if raw else ""

    async def _fetch_weibo_hot_trends(self) -> list[dict[str, Any]]:
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=12)
            headers = {
                "User-Agent": f"{PLUGIN_NAME}/hot-trends",
                "Accept": "application/json",
                "Referer": "https://weibo.com/",
            }
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get("https://weibo.com/ajax/side/hotSearch") as resp:
                    if resp.status >= 400:
                        return []
                    data = await resp.json(content_type=None)
        except Exception as exc:
            logger.debug("[PrivateCompanion] 微博热搜抓取失败: %s", exc)
            return []
        rows = (((data or {}).get("data") or {}).get("realtime") or []) if isinstance(data, dict) else []
        items: list[dict[str, Any]] = []
        for index, row in enumerate(rows[: max(1, self.news_hot_max_items)], 1):
            if not isinstance(row, dict):
                continue
            topic = _single_line(row.get("note") or row.get("word"), 80)
            if not topic:
                continue
            score = _safe_float(row.get("num") or row.get("raw_hot"), 0)
            item = {
                "source": "weibo",
                "title": topic,
                "snippet": f"微博热搜第 {index} 位" + (f"，热度 {int(score)}" if score > 0 else ""),
                "url": f"https://s.weibo.com/weibo?q={quote('#' + topic + '#')}",
                "score": score or max(1, self.news_hot_max_items - index + 1),
                "rank": index,
                "published": "",
                "published_ts": 0,
                "created_ts": _now_ts(),
            }
            item["summary"] = item["snippet"]
            item["link"] = item["url"]
            item["key"] = self._hot_trend_key(item)
            items.append(item)
        return items

    async def _fetch_hackernews_hot_trends(self) -> list[dict[str, Any]]:
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": f"{PLUGIN_NAME}/hot-trends"}) as session:
                async with session.get(
                    "https://hn.algolia.com/api/v1/search",
                    params={"tags": "front_page", "hitsPerPage": max(3, min(30, self.news_hot_max_items))},
                ) as resp:
                    if resp.status >= 400:
                        return []
                    data = await resp.json(content_type=None)
        except Exception as exc:
            logger.debug("[PrivateCompanion] Hacker News 热点抓取失败: %s", exc)
            return []
        hits = data.get("hits") if isinstance(data, dict) else []
        items: list[dict[str, Any]] = []
        for index, row in enumerate((hits or [])[: max(1, self.news_hot_max_items)], 1):
            if not isinstance(row, dict):
                continue
            title = _single_line(row.get("title") or row.get("story_title"), 120)
            if not title:
                continue
            object_id = _single_line(row.get("objectID"), 40)
            url = _single_line(row.get("url"), 420) or (f"https://news.ycombinator.com/item?id={object_id}" if object_id else "")
            points = _safe_float(row.get("points"), 0)
            comments = _safe_int(row.get("num_comments"), 0, 0, 999999)
            item = {
                "source": "hackernews",
                "title": title,
                "snippet": f"Hacker News 首页热点，{int(points)} points，{comments} comments",
                "url": url,
                "score": points + comments * 0.35,
                "rank": index,
                "published": _single_line(row.get("created_at"), 80),
                "published_ts": self._news_parse_time(str(row.get("created_at") or "")),
                "created_ts": _now_ts(),
            }
            item["summary"] = item["snippet"]
            item["link"] = item["url"]
            item["key"] = self._hot_trend_key(item)
            items.append(item)
        return items

    async def _fetch_hot_trend_candidates(self) -> list[dict[str, Any]]:
        if not self.enable_news_daily_hot_read:
            return []
        tasks = []
        for source in self._hot_trend_source_names():
            if source == "weibo":
                tasks.append(self._fetch_weibo_hot_trends())
            elif source == "hackernews":
                tasks.append(self._fetch_hackernews_hot_trends())
        if not tasks:
            return []
        batches = await asyncio.gather(*tasks, return_exceptions=True)
        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        for batch in batches:
            if not isinstance(batch, list):
                continue
            for item in batch:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or "")
                if not key or key in seen:
                    continue
                seen.add(key)
                items.append(item)
        items.sort(key=lambda item: (_safe_float(item.get("score"), 0), -_safe_float(item.get("rank"), 999)), reverse=True)
        return items[: max(3, min(30, self.news_hot_max_items))]

    def _format_hot_trend_candidates_for_prompt(self, hot_items: list[dict[str, Any]]) -> str:
        lines = []
        for idx, item in enumerate(hot_items[:10], 1):
            lines.append(
                f"{idx}. [{_single_line(item.get('source'), 20)}] {_single_line(item.get('title'), 80)}"
                + (f"｜{_single_line(item.get('snippet'), 120)}" if _single_line(item.get("snippet"), 120) else "")
            )
        return "\n".join(lines)

    async def _choose_web_exploration_query(self, hot_items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        provider_id = self._task_provider(self.web_exploration_provider_id, self.news_provider_id, self.llm_provider_id)
        fallback_topics = [
            "今天有什么有趣的新鲜事",
            "最近流行的网络梗",
            "最近值得了解的科技新闻",
            "适合睡前看的冷知识",
            "最近有什么有意思的游戏或动画消息",
        ]
        hot_items = hot_items or []
        if not provider_id:
            if hot_items:
                hot = random.choice(hot_items[: min(5, len(hot_items))])
                query = _single_line(hot.get("title"), 50) or random.choice(fallback_topics)
                return {"query": query, "reason": f"从{_single_line(hot.get('source'), 20)}热点里挑了一个想了解的话题", "topic": "news"}
            return {"query": random.choice(fallback_topics), "reason": "无模型时随机探索", "topic": "general"}
        hot_context = self._format_hot_trend_candidates_for_prompt(hot_items)
        prompt = f"""
请作为 Bot 自己,决定这会儿想上网搜索了解什么。

要求：
1. 选题要符合当前人格、状态、日程或最近聊天,可以是新闻、作品、技术、生活知识、流行梗、兴趣爱好、新鲜事物。
2. 不要总是搜新闻；也不要总是围着用户转。Bot 可以有自己的好奇心。
3. 如果热点候选里有符合 Bot 兴趣的内容,可以围绕它继续搜索；不合适也可以完全不选。
4. 搜索词要具体,适合直接丢给网页搜索。
5. 输出 JSON：query, reason, topic。topic 只能是 general 或 news。
6. query 40字以内；reason 80字以内。

【Bot 名称】
{self.bot_name}

【人格】
{self._get_default_persona_prompt()}

【当前上下文】
{self._web_exploration_recent_context()}

【公开热点候选】
{hot_context or "暂无可用热点候选"}
""".strip()
        raw = await self._llm_call(prompt, max_tokens=220, provider_id=provider_id, task="web_exploration_query")
        parsed = self._parse_json_object(raw)
        if not isinstance(parsed, dict):
            query = random.choice(fallback_topics)
            return {"query": query, "reason": "模型选题失败后随机探索", "topic": "general"}
        topic = str(parsed.get("topic") or "general").strip().lower()
        if topic not in {"general", "news"}:
            topic = "general"
        query = _single_line(parsed.get("query"), 60) or random.choice(fallback_topics)
        return {"query": query, "reason": _single_line(parsed.get("reason"), 120), "topic": topic}

    async def _summarize_web_exploration(self, query_info: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
        if not results:
            return {}

        def fallback_digest() -> dict[str, Any]:
            first = results[0] if results else {}
            note = fallback_note() or _single_line(first.get("snippet"), 260) or _single_line(first.get("title"), 120)
            return {
                "query": _single_line(query_info.get("query"), 80),
                "topic": _single_line(first.get("title"), 80) or _single_line(query_info.get("query"), 80) or "主动搜索",
                "note": note or "这次搜索拿到了结果,但还没整理出清晰笔记。",
                "source_title": _single_line(first.get("title"), 120),
                "source_url": _single_line(first.get("url"), 420),
                "reason": _single_line(query_info.get("reason"), 120),
                "possible_share": False,
                "results": results[:6],
                "created_ts": _now_ts(),
                "fallback": True,
            }

        def fallback_note() -> str:
            parts = []
            for item in results[:3]:
                title = _single_line(item.get("title"), 90)
                snippet = _single_line(item.get("snippet"), 160)
                if title and snippet:
                    parts.append(f"{title}：{snippet}")
                elif title:
                    parts.append(title)
                elif snippet:
                    parts.append(snippet)
            return _single_line("；".join(parts), 360)

        provider_id = self._task_provider(self.web_exploration_provider_id, self.news_provider_id, self.llm_provider_id)
        if not provider_id:
            first = results[0]
            return {
                "query": _single_line(query_info.get("query"), 80),
                "topic": _single_line(first.get("title"), 80),
                "note": _single_line(first.get("snippet"), 220) or fallback_note(),
                "source_title": _single_line(first.get("title"), 120),
                "source_url": _single_line(first.get("url"), 420),
                "reason": _single_line(query_info.get("reason"), 120),
                "results": results[:6],
                "created_ts": _now_ts(),
            }
        lines = []
        for idx, item in enumerate(results[:8], 1):
            lines.append(
                f"{idx}. {_single_line(item.get('title'), 120)}"
                + (f"｜{_single_line(item.get('snippet'), 180)}" if _single_line(item.get("snippet"), 180) else "")
                + (f"｜{_single_line(item.get('url'), 160)}" if _single_line(item.get("url"), 160) else "")
            )
        prompt = f"""
请把 Bot 这次自主网页探索整理成一条内部探索笔记。

要求：
1. 像 Bot 自己刚了解完后的留痕,不是给用户的正式回答。
2. 不要编造搜索结果外的事实；不确定就写成“看起来/大概/还想再查”。
3. 输出 JSON：topic, note, source_index, possible_share。
4. topic 40字以内；note 180字以内；source_index 是 1 到 {min(8, len(results))}；possible_share 是布尔值。

搜索动机：{_single_line(query_info.get('reason'), 120)}
搜索词：{_single_line(query_info.get('query'), 80)}

结果：
{chr(10).join(lines)}
""".strip()
        raw = await self._llm_call(prompt, max_tokens=280, provider_id=provider_id, task="web_exploration_digest")
        parsed = self._parse_json_object(raw)
        if not isinstance(parsed, dict):
            return fallback_digest()
        source_index = _safe_int(parsed.get("source_index"), 1, 1, len(results[:8])) - 1
        source = results[source_index] if 0 <= source_index < len(results) else results[0]
        note = (
            _single_line(parsed.get("note") or parsed.get("summary") or parsed.get("impression") or parsed.get("content"), 360)
            or _single_line(source.get("snippet"), 260)
            or fallback_note()
        )
        return {
            "query": _single_line(query_info.get("query"), 80),
            "topic": _single_line(parsed.get("topic"), 80) or _single_line(source.get("title"), 80),
            "note": note,
            "source_title": _single_line(source.get("title"), 120),
            "source_url": _single_line(source.get("url"), 420),
            "reason": _single_line(query_info.get("reason"), 120),
            "possible_share": bool(parsed.get("possible_share", True)),
            "results": results[:6],
            "created_ts": _now_ts(),
        }

    async def _maybe_trigger_web_exploration(self) -> None:
        if not (self.enable_web_exploration and self.enable_web_exploration_boredom_search):
            return
        state = self.data.setdefault("web_exploration", {})
        if not isinstance(state, dict):
            self.data["web_exploration"] = {}
            state = self.data["web_exploration"]
        now = _now_ts()
        min_interval = max(1, self.web_exploration_min_interval_hours) * 3600
        if now - _safe_float(state.get("last_explore_at"), 0) < min_interval:
            return
        if now - _safe_float(state.get("last_probe_at"), 0) < 45 * 60:
            return
        if not self._bot_currently_bored_enough_for_news():
            return
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        target_users = [
            (str(uid), item)
            for uid, item in users.items()
            if isinstance(item, dict) and self._is_target_private_user(str(uid), item) and item.get("enabled", True) and item.get("umo")
        ]
        target_user = random.choice(target_users)[1] if target_users else {}
        target_umo = str((target_user.get("umo") if isinstance(target_user, dict) else "") or "")
        use_custom_search = self._custom_web_exploration_search_configured()
        search_umo = "" if use_custom_search else self._pick_available_web_search_umo(target_umo)
        if not (use_custom_search or search_umo):
            state["last_probe_at"] = now
            state["last_status"] = "web_search_disabled_or_unconfigured"
            self._save_data_sync()
            return
        if random.random() > 0.46:
            return
        state["last_probe_at"] = now
        query_info = await self._choose_web_exploration_query()
        results: list[dict[str, Any]] = []
        results = await self._run_astrbot_web_search(
            str(query_info.get("query") or ""),
            umo=search_umo,
            topic=str(query_info.get("topic") or "general"),
            usage="web_exploration",
        )
        if not results:
            error_text = _single_line(getattr(self, "_last_web_search_error", ""), 240)
            state["last_status"] = "search_failed" if error_text else "no_results"
            state["last_query"] = query_info
            state["last_explore_at"] = now
            state["last_digest"] = {
                "query": _single_line(query_info.get("query"), 80),
                "topic": _single_line(query_info.get("query"), 80) or "主动搜索",
                "note": f"搜索调用失败：{error_text}" if error_text else "这次搜索没有拿到可用结果。",
                "reason": _single_line(query_info.get("reason"), 120),
                "results": [],
                "created_ts": now,
                "no_results": not bool(error_text),
                "search_failed": bool(error_text),
            }
            self._save_data_sync()
            return
        digest = await self._summarize_web_exploration(query_info, results)
        notes = state.setdefault("notes", [])
        if not isinstance(notes, list):
            notes = []
            state["notes"] = notes
        wish = await self._build_external_event_wish(digest, source_type="web_exploration")
        if wish:
            digest["self_link"] = wish
        notes.append(digest)
        state["last_explore_at"] = now
        state["last_status"] = "explored"
        state["last_query"] = query_info
        state["last_digest"] = digest
        state["latest_results"] = results[:8]
        self._queue_web_exploration_impulses(target_users, digest, wish, now=now)
        if digest.get("possible_share") and target_users:
            random.shuffle(target_users)
            for user_id, user in target_users[:3]:
                if now - _safe_float(user.get("last_seen"), 0) < max(self.idle_minutes, 90) * 60:
                    continue
                if now - _safe_float(user.get("last_web_exploration_share_at"), 0) < 10 * 3600:
                    continue
                if isinstance(wish, dict) and wish:
                    if now - _safe_float(user.get("last_external_event_self_link_at"), 0) < self.external_event_self_link_cooldown_hours * 3600:
                        continue
                    if not wish.get("should_share"):
                        continue
                    decision = self._external_event_share_decision(
                        user,
                        digest,
                        source_type="web_exploration",
                        wish=wish,
                        base_probability=self.web_exploration_share_probability,
                        now=now,
                    )
                    if decision.get("duplicate") or not decision.get("should_share"):
                        continue
                    share_probability = max(self.web_exploration_share_probability, _safe_float(decision.get("probability"), 0.0))
                    share_probability *= self.external_event_self_link_probability
                else:
                    decision = self._external_event_share_decision(
                        user,
                        digest,
                        source_type="web_exploration",
                        wish={"relevance": 4, "desire": 4, "should_share": True},
                        base_probability=self.web_exploration_share_probability,
                        now=now,
                    )
                    if decision.get("duplicate") or not decision.get("should_share"):
                        continue
                    share_probability = self.web_exploration_share_probability
                if random.random() > max(0.0, min(1.0, share_probability)):
                    continue
                self_link_motive = _single_line(wish.get("motive") if isinstance(wish, dict) else "", 180)
                self_link_tone = _single_line(wish.get("tone") if isinstance(wish, dict) else "", 60)
                self_link_boundary = _single_line(wish.get("boundary") if isinstance(wish, dict) else "", 140)
                accepted = self._offer_proactive_candidate(
                    user_id,
                    user,
                    {
                        "source": "web_exploration",
                        "reason": "web_exploration_share",
                        "action": "message",
                        "scheduled_ts": now + random.randint(12, 70) * 60,
                        "topic": _single_line(digest.get("topic"), 48) or "刚查到的新东西",
                        "score": max(4 if self_link_motive else 3, _safe_int(decision.get("score"), 0, 0, 100)),
                        "motive": self_link_motive or "刚自己上网查了点新东西,有一点想按自己的语气轻轻告诉用户",
                        "context_key": "web_exploration_context",
                        "context": {
                            **digest,
                            "share_tone": self_link_tone,
                            "share_boundary": self_link_boundary,
                            "share_decision": decision,
                        },
                    },
                )
                if accepted:
                    user["web_exploration_context"] = {
                        **digest,
                        "share_tone": self_link_tone,
                        "share_boundary": self_link_boundary,
                        "share_decision": decision,
                    }
                    user["last_web_exploration_share_at"] = now
                    if isinstance(wish, dict) and wish:
                        user["last_external_event_self_link_at"] = now
                    self._remember_external_event(digest, source_type="web_exploration", reason="web_exploration_share")
                    break
        self._save_data_sync()
        logger.info("[PrivateCompanion] 已完成一次网页探索: %s", _single_line(digest.get("topic"), 80))

    def _queue_web_exploration_impulses(
        self,
        target_users: list[tuple[str, dict[str, Any]]],
        digest: dict[str, Any],
        wish: dict[str, Any] | None,
        *,
        now: float,
    ) -> None:
        if not isinstance(digest, dict) or not digest.get("possible_share"):
            return
        if not target_users:
            return
        topic = _single_line(digest.get("topic") or digest.get("query"), 80)
        note = _single_line(digest.get("note"), 180)
        if not topic and not note:
            return
        self_link_motive = _single_line(wish.get("motive") if isinstance(wish, dict) else "", 180)
        self_link_tone = _single_line(wish.get("tone") if isinstance(wish, dict) else "", 60)
        self_link_boundary = _single_line(wish.get("boundary") if isinstance(wish, dict) else "", 140)
        context = {
            **digest,
            "share_tone": self_link_tone,
            "share_boundary": self_link_boundary,
            "queued_as_impulse": True,
        }
        motive = self_link_motive or "刚自己上网查了点新东西,不是急着汇报,但后面如果有合适空档可以轻轻提一句"
        shuffled = list(target_users)
        random.shuffle(shuffled)
        queued = 0
        for user_id, user in shuffled:
            if not isinstance(user, dict):
                continue
            if not self._user_enabled_for_proactive(str(user_id), user):
                continue
            if now - _safe_float(user.get("last_web_exploration_impulse_at"), 0) < 6 * 3600:
                continue
            if now - _safe_float(user.get("last_seen"), 0) < max(self.idle_minutes, 60) * 60:
                continue
            start_at = now + random.randint(25, 120) * 60
            impulse = self._build_proactive_impulse(
                user,
                reason="web_exploration_share",
                action="message",
                motive=motive,
                topic=topic or "刚查到的新东西",
                source="web_exploration",
                window_start_at=start_at,
                preferred_ts=start_at + random.randint(0, 45) * 60,
                best_until_at=start_at + 4 * 3600,
                expire_at=start_at + 10 * 3600,
                context_key="web_exploration_context",
                context=context,
            )
            impulse["salience"] = max(_safe_float(impulse.get("salience"), 0.0), 0.68)
            impulse["warmth"] = max(_safe_float(impulse.get("warmth"), 0.0), 0.54)
            impulse["urgency"] = min(_safe_float(impulse.get("urgency"), 0.0), 0.34)
            self._queue_proactive_impulse(user, impulse)
            user["last_web_exploration_impulse_at"] = now
            queued += 1
            if queued >= 3:
                break
        if queued:
            logger.info("[PrivateCompanion] 主动搜索已转入后续主动念头: topic=%s users=%s", topic, queued)

