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
from .conversation_prompt_section import (
    PromptRenderMode,
    prompt_section,
    render_prompt_sections,
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
from .logging_util import get_module_logger

logger = get_module_logger(__name__)

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



class _news_explorationHostRef:
    """延迟引用宿主 news_exploration 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import news_exploration as _host_module

        return getattr(_host_module, name)


_news_exploration_host = _news_explorationHostRef()
