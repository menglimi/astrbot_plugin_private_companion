# -*- coding: utf-8 -*-
"""
GroupWakeupMixin — 从 main.py 重新拆分出的群聊唤醒
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

class GroupWakeupMixin:
    """群聊唤醒"""

    def _group_message_addresses_bot(self, event: AstrMessageEvent, text: str) -> bool:
        if getattr(event, "is_at_or_wake_command", False) or getattr(event, "is_wake", False):
            return True
        signals = self._event_scene_signals(event)
        at_targets = signals.get("at_targets") if isinstance(signals.get("at_targets"), list) else []
        if any(isinstance(item, dict) and item.get("is_bot") for item in at_targets):
            return True
        if any(isinstance(item, dict) and str(item.get("user_id") or "").strip() and not item.get("is_bot") for item in at_targets):
            return False
        cleaned = str(text or "")
        if self.bot_name and self.bot_name in cleaned:
            return True
        return False

    def _group_message_explicitly_ats_bot(self, event: AstrMessageEvent) -> bool:
        signals = self._event_scene_signals(event)
        at_targets = signals.get("at_targets") if isinstance(signals.get("at_targets"), list) else []
        if any(isinstance(item, dict) and item.get("is_bot") for item in at_targets):
            return True
        self_id = str(signals.get("self_id") or "").strip()
        raw_text = str(getattr(event, "message_str", "") or "")
        if self_id and re.search(rf"\[CQ:at,qq={re.escape(self_id)}(?:,|\])", raw_text):
            return True
        if self_id:
            return False
        has_at = any(isinstance(item, dict) and str(item.get("user_id") or "").strip() for item in at_targets)
        if not has_at and re.search(r"\[CQ:at,qq=\d+", raw_text):
            has_at = True
        return bool(has_at and getattr(event, "is_at_or_wake_command", False))

    @staticmethod
    def _text_contains_wakeup_word(text: str, word: str) -> bool:
        cleaned = _single_line(text, 260)
        token = _single_line(word, 60)
        if not cleaned or not token:
            return False
        if re.fullmatch(r"[A-Za-z0-9_\-]{2,}", token):
            return bool(re.search(rf"(?<![A-Za-z0-9_\-]){re.escape(token)}(?![A-Za-z0-9_\-])", cleaned, re.IGNORECASE))
        return token in cleaned

    def _configured_group_direct_wakeup_words(self) -> list[str]:
        words = list(self.group_wakeup_direct_words or [])
        bot_name = _single_line(self.bot_name, 40)
        if bot_name and bot_name not in words:
            words.insert(0, bot_name)
        return list(dict.fromkeys(word for word in words if _single_line(word, 60)))

    def _generated_group_interest_keywords(self, group: dict[str, Any] | None = None) -> list[str]:
        words: list[str] = []

        def add(value: Any) -> None:
            text = _single_line(value, 32)
            if not text or len(text) < 2:
                return
            if text.isdigit():
                return
            if text in {"暂无", "未知", "平稳", "群聊", "用户", "大家", "有人", "什么", "怎么"}:
                return
            words.append(text)

        for item in self._parse_text_list_config(self.web_exploration_interests, limit=40):
            add(item)
        skill_state = self.data.get("skill_growth") if isinstance(getattr(self, "data", None), dict) else {}
        skills = skill_state.get("skills") if isinstance(skill_state, dict) and isinstance(skill_state.get("skills"), dict) else {}
        for item in list(skills.values())[:16]:
            if isinstance(item, dict):
                add(item.get("name"))
        if isinstance(group, dict):
            slang = group.get("slang_terms") if isinstance(group.get("slang_terms"), list) else []
            for item in slang[:20]:
                if isinstance(item, dict) and _safe_int(item.get("count"), 0, 0) >= 2:
                    add(item.get("term"))
            threads = group.get("topic_threads") if isinstance(group.get("topic_threads"), list) else []
            for item in threads[:12]:
                if isinstance(item, dict):
                    add(item.get("topic") or item.get("title"))
        cleaned: list[str] = []
        seen: set[str] = set()
        for word in words:
            key = word.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(word)
            if len(cleaned) >= self.group_wakeup_generated_keyword_limit:
                break
        return cleaned

    def _group_wakeup_interest_words(self, group: dict[str, Any] | None = None) -> list[str]:
        manual = list(self.group_wakeup_interest_keywords or [])
        generated = self._generated_group_interest_keywords(group)
        values: list[str] = []
        seen: set[str] = set()
        for word in manual + generated:
            word = _single_line(word, 40)
            key = word.lower()
            if not word or key in seen:
                continue
            seen.add(key)
            values.append(word)
        return values[: max(1, self.group_wakeup_generated_keyword_limit + len(manual))]

    def _group_scene_short_interjection(self, text: Any) -> bool:
        cleaned = _single_line(text, 80)
        compact = re.sub(r"\s+", "", cleaned)
        if not compact:
            return True
        if re.fullmatch(r"(?:\[图片\]|\[表情\]|\[动画表情\]|\[语音\]|\[视频\]|图片|表情|草|艹|6+|w+|哈{1,6}|笑死|确实|雀食|对|嗯|啊|哦|好|好耶|离谱|绷|急|乐|？|\?|!|！|。|…|~|～){1,6}", compact, flags=re.I):
            return True
        if len(compact) <= 8 and not re.search(r"(你|妳|bot|Bot|吗|呢|啥|什么|怎么|为什么|咋|然后|觉得|对吧|是吧|咋办|怎么办|帮|求)", compact):
            return True
        return False

    def _group_implicit_reply_score(self, text: Any, *, matched_word: str = "", relation_hit: bool = False, mentions_bot: bool = False) -> int:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return 0
        score = 0
        if mentions_bot:
            score += 70
        if matched_word:
            score += 30
        if relation_hit:
            score += 22
        if re.search(r"(你|妳|bot|Bot|机器人).{0,12}(觉得|看|说|怎么说|咋看|会不会|能不能|要不要|帮|解释|评价)", cleaned):
            score += 42
        if re.search(r"(你觉得|你看|你说|你来|问你|那你|所以你|按你说)", cleaned):
            score += 40
        if re.search(r"(然后呢|然后嘞|后来呢|接着呢|所以呢|咋办|怎么办|怎么说|怎么看|咋看)", cleaned):
            score += 32
        if re.search(r"(对吧|是吧|对不对|是不是|可以吧|行吧|没错吧)[。！？!?~～]*$", cleaned):
            score += 28
        if re.search(r"(吗|嘛|呢|？|\?)", cleaned):
            score += 20
        if re.search(r"(帮|求|救|解释|回答|评价|推荐|看看|分析|判断)", cleaned):
            score += 18
        if len(cleaned) <= 40 and (matched_word or mentions_bot):
            score += 18
        return score

    def _group_wakeup_context_should_reply(
        self,
        group: dict[str, Any],
        *,
        scene: dict[str, Any],
        sender_id: str,
        sender_name: str,
        text: str,
        matched_word: str,
    ) -> bool:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        if re.search(r"(别回|不要回|不用回|不是叫你|不是问你|别理|不要理)", cleaned):
            return False
        if str(scene.get("talking_to") or "") not in {"group", "bot"}:
            return False
        if str(scene.get("trigger") or "") in {"at_other", "reply_other"}:
            return False
        relation_hit = bool(self._select_worldbook_member_profiles_for_group(group, sender_id=sender_id, text=cleaned))
        mentions_bot = any(self._text_contains_wakeup_word(cleaned, word) for word in self._configured_group_direct_wakeup_words())
        score = self._group_implicit_reply_score(
            cleaned,
            matched_word=matched_word,
            relation_hit=relation_hit,
            mentions_bot=mentions_bot,
        )
        threshold = 45 if str(scene.get("talking_to") or "") == "bot" else 58
        return score >= threshold

    def _group_wakeup_fatigue(self, group: dict[str, Any]) -> dict[str, Any]:
        raw = group.get("group_wakeup_fatigue") if isinstance(group.get("group_wakeup_fatigue"), dict) else {}
        now = _now_ts()
        value = _safe_float(raw.get("value"), 0.0, 0.0)
        last_ts = _safe_float(raw.get("updated_ts"), 0.0, 0.0)
        decay_minutes = max(5, _safe_int(getattr(self, "group_wakeup_fatigue_decay_minutes", 90), 90, 5))
        if last_ts > 0 and now > last_ts:
            value = max(0.0, value - ((now - last_ts) / max(1.0, decay_minutes * 60.0)))
        limit = max(1.0, float(getattr(self, "group_wakeup_fatigue_limit", 5) or 5))
        ratio = value / limit
        if ratio >= 1.0:
            level = "high"
            label = "疲劳高"
        elif ratio >= 0.55:
            level = "medium"
            label = "有点累"
        elif value >= 0.4:
            level = "low"
            label = "轻微"
        else:
            level = "none"
            label = "无"
        return {
            "value": round(value, 2),
            "limit": int(limit),
            "ratio": round(min(1.0, max(0.0, ratio)), 3),
            "level": level,
            "label": label,
            "updated_ts": now,
        }

    def _bump_group_wakeup_fatigue(self, group: dict[str, Any], wakeup_type: str) -> dict[str, Any]:
        fatigue = self._group_wakeup_fatigue(group)
        weight = 0.6 if wakeup_type == "interest" else (1.2 if wakeup_type == "direct_word" else 1.0)
        limit = max(1.0, float(fatigue.get("limit") or getattr(self, "group_wakeup_fatigue_limit", 5) or 5))
        value = min(limit + 2.0, _safe_float(fatigue.get("value"), 0.0, 0.0) + weight)
        group["group_wakeup_fatigue"] = {
            "value": round(value, 2),
            "limit": int(limit),
            "updated_ts": _now_ts(),
            "last_type": _single_line(wakeup_type, 40),
        }
        return self._group_wakeup_fatigue(group)

    def _group_wakeup_strength(self, wakeup_type: str, group: dict[str, Any], scene: dict[str, Any]) -> str:
        fatigue = self._group_wakeup_fatigue(group)
        level = str(fatigue.get("level") or "none")
        state = self.data.get("daily_state") if isinstance(getattr(self, "data", None), dict) else {}
        runtime = state.get("sleep_runtime") if isinstance(state, dict) and isinstance(state.get("sleep_runtime"), dict) else {}
        phase = str(runtime.get("phase") or "")
        if phase in {"falling_asleep", "light_sleep", "sleeping_again", "woken"}:
            return "interrupt" if wakeup_type in {"direct_word", "context_word"} else "light"
        if level == "high" and wakeup_type != "direct_word":
            return "light"
        if wakeup_type == "direct_word":
            return "strong"
        if wakeup_type == "context_word":
            return "normal"
        if wakeup_type == "question":
            return "normal"
        if wakeup_type == "cold_group":
            return "light"
        return "normal" if level == "none" and str(scene.get("trigger") or "") == "quiet_flow" else "light"

    @staticmethod
    def _group_wakeup_strength_label(strength: str) -> str:
        return {
            "light": "轻唤醒",
            "normal": "普通唤醒",
            "strong": "强唤醒",
            "interrupt": "打断休息",
        }.get(str(strength or ""), "普通唤醒")

    @staticmethod
    def _group_wakeup_reason_label(wakeup_type: str, reason: str = "") -> str:
        wakeup_type = str(wakeup_type or "")
        reason = str(reason or "")
        labels = {
            "direct_wakeup_word": "提到 Bot 名字或强唤醒词",
            "contextual_wakeup_word": "提到弱相关唤醒词且语境需要 Bot 接话",
            "interest_keyword": "命中兴趣关键词",
            "probability_miss": "命中兴趣词但概率未触发",
            "cooldown": "命中线索但仍在冷却",
            "high_intensity": "高强度收口中暂停弱唤醒",
            "open_help": "群里有人向懂的人求助",
            "help_request": "群里有人明确求助",
            "explain_question": "群里有人问原因或含义",
            "identify_question": "群里有人请求识别或判断",
            "question_mark": "句末疑问且包含问题词",
            "question": "群里有人提出问题",
            "cold_group_opening": "群聊安静后有人开场叫人",
            "cold_group_greeting": "群聊安静后有人问候/冒泡",
            "cold_group_help": "群聊安静后有人求助",
            "cold_group": "群聊安静后有人重新开口",
        }
        return labels.get(reason) or {
            "question": "答疑唤醒",
            "cold_group": "冷群唤醒",
            "direct_word": "强唤醒词",
            "context_word": "弱相关唤醒",
            "interest": "兴趣唤醒",
        }.get(wakeup_type, reason or wakeup_type or "唤醒")

    def _group_wakeup_reason_detail(self, wakeup: dict[str, Any], *, score_notes: list[str] | None = None) -> str:
        wakeup_type = _single_line(wakeup.get("type"), 40)
        reason = _single_line(wakeup.get("reason"), 80)
        label = self._group_wakeup_reason_label(wakeup_type, reason)
        score = _safe_int(wakeup.get("score"), 0, 0)
        threshold = _safe_int(wakeup.get("threshold"), 0, 0)
        parts = [label]
        if score or threshold:
            parts.append(f"强度 {score}/{threshold or '-'}")
        help_type = _single_line(wakeup.get("help_type"), 24)
        if help_type:
            parts.append(f"类型 {help_type}")
        idle_seconds = _safe_float(wakeup.get("idle_seconds"), 0.0, 0.0)
        if idle_seconds > 0:
            parts.append(f"冷群 {max(1, int(idle_seconds // 60))} 分钟")
        if score_notes:
            parts.append("；".join(score_notes[:3]))
        return _single_line("，".join(parts), 180)

    def _record_group_wakeup_log(
        self,
        group: dict[str, Any],
        *,
        scene: dict[str, Any],
        sender_id: str,
        sender_name: str,
        text: str,
        wakeup: dict[str, Any],
        result: str = "woke",
        strength: str = "",
        fatigue: dict[str, Any] | None = None,
        note: str = "",
    ) -> None:
        logs = group.setdefault("group_wakeup_logs", [])
        if not isinstance(logs, list):
            logs = []
            group["group_wakeup_logs"] = logs
        fatigue = fatigue if isinstance(fatigue, dict) else self._group_wakeup_fatigue(group)
        wakeup_type = _single_line(wakeup.get("type"), 40)
        strength = _single_line(strength or wakeup.get("strength"), 24)
        reason = _single_line(wakeup.get("reason"), 80)
        reason_label = _single_line(wakeup.get("reason_label"), 80) or self._group_wakeup_reason_label(wakeup_type, reason)
        reason_detail = _single_line(wakeup.get("reason_detail"), 180) or self._group_wakeup_reason_detail(wakeup)
        logs.append(
            {
                "ts": _now_ts(),
                "result": _single_line(result, 32),
                "type": wakeup_type,
                "word": _single_line(wakeup.get("word"), 60),
                "strength": strength,
                "strength_label": self._group_wakeup_strength_label(strength),
                "probability": _safe_float(wakeup.get("probability"), 0.0, 0.0),
                "score": _safe_int(wakeup.get("score"), 0, 0),
                "threshold": _safe_int(wakeup.get("threshold"), 0, 0),
                "intensity": _single_line(wakeup.get("intensity"), 20),
                "help_type": _single_line(wakeup.get("help_type"), 30),
                "reason": reason,
                "reason_label": reason_label,
                "reason_detail": reason_detail,
                "topic_weight": wakeup.get("topic_weight") if isinstance(wakeup.get("topic_weight"), dict) else {},
                "note": _single_line(note or wakeup.get("note"), 180),
                "sender_id": _single_line(sender_id, 40),
                "sender_name": _single_line(sender_name, 40),
                "text": _single_line(text, 160),
                "scene_trigger": _single_line(scene.get("trigger"), 40),
                "fatigue_value": _safe_float(fatigue.get("value"), 0.0, 0.0),
                "fatigue_label": _single_line(fatigue.get("label"), 20),
            }
        )
        limit = max(10, _safe_int(getattr(self, "group_wakeup_log_limit", 80), 80, 10))
        del logs[:-limit]

    def _group_recent_wakeup_count(self, group: dict[str, Any], *, now: float | None = None, window_seconds: int | None = None) -> int:
        now = now or _now_ts()
        window = max(15, int(window_seconds or getattr(self, "group_high_intensity_wakeup_window_seconds", 60) or 60))
        logs = group.get("group_wakeup_logs") if isinstance(group.get("group_wakeup_logs"), list) else []
        count = 0
        for item in reversed(logs):
            if not isinstance(item, dict):
                continue
            ts = _safe_float(item.get("ts"), 0.0, 0.0)
            if ts <= 0:
                continue
            if now - ts > window:
                break
            if str(item.get("result") or "") == "woke":
                count += 1
        return count

    def _group_high_intensity_state(self, group: dict[str, Any], *, now: float | None = None, mutate: bool = True) -> dict[str, Any]:
        now = now or _now_ts()
        if not getattr(self, "enable_group_high_intensity_mode", True):
            return {"active": False, "reason": "disabled", "recent_wakeups": 0, "threshold": 0, "until_ts": 0.0}
        window = max(15, _safe_int(getattr(self, "group_high_intensity_wakeup_window_seconds", 60), 60, 15, 600))
        threshold = max(2, _safe_int(getattr(self, "group_high_intensity_wakeup_threshold", 3), 3, 2, 20))
        cooldown_getter = getattr(self, "_effective_group_high_intensity_cooldown_seconds", None)
        cooldown = cooldown_getter() if callable(cooldown_getter) else max(30, _safe_int(getattr(self, "group_high_intensity_cooldown_seconds", 150), 150, 30, 1800))
        recent_wakeups = self._group_recent_wakeup_count(group, now=now, window_seconds=window)
        fatigue = self._group_wakeup_fatigue(group)
        until_ts = _safe_float(group.get("group_high_intensity_until"), 0.0, 0.0)
        reason = ""
        burst_floor = max(2, min(threshold, threshold - 1 if threshold > 2 else threshold))
        triggered = False
        if recent_wakeups >= threshold:
            triggered = True
            reason = "recent_wakeups"
        elif str(fatigue.get("level") or "") == "high" and recent_wakeups >= burst_floor:
            triggered = True
            reason = "fatigue_high_recent"
        if triggered and mutate:
            next_until = now + cooldown
            if next_until > until_ts:
                group["group_high_intensity_until"] = round(next_until, 3)
                until_ts = next_until
        active = bool(triggered or until_ts > now)
        if active and not reason:
            reason = "cooldown"
        merge_active = bool(active and recent_wakeups >= burst_floor)
        return {
            "active": active,
            "merge_active": merge_active,
            "reason": reason,
            "recent_wakeups": recent_wakeups,
            "threshold": threshold,
            "merge_recent_floor": burst_floor,
            "window_seconds": window,
            "cooldown_seconds": cooldown,
            "until_ts": round(until_ts, 3) if until_ts > 0 else 0.0,
            "remaining_seconds": round(max(0.0, until_ts - now), 1),
            "fatigue": dict(fatigue),
        }

    def _group_wakeup_topic_interest_context(
        self,
        group: dict[str, Any],
        *,
        sender_id: str,
        text: str,
        group_id: str = "",
    ) -> dict[str, Any]:
        now = _now_ts()
        recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
        recent_texts = [
            _single_line(item.get("text"), 160)
            for item in recent[-8:]
            if isinstance(item, dict)
            and now - _safe_float(item.get("ts"), 0) <= 8 * 60
            and (not sender_id or str(item.get("sender_id") or "") == str(sender_id))
            and _single_line(item.get("text"), 160)
        ]
        buffer = {}
        if group_id and sender_id:
            buffer = self._semantic_buffer_active_snapshot(self._semantic_buffer_key(f"group:{group_id}", sender_id))
        buffer_texts = buffer.get("texts") if isinstance(buffer.get("texts"), list) else []
        signature = self._group_topic_signature(text)
        active_topic = None
        threads = group.get("topic_threads") if isinstance(group.get("topic_threads"), list) else []
        for item in threads[:8]:
            if not isinstance(item, dict):
                continue
            if now - _safe_float(item.get("last_ts"), 0) > 45 * 60:
                continue
            if signature and self._topic_signature_similar(signature, str(item.get("signature") or "")):
                active_topic = item
                break
            title = _single_line(item.get("title"), 80)
            if title and (title in text or text in title):
                active_topic = item
                break
        if not isinstance(active_topic, dict):
            active_topic = next(
                (
                    item for item in threads[:4]
                    if isinstance(item, dict) and now - _safe_float(item.get("last_ts"), 0) <= 12 * 60
                ),
                None,
            )
        topic_texts: list[str] = []
        if isinstance(active_topic, dict):
            topic_texts.append(_single_line(active_topic.get("title"), 80))
            examples = active_topic.get("recent_examples") if isinstance(active_topic.get("recent_examples"), list) else []
            topic_texts.extend(_single_line(item.get("text"), 100) for item in examples[-5:] if isinstance(item, dict))
        return {
            "recent_texts": [item for item in recent_texts if item],
            "buffer_texts": [_single_line(item, 160) for item in buffer_texts if _single_line(item, 160)],
            "buffer_active": bool(buffer.get("active")),
            "topic": active_topic if isinstance(active_topic, dict) else {},
            "topic_texts": [item for item in topic_texts if item],
        }

    def _group_wakeup_topic_interest_weight(
        self,
        group: dict[str, Any],
        word: str,
        *,
        sender_id: str,
        text: str,
        group_id: str = "",
    ) -> dict[str, Any]:
        token = _single_line(word, 60)
        if not token:
            return {"multiplier": 1.0, "score": 0.0, "reason": ""}
        context = self._group_wakeup_topic_interest_context(group, sender_id=sender_id, text=text, group_id=group_id)
        score = 0.0
        reasons: list[str] = []
        if self._text_contains_wakeup_word(text, token):
            score += 1.0
            reasons.append("当前句命中")
        recent_hits = sum(1 for item in context.get("recent_texts", []) if self._text_contains_wakeup_word(str(item), token))
        if recent_hits:
            score += min(1.2, recent_hits * 0.35)
            reasons.append(f"近句{recent_hits}次")
        buffer_hits = sum(1 for item in context.get("buffer_texts", []) if self._text_contains_wakeup_word(str(item), token))
        if buffer_hits:
            score += min(0.9, buffer_hits * 0.3)
            reasons.append(f"收口中{buffer_hits}次")
        topic_hits = sum(1 for item in context.get("topic_texts", []) if self._text_contains_wakeup_word(str(item), token))
        if topic_hits:
            topic = context.get("topic") if isinstance(context.get("topic"), dict) else {}
            participants = topic.get("participants") if isinstance(topic.get("participants"), list) else []
            messages = _safe_int(topic.get("message_count"), 0, 0)
            score += min(1.6, 0.55 + topic_hits * 0.22 + min(0.35, len(participants) * 0.05) + min(0.35, messages * 0.03))
            reasons.append("话题线升温")
        max_boost = max(0.0, min(1.5, float(getattr(self, "group_wakeup_topic_interest_max_boost", 0.45) or 0.0)))
        multiplier = 1.0 + min(max_boost, score * 0.16)
        if context.get("buffer_active"):
            penalty = max(0.0, min(1.0, float(getattr(self, "group_wakeup_debounce_pending_penalty", 0.65) or 0.0)))
            multiplier *= max(0.05, 1.0 - penalty)
            reasons.append("同轮收口降权")
        return {
            "multiplier": round(max(0.0, multiplier), 3),
            "score": round(score, 2),
            "reason": "、".join(reasons[:4]),
            "buffer_active": bool(context.get("buffer_active")),
            "recent_texts": list(context.get("recent_texts") or [])[-4:],
            "topic_texts": list(context.get("topic_texts") or [])[-4:],
        }

    def _group_wakeup_probability_context(
        self,
        group: dict[str, Any],
        scene: dict[str, Any],
        probability: float,
        wakeup_type: str,
    ) -> tuple[float, dict[str, Any]]:
        probability = max(0.0, min(1.0, float(probability or 0.0)))
        atmosphere = group.get("atmosphere") if isinstance(group.get("atmosphere"), dict) else {}
        pace = str(atmosphere.get("pace") or "")
        mood = str(atmosphere.get("mood") or "")
        if pace == "热闹":
            probability *= 0.55 if wakeup_type != "question" else 0.72
        elif pace == "安静" and wakeup_type in {"question", "cold_group"}:
            probability *= 1.15
        if mood == "求助" and wakeup_type == "question":
            probability *= 1.35
        if str(scene.get("trigger") or "") in {"reply_in_flow", "quick_follow"}:
            probability *= 0.65
        state = self.data.get("daily_state") if isinstance(getattr(self, "data", None), dict) else {}
        runtime = state.get("sleep_runtime") if isinstance(state, dict) and isinstance(state.get("sleep_runtime"), dict) else {}
        phase = str(runtime.get("phase") or "")
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        mood_bias = str(state.get("mood_bias") or "") if isinstance(state, dict) else ""
        if phase in {"falling_asleep", "light_sleep", "sleeping_again", "woken"}:
            probability *= 0.18
        if energy <= 35:
            probability *= 0.55
        elif energy >= 82:
            probability *= 1.12
        if any(token in mood_bias for token in ("无聊", "烦闷", "好奇", "兴奋")):
            probability *= 1.15
        fatigue = self._group_wakeup_fatigue(group)
        if str(fatigue.get("level") or "") == "medium":
            probability *= 0.65
        elif str(fatigue.get("level") or "") == "high":
            probability *= 0.35
        return min(0.95, max(0.0, probability)), fatigue

    def _group_wakeup_question_signal(self, text: str) -> dict[str, Any]:
        cleaned = _single_line(text, 260)
        if len(cleaned) < 4:
            return {}
        if re.search(r"(https?://|www\.|```|\[图片\]|\[语音\]|\[视频\]|\[转发消息\])", cleaned, flags=re.I):
            return {}
        if re.search(r"(哈哈|草|笑死|绷不住|乐|乐死|不是吧|不会吧).{0,8}[?？]?$", cleaned):
            return {}
        if re.search(r"(急急如律令|急急国王|急急急急|急死谁了)", cleaned):
            return {}
        failure_context_pattern = r"(安装|配置|运行|启动|登录|请求|发送|上传|下载|连接|编译|构建|部署|调用|识别|读取|解析|保存|加载|同步|执行|支付|打开|导入|导出|更新|提交|注册|验证|接口|插件|程序|脚本|命令|模型|图片|语音|视频|文件|消息|任务).{0,8}失败|(?:一直|总是|老是|反复|还是|又).{0,6}失败|失败.{0,8}(怎么办|咋办|怎么弄|怎么解决|怎么处理|原因|报错|日志|重试|一直|总是|还是|又)"
        help_context_pattern = r"(怎么|咋办|怎么办|怎么弄|怎么解决|怎么处理|帮忙|帮我|能不能|有没有人|有人懂|谁会|报错|异常|error|traceback|bug|卡住|跑不起来|急求|急问|在线等)"
        plain_help_meme = bool(re.search(r"救命", cleaned)) and not bool(re.search(help_context_pattern, cleaned, flags=re.I))
        if plain_help_meme and not (re.search(r"[?？]$", cleaned) and re.search(r"(怎么|咋办|怎么办|能不能|有没有|谁|哪|什么|啥)", cleaned)):
            return {}
        score = 0
        reason = ""
        help_type = "解释"
        urgent_help = r"(急求|急问|急用|急等|在线等|有点急|很急|比较急|挺急|急着|崩了|寄了|炸了|跑不起来|过不去|卡住|报错|异常|error|traceback|bug)"
        if re.search(urgent_help, cleaned, flags=re.I) or re.search(failure_context_pattern, cleaned, flags=re.I):
            score += 18
            help_type = "排障"
        if re.search(r"(报错|异常|error|traceback|bug|日志|堆栈|闪退|崩溃|跑不起来)", cleaned, flags=re.I) or re.search(failure_context_pattern, cleaned, flags=re.I):
            help_type = "排障"
        elif re.search(r"(怎么弄|怎么做|怎么搞|如何|教程|步骤|配置|安装|使用)", cleaned):
            help_type = "操作"
        elif re.search(r"(这是什么|这个是什么|看得懂|识别|图里|截图)", cleaned):
            help_type = "识别"
        elif re.search(r"(啥意思|什么意思|为什么|为啥|咋回事|怎么回事|什么情况|啥情况)", cleaned):
            help_type = "解释"
        strong_patterns = (
            (r"(有没有|有无|有没有人|有人|谁|哪位|大佬).{0,12}(懂|知道|会|看得懂|能解释|能帮|帮忙)", 76, "open_help"),
            (r"(求问|请教|咋办|怎么办|怎么弄|怎么解决|怎么处理|怎么搞)", 80, "help_request"),
            (r"救命.{0,12}(怎么|咋办|怎么办|怎么弄|怎么解决|怎么处理|帮忙|帮我|报错|异常|卡住|跑不起来)", 80, "help_request"),
            (r"(为什么|为啥|咋回事|怎么回事|什么情况|啥情况|啥意思|什么意思)", 68, "explain_question"),
            (r"(这是什么|这个是什么|这个咋|这个怎么|这个为啥|这个能不能|这能不能)", 64, "identify_question"),
        )
        for pattern, base_score, base_reason in strong_patterns:
            if re.search(pattern, cleaned):
                score = max(score, base_score)
                reason = base_reason
                break
        if re.search(r"[?？]$", cleaned) and re.search(r"(怎么|为什么|为啥|什么|啥|哪|谁|能不能|可以吗|行吗|会不会|是不是|有没有)", cleaned):
            score = max(score, 54)
            reason = reason or "question_mark"
        if score <= 0:
            return {}
        intensity = "高" if score >= 85 else ("中" if score >= 70 else "低")
        reason = reason or "question"
        return {
            "word": "疑问",
            "reason": reason,
            "reason_label": self._group_wakeup_reason_label("question", reason),
            "score": min(100, score),
            "intensity": intensity,
            "help_type": help_type,
            "raw_text": cleaned,
        }

    def _group_wakeup_question_context_gate(
        self,
        group: dict[str, Any],
        scene: dict[str, Any],
        text: str,
        signal: dict[str, Any],
    ) -> tuple[bool, list[str], int]:
        cleaned = _single_line(text, 260)
        reason = _single_line(signal.get("reason"), 60)
        base_score = _safe_int(signal.get("score"), 0, 0, 100)
        help_type = _single_line(signal.get("help_type"), 24)
        trigger = str(scene.get("trigger") or "")
        notes: list[str] = []
        penalty = 0
        strong_public_help = (
            reason in {"open_help", "help_request"}
            or help_type in {"排障", "操作"}
            or bool(
                re.search(
                    r"(有没有人|有人|谁|哪位|大佬).{0,14}(懂|知道|会|能帮|帮忙)|"
                    r"(求问|请教|急求|急问|在线等|帮忙|帮我)|"
                    r"(报错|异常|error|traceback|bug|日志|堆栈|闪退|崩溃|跑不起来|卡住|失败)",
                    cleaned,
                    flags=re.I,
                )
            )
        )
        conversational_only = bool(
            re.search(
                r"^(?:啊|诶|欸|哈|哈哈|草|不是|不会吧|真的假的|所以|那|这|啥|为什么|为啥|怎么|咋|什么情况|啥情况)[，,。.\s]*[^，,。！？!?]{0,24}[?？]?$",
                cleaned,
            )
        )
        if trigger in {"reply_in_flow", "quick_follow"}:
            if strong_public_help:
                penalty += 8
                notes.append("接话场景但像公共求助")
            else:
                penalty += 28 if base_score < 76 else 18
                notes.append("上下文像在接别人话")
        recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
        now = _now_ts()
        recent_other_messages = [
            item
            for item in recent[-6:]
            if isinstance(item, dict)
            and _safe_float(item.get("ts"), 0.0, 0.0) > 0
            and now - _safe_float(item.get("ts"), 0.0, 0.0) <= 90
        ]
        if conversational_only and not strong_public_help:
            penalty += 18
            notes.append("短反问/吐槽句")
        if recent_other_messages and not strong_public_help:
            last_text = _single_line(recent_other_messages[-1].get("text"), 160)
            if last_text and re.search(r"(就是|因为|所以|应该|可以|不用|不是|这个|那个|确实|对|已经|试试|看看)", cleaned):
                penalty += 10
                notes.append("疑似延续群内讨论")
            answered_like = any(
                re.search(r"(可以|应该|因为|原因|解决|试试|改成|换成|用|装|配置|设置|报错|日志|看起来|大概)", _single_line(item.get("text"), 160))
                for item in recent_other_messages[-3:]
            )
            if answered_like and reason in {"explain_question", "question_mark", "question"}:
                penalty += 8
                notes.append("附近已有讨论/回答")
        if re.search(r"(你们|你俩|他|她|它|这人|那人|楼上|上面|群主|管理员|作者|大佬).{0,16}[?？]$", cleaned) and not strong_public_help:
            penalty += 22
            notes.append("问题目标不像 Bot")
        block = False
        if not strong_public_help and trigger in {"reply_in_flow", "quick_follow"} and base_score - penalty < 60:
            block = True
        if not strong_public_help and conversational_only and base_score - penalty < 65:
            block = True
        return block, notes, penalty

    def _group_wakeup_question_score_context(
        self,
        group: dict[str, Any],
        scene: dict[str, Any],
        signal: dict[str, Any],
    ) -> tuple[int, dict[str, Any], list[str]]:
        score = max(0, min(100, _safe_int(signal.get("score"), 0, 0)))
        notes: list[str] = []
        atmosphere = group.get("atmosphere") if isinstance(group.get("atmosphere"), dict) else {}
        pace = str(atmosphere.get("pace") or "")
        mood = str(atmosphere.get("mood") or "")
        if pace == "热闹":
            score -= 8
            notes.append("热闹降权")
        elif pace == "安静":
            score += 4
            notes.append("安静加权")
        if mood == "求助":
            score += 10
            notes.append("求助气氛")
        if str(scene.get("trigger") or "") in {"reply_in_flow", "quick_follow"}:
            score -= 10
            notes.append("疑似接别人话")
        context_blocked, context_notes, context_penalty = self._group_wakeup_question_context_gate(
            group,
            scene,
            _single_line(signal.get("text"), 260) or _single_line(signal.get("raw_text"), 260) or "",
            signal,
        )
        if context_penalty:
            score -= context_penalty
            notes.extend(context_notes)
        if context_blocked:
            score = min(score, 49)
            if "上下文门控" not in notes:
                notes.append("上下文门控")
        state = self.data.get("daily_state") if isinstance(getattr(self, "data", None), dict) else {}
        runtime = state.get("sleep_runtime") if isinstance(state, dict) and isinstance(state.get("sleep_runtime"), dict) else {}
        phase = str(runtime.get("phase") or "")
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        if phase in {"falling_asleep", "light_sleep", "sleeping_again", "woken"}:
            score -= 35
            notes.append("休息降权")
        if energy <= 35:
            score -= 8
            notes.append("低能量降权")
        fatigue = self._group_wakeup_fatigue(group)
        if str(fatigue.get("level") or "") == "medium":
            score -= 10
            notes.append("疲劳降权")
        elif str(fatigue.get("level") or "") == "high":
            score -= 22
            notes.append("高疲劳降权")
        return max(0, min(100, score)), fatigue, notes

    def _group_wakeup_cold_group_signal(self, group: dict[str, Any], text: str, now: float) -> dict[str, Any]:
        if not bool(getattr(self, "enable_group_wakeup_cold_group", False)):
            return {}
        idle_minutes = max(3, _safe_int(getattr(self, "group_wakeup_cold_group_idle_minutes", 25), 25, 3))
        last_seen = _safe_float(group.get("last_seen"), 0)
        if last_seen <= 0 or now - last_seen < idle_minutes * 60:
            return {}
        cleaned = _single_line(text, 260)
        if not (3 <= len(cleaned) <= 120):
            return {}
        if re.search(r"^(?:[/!！#]|签到|打卡|菜单|帮助|help\b)", cleaned, flags=re.I):
            return {}
        if re.search(r"(https?://|www\.|```|\[图片\]|\[语音\]|\[视频\]|\[转发消息\])", cleaned, flags=re.I):
            return {}
        state = self.data.get("daily_state") if isinstance(getattr(self, "data", None), dict) else {}
        runtime = state.get("sleep_runtime") if isinstance(state, dict) and isinstance(state.get("sleep_runtime"), dict) else {}
        phase = str(runtime.get("phase") or "")
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        if phase in {"falling_asleep", "light_sleep", "sleeping_again", "woken"} or energy <= 35:
            return {}
        direct = re.search(r"(有人吗|在吗|还在吗|睡了吗|怎么没人|好安静|冷群|冒泡|路过|诈尸|开门|醒醒)", cleaned)
        if direct:
            return {"word": "冷群", "reason": "cold_group_opening", "reason_label": self._group_wakeup_reason_label("cold_group", "cold_group_opening"), "score": 86, "idle_seconds": round(now - last_seen, 1)}
        if re.search(r"(早|早上好|上午好|中午好|下午好|晚上好|晚好|嗨|hello|hi|哈喽|冒个泡|有人不|人呢)", cleaned, flags=re.I):
            return {"word": "冷群", "reason": "cold_group_greeting", "reason_label": self._group_wakeup_reason_label("cold_group", "cold_group_greeting"), "score": 72, "idle_seconds": round(now - last_seen, 1)}
        if re.search(r"(急急如律令|急急国王|急急急急|急死谁了)", cleaned):
            return {}
        failure_context_pattern = r"(安装|配置|运行|启动|登录|请求|发送|上传|下载|连接|编译|构建|部署|调用|识别|读取|解析|保存|加载|同步|执行|支付|打开|导入|导出|更新|提交|注册|验证|接口|插件|程序|脚本|命令|模型|图片|语音|视频|文件|消息|任务).{0,8}失败|(?:一直|总是|老是|反复|还是|又).{0,6}失败|失败.{0,8}(怎么办|咋办|怎么弄|怎么解决|怎么处理|原因|报错|日志|重试|一直|总是|还是|又)"
        if re.search(r"救命", cleaned) and not (re.search(r"(怎么|咋办|怎么办|怎么弄|怎么解决|怎么处理|帮忙|帮我|报错|异常|卡住|跑不起来|急求|急问|在线等)", cleaned, flags=re.I) or re.search(failure_context_pattern, cleaned, flags=re.I)):
            return {}
        if re.search(r"(救命.{0,12}(怎么|咋办|怎么办|怎么弄|怎么解决|怎么处理|帮忙|帮我|报错|异常|卡住|跑不起来)|急求|急问|急用|急等|在线等|有点急|很急|比较急|挺急|急着|求问|请教|有没有人|有人懂|谁会|帮忙|咋办|怎么办|怎么弄|报错|崩了|卡住)", cleaned, flags=re.I) or re.search(failure_context_pattern, cleaned, flags=re.I):
            return {"word": "冷群", "reason": "cold_group_help", "reason_label": self._group_wakeup_reason_label("cold_group", "cold_group_help"), "score": 78, "idle_seconds": round(now - last_seen, 1)}
        if re.search(r"[?？]$", cleaned):
            return {}
        return {}

    def _evaluate_group_wakeup(
        self,
        group: dict[str, Any],
        *,
        scene: dict[str, Any],
        sender_id: str,
        sender_name: str,
        text: str,
        group_id: str = "",
    ) -> dict[str, Any]:
        if not self.enable_group_wakeup_enhancement:
            return {}
        cleaned = _single_line(text, 260)
        if not cleaned:
            return {}
        if str(scene.get("talking_to") or "") == "bot":
            return {}
        if str(scene.get("trigger") or "") in {"at_other", "reply_other", "at_all"}:
            return {}
        now = _now_ts()
        direct_words = self._configured_group_direct_wakeup_words()
        for word in direct_words:
            if self._text_contains_wakeup_word(cleaned, word):
                strength = self._group_wakeup_strength("direct_word", group, scene)
                return {
                    "type": "direct_word",
                    "word": word,
                    "strength": strength,
                    "reason": "direct_wakeup_word",
                    "note": "群友提到了 Bot 名字或强唤醒词。",
                }
        question_signal = self._group_wakeup_question_signal(cleaned) if bool(getattr(self, "enable_group_wakeup_question", True)) else {}
        cold_group_signal = self._group_wakeup_cold_group_signal(group, cleaned, now)
        soft_signal_hit = bool(
            question_signal
            or cold_group_signal
            or any(self._text_contains_wakeup_word(cleaned, word) for word in list(self.group_wakeup_context_words or []) + self._group_wakeup_interest_words(group))
        )
        high_intensity = self._group_high_intensity_state(group)
        if high_intensity.get("active"):
            if soft_signal_hit:
                self._record_group_wakeup_log(
                    group,
                    scene=scene,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=cleaned,
                    wakeup={"type": "high_intensity", "word": "", "reason": "high_intensity", "probability": 0.0},
                    result="blocked",
                    strength="",
                    note="群聊处于高强度收口模式,暂停弱相关、解惑、冷群和兴趣唤醒,优先合并处理明确叫到 Bot 的消息。",
                )
            return {}
        cooldown_getter = getattr(self, "_effective_group_wakeup_cooldown_seconds", None)
        group_wakeup_cooldown = cooldown_getter() if callable(cooldown_getter) else self.group_wakeup_cooldown_seconds
        if group_wakeup_cooldown > 0 and now - _safe_float(group.get("last_group_wakeup_at"), 0) < group_wakeup_cooldown:
            if soft_signal_hit:
                self._record_group_wakeup_log(
                    group,
                    scene=scene,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=cleaned,
                    wakeup={"type": "cooldown", "word": "", "reason": "cooldown", "probability": 0.0},
                    result="blocked",
                    strength="",
                    note="命中了可唤醒线索,但仍在冷却时间内,所以没有接入回复链。",
                )
            return {}
        for word in self.group_wakeup_context_words:
            if not self._text_contains_wakeup_word(cleaned, word):
                continue
            if self._group_wakeup_context_should_reply(
                group,
                scene=scene,
                sender_id=sender_id,
                sender_name=sender_name,
                text=cleaned,
                matched_word=word,
            ):
                strength = self._group_wakeup_strength("context_word", group, scene)
                return {
                    "type": "context_word",
                    "word": word,
                    "strength": strength,
                    "reason": "contextual_wakeup_word",
                    "note": "群友提到了可能和 Bot 有关的唤醒词。",
                }
        if question_signal:
            threshold = max(0, min(100, _safe_int(getattr(self, "group_wakeup_question_threshold", 65), 65, 0)))
            score, fatigue, score_notes = self._group_wakeup_question_score_context(group, scene, question_signal)
            if score >= threshold:
                strength = self._group_wakeup_strength("question", group, scene)
                final_intensity = "高" if score >= 85 else ("中" if score >= threshold else "低")
                reason_detail = self._group_wakeup_reason_detail(
                    {
                        **question_signal,
                        "type": "question",
                        "score": score,
                        "threshold": threshold,
                        "intensity": final_intensity,
                    },
                    score_notes=score_notes,
                )
                return {
                    "type": "question",
                    "word": _single_line(question_signal.get("word"), 60) or "疑问",
                    "strength": strength,
                    "score": score,
                    "threshold": threshold,
                    "intensity": final_intensity,
                    "help_type": _single_line(question_signal.get("help_type"), 24) or "解释",
                    "reason": _single_line(question_signal.get("reason"), 60) or "open_question",
                    "reason_label": _single_line(question_signal.get("reason_label"), 80) or self._group_wakeup_reason_label("question", question_signal.get("reason")),
                    "reason_detail": reason_detail,
                    "note": f"答疑唤醒：{reason_detail}。",
                }
            self._record_group_wakeup_log(
                group,
                scene=scene,
                sender_id=sender_id,
                sender_name=sender_name,
                text=cleaned,
                wakeup={
                    "type": "question",
                    "word": _single_line(question_signal.get("word"), 60) or "疑问",
                    "reason": question_signal.get("reason"),
                    "score": score,
                    "threshold": threshold,
                    "intensity": question_signal.get("intensity"),
                    "help_type": question_signal.get("help_type"),
                },
                result="missed",
                strength="",
                fatigue=fatigue,
                note=f"解惑强度 {score}/{threshold} 未达阈值" + (f"（{'、'.join(score_notes[:3])}）" if score_notes else ""),
            )
        if cold_group_signal:
            threshold = max(0, min(100, _safe_int(getattr(self, "group_wakeup_cold_group_threshold", 65), 65, 0)))
            score = max(0, min(100, _safe_int(cold_group_signal.get("score"), 0, 0)))
            fatigue = self._group_wakeup_fatigue(group)
            if score >= threshold:
                strength = self._group_wakeup_strength("cold_group", group, scene)
                reason_detail = self._group_wakeup_reason_detail(
                    {
                        **cold_group_signal,
                        "type": "cold_group",
                        "score": score,
                        "threshold": threshold,
                    }
                )
                return {
                    "type": "cold_group",
                    "word": _single_line(cold_group_signal.get("word"), 60) or "冷群",
                    "strength": strength,
                    "score": score,
                    "threshold": threshold,
                    "reason": _single_line(cold_group_signal.get("reason"), 60) or "cold_group",
                    "reason_label": _single_line(cold_group_signal.get("reason_label"), 80) or self._group_wakeup_reason_label("cold_group", cold_group_signal.get("reason")),
                    "reason_detail": reason_detail,
                    "note": f"冷群唤醒：{reason_detail}。",
                }
            self._record_group_wakeup_log(
                group,
                scene=scene,
                sender_id=sender_id,
                sender_name=sender_name,
                text=cleaned,
                wakeup={
                    "type": "cold_group",
                    "word": _single_line(cold_group_signal.get("word"), 60) or "冷群",
                    "reason": cold_group_signal.get("reason"),
                    "score": score,
                    "threshold": threshold,
                },
                result="missed",
                strength="",
                fatigue=fatigue,
                note=f"冷群强度 {score}/{threshold} 未达阈值",
            )
        if self.group_wakeup_interest_probability <= 0:
            return {}
        for word in self._group_wakeup_interest_words(group):
            if not self._text_contains_wakeup_word(cleaned, word):
                continue
            probability, fatigue = self._group_wakeup_probability_context(group, scene, self.group_wakeup_interest_probability, "interest")
            topic_weight = self._group_wakeup_topic_interest_weight(
                group,
                word,
                sender_id=sender_id,
                text=cleaned,
                group_id=group_id,
            )
            probability *= _safe_float(topic_weight.get("multiplier"), 1.0, 0.0)
            probability = min(0.95, max(0.0, probability))
            if random.random() <= probability:
                strength = self._group_wakeup_strength("interest", group, scene)
                return {
                    "type": "interest",
                    "word": word,
                    "strength": strength,
                    "probability": round(probability, 3),
                    "reason": "interest_keyword",
                    "topic_weight": topic_weight,
                    "note": f"群聊出现了兴趣词：{word}。",
                }
            self._record_group_wakeup_log(
                group,
                scene=scene,
                sender_id=sender_id,
                sender_name=sender_name,
                text=cleaned,
                wakeup={"type": "interest", "word": word, "reason": "probability_miss", "probability": round(probability, 3), "topic_weight": topic_weight},
                result="missed",
                strength="",
                fatigue=fatigue,
                note="命中了兴趣词,但本次概率未触发,所以没有接话。" + (f" 话题权重：{topic_weight.get('reason')}" if topic_weight.get("reason") else ""),
            )
        return {}

    def _infer_group_scene(
        self,
        event: AstrMessageEvent | None,
        group: dict[str, Any],
        *,
        sender_id: str,
        sender_name: str,
        text: str,
    ) -> dict[str, Any]:
        sender_id = str(sender_id or "").strip()
        sender_name = _single_line(sender_name, 40) or sender_id or "群友"
        cleaned = _single_line(text, 260)
        signals = self._event_scene_signals(event) if event is not None else {"self_id": "", "at_targets": [], "at_all": False, "reply_to_id": ""}
        self_id = str(signals.get("self_id") or "").strip()
        at_targets = signals.get("at_targets") if isinstance(signals.get("at_targets"), list) else []
        non_bot_targets = [item for item in at_targets if isinstance(item, dict) and not item.get("is_bot")]
        scene = {
            "trigger": "group_message",
            "sender_id": sender_id,
            "sender_name": sender_name,
            "talking_to": "group",
            "talking_to_name": "群里所有人",
            "reason": "default_group",
            "at_targets": at_targets,
            "reply_to_id": _single_line(signals.get("reply_to_id"), 40),
        }
        if any(isinstance(item, dict) and item.get("is_bot") for item in at_targets):
            scene.update({"trigger": "at_bot", "talking_to": "bot", "talking_to_name": "你", "reason": "explicit_at_bot"})
            return scene
        if signals.get("at_all"):
            scene.update({"trigger": "at_all", "reason": "at_all"})
            return scene
        if non_bot_targets:
            target = non_bot_targets[0]
            target_id = str(target.get("user_id") or "")
            scene.update({
                "trigger": "at_other",
                "talking_to": target_id,
                "talking_to_name": self._group_member_identity_label(target_id, target.get("name"), limit=40),
                "reason": "explicit_at_other",
            })
            return scene
        reply_to_id = _single_line(signals.get("reply_to_id"), 40)
        if reply_to_id:
            if self_id and reply_to_id == self_id:
                scene.update({"trigger": "reply_bot", "talking_to": "bot", "talking_to_name": "你", "reason": "reply_to_bot"})
                return scene
            recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
            target_name = ""
            for item in reversed(recent):
                if isinstance(item, dict) and str(item.get("sender_id") or "") == reply_to_id:
                    target_name = _single_line(item.get("identity_name") or item.get("name"), 40)
                    break
            scene.update({
                "trigger": "reply_other",
                "talking_to": reply_to_id,
                "talking_to_name": self._group_member_identity_label(reply_to_id, target_name, limit=40),
                "reason": "reply_to_other",
            })
            return scene
        if self.bot_name and self.bot_name in cleaned:
            scene.update({"trigger": "mention_bot_name", "talking_to": "bot", "talking_to_name": "你", "reason": "bot_name_mentioned"})
            return scene
        recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
        last_other = None
        skipped_short = 0
        for item in reversed(recent[-8:]):
            if not isinstance(item, dict):
                continue
            if str(item.get("sender_id") or "") != sender_id:
                if self._group_scene_short_interjection(item.get("text")):
                    skipped_short += 1
                    continue
                last_other = item
                break
        if last_other:
            time_gap = _now_ts() - _safe_float(last_other.get("ts"), 0)
            if str(last_other.get("talking_to") or "") == sender_id and time_gap < 60:
                target_id = str(last_other.get("sender_id") or "")
                scene.update({
                    "trigger": "reply_in_flow",
                    "talking_to": target_id,
                    "talking_to_name": self._group_member_identity_label(target_id, last_other.get("identity_name") or last_other.get("name"), limit=40),
                    "reason": "recent_message_addressed_sender_after_short_interjection" if skipped_short else "recent_message_addressed_sender",
                })
            elif time_gap < 15 and str(last_other.get("talking_to") or "group") == "group":
                target_id = str(last_other.get("sender_id") or "")
                scene.update({
                    "trigger": "quick_follow",
                    "talking_to": target_id,
                    "talking_to_name": self._group_member_identity_label(target_id, last_other.get("identity_name") or last_other.get("name"), limit=40),
                    "reason": "quick_follow_after_group_message_after_short_interjection" if skipped_short else "quick_follow_after_group_message",
                })
        return scene

    def _scene_talking_to_text(self, scene: dict[str, Any]) -> str:
        target = str(scene.get("talking_to") or "group")
        name = _single_line(scene.get("talking_to_name"), 80)
        if target == "bot":
            return "你（Bot）"
        if target == "group":
            return "群里所有人（非特定对象）"
        return name or target

    def _scene_note_text(self, scene: dict[str, Any]) -> str:
        target = str(scene.get("talking_to") or "group")
        trigger = str(scene.get("trigger") or "")
        if target == "bot":
            if trigger.startswith("group_wakeup_"):
                return "本轮由群聊唤醒触发：群里提到 Bot 或相关话题。"
            return "当前消息在和 Bot 对话。"
        if target != "group":
            return f"当前消息主要在和{self._scene_talking_to_text(scene)}说话。"
        if trigger == "at_all":
            return "当前消息 @ 全体，包含 Bot。"
        return "当前消息主要面向整个群。"

    def _record_group_wakeup_state_adjustment(
        self,
        *,
        scene: dict[str, Any],
        text: str,
        state_note: str,
        updates: list[str],
        intensity: str = "轻",
        carry_rule: str = "群聊唤醒只作为状态和语气背景承接,不要在回复里暴露关键词触发、概率或内部判断。",
    ) -> None:
        note = _single_line(state_note, 180)
        if not note:
            return
        raw = self.data.setdefault("schedule_adjustments", [])
        if not isinstance(raw, list):
            raw = []
            self.data["schedule_adjustments"] = raw
        now = _now_ts()
        trigger = _single_line(scene.get("trigger"), 40)
        word = _single_line(scene.get("wakeup_word"), 60)
        raw.append(
            {
                "date": _today_key(),
                "source": "群聊唤醒",
                "note": note,
                "immediate_reaction": _single_line(f"群聊里{('提到“' + word + '”') if word else '出现了可接话契机'},她会按当前状态自然反应。", 140),
                "state_updates": updates[:5],
                "user_text": _single_line(text, 120),
                "intensity": intensity,
                "scope": "当前段和短时间群聊回复",
                "carry_rule": carry_rule,
                "created_at": now,
                "expires_at": now + (2 * 3600 if intensity == "轻" else 4 * 3600),
                "trigger": trigger,
            }
        )
        del raw[:-16]

