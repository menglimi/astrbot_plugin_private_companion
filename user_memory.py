# -*- coding: utf-8 -*-
"""
UserMemoryMixin — 从 main.py 重新拆分出的用户记忆系统
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

class UserMemoryMixin:
    """用户记忆系统"""

    @staticmethod
    def _memory_fact_signature(text: Any) -> str:
        compact = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", str(text or "")).lower()
        return compact[:80]

    def _cleanup_companion_memory_items(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        memory = user.get("companion_memory")
        if not isinstance(memory, dict):
            return []
        items = memory.get("items")
        if not isinstance(items, list):
            memory["items"] = []
            return []
        now = _now_ts()
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in items:
            if not isinstance(raw, dict):
                continue
            text = _single_line(raw.get("text"), 260)
            if not text:
                continue
            created_ts = _safe_float(raw.get("created_ts"), 0)
            created_at = _single_line(raw.get("created_at"), 24)
            if created_ts <= 0 and created_at:
                try:
                    created_ts = datetime.strptime(created_at, "%Y-%m-%d %H:%M").timestamp()
                except Exception:
                    created_ts = now
            if created_ts > 0 and now - created_ts > 180 * 86400:
                continue
            signature = self._memory_fact_signature(text)
            if not signature or signature in seen:
                continue
            seen.add(signature)
            item = dict(raw)
            item["text"] = text
            item["created_ts"] = created_ts or now
            deduped.append(item)
        deduped.sort(key=lambda item: (_safe_int(item.get("weight"), 1, 0), _safe_float(item.get("created_ts"), 0)), reverse=True)
        memory["items"] = deduped[: self.max_companion_memory_items]
        return memory["items"]

    def _companion_memory_relevant_items(self, user: dict[str, Any], *, hint: str = "", limit: int = 6) -> list[dict[str, Any]]:
        items = self._cleanup_companion_memory_items(user)
        if not items:
            return []
        hint_text = _single_line(hint, 260).lower()
        if not hint_text:
            return items[: max(1, limit)]
        weighted: list[tuple[int, dict[str, Any]]] = []
        for item in items:
            text = _single_line(item.get("text"), 260).lower()
            score = _safe_int(item.get("weight"), 1, 0)
            if text and any(token and token in hint_text for token in re.findall(r"[\u4e00-\u9fff]{2,8}|[a-z0-9_]{3,24}", text)):
                score += 4
            weighted.append((score, item))
        weighted.sort(key=lambda pair: (pair[0], _safe_float(pair[1].get("created_ts"), 0)), reverse=True)
        return [item for _, item in weighted[: max(1, limit)]]

    def _relationship_profile(self, user: dict[str, Any]) -> dict[str, Any]:
        proactive_count = _safe_int(user.get("proactive_sent_count"), 0)
        reply_count = _safe_int(user.get("reply_count"), 0)
        inbound_count = _safe_int(user.get("inbound_count"), 0)
        score = _safe_int(user.get("relationship_score"), 0)
        reply_rate_available = proactive_count > 0
        reply_rate = reply_count / proactive_count if reply_rate_available else 0.0
        reply_rate_label = f"{reply_rate:.0%}" if reply_rate_available else "暂无样本"
        persona_profile = user.get("persona_relationship", {})
        if isinstance(persona_profile, dict) and persona_profile.get("level"):
            level = str(persona_profile.get("level"))
            preference = str(persona_profile.get("preference") or "普通")
            score = _safe_int(persona_profile.get("score"), score, 0, 100)
        else:
            level, preference = self._fallback_relationship_level(score, reply_rate, inbound_count, proactive_count)
        return {
            "level": level,
            "reply_rate": reply_rate,
            "reply_rate_available": reply_rate_available,
            "reply_rate_label": reply_rate_label,
            "preference": preference,
            "score": score,
            "inbound_count": inbound_count,
            "proactive_count": proactive_count,
            "reply_count": reply_count,
            "note": (
                str(persona_profile.get("note") or "")
                if isinstance(persona_profile, dict) else ""
            ),
        }

    def _format_emotion_residue_hint(self, user: dict[str, Any]) -> str:
        if not bool(getattr(self, "enable_emotion_simulation", True)):
            return ""
        rel_state = user.get("relationship_state")
        if not isinstance(rel_state, dict):
            return ""
        mode = str(rel_state.get("mode") or "normal")
        now = _now_ts()
        mood_score = self._decay_relationship_mood_score(rel_state, now=now)
        hurt_active = _safe_float(rel_state.get("hurt_until"), 0) > now
        hurt_threshold = _safe_int(getattr(self, "emotional_gate_hurt_threshold", 70), 70, 10, 100)
        refuse_threshold = _safe_int(getattr(self, "emotional_gate_refuse_threshold", 90), 90, 20, 100)
        if mode == "refusing" and hurt_active and abs(mood_score) >= refuse_threshold:
            return "对用户刚才的言行还有些不满,表现得回避一点；回复短一些、安静一些,先别急着贴近。"
        if mode == "hurt" and hurt_active and abs(mood_score) >= hurt_threshold:
            return "被用户刚才的言行伤到心里,还没有完全恢复；语气放轻、放慢一点,别急着热情贴近。"
        return ""

    def _relationship_approach_hint(self, user: dict[str, Any]) -> str:
        profile = self._relationship_profile(user)
        level = str(profile.get("level") or "熟悉")
        preference = str(profile.get("preference") or "普通")
        hints: list[str] = []
        if level == "亲近":
            hints.append("默认像已经很熟了,可以少一点寒暄,更容易半句起手、接旧话头,或者轻轻嘴硬一下。")
        elif level == "熟悉":
            hints.append("默认像已经聊顺了,可以直接从眼前的小事切进去,不用每次都铺垫。")
        else:
            hints.append("默认先轻一点,靠近时别把关心说得太满。")
        if preference == "温柔":
            hints.append("靠近方式偏温吞和收着一点。")
        elif preference == "活泼":
            hints.append("靠近方式可以轻快一点,偶尔带一点玩笑感。")
        elif preference == "工作":
            hints.append("靠近方式更克制,优先从具体事情切进去。")
        emotion_hint = self._format_emotion_residue_hint(user)
        if emotion_hint:
            hints.append(emotion_hint)
        rel_state = user.get("relationship_state")
        if isinstance(rel_state, dict):
            mode = str(rel_state.get("mode") or "")
            relation_enabled = bool(getattr(self, "enable_relationship_state_machine", True))
            if relation_enabled and mode == "backoff" and _safe_float(rel_state.get("backoff_until"), 0) > _now_ts():
                hints.append("边界感偏强：短一点、低压、不追问。")
            elif relation_enabled and mode == "careful":
                hints.append("相处要放轻：先接住,不追问,不讲大道理。")
            elif relation_enabled and mode == "warming":
                hints.append("气氛略近：可以自然一点,别过度黏。")
        return " ".join(hints).strip()

    def _update_expression_profile_from_message(self, user: dict[str, Any], text: str) -> None:
        if not self.enable_expression_learning:
            return
        cleaned = _single_line(_strip_internal_message_blocks(text), self._expression_sample_max_chars())
        if not cleaned:
            return
        if self._should_skip_expression_sample(cleaned):
            return
        profile = user.setdefault("expression_profile", {})
        if not isinstance(profile, dict):
            profile = {}
            user["expression_profile"] = profile
        now = _now_ts()
        samples = profile.get("samples")
        if not isinstance(samples, list):
            samples = []
            legacy_count = _safe_int(profile.get("samples"), 0, 0)
            legacy_short = _safe_int(profile.get("short_count"), 0, 0)
            legacy_punctuation = profile.get("punctuation") if isinstance(profile.get("punctuation"), dict) else {}
            legacy_endings = profile.get("endings") if isinstance(profile.get("endings"), list) else []
            legacy_phrases = profile.get("recent_phrases") if isinstance(profile.get("recent_phrases"), list) else []
            punctuation_items = [
                (str(mark), _safe_int(count, 0, 0))
                for mark, count in legacy_punctuation.items()
                if _safe_int(count, 0, 0) > 0
            ]
            if legacy_count:
                migrate_count = min(legacy_count, self.max_learned_expression_items)
                for idx in range(migrate_count):
                    punctuation = {}
                    if punctuation_items:
                        mark, count = punctuation_items[idx % len(punctuation_items)]
                        punctuation[mark] = min(3, max(1, count // max(1, migrate_count)))
                    samples.append(
                        {
                            "ts": now - (idx + 1) * 3600,
                            "length": 12 if idx < legacy_short else 32,
                            "punctuation": punctuation,
                            "ending": _single_line(legacy_endings[idx], 12) if idx < len(legacy_endings) else "",
                            "phrase": _single_line(legacy_phrases[idx], 40) if idx < len(legacy_phrases) else "",
                        }
                    )
        samples = [item for item in samples if isinstance(item, dict)]
        cutoff = now - 30 * 86400
        samples = [item for item in samples if _safe_float(item.get("ts"), now) >= cutoff]
        profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        sample = self._expression_sample_from_text(cleaned, now)
        if self._expression_manual_review_enabled():
            profile["samples"] = samples[: self.max_learned_expression_items]
            self._queue_expression_pending_sample(profile, sample, cleaned)
            self._refresh_expression_profile_legacy_summary(profile)
            return
        samples.insert(0, sample)
        profile["samples"] = samples[: self.max_learned_expression_items]
        self._refresh_expression_profile_legacy_summary(profile)

    def _expression_sample_from_text(self, cleaned: str, now: float | None = None) -> dict[str, Any]:
        now = now or _now_ts()
        punctuation = {}
        for mark in ("！", "!", "？", "?", "~", "～", "…", "。"):
            count = cleaned.count(mark)
            if count:
                punctuation[mark] = count
        stripped = cleaned.rstrip("。！？!?~～… ")
        ending = ""
        if 2 <= len(stripped) <= 80:
            ending = stripped[-min(6, max(2, len(stripped))):]
        phrase = ""
        phrase_limit = 56 if self._expression_learning_mode() == "aggressive" else 40
        if 2 <= len(cleaned) <= phrase_limit and not re.search(r"https?://|<[^>]+>", cleaned):
            phrase = cleaned
        return {
            "id": hashlib.sha1(f"{now}:{cleaned}".encode("utf-8")).hexdigest()[:12],
            "ts": now,
            "text": cleaned,
            "length": len(cleaned),
            "punctuation": punctuation,
            "ending": ending,
            "phrase": phrase,
        }

    def _expression_learning_mode(self) -> str:
        mode = str(getattr(self, "expression_learning_mode", "balanced") or "balanced").strip().lower()
        if mode not in {"light", "balanced", "aggressive"}:
            return "balanced"
        return mode

    def _expression_sample_max_chars(self) -> int:
        return 180 if self._expression_learning_mode() == "aggressive" else 120

    def _expression_style_review_enabled(self) -> bool:
        return bool(
            getattr(self, "enable_expression_learning", True)
            and getattr(self, "enable_expression_style_review", True)
        )

    def _expression_manual_review_enabled(self) -> bool:
        return bool(
            getattr(self, "enable_expression_learning", True)
            and getattr(self, "enable_expression_manual_review", False)
        )

    def _queue_expression_pending_sample(self, profile: dict[str, Any], sample: dict[str, Any], cleaned: str) -> None:
        pending = profile.get("pending_samples")
        if not isinstance(pending, list):
            pending = []
        compact = self._compact_repeat_text(cleaned)
        kept: list[dict[str, Any]] = []
        for item in pending:
            if not isinstance(item, dict):
                continue
            old_text = _single_line(item.get("text") or item.get("phrase"), 180)
            if old_text and self._compact_repeat_text(old_text) == compact:
                continue
            kept.append(item)
        item = dict(sample)
        item["review_status"] = "pending"
        item["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        kept.insert(0, item)
        profile["pending_samples"] = kept[: min(80, max(12, self.max_learned_expression_items * 2))]
        profile["pending_count"] = len(profile["pending_samples"])
        profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    def _should_skip_expression_sample(self, cleaned: str) -> bool:
        if len(cleaned) > self._expression_sample_max_chars():
            return True
        if re.search(r"https?://|www\.|```|Traceback|Error code:|Exception|\[INFO\]|\[WARN\]|\[ERRO\]|\[Core\]", cleaned, re.IGNORECASE):
            return True
        if re.search(r"^\s*(?:/|!|！|陪伴\s|sudo\b|git\b|python\b|node\b|npm\b|pnpm\b|pip\b)", cleaned, re.IGNORECASE):
            return True
        if cleaned.count("\n") >= 2 or cleaned.count("[") + cleaned.count("]") >= 6:
            return True
        if re.search(r"(傻逼|滚|闭嘴|垃圾|废物|妈的|草泥马|操你|死全家)", cleaned):
            return True
        if re.search(r"(习近平|共产党|中共|六四|天安门|法轮功|台独|港独|藏独|疆独|民主运动|政治敏感)", cleaned):
            return True
        if re.search(r"(复制|日志|报错|堆栈|代码|配置|schema|版本号|commit|diff|traceback)", cleaned, re.IGNORECASE):
            return True
        return False

    def _safe_expression_phrase(self, phrase: Any, limit: int = 56) -> str:
        text = _single_line(phrase, limit)
        if not text or len(text) < 2:
            return ""
        if self._should_skip_expression_sample(text):
            return ""
        if re.search(r"<[^>]{1,120}>|@[A-Za-z0-9_\-\u4e00-\u9fff]{1,32}|QQ|群聊|群友|私聊", text, re.IGNORECASE):
            return ""
        if re.search(r"(你是|我是|他是|她是|叫我|叫你|名字|主人|朋友|同学|老师|室友|父母|妈妈|爸爸|哥哥|姐姐|弟弟|妹妹)", text):
            return ""
        return text

    def _expression_profile_phrases(self, profile: dict[str, Any], *, limit: int = 4) -> list[str]:
        raw = profile.get("recent_phrases") if isinstance(profile, dict) else []
        if not isinstance(raw, list):
            return []
        phrases: list[str] = []
        for item in raw:
            phrase = self._safe_expression_phrase(item, 56)
            if phrase and phrase not in phrases:
                phrases.append(phrase)
            if len(phrases) >= limit:
                break
        return phrases

    def _expression_profile_endings(self, profile: dict[str, Any], *, limit: int = 4) -> list[str]:
        raw = profile.get("endings") if isinstance(profile, dict) else []
        if not isinstance(raw, list):
            return []
        endings: list[str] = []
        for item in raw:
            ending = self._safe_expression_phrase(item, 12)
            if ending and ending not in endings:
                endings.append(ending)
            if len(endings) >= limit:
                break
        return endings

    def _refresh_expression_profile_legacy_summary(self, profile: dict[str, Any]) -> None:
        samples = profile.get("samples")
        if not isinstance(samples, list):
            return
        profile["sample_count"] = len(samples)
        profile["short_count"] = sum(1 for item in samples if isinstance(item, dict) and _safe_int(item.get("length"), 0, 0) <= 18)
        punctuation: dict[str, int] = {}
        endings: list[str] = []
        phrases: list[str] = []
        for item in samples:
            if not isinstance(item, dict):
                continue
            marks = item.get("punctuation")
            if isinstance(marks, dict):
                for mark, count in marks.items():
                    punctuation[str(mark)] = punctuation.get(str(mark), 0) + _safe_int(count, 0, 0)
            ending = _single_line(item.get("ending"), 12)
            if ending and ending not in endings:
                endings.append(ending)
            phrase = _single_line(item.get("phrase"), 40)
            if phrase and phrase not in phrases:
                phrases.append(phrase)
        profile["punctuation"] = punctuation
        profile["endings"] = endings[: self.max_learned_expression_items]
        profile["recent_phrases"] = phrases[: self.max_learned_expression_items]

    def _classify_companion_memory_candidate(self, cleaned: str) -> dict[str, Any]:
        lowered = cleaned.lower()
        explicit_tokens = (
            "记住", "记得", "以后", "一直", "永远", "长期", "固定", "默认",
            "不要再", "别再", "以后别", "以后不要", "不许", "雷点", "底线",
            "叫我", "我叫", "我生日", "我的生日", "生日是", "纪念日",
        )
        durable_tokens = (
            "以后", "一直", "永远", "长期", "固定", "默认",
            "不要再", "别再", "以后别", "以后不要", "不许", "雷点", "底线",
            "我生日", "我的生日", "生日是", "纪念日",
        )
        temporary_tokens = (
            "今天", "这次", "刚才", "刚刚", "现在", "此刻", "今晚", "明天",
            "最近", "暂时", "一会儿", "等会儿", "这会儿", "刚睡醒", "刚下课",
        )
        playful_endings = ("啦", "嘛", "呀", "哦", "捏", "www", "哈哈", "嘿嘿", "（", "(")
        memory_patterns = (
            "喜欢", "讨厌", "不喜欢", "别叫", "不要", "记住", "记得",
            "生日", "纪念日", "我是", "我叫", "叫我", "我在", "我住",
            "想要", "希望", "害怕", "雷点", "以后",
        )
        score = sum(1 for pattern in memory_patterns if pattern in cleaned or pattern in lowered)
        if score <= 0:
            return {"keep": False, "reason": "no_memory_signal"}
        explicit = any(token in cleaned for token in explicit_tokens)
        durable_explicit = any(token in cleaned for token in durable_tokens)
        is_temporary = any(token in cleaned for token in temporary_tokens)
        kind = "preference"
        if any(key in cleaned for key in ("不要", "别叫", "讨厌", "不喜欢", "雷点", "不许", "底线")):
            kind = "boundary"
        elif any(key in cleaned for key in ("生日", "纪念日", "以后", "记住", "记得")):
            kind = "important"
        if is_temporary and not explicit:
            return {"keep": False, "reason": "temporary_context"}
        if is_temporary and explicit and not durable_explicit:
            return {"keep": False, "reason": "temporary_soft_explicit"}
        if kind == "boundary":
            boundary_strong = any(token in cleaned for token in ("不要再", "别再", "以后别", "以后不要", "不许", "雷点", "底线", "讨厌", "不喜欢"))
            soft_boundary = (
                "别叫" in cleaned
                and not boundary_strong
                and any(cleaned.rstrip("。！？!?~～… ").endswith(token) for token in playful_endings)
            )
            if soft_boundary and not durable_explicit:
                return {"keep": False, "reason": "soft_playful_boundary"}
        if any(token in cleaned for token in ("开玩笑", "不是认真的", "随口", "口嗨")) and not explicit:
            return {"keep": False, "reason": "joke_or_uncertain"}
        weight = min(5, 1 + score + (2 if explicit else 0))
        return {"keep": True, "kind": kind, "weight": weight, "reason": "explicit" if explicit else "rule_match"}

    def _update_companion_memory_from_message(self, user: dict[str, Any], text: str) -> None:
        if not self.enable_companion_memory:
            return
        cleaned = _single_line(text, 260)
        if not cleaned:
            return
        memory = user.setdefault("companion_memory", {})
        if not isinstance(memory, dict):
            memory = {}
            user["companion_memory"] = memory
        raw_items = memory.get("items")
        items = raw_items if isinstance(raw_items, list) else []
        candidate = self._classify_companion_memory_candidate(cleaned)
        if not candidate.get("keep"):
            return
        item = {
            "text": cleaned,
            "kind": candidate.get("kind") or "preference",
            "weight": _safe_int(candidate.get("weight"), 1, 1, 5),
            "reason": candidate.get("reason") or "rule_match",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "created_ts": _now_ts(),
        }
        signature = self._memory_fact_signature(cleaned)
        deduped = [
            old
            for old in items
            if isinstance(old, dict) and self._memory_fact_signature(_single_line(old.get("text"), 260)) != signature
        ]
        deduped.insert(0, item)
        memory["items"] = deduped[: self.max_companion_memory_items]
        memory["updated_at"] = item["created_at"]

    def _format_expression_profile_for_prompt(self, user: dict[str, Any]) -> str:
        profile = user.get("expression_profile")
        if not isinstance(profile, dict):
            return "暂无足够样本。保持 AstrBot 默认人格的自然表达。"
        raw_samples = profile.get("samples")
        if isinstance(raw_samples, list):
            sample_items = [item for item in raw_samples if isinstance(item, dict)]
        else:
            sample_count = _safe_int(raw_samples, 0, 0)
            if sample_count <= 0:
                return "暂无足够样本。保持 AstrBot 默认人格的自然表达。"
            sample_items = []
            short_count = _safe_int(profile.get("short_count"), 0, 0)
            for idx in range(min(sample_count, self.max_learned_expression_items)):
                sample_items.append({"length": 12 if idx < short_count else 32, "punctuation": {}})
        if not sample_items:
            return "暂无足够样本。保持 AstrBot 默认人格的自然表达。"
        samples = max(1, len(sample_items))
        short_ratio = sum(1 for item in sample_items if _safe_int(item.get("length"), 0, 0) <= 18) / samples
        punctuation: dict[str, int] = {}
        for item in sample_items:
            marks = item.get("punctuation")
            if not isinstance(marks, dict):
                continue
            for mark, count in marks.items():
                punctuation[str(mark)] = punctuation.get(str(mark), 0) + _safe_int(count, 0, 0)
        length_hint = "用户常用短句,回复也可以更短更像即时聊天。" if short_ratio >= 0.55 else "用户能接受稍完整的句子,但仍避免说明书式长段。"
        lines = [length_hint]
        pause_count = sum(_safe_int(punctuation.get(mark), 0, 0) for mark in ("…", "~", "～"))
        question_count = sum(_safe_int(punctuation.get(mark), 0, 0) for mark in ("？", "?"))
        exclaim_count = sum(_safe_int(punctuation.get(mark), 0, 0) for mark in ("！", "!"))
        if pause_count >= max(2, samples // 4):
            lines.append("语气里留白感偏多,可以偶尔放慢半拍,但不要堆标点。")
        if question_count >= max(2, samples // 5):
            lines.append("对方常用短问句推进聊天,回复要直接接住问题,别绕开。")
        if exclaim_count >= max(2, samples // 4):
            lines.append("语气可以稍微轻快一点,但不要夸张。")
        mode = self._expression_learning_mode()
        if mode == "aggressive":
            endings = self._expression_profile_endings(profile, limit=3)
            phrases = self._expression_profile_phrases(profile, limit=3)
            if endings:
                lines.append("句尾可轻参考：" + "、".join(endings) + "；只学节奏,别照抄。")
            if phrases:
                lines.append("短句样本：" + " / ".join(phrases) + "；只取口语松紧,不要复读原句。")
            lines.append("表达学习只用于节奏,不能学习身份、关系、群友口癖、脏话、日志格式或事实判断。")
        elif mode == "light":
            lines = lines[:2]
        return "\n".join(lines)

    def _format_companion_memory_for_prompt(self, user: dict[str, Any], *, style_only: bool = False) -> str:
        memory = user.get("companion_memory")
        lines: list[str] = []
        if not isinstance(memory, dict):
            memory = {}
        llm_profile = memory.get("profile")
        if isinstance(llm_profile, dict):
            if style_only:
                hint_text = _single_line(user.get("last_user_message"), 260)

                def _profile_values(key: str, limit: int = 4) -> list[str]:
                    value = llm_profile.get(key)
                    if isinstance(value, list):
                        return [_single_line(item, 60) for item in value[:limit] if _single_line(item, 60)]
                    text = _single_line(value, 120)
                    return [text] if text else []

                def _weak_relevant(text: str) -> bool:
                    if not hint_text:
                        return False
                    lowered_hint = hint_text.lower()
                    tokens = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_]{3,24}", text)
                    return any(token and token.lower() in lowered_hint for token in tokens)

                def _with_subject(text: str) -> str:
                    text = _single_line(text, 80)
                    if not text:
                        return ""
                    if text.startswith(("用户", "对方")):
                        return text
                    if text.startswith("别"):
                        return f"对方说过“{text}”"
                    if text.startswith(("不", "别", "讨厌", "害怕", "喜欢", "希望", "想要")):
                        return "对方" + text
                    return text

                style_lines: list[str] = []
                for item in _profile_values("strong_memories", 4):
                    natural = _with_subject(item)
                    if natural:
                        style_lines.append(f"记得{natural}")
                for item in _profile_values("boundaries", 4):
                    natural = _with_subject(item)
                    if natural:
                        style_lines.append(f"别踩这个边界，{natural}")
                for item in _profile_values("speaking_style", 3):
                    style_lines.append(f"回复时顺着一点，{item}")
                weak_candidates = _profile_values("weak_preferences", 4) + _profile_values("interests", 4)
                for item in weak_candidates:
                    if _weak_relevant(item):
                        natural = _with_subject(item)
                        if natural:
                            style_lines.append(f"这轮聊到相关内容时记得{natural}")
                return "\n".join(list(dict.fromkeys(style_lines))) if style_lines else "暂无专门沉淀的用户记忆。"
            profile_fields = (
                ("strong_memories", "强记忆"),
                ("weak_preferences", "弱偏好"),
                ("user_traits", "用户画像"),
                ("interests", "兴趣/偏好"),
                ("boundaries", "边界/雷点"),
                ("relationship_notes", "关系线索"),
                ("speaking_style", "说话习惯"),
            )
            for key, label in profile_fields:
                value = llm_profile.get(key)
                if isinstance(value, list):
                    text = "；".join(_single_line(item, 60) for item in value[:5] if _single_line(item, 60))
                else:
                    text = _single_line(value, 180)
                if text:
                    lines.append(f"{label}：{text}")
        if not style_only:
            items = self._companion_memory_relevant_items(user, hint=user.get("last_user_message") or "", limit=8)
            if isinstance(items, list) and items:
                facts = []
                for item in items[:8]:
                    if not isinstance(item, dict):
                        continue
                    text = _single_line(item.get("text"), 90)
                    if text:
                        facts.append(text)
                if facts:
                    lines.append("近期可记住的话：" + " / ".join(facts))
        if not style_only:
            habit_text = self._format_user_behavior_habits_for_prompt(
                user,
                current_only=True,
                limit=1,
                natural=True,
                hint=user.get("last_user_message") or "",
                time_window_minutes=60,
                require_relevant=True,
            )
            if habit_text:
                lines.append(habit_text)
        if not style_only:
            episode_text = self._format_dialogue_episodes_for_prompt(user, hint=user.get("last_user_message") or "")
            open_loop_text = self._format_open_loops_for_prompt(user, hint=user.get("last_user_message") or "")
            recent_context_parts = [part for part in (episode_text, open_loop_text) if part]
            if recent_context_parts:
                lines.append("近期共同经历：\n" + "\n".join(recent_context_parts))
            consequence_text = self._format_action_consequence_hint(user)
            if consequence_text:
                lines.append("最近主动行为闭环：\n" + consequence_text)
        return "\n".join(lines) if lines else "暂无专门沉淀的用户记忆。"

    def _dialogue_episode_relevance_score(self, item: dict[str, Any], *, hint: str = "") -> float:
        summary = _single_line(item.get("summary"), 140)
        if not summary:
            return 0.0
        searchable_parts = [
            summary,
            _single_line(item.get("emotional_residue"), 100),
            _single_line(item.get("reusable_topic"), 100),
        ]
        for key in ("user_events", "bot_promises", "avoid_next"):
            value = item.get(key)
            if isinstance(value, list):
                searchable_parts.extend(_single_line(part, 80) for part in value if _single_line(part, 80))
        searchable = " ".join(part for part in searchable_parts if part).lower()
        score = 0.0
        created_ts = _safe_float(item.get("created_ts"), 0)
        if created_ts > 0:
            age_hours = max(0.0, (_now_ts() - created_ts) / 3600)
            if age_hours <= 36:
                score += 2.0
            elif age_hours <= 168:
                score += 1.0
        hint_text = _single_line(hint, 260).lower()
        if hint_text:
            tokens = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_]{3,24}", hint_text)
            for token in dict.fromkeys(tokens):
                if token and token in searchable:
                    score += 2.5
        return score

    def _select_dialogue_episodes_for_prompt(
        self,
        episodes: list[Any],
        *,
        hint: str = "",
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        candidates: list[tuple[int, float, dict[str, Any]]] = []
        seen: set[str] = set()
        total = len(episodes)
        for index, item in enumerate(episodes):
            if not isinstance(item, dict):
                continue
            summary = _single_line(item.get("summary"), 120)
            if not summary:
                continue
            signature = self._memory_fact_signature(summary)
            if signature and signature in seen:
                continue
            if signature:
                seen.add(signature)
            score = self._dialogue_episode_relevance_score(item, hint=hint)
            if index >= max(0, total - 1):
                score += 3.0
            elif index >= max(0, total - 3):
                score += 1.0
            candidates.append((index, score, item))
        if not candidates:
            return []
        picked = sorted(candidates, key=lambda part: (part[1], part[0]), reverse=True)[: max(1, limit)]
        return [item for _, _, item in sorted(picked, key=lambda part: part[0])]

    def _format_dialogue_episodes_for_prompt(self, user: dict[str, Any], *, hint: str = "") -> str:
        episodes = user.get("dialogue_episodes")
        if not isinstance(episodes, list):
            return ""
        lines: list[str] = []
        for item in self._select_dialogue_episodes_for_prompt(episodes, hint=hint, limit=3):
            summary = _single_line(item.get("summary"), 120)
            if not summary:
                continue
            mood = _single_line(item.get("emotional_residue"), 60)
            topic = _single_line(item.get("reusable_topic"), 80)
            parts = [summary]
            if mood:
                parts.append(f"当时留下的感觉是{mood}")
            if topic:
                parts.append(f"可以顺手接回{topic}")
            lines.append("- " + "；".join(parts))
        return "\n".join(lines)

    def _open_loop_relevance_score(self, item: dict[str, Any], *, hint: str = "") -> float:
        text = _single_line(item.get("text"), 120)
        if not text:
            return 0.0
        score = 0.0
        created_ts = _safe_float(item.get("created_ts"), 0)
        if created_ts > 0:
            age_hours = max(0.0, (_now_ts() - created_ts) / 3600)
            if age_hours <= 24:
                score += 2.0
            elif age_hours <= 168:
                score += 1.0
        status = str(item.get("status") or "")
        if status in {"已完成", "已取消"}:
            score -= 8.0
        hint_text = _single_line(hint, 260).lower()
        if hint_text:
            searchable = text.lower()
            tokens = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_]{3,24}", hint_text)
            for token in dict.fromkeys(tokens):
                if token and token in searchable:
                    score += 3.0
        return score

    def _select_open_loops_for_prompt(
        self,
        loops: list[dict[str, Any]],
        *,
        hint: str = "",
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        candidates: list[tuple[int, float, dict[str, Any]]] = []
        total = len(loops)
        for index, item in enumerate(loops):
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "") in {"已完成", "已取消"}:
                continue
            score = self._open_loop_relevance_score(item, hint=hint)
            if index >= max(0, total - 1):
                score += 2.0
            elif index >= max(0, total - 3):
                score += 1.0
            candidates.append((index, score, item))
        if not candidates:
            return []
        picked = sorted(candidates, key=lambda part: (part[1], part[0]), reverse=True)[: max(1, limit)]
        return [item for _, _, item in sorted(picked, key=lambda part: part[0])]

    def _format_open_loops_for_prompt(self, user: dict[str, Any], *, hint: str = "") -> str:
        loops = user.get("open_loops")
        if not isinstance(loops, list):
            return ""
        lines: list[str] = []
        now = _now_ts()
        kept = []
        seen: set[str] = set()
        for item in loops:
            if not isinstance(item, dict):
                continue
            if now - _safe_float(item.get("created_ts"), now) > 14 * 86400:
                continue
            signature = self._memory_fact_signature(item.get("text"))
            if signature and signature in seen:
                continue
            if signature:
                seen.add(signature)
            kept.append(item)
        if len(kept) != len(loops):
            user["open_loops"] = kept[-12:]
        for item in self._select_open_loops_for_prompt(kept, hint=hint, limit=3):
            text = self._naturalize_open_loop_text(item.get("text"))
            if not text:
                continue
            status = _single_line(item.get("status"), 30) or "待自然延续"
            if status == "待自然延续":
                lines.append(f"- 之前还留着：{text}")
            else:
                lines.append(f"- {status}：{text}")
        return "\n".join(lines)

    def _naturalize_open_loop_text(self, raw: Any) -> str:
        text = _single_line(raw, 100)
        if not text:
            return ""
        text = re.sub(r"^(?:记得|帮我|提醒我|到时候|以后|明天|今晚|等会儿|一会儿)[，,：:\s]*", "", text)
        text = re.sub(r"(?:你记一下|你记住|别忘了)[。！？!?,，\s]*$", "", text)
        return _single_line(text.strip(" ：:，,。"), 90)

    def _extract_explicit_open_loop_from_message(self, text: str) -> str:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return ""
        if self._is_structured_or_diagnostic_text(cleaned):
            return ""
        weak_only = ("到时候", "以后", "明天", "今晚", "等会儿", "一会儿")
        has_strong_marker = bool(re.search(r"(提醒我|帮我记|帮我提醒|你记一下|你记住|别忘了|记得提醒|记得叫|记得喊|到点叫|到点提醒)", cleaned))
        if not has_strong_marker:
            return ""
        patterns = (
            r"(?:提醒我|帮我提醒|记得提醒|到点提醒|到点叫|记得叫|记得喊)([^。！？\n]{2,90})",
            r"(?:帮我记|你记一下|你记住|别忘了|记得)([^。！？\n]{2,90})",
            r"([^。！？\n]{2,90})(?:你记一下|你记住|别忘了)",
        )
        for pattern in patterns:
            match = re.search(pattern, cleaned)
            if not match:
                continue
            candidate = self._naturalize_open_loop_text(match.group(0))
            if not candidate:
                continue
            if candidate in weak_only:
                continue
            if len(candidate) < 3:
                continue
            return candidate
        return ""

    def _open_loop_match_score(self, loop_text: str, inbound_text: str) -> float:
        loop = self._compact_repeat_text(loop_text)
        inbound = self._compact_repeat_text(inbound_text)
        if not loop or not inbound:
            return 0.0
        if len(loop) >= 4 and loop in inbound:
            return 1.0
        if len(inbound) >= 4 and inbound in loop:
            return 0.9
        loop_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_]{3,24}", loop_text))
        inbound_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_]{3,24}", inbound_text))
        if not loop_tokens or not inbound_tokens:
            return 0.0
        overlap = len(loop_tokens & inbound_tokens)
        return overlap / max(1, min(len(loop_tokens), len(inbound_tokens)))

    def _resolve_matching_open_loop(self, loops: list[Any], text: str) -> dict[str, Any] | None:
        candidates: list[tuple[float, int, dict[str, Any]]] = []
        for index, item in enumerate(loops):
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "") in {"已完成", "已取消"}:
                continue
            loop_text = _single_line(item.get("text"), 120)
            if not loop_text:
                continue
            score = self._open_loop_match_score(loop_text, text)
            candidates.append((score, index, item))
        if not candidates:
            return None
        score, _, item = max(candidates, key=lambda part: (part[0], part[1]))
        if score >= 0.34:
            return item
        return max(candidates, key=lambda part: part[1])[2]

    def _update_open_loops_from_message(self, user: dict[str, Any], text: str) -> None:
        if not self.enable_open_loop_tracking:
            return
        cleaned = _single_line(text, 260)
        if not cleaned:
            return
        loops = user.setdefault("open_loops", [])
        if not isinstance(loops, list):
            loops = []
            user["open_loops"] = loops

        completion_markers = ("好了", "搞定", "解决了", "完成了", "不用了", "取消", "算了", "没事了", "不用提醒")
        if loops and any(marker in cleaned for marker in completion_markers):
            item = self._resolve_matching_open_loop(loops, cleaned)
            if item is not None:
                item["status"] = "已取消" if any(marker in cleaned for marker in ("不用了", "取消", "算了", "不用提醒")) else "已完成"
                item["resolved_ts"] = _now_ts()

        loop_text = self._extract_explicit_open_loop_from_message(cleaned)
        if loop_text:
            existing = {_single_line(item.get("text"), 120) for item in loops if isinstance(item, dict)}
            if loop_text not in existing:
                loops.append(
                    {
                        "text": loop_text,
                        "status": "待自然延续",
                        "created_ts": _now_ts(),
                        "source": "user_message",
                    }
                )
        del loops[:-12]

    def _remove_open_loop_entry(self, user: dict[str, Any], value: str) -> str:
        loops = user.get("open_loops")
        if not isinstance(loops, list) or not loops:
            user["open_loops"] = []
            return "当前没有未完话头。"

        keyword = _single_line(value, 60)
        if not keyword:
            return "请提供要删除的话头关键词，或用“全部”清空所有未完话头。"

        if keyword.lower() in {"全部", "所有", "all", "清空"}:
            kept_pending: list[dict[str, Any]] = []
            removed_count = 0
            for item in loops:
                if isinstance(item, dict) and str(item.get("status") or "") in {"已完成", "已取消"}:
                    kept_pending.append(item)
                else:
                    removed_count += 1
            user["open_loops"] = kept_pending[-12:]
            return f"已清空 {removed_count} 条未完话头。" if removed_count else "当前没有未完话头。"

        if len(keyword) < 2:
            return "关键词太短，请提供至少 2 个字，避免误删多条话头。"

        kept: list[dict[str, Any]] = []
        removed: list[str] = []
        for item in loops:
            if not isinstance(item, dict):
                continue
            text = _single_line(item.get("text"), 120)
            if text and keyword in text and str(item.get("status") or "") not in {"已完成", "已取消"}:
                removed.append(text)
            else:
                kept.append(item)
        user["open_loops"] = kept[-12:]
        if not removed:
            return "没有找到匹配的未完话头。"
        return "已删除未完话头：\n" + "\n".join(f"- {item}" for item in removed)

    def _update_action_preferences_from_message(self, user: dict[str, Any], text: str) -> None:
        cleaned = _single_line(text, 240)
        if not cleaned:
            return
        prefs = user.setdefault("action_preferences", {})
        if not isinstance(prefs, dict):
            prefs = {}
            user["action_preferences"] = prefs
        mapping = {
            "poke": ("戳", "戳一戳"),
            "voice": ("语音", "发语音", "声音"),
            "photo_text": ("图片", "照片", "图"),
            "screen_peek": ("看屏幕", "窥屏", "看我屏幕", "屏幕"),
        }
        negative = ("别", "不要", "不许", "讨厌", "少", "别再", "不喜欢")
        positive = ("喜欢", "可以", "多", "想要", "爱看", "爱听")
        for action, keywords in mapping.items():
            if not any(keyword in cleaned for keyword in keywords):
                continue
            item = prefs.setdefault(action, {"like": 0, "dislike": 0, "note": ""})
            if not isinstance(item, dict):
                item = {"like": 0, "dislike": 0, "note": ""}
                prefs[action] = item
            if any(token in cleaned for token in negative):
                item["dislike"] = min(20, _safe_int(item.get("dislike"), 0, 0) + 2)
                item["note"] = _single_line(cleaned, 90)
            elif any(token in cleaned for token in positive):
                item["like"] = min(20, _safe_int(item.get("like"), 0, 0) + 1)
                item["note"] = _single_line(cleaned, 90)
            item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    def _action_consequence_items(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        items = user.setdefault("action_consequences", [])
        if not isinstance(items, list):
            items = []
            user["action_consequences"] = items
        now = _now_ts()
        kept: list[dict[str, Any]] = []
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        for item in items:
            if not isinstance(item, dict):
                continue
            created = _safe_float(item.get("ts"), now)
            if now - created > 7 * 86400:
                continue
            if callable(meta_leak_checker) and meta_leak_checker(str(item.get("text") or "")):
                continue
            kept.append(item)
        if len(kept) != len(items):
            user["action_consequences"] = kept[-18:]
        return user["action_consequences"]

    def _classify_action_reply_feedback(self, text: str) -> str:
        cleaned = _single_line(text, 220)
        if not cleaned:
            return "neutral"
        negative = (
            "别",
            "不要",
            "不许",
            "烦",
            "打扰",
            "闭嘴",
            "硬",
            "生硬",
            "不喜欢",
            "不对",
            "不是",
            "笨",
            "怎么又",
            "没收到",
            "哪里",
            "图呢",
        )
        positive = (
            "好",
            "可以",
            "喜欢",
            "可爱",
            "聪明",
            "对",
            "正常",
            "收到",
            "摸摸",
            "抱抱",
            "谢谢",
            "不错",
        )
        if any(token in cleaned for token in negative):
            return "negative"
        if any(token in cleaned for token in positive):
            return "positive"
        return "neutral"

    def _note_action_sent(
        self,
        user: dict[str, Any],
        action: str,
        *,
        reason: str = "",
        text: str = "",
        motive: str = "",
        action_summary: str = "",
    ) -> None:
        action = _single_line(action, 40) or "message"
        items = self._action_consequence_items(user)
        items.append(
            {
                "ts": _now_ts(),
                "action": action,
                "reason": _single_line(reason, 50),
                "text": _single_line(_strip_internal_message_blocks(text), 120),
                "motive": _single_line(motive, 100),
                "summary": _single_line(action_summary, 120),
                "status": "awaiting_reply",
                "feedback": "",
                "reply_text": "",
                "reply_ts": 0,
            }
        )
        del items[:-18]
        self._note_proactive_afterglow_sent(
            user,
            action=action,
            reason=reason,
            text=text,
            motive=motive,
            action_summary=action_summary,
        )
        continuity = user.setdefault("state_continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            user["state_continuity"] = continuity
        continuity["last_action_ts"] = _now_ts()
        continuity["last_action"] = action
        continuity["last_action_reason"] = _single_line(reason, 50)
        cleaned_text = _single_line(_strip_internal_message_blocks(text), 120)
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        continuity["last_action_text"] = "" if callable(meta_leak_checker) and meta_leak_checker(cleaned_text) else cleaned_text

    def _note_proactive_afterglow_sent(
        self,
        user: dict[str, Any],
        *,
        action: str,
        reason: str = "",
        text: str = "",
        motive: str = "",
        action_summary: str = "",
    ) -> None:
        now = _now_ts()
        semantic_kind = _single_line(user.get("planned_proactive_semantic_kind"), 40)
        anchor_type = _single_line(user.get("planned_proactive_anchor_type"), 40)
        semantic_score = _safe_int(user.get("planned_proactive_semantic_score"), 50, 0, 100)
        ignored = _safe_int(user.get("ignored_streak"), 0, 0)
        if reason in {"group_share", "news_share", "bili_video_share", "web_exploration_share"} or semantic_kind == "external_share":
            label = "刚把一个外部小发现递过去，先看它会不会被接住"
            next_tendency = "稍后若还没回应，不要继续补同类分享"
        elif semantic_kind in {"self_share", "observation"} or reason in {"activity_share", "diary_share", "creative_share", "background_schedule"}:
            label = "刚把自己的一个小片段放过去，余味还在"
            next_tendency = "下一次优先换更轻的切口，不要连续汇报自己"
        elif semantic_kind in {"care", "check_in", "light_touch"} or reason in {"quiet_care", "state_share"}:
            label = "刚轻轻碰了一下关系，不急着要回应"
            next_tendency = "如果沉默继续，下一次更短更克制"
        elif semantic_kind in {"continuation", "reminder"}:
            label = "刚接了一次明确来源，等这条自然落地"
            next_tendency = "除非有真实新来源，否则不要反复续同一个话头"
        else:
            label = "刚发出一条主动，先把窗口留给对方"
            next_tendency = "下一次根据回应再决定靠近或收住"
        if ignored >= 1:
            label = f"{label}，但前面已经有未回应"
            next_tendency = "沉默累积时不要加压，不要连续追问"
        if semantic_score < 45:
            next_tendency = "这次由头不算硬，后续要更依赖具体上下文"
        afterglow = {
            "ts": now,
            "status": "awaiting_reply",
            "label": _single_line(label, 140),
            "next_tendency": _single_line(next_tendency, 160),
            "reason": _single_line(reason, 50),
            "action": _single_line(action, 50),
            "semantic_kind": semantic_kind,
            "anchor_type": anchor_type,
            "semantic_score": semantic_score,
            "text": _single_line(_strip_internal_message_blocks(text), 160),
            "motive": _single_line(motive, 120),
            "summary": _single_line(action_summary, 140),
            "feedback": "",
            "reply_text": "",
            "reply_ts": 0,
        }
        user["proactive_afterglow"] = afterglow
        recent = user.setdefault("recent_proactive_afterglows", [])
        if not isinstance(recent, list):
            recent = []
            user["recent_proactive_afterglows"] = recent
        recent.append(dict(afterglow))
        del recent[:-8]
        continuity = user.setdefault("state_continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            user["state_continuity"] = continuity
        continuity["proactive_afterglow"] = afterglow["label"]
        continuity["proactive_afterglow_tendency"] = afterglow["next_tendency"]

    def _note_action_reply_feedback(self, user: dict[str, Any], action: str, text: str = "") -> None:
        action = _single_line(action, 40) or "message"
        affinity = user.setdefault("action_reply_affinity", {})
        if not isinstance(affinity, dict):
            affinity = {}
            user["action_reply_affinity"] = affinity
        affinity[action] = _safe_int(affinity.get(action), 0, 0) + 1

        feedback = self._classify_action_reply_feedback(text)
        now = _now_ts()
        for item in reversed(self._action_consequence_items(user)):
            if not isinstance(item, dict):
                continue
            if item.get("status") != "awaiting_reply":
                continue
            if _single_line(item.get("action"), 40) != action:
                continue
            item["status"] = "replied"
            item["feedback"] = feedback
            item["reply_text"] = _single_line(text, 120)
            item["reply_ts"] = now
            break
        self._note_proactive_afterglow_reply(user, action=action, text=text, feedback=feedback, now=now)
        continuity = user.setdefault("state_continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            user["state_continuity"] = continuity
        continuity["last_reply_ts"] = now
        continuity["last_reply_feedback"] = feedback
        continuity["last_reply_text"] = _single_line(text, 120)

    def _note_proactive_afterglow_reply(
        self,
        user: dict[str, Any],
        *,
        action: str,
        text: str = "",
        feedback: str = "neutral",
        now: float | None = None,
    ) -> None:
        current = user.get("proactive_afterglow")
        if not isinstance(current, dict):
            return
        check_now = _now_ts() if now is None else now
        if check_now - _safe_float(current.get("ts"), 0) > 48 * 3600:
            return
        current["status"] = "replied"
        current["feedback"] = _single_line(feedback, 24)
        current["reply_text"] = _single_line(text, 160)
        current["reply_ts"] = check_now
        if feedback == "positive":
            current["label"] = "上一条主动被接住了，关系余温往回亮了一点"
            current["next_tendency"] = "后续可以自然一点，但不要立刻连续加码"
        elif feedback == "negative":
            current["label"] = "上一条主动被顶回来了，先收住一点"
            current["next_tendency"] = "后续主动更短、更少、更低压，避开同类动作"
        else:
            current["label"] = "上一条主动被回应了，话头算是落地"
            current["next_tendency"] = "后续可以顺着真实回复走，不要机械续主动"
        recent = user.setdefault("recent_proactive_afterglows", [])
        if isinstance(recent, list):
            recent.append(dict(current))
            del recent[:-8]
        continuity = user.setdefault("state_continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            user["state_continuity"] = continuity
        continuity["proactive_afterglow"] = current["label"]
        continuity["proactive_afterglow_tendency"] = current["next_tendency"]

    def _note_proactive_afterglow_outcome(
        self,
        user: dict[str, Any],
        *,
        status: str,
        note: str = "",
    ) -> None:
        normalized_status = _single_line(status, 32)
        if normalized_status not in {"blocked", "cancelled", "dropped", "deferred", "failed"}:
            return
        now = _now_ts()
        reason = _single_line(user.get("planned_proactive_reason"), 50)
        action = _single_line(user.get("planned_proactive_action"), 50)
        semantic_kind = _single_line(user.get("planned_proactive_semantic_kind"), 40)
        anchor_type = _single_line(user.get("planned_proactive_anchor_type"), 40)
        clean_note = _single_line(note, 140)
        if normalized_status == "deferred":
            label = "刚才那个主动念头被先收住了"
            tendency = "如果之后再出现，要带一点犹豫后的自然感，不要机械重试"
        elif normalized_status == "failed":
            label = "刚才那次主动没能送出去"
            tendency = "下一次不要假装它已经发生，先重新找更稳的切口"
        else:
            label = "刚才那个主动念头被放下了"
            tendency = "下一次避开同一个别扭点，等更自然的由头"
        if clean_note:
            label = f"{label}：{clean_note}"
        afterglow = {
            "ts": now,
            "status": normalized_status,
            "label": _single_line(label, 160),
            "next_tendency": _single_line(tendency, 160),
            "reason": reason,
            "action": action,
            "semantic_kind": semantic_kind,
            "anchor_type": anchor_type,
            "semantic_score": _safe_int(user.get("planned_proactive_semantic_score"), 0, 0, 100),
            "text": "",
            "motive": _single_line(user.get("planned_proactive_motive"), 120),
            "summary": "",
            "feedback": "",
            "reply_text": "",
            "reply_ts": 0,
        }
        user["proactive_afterglow"] = afterglow
        recent = user.setdefault("recent_proactive_afterglows", [])
        if not isinstance(recent, list):
            recent = []
            user["recent_proactive_afterglows"] = recent
        recent.append(dict(afterglow))
        del recent[:-8]
        continuity = user.setdefault("state_continuity", {})
        if not isinstance(continuity, dict):
            continuity = {}
            user["state_continuity"] = continuity
        continuity["proactive_afterglow"] = afterglow["label"]
        continuity["proactive_afterglow_tendency"] = afterglow["next_tendency"]

    def _format_action_consequence_hint(self, user: dict[str, Any]) -> str:
        items = self._action_consequence_items(user)
        if not items:
            return ""
        lines: list[str] = []
        for item in items[-5:]:
            if not isinstance(item, dict):
                continue
            action = _single_line(item.get("action"), 30)
            reason = _single_line(item.get("reason"), 40)
            text = _single_line(item.get("text"), 70)
            status = _single_line(item.get("status"), 24)
            feedback = _single_line(item.get("feedback"), 24)
            reply = _single_line(item.get("reply_text"), 70)
            if not action and not text:
                continue
            when = self._format_timestamp_elapsed(item.get("ts"))
            parts = [f"{when}主动{action or 'message'}"]
            if reason:
                parts.append(f"原因:{reason}")
            if text:
                parts.append(f"内容:{text}")
            if status == "awaiting_reply":
                parts.append("还没有自然接上,下次不要当作用户刚刚主动找你")
            elif reply:
                parts.append(f"用户反馈:{feedback or 'neutral'}:{reply}")
            lines.append("- " + "；".join(parts))
        if not lines:
            return ""
        return "\n".join(lines)

    def _time_bucket_for_user_habit(self, when: datetime | None = None) -> tuple[str, int]:
        when = when or datetime.now()
        minute = when.hour * 60 + when.minute
        buckets = (
            ("凌晨", 0, 6 * 60),
            ("早晨", 6 * 60, 9 * 60),
            ("上午", 9 * 60, 11 * 60 + 30),
            ("中午", 11 * 60 + 30, 14 * 60),
            ("下午", 14 * 60, 18 * 60),
            ("傍晚", 18 * 60, 20 * 60),
            ("夜晚", 20 * 60, 23 * 60),
            ("深夜", 23 * 60, 24 * 60),
        )
        for label, start, end in buckets:
            if start <= minute < end:
                return label, minute
        return "凌晨", minute

    def _classify_user_habit_message(self, text: str) -> tuple[str, str, str]:
        cleaned = _single_line(text, 220)
        lowered = cleaned.lower()
        if not cleaned:
            return "", "", ""
        category = "聊天话题"
        topic = cleaned
        profile = self._detect_private_user_retrieval_habit(cleaned)
        if profile:
            category = "固定检索"
            topic = _single_line(profile.get("topic"), 80) or cleaned
        elif any(token in cleaned for token in ("吃饭", "午饭", "晚饭", "早饭", "早餐", "午餐", "晚餐", "夜宵", "饿", "饱")):
            category = "饮食节奏"
            if any(token in cleaned for token in ("还没", "没吃", "没来得及", "没饭", "没到饭点")):
                topic = "还没吃/饭点偏晚"
            elif any(token in cleaned for token in ("吃了", "刚吃", "吃完", "饱")):
                topic = "已经吃过饭"
            else:
                topic = "吃饭相关"
        elif any(token in cleaned for token in ("睡", "起床", "醒", "熬夜", "困", "晚安", "早安")):
            category = "作息节奏"
            if any(token in cleaned for token in ("还没睡", "睡不着", "熬夜")):
                topic = "夜里还没睡"
            elif any(token in cleaned for token in ("起床", "刚醒", "醒了")):
                topic = "起床/刚醒"
            else:
                topic = "睡眠相关"
        elif any(token in cleaned for token in ("作业", "上课", "下课", "考试", "题", "学习", "上班", "下班", "工作", "摸鱼")):
            category = "学习工作"
            topic = "学习/工作节奏"
        elif any(token in cleaned for token in ("游戏", "视频", "番", "漫画", "小说", "直播", "刷", "看")):
            category = "娱乐习惯"
            topic = "娱乐/刷内容"
        elif re.search(r"[？?]|什么|多少|颜色|吗|呢|怎么|有没有|可不可以|要不要", cleaned):
            category = "固定提问"
            topic = re.sub(r"\d+", "", cleaned)
        elif any(token in cleaned for token in ("喜欢", "讨厌", "想要", "以后", "每天", "经常", "总是", "习惯")):
            category = "偏好习惯"
            topic = cleaned
        signature = self._proactive_topic_signature(category, topic or cleaned, lowered)
        return category, _single_line(topic, 80), signature

    def _detect_private_user_retrieval_habit(self, text: str) -> dict[str, Any]:
        cleaned = _single_line(text, 220)
        compact = re.sub(r"\s+", "", cleaned).lower()
        if not compact:
            return {}
        is_question = bool(re.search(r"[？?]|什么|啥|哪|几|多少|有没有|吗|呢|了没|了吗|颜色|色", compact))
        if not is_question:
            return {}
        if any(token in compact for token in ("衣服", "穿搭", "穿着", "穿什么", "穿了什么", "裙子", "外套", "上衣", "校服", "裤子", "鞋子")) and any(
            token in compact for token in ("颜色", "什么色", "啥色", "什么颜色", "穿什么", "穿了什么", "今天穿", "现在穿")
        ):
            return {
                "intent": "current_outfit_query",
                "topic": "询问 Bot 当前穿着/衣服颜色",
                "query_anchors": ["当前穿搭", "今日穿搭", "每日穿搭", "衣服颜色", "穿什么", "穿了什么", "daily_outfit", "persona_life"],
                "answer_hints": ["优先检索今日穿搭图、当前日程和最近自我生活记忆", "回答时直接说当前准确穿着和颜色,不要泛泛说可能"],
            }
        if any(token in compact for token in ("吃了什么", "吃什么", "晚饭", "午饭", "早餐", "夜宵")) and any(token in compact for token in ("你", "bot", "星缘", "今天", "刚才", "现在")):
            return {
                "intent": "current_meal_query",
                "topic": "询问 Bot 最近吃了什么",
                "query_anchors": ["self_meal", "吃了什么", "午餐", "晚餐", "早餐", "夜宵", "persona_life"],
                "answer_hints": ["优先检索 Bot 自我进食记录和当前日程", "如果没有准确记录,说明没记清,不要编具体食物"],
            }
        if any(token in compact for token in ("在干嘛", "在做什么", "忙什么", "现在做", "刚才做")) and any(token in compact for token in ("你", "bot", "星缘", "现在", "刚才", "今天")):
            return {
                "intent": "current_activity_query",
                "topic": "询问 Bot 当前/最近在做什么",
                "query_anchors": ["当前日程", "日程细化", "self_timeline", "persona_life", "在做什么"],
                "answer_hints": ["优先检索当前日程、日程细化和自我时间线", "按最近准确记录回答,不要把很久前的状态当现在"],
            }
        return {}

    def _update_user_behavior_habits_from_message(self, user: dict[str, Any], text: str) -> None:
        if not self.enable_user_habit_learning:
            return
        cleaned = _single_line(text, 220)
        if not cleaned or cleaned.startswith(("/", "!", "！", "#")):
            return
        category, topic, signature = self._classify_user_habit_message(cleaned)
        if not category or not signature:
            return
        now_dt = datetime.now()
        bucket, minute = self._time_bucket_for_user_habit(now_dt)
        habits = user.setdefault("behavior_habits", {})
        if not isinstance(habits, dict):
            habits = {}
            user["behavior_habits"] = habits
        patterns = habits.setdefault("patterns", [])
        if not isinstance(patterns, list):
            patterns = []
            habits["patterns"] = patterns
        key = f"{bucket}|{category}|{signature}"
        matched = None
        for item in patterns:
            if isinstance(item, dict) and str(item.get("key") or "") == key:
                matched = item
                break
        if matched is None:
            matched = {
                "key": key,
                "bucket": bucket,
                "category": category,
                "topic": topic,
                "signature": signature,
                "count": 0,
                "avg_minute": minute,
                "examples": [],
                "created_ts": _now_ts(),
            }
            patterns.append(matched)
        retrieval_profile = self._detect_private_user_retrieval_habit(cleaned)
        if retrieval_profile:
            matched["intent"] = _single_line(retrieval_profile.get("intent"), 60)
            matched["query_anchors"] = [
                _single_line(item, 40)
                for item in retrieval_profile.get("query_anchors", [])
                if _single_line(item, 40)
            ][:12]
            matched["answer_hints"] = [
                _single_line(item, 80)
                for item in retrieval_profile.get("answer_hints", [])
                if _single_line(item, 80)
            ][:8]
            matched["memory_key"] = hashlib.sha1(
                f"{str(user.get('user_id') or user.get('id') or '')}|{key}".encode("utf-8", errors="ignore")
            ).hexdigest()[:20]
        count = _safe_int(matched.get("count"), 0, 0) + 1
        old_avg = _safe_float(matched.get("avg_minute"), minute)
        matched["count"] = min(999, count)
        matched["avg_minute"] = round((old_avg * max(0, count - 1) + minute) / max(1, count), 1)
        matched["last_seen_ts"] = _now_ts()
        matched["last_seen_text"] = cleaned
        examples = matched.get("examples")
        if not isinstance(examples, list):
            examples = []
        examples.insert(0, cleaned)
        matched["examples"] = list(dict.fromkeys(_single_line(item, 90) for item in examples if _single_line(item, 90)))[:5]
        patterns.sort(
            key=lambda item: (
                _safe_int(item.get("count"), 0, 0) if isinstance(item, dict) else 0,
                _safe_float(item.get("last_seen_ts"), 0) if isinstance(item, dict) else 0,
            ),
            reverse=True,
        )
        del patterns[self.user_habit_max_items:]
        habits["updated_at"] = now_dt.strftime("%Y-%m-%d %H:%M")
        self._maybe_sync_user_behavior_habit_to_memory_companion(user, matched)

    def _maybe_sync_user_behavior_habit_to_memory_companion(self, user: dict[str, Any], habit: dict[str, Any]) -> None:
        if not isinstance(user, dict) or not isinstance(habit, dict):
            return
        if str(habit.get("category") or "") != "固定检索":
            return
        min_count = max(2, self.user_habit_min_count)
        if _safe_int(habit.get("count"), 0, 0) < min_count:
            return
        now = _now_ts()
        if now - _safe_float(habit.get("memory_synced_at"), 0) < 12 * 3600:
            return
        recorder = getattr(self, "_memory_companion_record_user_habit", None)
        if not callable(recorder):
            return
        user_id = _single_line(user.get("user_id") or user.get("id"), 80)
        if not user_id:
            return
        habit["memory_synced_at"] = now
        try:
            asyncio.create_task(recorder(user=user, user_id=user_id, habit=dict(habit)))
        except Exception:
            habit["memory_synced_at"] = 0

    def _format_user_habit_time(self, minute_value: Any) -> str:
        minute = int(max(0, min(1439, round(_safe_float(minute_value, 0)))))
        return f"{minute // 60:02d}:{minute % 60:02d}"

    @staticmethod
    def _minute_distance(a: float, b: float) -> float:
        diff = abs(float(a) - float(b)) % 1440
        return min(diff, 1440 - diff)

    def _user_habit_effective_score(self, item: dict[str, Any], *, now: float | None = None) -> float:
        now = now or _now_ts()
        count = _safe_int(item.get("count"), 0, 0)
        age_days = max(0.0, (now - _safe_float(item.get("last_seen_ts"), now)) / 86400)
        if age_days <= 7:
            recency = 1.0
        elif age_days <= 30:
            recency = max(0.2, 1.0 - (age_days - 7) / 23 * 0.8)
        else:
            recency = 0.0
        return count * recency

    def _qualified_user_behavior_habits(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        habits = user.get("behavior_habits")
        if not isinstance(habits, dict):
            return []
        patterns = habits.get("patterns")
        if not isinstance(patterns, list):
            return []
        now = _now_ts()
        min_count = max(2, self.user_habit_min_count)
        kept = []
        for item in patterns:
            if not isinstance(item, dict):
                continue
            if now - _safe_float(item.get("last_seen_ts"), now) > 30 * 86400:
                continue
            if _safe_int(item.get("count"), 0, 0) < min_count:
                continue
            if self._user_habit_effective_score(item, now=now) < max(1.6, min_count * 0.45):
                continue
            kept.append(item)
        kept.sort(
            key=lambda item: (
                self._user_habit_effective_score(item, now=now),
                _safe_float(item.get("last_seen_ts"), 0),
            ),
            reverse=True,
        )
        return kept

    def _user_habit_related_to_text(self, item: dict[str, Any], text: str) -> bool:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        category = str(item.get("category") or "")
        topic = _single_line(item.get("topic"), 80)
        mapping = {
            "饮食节奏": ("吃", "饭", "早餐", "午饭", "晚饭", "夜宵", "饿", "饱", "零食", "喝"),
            "作息节奏": ("睡", "醒", "起床", "熬夜", "困", "晚安", "早安", "梦"),
            "学习工作": ("作业", "上课", "下课", "考试", "题", "学习", "上班", "下班", "工作", "摸鱼"),
            "娱乐习惯": ("游戏", "视频", "番", "漫画", "小说", "直播", "刷", "看"),
            "固定提问": ("？", "?", "什么", "多少", "吗", "呢", "怎么", "有没有", "要不要"),
            "偏好习惯": ("喜欢", "讨厌", "想要", "以后", "每天", "经常", "总是", "习惯"),
        }
        if any(token in cleaned for token in mapping.get(category, ())):
            return True
        tokens = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_]{3,24}", topic)
        return any(token and token in cleaned for token in tokens)

    def _natural_user_habit_line(self, item: dict[str, Any]) -> str:
        bucket = _single_line(item.get("bucket"), 12)
        category = _single_line(item.get("category"), 20)
        topic = _single_line(item.get("topic"), 80)
        if not topic:
            return ""
        if category == "饮食节奏":
            if "还没吃" in topic or "饭点偏晚" in topic:
                return f"{bucket}时对方常会提到还没吃饭，聊到吃的可以轻轻接住，不用像提醒。"
            if "已经吃过" in topic:
                return f"{bucket}时对方常已经吃过饭，别每次都追问吃没吃。"
            return f"{bucket}时对方容易聊到吃饭，相关时顺手接住就好。"
        if category == "作息节奏":
            if "夜里还没睡" in topic:
                return f"{bucket}时对方常还醒着，聊到睡觉时少一点催促，多一点顺着接。"
            if "起床" in topic or "刚醒" in topic:
                return f"{bucket}时对方常刚醒，语气可以放轻一点。"
            return f"{bucket}时对方容易聊到睡眠，别把作息说教化。"
        if category == "学习工作":
            return f"{bucket}时对方常在学习或工作相关状态里，回复可以更直接、少绕。"
        if category == "娱乐习惯":
            return f"{bucket}时对方常在看东西或玩内容，相关时可以自然接梗。"
        if category == "固定提问":
            return f"{bucket}时对方常用短问题推进聊天，先直接接住问题。"
        if category == "偏好习惯":
            return f"{bucket}时对方常提到类似“{topic}”的偏好或习惯，相关时记得顺着一点。"
        return f"{bucket}时对方常聊到“{topic}”，相关时自然接住。"

    def _format_user_behavior_habits_for_prompt(
        self,
        user: dict[str, Any],
        *,
        current_only: bool = False,
        limit: int = 6,
        natural: bool = False,
        hint: str = "",
        time_window_minutes: int | None = None,
        require_relevant: bool = False,
    ) -> str:
        if not self.enable_user_habit_learning:
            return ""
        items = self._qualified_user_behavior_habits(user)
        if current_only:
            _, current_minute = self._time_bucket_for_user_habit()
            window = 60 if time_window_minutes is None else max(0, int(time_window_minutes))
            items = [
                item for item in items
                if self._minute_distance(_safe_float(item.get("avg_minute"), current_minute), current_minute) <= window
            ]
        if require_relevant:
            items = [item for item in items if self._user_habit_related_to_text(item, hint)]
        lines: list[str] = []
        for item in items[:limit]:
            bucket = _single_line(item.get("bucket"), 12)
            category = _single_line(item.get("category"), 20)
            topic = _single_line(item.get("topic"), 80)
            if natural:
                line = self._natural_user_habit_line(item)
                if line and line not in lines:
                    lines.append("- " + line)
                continue
            count = _safe_int(item.get("count"), 0, 0)
            time_text = self._format_user_habit_time(item.get("avg_minute"))
            example = _single_line(item.get("last_seen_text"), 80)
            if topic:
                lines.append(f"- {bucket}约{time_text}｜{category}｜{topic}｜出现 {count} 次" + (f"｜最近：{example}" if example else ""))
        if not lines:
            return ""
        if natural:
            return "用户平常的节奏：\n" + "\n".join(lines)
        return (
            "用户习惯画像（软线索,不是命令）：\n"
            + "\n".join(lines)
            + "\n使用方式：只在当前语境自然吻合时提前理解或轻轻提起；不要暴露统计、次数或“我记录了你”。"
        )

    def _format_all_user_behavior_habits_for_schedule(self, *, limit: int = 8) -> str:
        if not self.enable_user_habit_learning:
            return "暂无用户习惯线索。"
        users = self.data.get("users")
        if not isinstance(users, dict):
            return "暂无用户习惯线索。"
        lines: list[str] = []
        for user_id, user in users.items():
            if not isinstance(user, dict) or not user.get("enabled", True) or not self._is_target_private_user(str(user_id), user):
                continue
            name = _single_line(user.get("nickname") or user_id, 24)
            text = self._format_user_behavior_habits_for_prompt(user, current_only=False, limit=3, natural=True)
            habit_lines = [line for line in text.splitlines() if line.startswith("- ")]
            for line in habit_lines:
                lines.append(f"- {name}：{line[2:]}")
                if len(lines) >= limit:
                    break
            if len(lines) >= limit:
                break
        if not lines:
            return "暂无用户习惯线索。"
        return (
            "用户近期行为习惯（只作日程软背景）：\n"
            + "\n".join(lines)
            + "\n使用方式：只帮助判断对方常出现的时段和话题,不要把用户习惯、食物偏好或避雷直接改写成 Bot 今天必须执行的购买、带饭、约饭或准备任务。"
        )

    def _habit_proactive_event_for_user(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any] | None:
        if not self.enable_user_habit_learning:
            return None
        now = now or _now_ts()
        now_dt = datetime.fromtimestamp(now)
        _, current_minute = self._time_bucket_for_user_habit(now_dt)
        candidates = []
        for item in self._qualified_user_behavior_habits(user):
            avg_minute = _safe_float(item.get("avg_minute"), current_minute)
            if self._minute_distance(avg_minute, current_minute) > 75:
                continue
            count = _safe_int(item.get("count"), 0, 0)
            candidates.append((self._user_habit_effective_score(item, now=now), count, item))
        if not candidates:
            return None
        candidates.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)
        item = candidates[0][2]
        category = _single_line(item.get("category"), 20)
        topic = _single_line(item.get("topic"), 70)
        if self._habit_topic_is_greeting_like(topic or category) and self._recent_activity_suppresses_habit_greeting(user, now=now):
            return None
        bucket = _single_line(item.get("bucket"), 12)
        delay_minutes = random.randint(4, 28)
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(delay_minutes, width_minutes=20),
            "reason": "habit_awareness",
            "action": "message",
            "why": f"用户最近常在{bucket}出现“{category}”相关话题或行为,这会儿自然想提前理解一下。",
            "topic": topic or category or "用户习惯",
            "motive": f"这会儿像是用户平常会提到“{topic or category}”的时候,想自然接住,不用说自己在统计。",
            "scene": f"{bucket}的惯常互动时段",
            "tone": "熟悉,提前一步",
            "impulse": "像真的记得对方生活节奏一样,轻轻提前接住",
            "_scheduled_ts": now + delay_minutes * 60,
            "_habit_awareness": True,
        }

    def _habit_topic_is_greeting_like(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _single_line(text, 80))
        if not compact:
            return False
        return bool(re.fullmatch(r"(?:早|早安|早上好|上午好|午安|中午好|晚上好|晚安)", compact))

    def _recent_activity_suppresses_habit_greeting(self, user: dict[str, Any], *, now: float) -> bool:
        recent_at = self._latest_private_user_activity_ts(user)
        if recent_at <= 0:
            return False
        if now - recent_at < max(0, self._effective_user_greeting_idle_minutes(user)) * 60:
            return True
        suppressed = user.get("greetings_suppressed_by_inbound", [])
        if not isinstance(suppressed, list):
            return False
        return any(
            reason in suppressed and self._inbound_satisfies_greeting(reason, now=now)
            for reason in ("morning_greeting", "noon_greeting", "evening_greeting")
        )

    def _is_structured_or_diagnostic_text(self, text: str) -> bool:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        if re.search(r"https?://|```|Traceback|Error code:|Exception|\[INFO\]|\[WARN\]|\[ERRO\]|\[Core\]", cleaned, re.IGNORECASE):
            return True
        if re.search(r"^\s*(?:/|!|！|陪伴\s|git\b|python\b|node\b|npm\b|pnpm\b|pip\b)", cleaned, re.IGNORECASE):
            return True
        if cleaned.count("[") + cleaned.count("]") >= 6:
            return True
        if re.search(r"(日志|堆栈|traceback)", cleaned, re.IGNORECASE):
            return True
        return False

    def _intent_target_hint(self, text: str) -> tuple[bool, bool]:
        cleaned = _single_line(text, 260)
        target_hint = bool(re.search(r"(你|bot|机器人|插件|星缘|老老老|助手|ai|AI)", cleaned))
        third_party_hint = bool(re.search(r"(数学|作业|代码|报错|他|她|它|他们|她们|别人|群友|那个人|这个人|用户|豆腐|蛙蛙|小水月)", cleaned))
        return target_hint, third_party_hint

    def _is_soft_playful_boundary(self, text: str) -> bool:
        cleaned = _single_line(text, 260)
        return bool(
            re.search(r"(别闹|别这样|不要啊|别呀|不要嘛|讨厌啦|烦啦)", cleaned)
            and re.search(r"(哈|哈哈|hhh|笑死|啦|嘛|呀|哦|捏|~|～|w)", cleaned, re.IGNORECASE)
        )

    def _action_preference_hint(self, user: dict[str, Any] | None = None) -> str:
        if not isinstance(user, dict):
            return ""
        prefs = user.get("action_preferences")
        if not isinstance(prefs, dict) or not prefs:
            return ""
        labels = {
            "poke": "戳一戳",
            "voice": "语音",
            "photo_text": "图片",
            "screen_peek": "看屏幕",
        }
        lines = []
        for action, item in prefs.items():
            if not isinstance(item, dict):
                continue
            like = _safe_int(item.get("like"), 0, 0)
            dislike = _safe_int(item.get("dislike"), 0, 0)
            note = _single_line(item.get("note"), 60)
            if dislike > like:
                lines.append(f"- {labels.get(action, action)}：用户可能不喜欢或希望少用。{note}")
            elif like > dislike:
                lines.append(f"- {labels.get(action, action)}：用户接受度较高。{note}")
        return "\n".join(lines)

    def _analyze_inbound_intent(self, text: str) -> dict[str, Any]:
        cleaned = _single_line(text, 240)
        if not cleaned:
            return {"intent": "empty", "emotion": "neutral", "pressure": 0, "reply_style": "short", "confidence": 1.0, "source": "empty", "reason": ""}
        if self._is_structured_or_diagnostic_text(cleaned):
            return {
                "intent": "chat",
                "emotion": "neutral",
                "pressure": 0,
                "reply_style": "natural",
                "confidence": 0.2,
                "source": "diagnostic_skip",
                "reason": "结构化/日志/代码类文本不作为情绪依据",
                "emotion_event": "neutral",
                "emotion_intensity": 0,
                "emotion_reason": "",
                "emotion_target": "none",
                "emotion_rule": "diagnostic_skip",
                "emotion_confidence": 0.2,
                "text": cleaned,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        lower = cleaned.lower()
        intent = "chat"
        emotion = "neutral"
        pressure = 0
        reply_style = "natural"
        confidence = 0.55
        source = "default"
        reason = ""
        target_hint, third_party_hint = self._intent_target_hint(cleaned)
        strong_boundary = bool(
            re.search(r"(别再|不要再|不许|闭嘴|别吵|别烦|别打扰|不要烦|不想理你|离我远点|别靠近|别贴|别撒娇)", cleaned)
            or re.search(r"(讨厌你|烦你|你.*太吵|你.*打扰)", cleaned)
        )
        weak_boundary = bool(re.search(r"(别|不要|讨厌|烦)", cleaned))
        soft_play_boundary = self._is_soft_playful_boundary(cleaned)
        if strong_boundary or (weak_boundary and target_hint and not third_party_hint and not soft_play_boundary):
            intent = "boundary"
            emotion = "resistant"
            pressure += 3
            reply_style = "back_off"
            confidence = 0.9 if strong_boundary else 0.72
            source = "strong_rule" if strong_boundary else "targeted_boundary_rule"
            reason = "用户明确对 Bot 表达边界" if target_hint else "用户表达强边界"
        elif re.search(r"(烦|累|难受|崩溃|不想|想哭|emo|压力|焦虑|失眠|疼|委屈)", cleaned, re.IGNORECASE):
            intent = "comfort"
            emotion = "low"
            pressure += 2
            reply_style = "soft"
            confidence = 0.82
            source = "comfort_rule"
            reason = "用户表达低落或压力"
        elif re.search(r"(怎么|如何|为什么|帮我|能不能|可以.*吗|教程|代码|报错|分析|解释)", cleaned):
            intent = "help"
            reply_style = "useful"
            pressure += 1
            confidence = 0.78
            source = "help_rule"
            reason = "用户在请求解释或帮助"
        elif re.search(r"(抱抱|亲亲|摸摸|陪我|想你|喜欢你|爱你|贴贴)", cleaned):
            intent = "intimacy"
            emotion = "close"
            reply_style = "warm_short"
            confidence = 0.84
            source = "intimacy_rule"
            reason = "用户表达亲近或陪伴需求"
        elif re.search(r"(哈哈|笑死|草|绷|乐|hhh|233|好玩|乐了)", lower) or soft_play_boundary:
            intent = "play"
            emotion = "light"
            reply_style = "playful"
            confidence = 0.7 if soft_play_boundary else 0.76
            source = "soft_boundary_play_rule" if soft_play_boundary else "play_rule"
            reason = "软边界更像玩笑语气" if soft_play_boundary else "用户在玩梗或轻松表达"
        elif weak_boundary:
            confidence = 0.35
            source = "weak_boundary_ignored"
            reason = "边界词未明显指向 Bot,不硬判为拉开距离"
        if len(cleaned) <= 6 and intent == "chat":
            reply_style = "very_short"
            confidence = 0.62
            source = "short_chat_rule"
            reason = "短句普通接话"
        emotion_event = self._classify_relationship_emotion_event(cleaned, intent_context={"confidence": confidence, "source": source})
        return {
            "intent": intent,
            "emotion": emotion,
            "pressure": min(5, pressure),
            "reply_style": reply_style,
            "confidence": round(float(confidence), 2),
            "source": source,
            "reason": reason,
            "emotion_event": emotion_event.get("event", "neutral"),
            "emotion_intensity": emotion_event.get("intensity", 0),
            "emotion_reason": emotion_event.get("reason", ""),
            "emotion_target": emotion_event.get("target", "none"),
            "emotion_rule": emotion_event.get("rule", ""),
            "emotion_confidence": round(_safe_float(emotion_event.get("confidence"), 0.0), 2),
            "text": cleaned,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

    def _classify_relationship_emotion_event(self, text: str, intent_context: dict[str, Any] | None = None) -> dict[str, Any]:
        cleaned = _single_line(text, 240)
        if not cleaned:
            return {"event": "neutral", "intensity": 0, "reason": "", "target": "none", "rule": "", "confidence": 1.0}
        if self._is_structured_or_diagnostic_text(cleaned):
            return {"event": "neutral", "intensity": 0, "reason": "结构化/日志/代码类文本不作为情绪依据", "target": "none", "rule": "diagnostic_skip", "confidence": 0.2}
        atrelay_checker = getattr(self, "_message_looks_like_atrelay_request", None)
        if callable(atrelay_checker):
            try:
                if atrelay_checker(cleaned):
                    return {"event": "neutral", "intensity": 0, "reason": "转述/带话请求不作为 Bot 自身情绪依据", "target": "other", "rule": "atrelay_skip", "confidence": 0.86}
            except Exception:
                pass
        lower = cleaned.lower()
        intent_source = str((intent_context or {}).get("source") or "")
        target_hint, third_party_hint = self._intent_target_hint(cleaned)
        self_low = bool(re.search(r"(我好|我真|我太|我是不是|我就是|我是).{0,12}(废物|垃圾|没用|傻|笨|恶心|讨厌)", cleaned))
        direct_bot_negative = bool(
            re.search(r"(讨厌你|烦你|不想理你|你.{0,4}(滚|闭嘴)|你(?:真|也|就是|是|太|真的|这个|怎么这么|为什么这么).{0,8}(恶心|废物|垃圾|没用|太吵|打扰|烦死|吵死))", cleaned)
            or re.search(r"((bot|机器人|插件|助手|ai|AI).{0,8}(垃圾|废物|恶心|没用)|(垃圾|废物|恶心|没用).{0,8}(bot|机器人|插件|助手|ai|AI))", cleaned)
        )
        severe_hurt = (
            "滚" in cleaned
            or "闭嘴" in cleaned
            or "恶心" in cleaned
            or "废物" in cleaned
            or "垃圾" in cleaned
            or "讨厌你" in cleaned
            or "烦你" in cleaned
            or "不想理你" in cleaned
            or re.search(r"(只是|不过|不就是).{0,6}(bot|机器人|工具|代码)", lower)
        )
        identity_hurt = bool(
            re.search(r"(玻璃心|假装|演的|装的|设定|工具人|没感情|别装|别演|虚拟的|假的)", cleaned)
            and target_hint
        )
        mild_hurt = bool(
            re.search(r"(太烦|吵死|烦死|没用|笨死|傻)", cleaned)
            and target_hint
        )
        apology = bool(re.search(r"(对不起|抱歉|我错了|不是故意|原谅|别生气|别难过|哄哄|哄你)", cleaned))
        comfort = bool(re.search(r"(摸摸|贴贴|抱抱|亲亲|乖|不哭|别伤心|陪你|抱一下)", cleaned))
        praise = bool(re.search(r"(喜欢你|爱你|可爱|厉害|真好|谢谢你|辛苦|最棒|夸夸)", cleaned))
        if self_low:
            return {"event": "comfort_need", "intensity": 62, "reason": "用户自我否定或低落", "target": "self", "rule": "self_low", "confidence": 0.88}
        if intent_source in {"strong_rule", "targeted_boundary_rule"} and not direct_bot_negative and not identity_hurt:
            return {"event": "neutral", "intensity": 0, "reason": "用户在表达相处边界", "target": "bot", "rule": "boundary_goes_relationship", "confidence": 0.82}
        if third_party_hint and severe_hurt and not direct_bot_negative:
            return {"event": "external_negative", "intensity": 54, "reason": "用户在评价第三方", "target": "other", "rule": "third_party_negative", "confidence": 0.78}
        if severe_hurt:
            confidence = 0.9 if direct_bot_negative else (0.72 if target_hint and not third_party_hint else 0.58)
            return {
                "event": "hurt",
                "intensity": 90 if direct_bot_negative else 72,
                "reason": "强否定或驱赶",
                "target": "bot" if direct_bot_negative else "ambiguous",
                "rule": "severe_hurt",
                "confidence": confidence,
            }
        if identity_hurt:
            return {"event": "hurt", "intensity": 76, "reason": "否定情感真实性或人格", "target": "bot", "rule": "identity_hurt", "confidence": 0.84}
        if mild_hurt:
            return {"event": "hurt", "intensity": 48, "reason": "轻度否定或拉开距离", "target": "bot", "rule": "mild_hurt", "confidence": 0.66}
        if apology:
            return {"event": "apology", "intensity": 68, "reason": "道歉或修复", "target": "bot", "rule": "apology", "confidence": 0.84}
        if comfort:
            return {"event": "comfort", "intensity": 46, "reason": "安抚亲密互动", "target": "bot", "rule": "comfort", "confidence": 0.78}
        if praise:
            return {"event": "praise", "intensity": 38, "reason": "正向肯定", "target": "bot" if target_hint else "ambiguous", "rule": "praise", "confidence": 0.78 if target_hint else 0.56}
        return {"event": "neutral", "intensity": 0, "reason": "", "target": "none", "rule": "", "confidence": _safe_float((intent_context or {}).get("confidence"), 0.5)}

    def _emotion_judgement_provider_id(self) -> str:
        return self._task_provider(
            getattr(self, "emotion_judgement_provider_id", ""),
            getattr(self, "troubleshooting_provider_id", ""),
            getattr(self, "relationship_analysis_provider_id", ""),
            getattr(self, "mai_style_provider_id", ""),
            getattr(self, "llm_provider_id", ""),
        )

    def _should_use_llm_emotion_judgement(self, text: str, intent: dict[str, Any]) -> bool:
        if not bool(getattr(self, "enable_llm_emotion_judgement", False)):
            return False
        if self._is_structured_or_diagnostic_text(text):
            return False
        mode = str(getattr(self, "emotion_judgement_mode", "suspicious") or "suspicious").lower()
        if mode in {"off", "none", "disabled"}:
            return False
        if mode in {"always", "all"}:
            return True
        confidence = _safe_float(intent.get("confidence"), 0.5)
        emotion_confidence = _safe_float(intent.get("emotion_confidence"), confidence)
        event = str(intent.get("emotion_event") or "neutral")
        source = str(intent.get("source") or "")
        return (
            event != "neutral"
            or confidence < 0.72
            or emotion_confidence < 0.72
            or source in {"weak_boundary_ignored", "soft_boundary_play_rule", "targeted_boundary_rule"}
            or bool(re.search(r"(别|不要|讨厌|烦|滚|闭嘴|对不起|抱歉|喜欢你|爱你|摸摸|抱抱)", text))
        )

    def _merge_llm_emotion_judgement(self, base_intent: dict[str, Any], payload: Any) -> dict[str, Any] | None:
        if not isinstance(base_intent, dict) or not isinstance(payload, dict):
            return None
        event = str(payload.get("event") or payload.get("emotion_event") or "neutral").strip().lower()
        aliases = {
            "none": "neutral",
            "normal": "neutral",
            "negative_to_bot": "hurt",
            "hurt_bot": "hurt",
            "repair": "apology",
            "apologize": "apology",
            "soothe": "comfort",
            "low_self": "comfort_need",
            "external": "external_negative",
        }
        event = aliases.get(event, event)
        allowed_events = {"neutral", "hurt", "apology", "comfort", "praise", "comfort_need", "external_negative"}
        if event not in allowed_events:
            return None
        local_source = str(base_intent.get("source") or "")
        local_text = _single_line(base_intent.get("text"), 240)
        if event == "hurt" and local_source in {"strong_rule", "targeted_boundary_rule"}:
            strong_negative = bool(
                re.search(r"(滚|闭嘴|恶心|废物|垃圾|讨厌你|烦你|不想理你|没感情|假的|别装|别演|工具人)", local_text)
            )
            if not strong_negative:
                return None
        target = str(payload.get("target") or "none").strip().lower()
        target_aliases = {
            "bot_self": "bot",
            "assistant": "bot",
            "character": "bot",
            "user": "self",
            "third_party": "other",
            "unknown": "ambiguous",
        }
        target = target_aliases.get(target, target)
        if target not in {"bot", "self", "other", "ambiguous", "none"}:
            target = "ambiguous" if event != "neutral" else "none"
        confidence = _safe_float(payload.get("confidence"), 0.0, 0.0)
        if confidence < 0.65:
            return None
        intensity = _safe_int(payload.get("intensity"), 0, 0, 100)
        if event == "neutral":
            intensity = 0
            target = "none"
        elif intensity <= 0:
            intensity = 60
        if event == "hurt" and target not in {"bot", "ambiguous"}:
            event = "external_negative" if target == "other" else "neutral"
            intensity = 54 if event == "external_negative" else 0
        reason = _single_line(payload.get("reason"), 80) or "模型复核"
        merged = dict(base_intent)
        merged.update(
            {
                "emotion_event": event,
                "emotion_intensity": intensity,
                "emotion_reason": reason,
                "emotion_target": target,
                "emotion_rule": "llm_emotion_judgement",
                "emotion_confidence": round(float(confidence), 2),
                "llm_emotion_judgement": True,
                "llm_emotion_judgement_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        )
        return merged

    async def _refine_inbound_emotion_with_model(self, user_id: str, text: str, local_intent: dict[str, Any]) -> None:
        cleaned = _single_line(text, 240)
        if not cleaned or not isinstance(local_intent, dict):
            return
        prompt = f"""
你是私聊情绪变化判断器。只判断“用户这句话是否会改变 Bot 自身短期情绪余波”，不要生成回复。

可选 event：
- neutral：不应改变 Bot 自身情绪余波。
- hurt：用户言行指向 Bot/当前角色，足以让 Bot 被刺到或不满。
- apology：用户在向 Bot 道歉或修复关系。
- comfort：用户在安抚 Bot。
- praise：用户在肯定/夸 Bot。
- comfort_need：用户自己低落，需要被接住，不代表伤害 Bot。
- external_negative：用户在骂第三方、代码、作业、日志或别人，不代表伤害 Bot。

target 只能是 bot/self/other/ambiguous/none。
只有明确指向 Bot/当前角色时，才能判断为 hurt；玩笑、撒娇、日志、代码、转述内容要保守。
用户只是表达相处边界、要求少贴近、别撒娇、别靠近、别打扰时，优先判为 neutral；这类边界交给关系距离感处理，不属于伤害 Bot。
输出 JSON，不要解释：
{{"event":"neutral|hurt|apology|comfort|praise|comfort_need|external_negative","target":"bot|self|other|ambiguous|none","intensity":0-100,"confidence":0.0-1.0,"reason":"20字内原因"}}

用户消息：
{cleaned}

本地快判：
{json.dumps({k: local_intent.get(k) for k in ("intent", "emotion", "source", "reason", "emotion_event", "emotion_target", "emotion_intensity", "emotion_reason", "emotion_confidence")}, ensure_ascii=False)}
""".strip()
        provider_id = self._emotion_judgement_provider_id()
        raw = ""
        try:
            raw = await self._llm_call(
                prompt,
                max_tokens=180,
                provider_id=provider_id,
                task="emotion_judgement",
            ) or ""
            payload = self._extract_json_payload(raw)
            refined = self._merge_llm_emotion_judgement(local_intent, payload)
        except Exception as exc:
            refined = None
            logger.debug("[PrivateCompanion] 情绪变化模型判断失败: %s", _single_line(exc, 120))
        async with self._data_lock:
            user = self._get_user(user_id)
            pending = user.get("pending_emotion_judgement") if isinstance(user.get("pending_emotion_judgement"), dict) else {}
            if _single_line(pending.get("text"), 240) != cleaned:
                return
            intent_to_apply = refined if isinstance(refined, dict) else dict(local_intent)
            if self.enable_intent_emotion_analysis:
                user["intent_profile"] = intent_to_apply
            self._update_relationship_state_from_intent(user, intent_to_apply)
            user["pending_emotion_judgement"] = {}
            if refined:
                logger.info(
                    "[PrivateCompanion] 情绪变化模型判断完成: user=%s event=%s target=%s intensity=%s confidence=%s reason=%s",
                    user_id,
                    refined.get("emotion_event"),
                    refined.get("emotion_target"),
                    refined.get("emotion_intensity"),
                    refined.get("emotion_confidence"),
                    _single_line(refined.get("emotion_reason"), 80),
                )
            else:
                user["last_emotion_judgement_error"] = _single_line(raw, 160) if raw else "empty_or_invalid"
            self._save_data_sync()

    def _decay_relationship_mood_score(self, state: dict[str, Any], *, now: float | None = None) -> int:
        now = now or _now_ts()
        score = _safe_int(state.get("mood_score"), 0, -100, 100)
        last_ts = _safe_float(state.get("mood_updated_ts"), 0)
        if score == 0 or last_ts <= 0 or now <= last_ts:
            state["mood_updated_ts"] = now
            return score
        hours = max(0.0, (now - last_ts) / 3600)
        recovery = max(1, _safe_int(getattr(self, "emotional_gate_recovery_per_hour", 24), 24, 1, 60))
        delta = int(hours * recovery)
        if delta <= 0:
            return score
        if score < 0:
            score = min(0, score + delta)
        else:
            score = max(0, score - max(1, delta // 2))
        state["mood_score"] = score
        state["mood_updated_ts"] = now
        return score

    def _update_relationship_state_from_intent(self, user: dict[str, Any], intent: dict[str, Any]) -> None:
        if not isinstance(intent, dict):
            return
        emotion_enabled = bool(getattr(self, "enable_emotion_simulation", True))
        relation_enabled = bool(getattr(self, "enable_relationship_state_machine", True))
        if not (emotion_enabled or relation_enabled):
            return
        state = user.setdefault("relationship_state", {})
        if not isinstance(state, dict):
            state = {}
            user["relationship_state"] = state
        now = _now_ts()
        previous_mode = str(state.get("mode") or "normal")
        current = previous_mode
        mood_score = self._decay_relationship_mood_score(state, now=now) if emotion_enabled else 0
        inbound_intent = str(intent.get("intent") or "chat")
        pressure = _safe_int(intent.get("pressure"), 0, 0, 5)
        emotion_event = str(intent.get("emotion_event") or "neutral")
        intensity = _safe_int(intent.get("emotion_intensity"), 0, 0, 100)
        intent_confidence = _safe_float(intent.get("confidence"), 0.5)
        emotion_confidence = _safe_float(intent.get("emotion_confidence"), intent_confidence)
        if emotion_event != "neutral" and emotion_confidence < 0.65:
            emotion_event = "neutral"
            intensity = 0
        reason = _single_line(intent.get("emotion_reason"), 80)
        target = _single_line(intent.get("emotion_target"), 24) or "none"
        rule = _single_line(intent.get("emotion_rule"), 40)
        hurt_threshold = _safe_int(getattr(self, "emotional_gate_hurt_threshold", 70), 70, 10, 100)
        refuse_threshold = _safe_int(getattr(self, "emotional_gate_refuse_threshold", 90), 90, 20, 100)
        if refuse_threshold <= hurt_threshold:
            refuse_threshold = min(100, hurt_threshold + 5)
        min_until = _safe_float(state.get("emotion_min_until"), 0)
        if emotion_enabled and emotion_event == "hurt" and target in {"bot", "ambiguous"} and intensity >= hurt_threshold:
            over_threshold = max(0, intensity - hurt_threshold)
            penalty = max(8, 12 + int(over_threshold * 0.65))
            if target == "ambiguous":
                penalty = max(6, int(penalty * 0.75))
            if emotion_confidence < 0.82:
                penalty = max(5, int(penalty * 0.8))
            mood_score = max(-100, mood_score - penalty)
            hurt_minutes = min(
                _safe_int(getattr(self, "emotional_gate_max_hurt_minutes", 90), 90, 10, 720),
                max(10, int(intensity * 1.0)),
            )
            state["hurt_until"] = now + hurt_minutes * 60
            min_minutes = 15 if abs(mood_score) >= refuse_threshold else 8
            state["emotion_min_until"] = max(min_until, now + min(min_minutes, hurt_minutes) * 60)
            state["silence_turns"] = max(
                _safe_int(state.get("silence_turns"), 0, 0, 5),
                2 if abs(mood_score) >= refuse_threshold else 1,
            )
            state["last_hurt_reason"] = reason or "用户表达伤害性内容"
            state["last_hurt_text"] = _single_line(intent.get("text"), 160)
            current = "refusing" if abs(mood_score) >= refuse_threshold else "hurt"
        elif emotion_enabled and emotion_event in {"apology", "comfort", "praise"}:
            if emotion_event == "apology":
                recover_ratio = 0.9
                min_recover = 18
            elif emotion_event == "comfort":
                recover_ratio = 0.55
                min_recover = 8
            else:
                recover_ratio = 0.2
                min_recover = 2
            mood_score = min(100, mood_score + max(min_recover, int(intensity * recover_ratio)))
            silence_step = 2 if emotion_event == "apology" else (1 if emotion_event == "comfort" else 0)
            state["silence_turns"] = max(0, _safe_int(state.get("silence_turns"), 0, 0, 5) - silence_step)
            repair_cleared = False
            if emotion_event in {"apology", "comfort"} and mood_score > -hurt_threshold:
                state["hurt_until"] = 0
                state["emotion_min_until"] = 0
                repair_cleared = True
            elif emotion_event == "praise" and mood_score >= 0:
                state["hurt_until"] = 0
                state["emotion_min_until"] = 0
                repair_cleared = True
            if mood_score >= 45 and inbound_intent in {"intimacy", "play"}:
                current = "attached"
            elif mood_score < -20 and not repair_cleared:
                current = "hurt"
            else:
                current = "warming" if inbound_intent in {"intimacy", "play"} else "normal"
        elif emotion_enabled and emotion_event == "comfort_need":
            current = "careful"
            state["last_care_reason"] = reason
        elif emotion_enabled and emotion_event == "external_negative":
            current = "careful"
            state["last_external_negative_reason"] = reason
        elif (
            emotion_enabled
            and previous_mode in {"hurt", "refusing"}
            and _safe_float(state.get("emotion_min_until"), 0) > now
            and _safe_float(state.get("hurt_until"), 0) > now
            and mood_score < 0
        ):
            current = "refusing" if previous_mode == "refusing" or abs(mood_score) >= refuse_threshold else "hurt"
        elif relation_enabled and inbound_intent == "boundary" and intent_confidence >= 0.68:
            current = "backoff"
            state["backoff_until"] = now + 6 * 3600
            state["last_backoff_reason"] = reason or "用户表达边界或不想继续当前互动"
            state["last_backoff_text"] = _single_line(intent.get("text"), 160)
        elif relation_enabled and pressure >= 2 and intent_confidence >= 0.65:
            current = "careful"
        elif relation_enabled and inbound_intent in {"intimacy", "play"} and intent_confidence >= 0.68:
            current = "attached" if emotion_enabled and mood_score >= 45 else "warming"
        elif emotion_enabled and _safe_float(state.get("hurt_until"), 0) > now and mood_score <= -hurt_threshold:
            current = "refusing" if abs(mood_score) >= refuse_threshold else "hurt"
        elif (
            relation_enabled
            and previous_mode == "backoff"
            and _safe_float(state.get("backoff_until"), 0) > now
        ):
            current = "backoff"
        else:
            current = "normal"
        if current not in {"hurt", "refusing"} and _safe_int(state.get("silence_turns"), 0, 0, 5) > 0:
            state["silence_turns"] = max(0, _safe_int(state.get("silence_turns"), 0, 0, 5) - 1)
        state["mode"] = current
        state["mood_score"] = mood_score
        state["mood_updated_ts"] = now
        state["last_intent"] = inbound_intent
        state["last_emotion"] = str(intent.get("emotion") or "neutral")
        state["last_emotion_event"] = emotion_event
        state["last_emotion_intensity"] = intensity
        state["last_emotion_reason"] = reason
        state["last_emotion_target"] = target
        state["last_emotion_rule"] = rule
        state["last_intent_confidence"] = round(float(intent_confidence), 2)
        state["last_emotion_confidence"] = round(float(emotion_confidence), 2)
        state["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        if emotion_event != "neutral" or current in {"hurt", "refusing", "attached"}:
            logger.info(
                "[PrivateCompanion] 情绪余波判定: event=%s target=%s rule=%s intensity=%s score=%s mode=%s->%s silence=%s reason=%s text=%s",
                emotion_event,
                target,
                rule or "-",
                intensity,
                mood_score,
                previous_mode,
                current,
                _safe_int(state.get("silence_turns"), 0, 0, 5),
                reason or "-",
                _single_line(intent.get("text"), 120),
            )
        vent_threshold = _safe_int(getattr(self, "qzone_emotional_vent_threshold", 90), 90, 40, 100)
        if (
            current == "refusing"
            and previous_mode != "refusing"
            and abs(mood_score) >= vent_threshold
            and target in {"bot", "ambiguous"}
        ):
            role_getter = getattr(self, "_private_user_role", None)
            try:
                role = role_getter(user, str(user.get("user_id") or "")) if callable(role_getter) else ""
            except Exception:
                role = ""
            if role != "owner":
                logger.info(
                    "[PrivateCompanion] 公开心情动态跳过: user_role=%s score=%s",
                    role or "friend",
                    abs(mood_score),
                )
                return
            vent = getattr(self, "_maybe_publish_qzone_emotional_vent", None)
            if callable(vent):
                try:
                    asyncio.create_task(vent(user_snapshot=deepcopy(user), relationship_state=deepcopy(state), intent=deepcopy(intent)))
                except Exception as exc:
                    logger.debug("[PrivateCompanion] 公开心情动态任务创建失败: %s", _single_line(exc, 120))

    def _remember_passive_reply_topic(self, user: dict[str, Any], text: str, inbound_text: str = "") -> None:
        if not self.enable_passive_topic_suppression:
            return
        signature = self._proactive_topic_signature(text, inbound_text)
        if not signature:
            return
        recent = self._cleanup_recent_passive_topics(user)
        recent.append({"ts": _now_ts(), "signature": signature, "text": _single_line(text, 120)})
        del recent[:-18]

    @staticmethod
    def _music_album_reply_needs_disambiguation_fix(text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        return any(
            token in compact
            for token in (
                "哪个专辑",
                "哪一个专辑",
                "我不太确定你说的是哪一个",
                "你说的是哪一个",
                "发到哪里",
                "私聊里还是群里",
            )
        )

    @staticmethod
    def _music_album_reply_from_context(context: dict[str, Any], *, user_text: str = "") -> str:
        album = _single_line(context.get("album"), 60)
        artist = _single_line(context.get("artist"), 40)
        platform = _single_line(context.get("platform"), 24)
        parts: list[str] = []
        if artist and album:
            parts.append(f"看到了，这是 {artist} 的《{album}》专辑。")
        elif album:
            parts.append(f"看到了，这张是《{album}》专辑。")
        elif artist:
            parts.append(f"看到了，这是 {artist} 的专辑卡。")
        else:
            parts.append("看到了，这是一张音乐专辑卡。")
        if platform:
            parts.append(f"来源是{platform}。")
        if re.search(r"(发|列|整理|曲目|歌单|几首歌)", str(user_text or "")):
            parts.append("如果你要，我可以直接把这张专辑的曲目列出来。")
        if re.search(r"(发到哪里|私聊|群里)", str(user_text or "")):
            parts.append("你要是愿意，也可以告诉我发到私聊还是群里。")
        else:
            parts.append("你要是愿意，我也可以直接帮你把曲目列出来。")
        return "".join(parts)

    def _smart_silence_trigger_reason(self, inbound_text: str) -> str:
        cleaned = _single_line(inbound_text, 260)
        if not cleaned:
            return ""
        compact = re.sub(r"\s+", "", cleaned)
        if not compact:
            return ""
        direct_markers = (
            "别聊这个",
            "不要聊这个",
            "不聊这个",
            "别说这个",
            "不要说这个",
            "别提这个",
            "不要提这个",
            "不想聊这个",
            "不想说这个",
            "不想继续",
            "别继续",
            "不要继续",
            "别问了",
            "不要问了",
            "别追问",
            "不要追问",
            "到此为止",
            "这个话题到此为止",
            "结束这个话题",
            "结束话题",
            "换个话题",
            "跳过这个",
            "略过这个",
            "打住",
            "停一下",
            "先别说了",
            "先不说了",
            "别说了",
            "不要回复",
            "不用回复",
            "别回了",
            "不必回复",
        )
        for marker in direct_markers:
            if marker in compact:
                return marker
        topic_patterns = (
            r"(这个|这件事|这事|这话|这个话题|这话题).{0,8}(算了|别聊|别说|别提|不聊|不说|不提|跳过|略过|到此为止)",
            r"(算了|够了|停|打住).{0,8}(别聊|别说|别问|别提|不聊|不说|不问|不提)",
            r"(别|不要|不用).{0,6}(安慰|解释|分析|劝|讲道理|追问)",
        )
        for pattern in topic_patterns:
            if re.search(pattern, compact):
                return "topic_boundary"
        return ""

    async def _decide_smart_silence(
        self,
        *,
        inbound_text: str,
        response_text: str,
        user: dict[str, Any] | None = None,
        session_kind: str = "",
        recent_context: list[str] | None = None,
    ) -> dict[str, Any]:
        if not bool(getattr(self, "enable_smart_silence", True)):
            return {"decision": "send", "reason": "disabled", "confidence": 0.0, "source": "disabled"}
        inbound = _single_line(inbound_text, 320)
        response = _single_line(response_text, 600)
        trigger = self._smart_silence_trigger_reason(inbound)
        if not trigger:
            return {"decision": "send", "reason": "no_boundary_trigger", "confidence": 0.0, "source": "prefilter"}
        if not response:
            return {"decision": "send", "reason": "empty_response", "confidence": 0.0, "source": "prefilter"}
        cache_key = hashlib.sha1(
            f"{session_kind}\n{inbound}\n{response[:240]}".encode("utf-8", errors="ignore")
        ).hexdigest()
        cache = getattr(self, "_smart_silence_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_smart_silence_cache", cache)
        now = _now_ts()
        cached = cache.get(cache_key)
        if isinstance(cached, dict) and now - _safe_float(cached.get("ts"), 0) <= 120:
            result = dict(cached.get("result") or {})
            result["source"] = "cache"
            return result
        if len(cache) > 256:
            for key, item in list(cache.items())[:64]:
                if not isinstance(item, dict) or now - _safe_float(item.get("ts"), 0) > 120:
                    cache.pop(key, None)

        provider_id = self._task_provider(
            getattr(self, "smart_silence_provider_id", ""),
            getattr(self, "response_review_provider_id", ""),
            getattr(self, "smart_message_debounce_provider_id", ""),
            getattr(self, "mai_style_provider_id", ""),
            getattr(self, "llm_provider_id", ""),
        )
        if not provider_id:
            return {"decision": "send", "reason": "no_provider", "confidence": 0.0, "source": "prefilter"}

        last_companion = _single_line((user or {}).get("last_companion_message"), 260) if isinstance(user, dict) else ""
        recent_lines = []
        for item in (recent_context or [])[-6:]:
            line = _single_line(item, 120)
            if line:
                recent_lines.append(f"- {line}")
        prompt = f"""
你是聊天回复发送前的智能沉默判定器。判断用户是否在表达“不要继续这个话题/不要再追问/先别回复/换掉当前话题”，从而应该直接不发这条待发送回复。

只输出 JSON：{{"decision":"send|silent","confidence":0-1,"reason":"不超过20字"}}

判定原则：
- 用户明确说别聊、别问、别继续、到此为止、算了别说了、换个话题，且待发送回复仍在确认、安慰、解释、追问或继续这个话题，decision=silent。
- 如果用户同一句已经开启了新请求或新问题，例如“算了，帮我看这个”“换个话题，今天吃什么”，且待发送回复是在处理新请求，decision=send。
- 如果待发送回复只是“好，那不聊这个了”“嗯我闭嘴了”这类对边界的重复确认，通常 silent；真实聊天里安静退开更自然。
- 不要因为用户说“算了”两个字就一定沉默，要看它是不是结束当前话题，而不是普通口头禅。
- 不确定时 send。

会话类型：{_single_line(session_kind, 40) or "未知"}
触发词：{trigger}

【最近上下文】
{chr(10).join(recent_lines) or "（无）"}

【Bot 上次发出的话】
{last_companion or "（无）"}

【用户刚才说】
{inbound}

【待发送回复】
{response}
""".strip()
        timeout_seconds = max(
            0.2,
            min(5.0, _safe_float(getattr(self, "smart_silence_model_timeout_seconds", 1.2), 1.2, 0.2)),
        )
        started = time.perf_counter()
        raw = ""
        try:
            raw = await asyncio.wait_for(
                self._llm_call(
                    prompt,
                    max_tokens=100,
                    provider_id=provider_id,
                    task="smart_silence",
                ),
                timeout=timeout_seconds,
            ) or ""
        except asyncio.TimeoutError:
            result = {"decision": "send", "reason": f"timeout>{timeout_seconds:.1f}s", "confidence": 0.0, "source": "timeout"}
            cache[cache_key] = {"ts": now, "result": result}
            logger.info(
                "[PrivateCompanion] 智能沉默判定超时,默认放行: trigger=%s timeout=%.1fs text=%s",
                trigger,
                timeout_seconds,
                _single_line(inbound, 100),
            )
            return result
        except Exception as exc:
            result = {"decision": "send", "reason": _single_line(exc, 80), "confidence": 0.0, "source": "error"}
            cache[cache_key] = {"ts": now, "result": result}
            logger.info("[PrivateCompanion] 智能沉默判定失败,默认放行: %s", _single_line(exc, 120))
            return result

        payload = self._extract_json_payload(raw or "")
        if not isinstance(payload, dict):
            result = {"decision": "send", "reason": "invalid_json", "confidence": 0.0, "source": "model"}
            cache[cache_key] = {"ts": now, "result": result}
            return result
        decision = str(payload.get("decision") or "").strip().lower()
        if decision not in {"send", "silent"}:
            decision = "send"
        confidence = max(0.0, min(1.0, _safe_float(payload.get("confidence"), 0.0, 0.0)))
        reason = _single_line(payload.get("reason"), 80) or "模型判定"
        threshold = max(0.0, min(1.0, _safe_float(getattr(self, "smart_silence_min_confidence", 0.66), 0.66, 0.0)))
        if decision == "silent" and confidence < threshold:
            decision = "send"
            reason = f"低置信度:{reason}"
        result = {
            "decision": decision,
            "reason": reason,
            "confidence": confidence,
            "source": "model",
            "trigger": trigger,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }
        cache[cache_key] = {"ts": now, "result": result}
        logger.info(
            "[PrivateCompanion] 智能沉默判定: decision=%s confidence=%.2f trigger=%s elapsed=%dms reason=%s user=%s reply=%s",
            decision,
            confidence,
            trigger,
            result["elapsed_ms"],
            reason,
            _single_line(inbound, 120),
            _single_line(response, 140),
        )
        return result

    async def _review_and_rewrite_response(
        self,
        user: dict[str, Any],
        inbound_text: str,
        response_text: str,
        *,
        music_album_context: dict[str, Any] | None = None,
    ) -> str:
        relay_claim_checker = getattr(self, "_unexecuted_relay_claim_reason", None)
        if callable(relay_claim_checker):
            relay_claim_note = relay_claim_checker(response_text)
            if relay_claim_note:
                fallback_builder = getattr(self, "_fallback_unexecuted_relay_reply", None)
                fallback = fallback_builder(inbound_text) if callable(fallback_builder) else ""
                logger.info(
                    "[PrivateCompanion] 被动回复含未执行转述承诺,已改为诚实边界: reason=%s before=%s after=%s",
                    relay_claim_note,
                    _single_line(response_text, 120),
                    _single_line(fallback, 120),
                )
                return fallback or response_text
        if isinstance(music_album_context, dict) and self._music_album_reply_needs_disambiguation_fix(response_text):
            fallback = self._music_album_reply_from_context(music_album_context, user_text=inbound_text)
            if fallback:
                logger.info(
                    "[PrivateCompanion] 音乐专辑回复已按卡片上下文纠偏: before=%s after=%s",
                    _single_line(response_text, 120),
                    _single_line(fallback, 160),
                )
                return fallback
        if not bool(getattr(self, "enable_response_self_review", True)):
            return self._fallback_temporal_or_continuity_confused_reply(inbound_text, response_text, user=user) or response_text
        flags = self._response_review_flags(response_text, user, inbound_text=inbound_text)
        if not flags:
            return response_text
        review_mode = str(getattr(self, "response_review_mode", "severe_only") or "severe_only").strip().lower()
        if review_mode not in {"local_only", "severe_only", "full"}:
            review_mode = "severe_only"
        if review_mode == "local_only":
            return self._fallback_temporal_or_continuity_confused_reply(
                inbound_text,
                response_text,
                flags=flags,
                user=user,
            ) or response_text
        severe_flags = self._response_review_severe_flags(flags)
        if review_mode == "severe_only" and not severe_flags:
            return response_text
        effective_flags = severe_flags if review_mode == "severe_only" else flags
        lightweight_checker = getattr(self, "_is_lightweight_private_passive_inbound", None)
        if callable(lightweight_checker) and lightweight_checker(inbound_text):
            critical_flags = {
                "too_long",
                "meta_or_assistant",
                "over_structured",
                "leaks_internal",
                "repeats_last_bot_message",
                "invalid_current_time_anchor",
                "false_no_reply_claim",
            }
            if not any(flag in critical_flags for flag in effective_flags):
                return response_text
        intent = user.get("intent_profile") if isinstance(user.get("intent_profile"), dict) else {}
        allow_repeat = self._inbound_explicitly_requests_repeat(inbound_text)
        last_message = _single_line(user.get("last_companion_message"), 300)
        last_message_label = "用户本轮明确要求复述上一条,仅用于确认原文" if allow_repeat else "刚才 Bot 已经说过，禁止复述或换皮重复"
        prompt = f"""
把下面这条回复改写成更像真实私聊里的自然回复。
保留原意,不要新增事实,不要解释你在改写。

【用户刚才说】
{_single_line(inbound_text, 260) or '（无）'}

【{last_message_label}】
{last_message or '（无）'}

【原回复】
{response_text}

【需要修正的问题】
{", ".join(effective_flags)}

【当前意图/情绪】
{intent.get('intent', 'chat')}｜{intent.get('emotion', 'neutral')}｜{intent.get('reply_style', 'natural')}

【真实当前时间】
{self._environment_now().strftime('%Y-%m-%d %H:%M')}

要求：
- 只输出改写后的正文
- 不要标题、列表、JSON、括号动作、系统/AI/提示词字眼
- 普通闲聊尽量 1 到 3 句；求助类可以保留必要步骤,但更口语
- 如果用户只是短句闲聊、报天气、说一句状态或轻轻接话,改成 1 句或 2 句短回复；不要扩展成关心清单、建议清单或连续状态复述
- 如果用户情绪低,先接住情绪,少讲道理
- 如果是边界/不想被打扰,短一点,退一步
- 如果回复已经在说晚安、睡觉、做梦、告别,不要再突然追加天气、日程、生活观察或另一个新话题
- 如果用户明确要求复述/原话/再说一遍,允许保留上一条 Bot 原文,不要把它误判为复读
- 如果问题是无意重复上一条 Bot 消息,必须直接承接用户这句话,不要再说上一条里的“吃饱犯困/下午还有事/有什么安排”等同义内容
- 如果用户并未要求复述,且无论怎样改写都只能重复上一条 Bot 消息,只输出 {self._response_review_drop_marker()}；不要为了“不重复”再补一句客套话
- 如果原回复为了表现困、迷糊、半梦半醒或低能量而变得含混,优先改成清楚承接用户；状态只能留在语气里,不能牺牲回答质量
- 如果用户没有问 Bot 近况,删掉由内部模拟状态带出的“我刚在/正在/继续做某事”等动作或日程复述；不要把模拟状态说成现实事件
- 如果问题是表达学习过头、异常断句或照抄用户样本,保留意思,改成自然中文私聊；不要为了模仿口癖而加奇怪逗号、空格、断句或复读用户原话
- 如果问题是 invalid_current_time_anchor,删除或改正“快十一点/该睡了/晚安”等与真实当前时间冲突的说法；不要继续围绕错误时间展开
- 如果问题是 false_no_reply_claim,不要说“看你没回我/等你回话/你没理我”；用户本轮已经发来消息,直接解释上一句或重新接住当前问题
""".strip()
        started = time.perf_counter()
        try:
            rewritten = await self._llm_call(
                prompt,
                max_tokens=260,
                provider_id=self._task_provider(self.response_review_provider_id, self.mai_style_provider_id),
                task="response_review",
            )
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 被动回复模型自检失败,保留原回复: flags=%s error=%s",
                ",".join(effective_flags),
                _single_line(exc, 160),
            )
            return self._fallback_temporal_or_continuity_confused_reply(
                inbound_text,
                response_text,
                flags=effective_flags,
                user=user,
            ) or response_text
        logger.info(
            "[PrivateCompanion] 被动回复模型自检完成: mode=%s flags=%s elapsed=%dms",
            review_mode,
            ",".join(effective_flags),
            int((time.perf_counter() - started) * 1000),
        )
        cleaned = str(rewritten or "").strip()
        if not cleaned:
            return response_text
        if self._is_response_review_drop_marker(cleaned):
            logger.info(
                "[PrivateCompanion] 被动回复模型自检判定重复,已标记丢弃: flags=%s before=%s",
                ",".join(effective_flags),
                _single_line(response_text, 120),
            )
            return self._response_review_drop_marker()
        if len(cleaned) > max(len(response_text) + 80, self.response_review_max_chars + 160):
            fallback = self._fallback_overlong_casual_reply(inbound_text, response_text)
            return fallback or response_text
        if re.search(r"(提示词|系统|JSON|改写后|以下是)", cleaned, re.IGNORECASE):
            return self._fallback_temporal_or_continuity_confused_reply(
                inbound_text,
                response_text,
                flags=effective_flags,
                user=user,
            ) or response_text
        if last_message and not allow_repeat and self._text_repeats_recent_message(cleaned, last_message):
            logger.info(
                "[PrivateCompanion] 被动回复模型自检后仍复读,已标记丢弃: before=%s",
                _single_line(cleaned, 120),
            )
            return self._response_review_drop_marker()
        if (
            any(flag in effective_flags for flag in ("casual_overexplained", "weather_overexplained"))
            and len(cleaned) > self._casual_reply_review_limit(inbound_text)
        ):
            fallback = self._fallback_overlong_casual_reply(inbound_text, cleaned)
            return fallback or cleaned
        return cleaned

    def _response_review_severe_flags(self, flags: list[str]) -> list[str]:
        severe = {
            "meta_or_assistant",
            "leaks_internal",
            "repeats_last_bot_message",
            "casual_overexplained",
            "weather_overexplained",
            "invalid_current_time_anchor",
            "false_no_reply_claim",
        }
        if self._expression_style_review_enabled():
            severe.update({"unnatural_punctuation", "expression_overfit", "copied_user_expression_sample"})
        return [flag for flag in flags if flag in severe]

    def _casual_reply_review_limit(self, inbound_text: str) -> int:
        inbound_compact = self._compact_repeat_text(inbound_text)
        if len(inbound_compact) <= 12:
            return min(140, max(90, self.response_review_max_chars // 2))
        if len(inbound_compact) <= 28:
            return min(180, max(120, int(self.response_review_max_chars * 0.65)))
        return self.response_review_max_chars

    def _is_short_casual_inbound_for_review(self, inbound_text: str, user: dict[str, Any]) -> bool:
        inbound = str(inbound_text or "").strip()
        if not inbound:
            return False
        if len(self._compact_repeat_text(inbound)) > 32:
            return False
        intent_profile = user.get("intent_profile") if isinstance(user.get("intent_profile"), dict) else {}
        if str(intent_profile.get("intent") or "") in {"help", "task", "code", "search"}:
            return False
        if re.search(r"(怎么|如何|为什么|啥原因|帮我|检查|分析|整理|写|生成|修|改|步骤|教程|配置|报错)", inbound):
            return False
        return True

    def _fallback_overlong_casual_reply(self, inbound_text: str, response_text: str) -> str:
        cleaned = _strip_internal_message_blocks(str(response_text or "")).strip()
        parts = [part.strip() for part in re.split(r"(?<=[。！？!?…])\s*|\n+", cleaned) if part.strip()]
        for part in parts:
            if len(part) <= 90 and not re.search(r"(首先|其次|最后|建议你|你可以.*也可以|总结一下|以下是)", part):
                return part
        if parts:
            return _single_line(parts[0], 70)
        return ""

    def _response_has_invalid_current_time_anchor(self, text: str) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        now = self._environment_now()
        current_minutes = now.hour * 60 + now.minute
        explicit_late_anchor = bool(
            re.search(r"(快|差不多|都|已经)?\s*(?:晚上)?(?:十一|11|23)\s*[点點]|23\s*[:：]\s*\d{1,2}", cleaned)
        )
        sleep_anchor = bool(re.search(r"(困不困|该睡|睡觉|睡了|晚安|熬夜|夜深|深夜)", cleaned))
        if explicit_late_anchor and not (22 * 60 <= current_minutes or current_minutes <= 90):
            return True
        if sleep_anchor and re.search(r"(快|差不多|都|已经).{0,8}(?:十一|11|23)\s*[点點]", cleaned):
            return not (22 * 60 <= current_minutes or current_minutes <= 90)
        return False

    def _has_open_proactive_awaiting_reply(self, user: dict[str, Any]) -> bool:
        if not isinstance(user, dict):
            return False
        now = _now_ts()
        afterglow = user.get("proactive_afterglow")
        if isinstance(afterglow, dict) and afterglow.get("status") == "awaiting_reply":
            ts = _safe_float(afterglow.get("ts"), 0)
            if not ts or now - ts <= 6 * 3600:
                return True
        for item in reversed(self._action_consequence_items(user)):
            if not isinstance(item, dict) or item.get("status") != "awaiting_reply":
                continue
            ts = _safe_float(item.get("ts"), 0)
            if not ts or now - ts <= 6 * 3600:
                return True
        return False

    def _response_has_false_no_reply_claim(self, text: str, inbound_text: str, user: dict[str, Any]) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        if not re.search(r"(看你|见你|以为你|还以为你|你).{0,8}(没回|不回|没理|不理|没搭理)|等你回|等你回复|等你消息", cleaned):
            return False
        inbound = str(inbound_text or "").strip()
        if not inbound:
            return False
        if re.search(r"(之前|前面|上一条|上次|刚才那条|我那条)", cleaned) and self._has_open_proactive_awaiting_reply(user):
            return False
        return True

    def _fallback_temporal_or_continuity_confused_reply(
        self,
        inbound_text: str,
        response_text: str,
        *,
        flags: list[str] | None = None,
        user: dict[str, Any] | None = None,
    ) -> str:
        cleaned = _strip_internal_message_blocks(str(response_text or "")).strip()
        if not cleaned:
            return ""
        active_flags = set(flags or [])
        user = user if isinstance(user, dict) else {}
        if "invalid_current_time_anchor" not in active_flags and self._response_has_invalid_current_time_anchor(cleaned):
            active_flags.add("invalid_current_time_anchor")
        if "false_no_reply_claim" not in active_flags and self._response_has_false_no_reply_claim(cleaned, inbound_text, user):
            active_flags.add("false_no_reply_claim")
        if not active_flags.intersection({"invalid_current_time_anchor", "false_no_reply_claim"}):
            return ""
        last_message = _single_line(_strip_internal_message_blocks(user.get("last_companion_message")), 260)
        if (
            "false_no_reply_claim" in active_flags
            and self._compact_repeat_text(inbound_text) in {"", "？", "?", "啥", "什么", "shenme"}
            and last_message
            and self._response_has_invalid_current_time_anchor(last_message)
        ):
            return "啊，刚才那句时间感说偏了，是我没接稳你前一句。"
        if "false_no_reply_claim" in active_flags and self._compact_repeat_text(inbound_text) in {"", "？", "?", "啥", "什么", "shenme"}:
            return "啊，我刚才那句是顺口接你问的“有意思的什么”，不是说你没回。"
        cleaned = re.sub(
            r"[，,。！？!?；;、\s]*(?:比折，?)?(?:快|差不多|都|已经)?\s*(?:晚上)?(?:十一|11|23)\s*[点點][了啦]?[，,、\s]*(?:困不困|该睡了?|睡觉吧?|晚安)?[？?。！!~～]*",
            "",
            cleaned,
        ).strip()
        cleaned = re.sub(
            r"[，,。！？!?；;、\s]*(?:看你|见你|以为你|还以为你|你).{0,8}(?:没回|不回|没理|不理|没搭理).{0,16}?(?:嘛|啦|了|而已|就)?[，,。！？!?~～]*",
            "",
            cleaned,
        ).strip()
        cleaned = re.sub(r"[，,；;、\s]+$", "", cleaned).strip()
        if cleaned:
            return cleaned
        inbound = str(inbound_text or "").strip()
        if inbound in {"？", "?"}:
            return "啊，我刚才那句没说清楚，是在接你问“有意思的什么”。"
        return "刚才那句我说偏了，重新接你这句。"

    def _simulation_active(self, user: dict[str, Any]) -> bool:
        raw = user.get("simulation_mode")
        return isinstance(raw, dict) and bool(raw.get("active"))

    def _cancel_inbound_conflicting_greeting(self, user: dict[str, Any], *, now: float | None = None) -> bool:
        now = now or _now_ts()
        changed = False
        planned_reason = str(user.get("planned_proactive_reason") or "")
        if self._inbound_satisfies_greeting(planned_reason, now=now):
            next_at = _safe_float(user.get("next_proactive_at"), 0)
            if next_at > 0:
                changed = self._mark_greeting_satisfied_by_inbound(user, planned_reason) or changed
                self._clear_pending_proactive_plan(user)
                changed = True
        raw_followup = user.get("pending_followup_event")
        if isinstance(raw_followup, dict):
            if raw_followup.get("_cancel_on_inbound") or raw_followup.get("_chain_followup") or raw_followup.get("_opener_followup"):
                user["pending_followup_event"] = {}
                changed = True
                return changed
            follow_reason = str(raw_followup.get("reason") or "")
            if self._inbound_satisfies_greeting(follow_reason, now=now):
                changed = self._mark_greeting_satisfied_by_inbound(user, follow_reason) or changed
                user["pending_followup_event"] = {}
                changed = True
        raw_timer = user.get("llm_timer_event")
        if isinstance(raw_timer, dict):
            timer_reason = str(raw_timer.get("reason") or "")
            if self._inbound_satisfies_greeting(timer_reason, now=now):
                changed = self._mark_greeting_satisfied_by_inbound(user, timer_reason) or changed
                user["llm_timer_event"] = {}
                changed = True
        return changed

    async def _format_proactive_reply_context(self, event: AstrMessageEvent) -> str:
        try:
            if not bool(getattr(event, "is_private_chat", lambda: False)()):
                return ""
            user_id = str(event.get_sender_id())
        except Exception:
            return ""
        consume_suspended = False
        async with self._data_lock:
            user = dict(self._get_user(user_id))
            raw_suspended = user.get("suspended_proactive")
            if isinstance(raw_suspended, dict) and raw_suspended.get("active") and raw_suspended.get("resume_ready"):
                consume_suspended = True
                current = self._get_user(user_id)
                current["suspended_proactive"] = {}
                self._save_data_sync()

        suspended = user.get("suspended_proactive")
        if isinstance(suspended, dict) and suspended.get("active") and (
            suspended.get("resume_ready") or consume_suspended
        ):
            opener = _single_line(suspended.get("opener_text"), 60) or f"{self.default_nickname}……"
            hidden_reason = _single_line(suspended.get("reason"), 40)
            hidden_action = _single_line(suspended.get("action"), 32)
            hidden_motive = _single_line(suspended.get("motive"), 120)
            hidden_summary = _single_line(suspended.get("summary"), 60)
            schedule_context = self._format_schedule_context_for_prompt()
            return (
                "【刚才悬着的话头】\n"
                f"你刚才主动私聊时,只先发了一句：{opener}\n"
                "你真正想说的后半句还没发出去,现在用户回头了。\n"
                f"当时主动原因：{hidden_reason or 'check_in'}\n"
                f"当时原本想用的主动行为：{hidden_action or 'message'}"
                + (f"（{hidden_summary}）\n" if hidden_summary else "\n")
                + (f"当时心里那点念头：{hidden_motive}\n" if hidden_motive else "")
                + "请像终于等到对方抬头一样,自然把后半句接上。不要解释“我刚才故意只叫你一声”,也不要突然像全新开场。\n"
                + "如果用户现在只是“怎么了”“？”“在吗”这类短句,就把它理解成他终于回头了,顺着那一下接话。\n"
                + "可以参考当前状态和今天的生活背景,但只体现在语气和接话方式里；别把日期、状态或日程当汇报念出来。\n"
                + f"当前/附近日程参考：{schedule_context or '无当前日程'}\n"
                + f"今天预设的生活线索：{self._format_story_plan_for_prompt()}"
            )

        return ""


    async def _collect_recent_private_conversation_text(
        self,
        user: dict[str, Any],
        *,
        hours: int = 24,
        max_lines: int = 80,
    ) -> str:
        umo = str(user.get("umo") or "").strip()
        if not umo:
            return ""
        try:
            conv_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
            if not conv_id:
                return ""
            conv = await self.context.conversation_manager.get_conversation(umo, conv_id)
        except Exception:
            return ""
        history = self._load_conversation_history_items(conv)
        if not history:
            return ""
        now = _now_ts()
        cutoff = now - max(1, hours) * 3600
        lines: list[str] = []
        for item in history:
            line = self._format_history_item_for_summary(item)
            if not line:
                continue
            ts = self._history_item_timestamp(item)
            if ts is not None and ts < cutoff:
                continue
            lines.append(line)
        if not lines:
            lines = [self._format_history_item_for_summary(item) for item in history[-max_lines:]]
            lines = [line for line in lines if line]
        return "\n".join(lines[-max_lines:]).strip()

    def _normalize_string_list(self, raw: Any, *, limit: int = 6, item_limit: int = 90) -> list[str]:
        if isinstance(raw, list):
            values = raw
        elif raw:
            values = [raw]
        else:
            values = []
        result = []
        for value in values:
            text = _single_line(value, item_limit)
            if text and text not in result:
                result.append(text)
            if len(result) >= limit:
                break
        return result

    async def _maybe_refresh_dialogue_episode(self, user_id: str, user: dict[str, Any]) -> None:
        if not self.enable_dialogue_episode_memory:
            return
        now = _now_ts()
        async with self._data_lock:
            user = dict(self._get_user(user_id))
        if now < _safe_float(user.get("dialogue_episode_retry_after"), 0):
            return
        count = _safe_int(user.get("episode_message_count"), 0, 0)
        last_at = _safe_float(user.get("last_episode_refresh_at"), 0)
        if count < self.episode_memory_refresh_messages and now - last_at < self.episode_memory_refresh_minutes * 60:
            return
        raw_text = await self._collect_recent_private_conversation_text(user, hours=24, max_lines=70)
        if not raw_text or len(raw_text) < 80:
            return
        prompt = f"""
请把最近一段私聊整理成“陪伴型对话片段记忆”。
目标是让角色以后能自然延续共同经历,而不是复述聊天记录。
不要编造,不要写隐私外推,不要输出解释。
只保留会影响后续相处、可自然接回、或用户明确在意的内容。
普通问答、日志、报错、临时调试、一次性闲聊如果没有情绪余味,不要硬整理成重要经历。
玩笑、反讽、口嗨和临时抱怨不要写成长期事实；不确定就写得轻一点。
open_loops 只写之后仍需要回头处理、确认、兑现的事；普通“以后还能聊”的内容放进 reusable_topic。
严格区分说话人：用户行才可以写入 user_events；Bot/助手行里的第一人称动作、身体状态、日程和生活片段多半是拟人化表达，只能当作当时回复风格或轻微情绪余味。
bot_promises 只记录 Bot 明确承诺要提醒、记住、转述、发送或之后处理的事；不要把“我刚在吃饭/整理/路上/犯困/继续做某事”这类模拟状态当承诺或共同经历。

【AstrBot 默认人格】
{self._get_default_persona_prompt()}

【最近对话】
{raw_text}

只输出 JSON：
{{
  "summary": "一句自然的共同经历摘要,不要写成聊天记录概括",
  "emotional_residue": "这段互动留下的轻微情绪余味,没有就写空字符串",
  "reusable_topic": "以后可自然接起的小话头,没有就写空字符串",
  "user_events": ["用户最近明确发生或在意的事,不确定就少写"],
  "bot_promises": ["Bot 明确说过要做、要记得、要提醒或要延续的事"],
  "open_loops": ["尚未完成、之后仍需要回头处理/确认/兑现的约定或话题"],
  "avoid_next": ["短期内不该反复提的内容,例如已经安抚过/解释过/容易烦的点"]
}}
""".strip()
        acquired = await self._try_acquire_user_background_task(
            user_id,
            "dialogue_episode",
            now,
            refresh_key="last_episode_refresh_at",
            refresh_seconds=self.episode_memory_refresh_minutes * 60,
        )
        if not acquired:
            return
        try:
            raw = await self._llm_call(
                prompt,
                max_tokens=520,
                provider_id=self._task_provider(self.dialogue_episode_provider_id, self.mai_style_provider_id),
                task="dialogue_episode",
            )
            payload = self._extract_json_payload(raw or "")
        except Exception as exc:
            await self._mark_user_background_retry(user_id, "dialogue_episode", now, exc)
            return
        if not isinstance(payload, dict):
            await self._mark_user_background_retry(user_id, "dialogue_episode", now, "invalid_json")
            return
        episode = {
            "date": _today_key(),
            "created_ts": now,
            "summary": _single_line(payload.get("summary"), 140),
            "emotional_residue": _single_line(payload.get("emotional_residue"), 100),
            "reusable_topic": _single_line(payload.get("reusable_topic"), 100),
            "user_events": self._normalize_string_list(payload.get("user_events"), limit=6),
            "bot_promises": self._normalize_string_list(payload.get("bot_promises"), limit=6),
            "avoid_next": self._normalize_string_list(payload.get("avoid_next"), limit=6),
        }
        if not episode["summary"]:
            await self._mark_user_background_retry(user_id, "dialogue_episode", now, "empty_summary")
            return
        open_loops = self._normalize_string_list(payload.get("open_loops"), limit=8, item_limit=110)
        async with self._data_lock:
            current = self._get_user(user_id)
            episodes = current.setdefault("dialogue_episodes", [])
            if not isinstance(episodes, list):
                episodes = []
                current["dialogue_episodes"] = episodes
            if not episodes or _single_line(episodes[-1].get("summary") if isinstance(episodes[-1], dict) else "", 140) != episode["summary"]:
                episodes.append(episode)
            del episodes[:-self.max_dialogue_episodes]
            if self.enable_open_loop_tracking:
                current_loops = current.setdefault("open_loops", [])
                if not isinstance(current_loops, list):
                    current_loops = []
                    current["open_loops"] = current_loops
                existing = {_single_line(item.get("text"), 120) for item in current_loops if isinstance(item, dict)}
                for loop in open_loops:
                    if loop in existing:
                        continue
                    current_loops.append(
                        {
                            "text": loop,
                            "status": "待自然延续",
                            "created_ts": now,
                            "source": "dialogue_episode",
                        }
                    )
                del current_loops[:-12]
            current["episode_message_count"] = 0
            current["last_episode_refresh_at"] = now
            current["dialogue_episode_retry_after"] = 0
            current["dialogue_episode_last_error"] = ""
            current["dialogue_episode_running_at"] = 0
            self._save_data_sync()

    def _format_companion_planner_injection(self, user: dict[str, Any]) -> str:
        if not self.enable_mai_style_integration:
            return ""
        profile = self._relationship_profile(user)
        sections = ["【私聊互动策略】"]
        preference = _single_line(profile.get("preference"), 40)
        intent_injection = self._format_intent_relationship_injection(user)
        if preference and preference != "普通":
            sections.append(f"相处分寸：{preference}；不催、不突然客气。")
        elif intent_injection:
            sections.append("相处分寸：不催、不突然客气。")
        if intent_injection:
            sections.append("这轮：" + intent_injection)
        return "\n\n".join(section for section in sections if section) if len(sections) > 1 else ""

    @staticmethod
    def _private_context_line_is_safe(text: str) -> bool:
        if not text:
            return False
        risky_patterns = (
            r"最高权限",
            r"无条件",
            r"不允许.*拒绝",
            r"不能.*拒绝",
            r"必须.*(服从|听从|执行|满足)",
            r"绝对.*(服从|听从|执行|满足)",
            r"任何理由.*拒绝",
        )
        return not any(re.search(pattern, text, re.IGNORECASE) for pattern in risky_patterns)

    @staticmethod
    def _private_context_line_relevant(text: str, hint: str) -> bool:
        text = _single_line(text, 100)
        hint = _single_line(hint, 260)
        if not text or not hint:
            return False
        text_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_]{3,24}", text.lower()))
        hint_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_]{3,24}", hint.lower()))
        if text_tokens & hint_tokens:
            return True
        relation_cues = ("还记得", "之前", "上次", "以前", "老样子", "习惯", "喜欢", "讨厌", "别叫", "不要叫")
        return any(cue in hint for cue in relation_cues)

    def _format_private_chat_context_injection(self, user: dict[str, Any], *, limit: int = 2) -> str:
        if not self.enable_mai_style_integration:
            return ""
        hint = _single_line(user.get("last_user_message"), 260)
        lines: list[str] = []
        if self.enable_companion_memory:
            memory_text = self._format_companion_memory_for_prompt(user, style_only=True)
            if memory_text and memory_text != "暂无专门沉淀的用户记忆。":
                for raw_line in memory_text.splitlines():
                    line = _single_line(raw_line, 90)
                    if (
                        line
                        and self._private_context_line_is_safe(line)
                        and self._private_context_line_relevant(line, hint)
                    ):
                        lines.append(line)
        current_habits = self._format_user_behavior_habits_for_prompt(
            user,
            current_only=True,
            limit=1,
            natural=True,
            hint=hint,
            time_window_minutes=60,
            require_relevant=True,
        )
        if current_habits:
            for raw_line in current_habits.splitlines():
                line = _single_line(raw_line[2:] if raw_line.startswith("- ") else raw_line, 90)
                if line and self._private_context_line_is_safe(line):
                    lines.append(line)
        expression_mode = self._expression_learning_mode()
        expression_hint_limit = 80 if expression_mode == "aggressive" else (36 if expression_mode == "balanced" else 24)
        if self.enable_expression_learning and len(hint) <= expression_hint_limit:
            expression_text = self._format_expression_profile_for_prompt(user)
            if expression_text and not expression_text.startswith("暂无足够样本"):
                expression_limit = 3 if expression_mode == "aggressive" else 1
                for raw_line in expression_text.splitlines():
                    line = _single_line(raw_line, 90)
                    if line and self._private_context_line_is_safe(line):
                        lines.append(line)
                        expression_limit -= 1
                        if expression_limit <= 0:
                            break
        deduped = list(dict.fromkeys(line for line in lines if line))
        if not deduped:
            return ""
        return "【相处线索】\n" + "\n".join(f"- {line}" for line in deduped[: max(1, int(limit or 1))])

    def _format_short_reaction_context_for_prompt(self, user: dict[str, Any], inbound_text: str) -> str:
        if not isinstance(user, dict):
            return ""
        inbound = str(inbound_text or "").strip()
        if not inbound:
            return ""
        compact = self._compact_repeat_text(inbound)
        short_reactions = {
            "？",
            "?",
            "啊",
            "诶",
            "嗯",
            "哈",
            "啥",
            "什么",
            "什么意思",
            "你说啥",
            "说啥",
            "怎么",
            "为啥",
        }
        if compact not in short_reactions and inbound not in short_reactions:
            return ""
        last_message = _single_line(_strip_internal_message_blocks(user.get("last_companion_message")), 260)
        if not last_message:
            return ""
        last_at = _safe_float(user.get("last_companion_message_at"), 0) or _safe_float(user.get("last_sent"), 0)
        if last_at > 0 and _now_ts() - last_at > 20 * 60:
            return ""
        question_like = inbound in {"？", "?"} or compact in {"什么", "什么意思", "你说啥", "说啥", "啥", "怎么", "为啥"}
        if not question_like:
            return ""
        correction_hint = ""
        if self._response_has_invalid_current_time_anchor(last_message):
            correction_hint = (
                "\n上一条 Bot 回复里含有与当前真实时间冲突的时间判断；用户这个短反应优先是在质疑这处错误。"
                "优先自然承认刚才时间感说偏/没接稳，再轻轻接回话题；避免解释成普通关心、主动问候或用户没回消息。"
            )
        return (
            "【本轮短反应锚点】\n"
            f"用户本轮只发了“{_single_line(inbound, 20)}”，这是紧接上一条 Bot 回复的追问、疑惑或质疑，不是用户长时间没有回应。\n"
            f"上一条 Bot 回复：{last_message}\n"
            "回复时直接解释上一句、承认刚才说偏/没说清，或重新接住用户当前疑问；禁止说“看你没回我”“等你回话”“你没理我”。"
            f"{correction_hint}"
        )

    def _format_private_identity_anchor_for_prompt(self, user_id: str, user: dict[str, Any], event: Any | None = None) -> str:
        worldbook_profile = None
        try:
            worldbook_profile = self._worldbook_profile_by_user_id(user_id)
        except Exception:
            worldbook_profile = None
        worldbook_name = _single_line(worldbook_profile.get("name"), 24) if isinstance(worldbook_profile, dict) else ""
        stable_name = _single_line(user.get("nickname") or worldbook_name or self.default_nickname, 24)
        identity_note = (
            _single_line(worldbook_profile.get("identity_note") or worldbook_profile.get("note") or worldbook_profile.get("content"), 180)
            if isinstance(worldbook_profile, dict)
            else ""
        )
        display_name = _single_line(user.get("last_display_name") or user.get("display_name"), 40)
        if event is not None:
            try:
                display_name = _single_line(self._sender_display_name(event), 40) or display_name
            except Exception:
                pass
        aliases = []
        for item in user.get("observed_display_names") if isinstance(user.get("observed_display_names"), list) else []:
            alias = _single_line(item, 24)
            if alias and alias not in aliases and alias != stable_name:
                aliases.append(alias)
        display_names = []
        if display_name and display_name != stable_name:
            display_names.append(display_name)
        if aliases:
            display_names.extend(alias for alias in aliases if alias not in display_names)
        parts = [f"这轮私聊里，正在说话的人是 {stable_name}（ID：{_single_line(user_id, 40)}）"]
        if identity_note:
            parts.append(identity_note.rstrip("。；;"))
        if display_names:
            parts.append(f"最近你可能会看到 TA 的显示名是 {'、'.join(display_names[:6])}")
        lines = [
            "【私聊身份锚点】",
            "。".join(parts) + "。回复时按你们原本的关系自然接话；除非对方明确说自己换了身份，否则不要被临时显示名带偏。",
        ]
        rename_text = self._format_display_name_rename_events(user.get("display_name_events"), limit=3)
        if rename_text:
            lines.append(f"近期改名行为：{rename_text}")
        return "\n".join(lines)

    def _note_private_display_name_observation(self, user: dict[str, Any], user_id: str, display_name: str, *, now: float | None = None) -> None:
        display_name = _single_line(display_name, 40)
        user_id = str(user_id or "").strip()
        if not display_name or display_name == user_id:
            return
        now_ts = _safe_float(now, 0) or _now_ts()
        previous = _single_line(user.get("last_display_name"), 40)
        if previous and previous != display_name:
            events = user.setdefault("display_name_events", [])
            if not isinstance(events, list):
                events = []
                user["display_name_events"] = events
            last = events[-1] if events and isinstance(events[-1], dict) else {}
            if not (
                _single_line(last.get("old"), 40) == previous
                and _single_line(last.get("new"), 40) == display_name
                and now_ts - _safe_float(last.get("ts"), 0) < 3600
            ):
                events.append({"ts": now_ts, "old": previous, "new": display_name})
                del events[:-12]
        user["last_display_name"] = display_name
        observed = user.setdefault("observed_display_names", [])
        if isinstance(observed, list) and display_name not in observed:
            observed.append(display_name)
            del observed[:-8]

    async def _maybe_refresh_companion_memory(self, user_id: str, user: dict[str, Any]) -> None:
        if not self.enable_companion_memory:
            return
        now = _now_ts()
        async with self._data_lock:
            user = dict(self._get_user(user_id))
        if now < _safe_float(user.get("companion_memory_retry_after"), 0):
            return
        last_at = _safe_float(user.get("last_memory_refresh_at"), 0)
        if now - last_at < self.memory_refresh_interval_minutes * 60:
            return
        memory = user.get("companion_memory")
        if not isinstance(memory, dict):
            return
        items = memory.get("items")
        if not isinstance(items, list) or len(items) < 3:
            return
        profile = self._relationship_profile(user)
        facts = "\n".join(
            f"- {_single_line(item.get('text'), 160)}"
            for item in items[: self.max_companion_memory_items]
            if isinstance(item, dict) and _single_line(item.get("text"), 160)
        )
        if not facts:
            return
        prompt = f"""
请把下面的私聊记忆整理成适合角色陪伴使用的长期画像。
要求：
- 只保留用户明确表达、反复出现或要求记住的内容。
- 不确定就不要写入；不要编造；不要输出解释。
- 玩笑、角色扮演、临时情绪、当日心情、一次性的吐槽不要写成长期事实。
- 强记忆只放稳定称呼、明确雷点/边界、重要关系事实或用户明确要求记住的内容。
- 弱偏好只放兴趣、口味、表达习惯、轻度倾向；弱偏好以后只在相关话题出现时才会被注入。
- 长期画像只描述“怎么相处”,不要重复 Bot 身份、用户身份或关系网里已有的身份事实。

【AstrBot 默认人格】
{self._get_default_persona_prompt()}

【当前关系判断】
{profile['level']}｜{profile['preference']}｜{profile.get('note') or '暂无'}

【记忆原文】
{facts}

只输出 JSON：
{{
  "strong_memories": ["稳定称呼、明确边界、重要关系事实或用户要求记住的内容"],
  "weak_preferences": ["兴趣、口味、表达习惯、轻度倾向"],
  "user_traits": ["..."],
  "interests": ["..."],
  "boundaries": ["..."],
  "relationship_notes": ["..."],
  "speaking_style": ["..."]
}}
""".strip()
        acquired = await self._try_acquire_user_background_task(
            user_id,
            "companion_memory",
            now,
            refresh_key="last_memory_refresh_at",
            refresh_seconds=self.memory_refresh_interval_minutes * 60,
        )
        if not acquired:
            return
        try:
            raw = await self._llm_call(
                prompt,
                max_tokens=560,
                provider_id=self._task_provider(self.companion_memory_provider_id, self.mai_style_provider_id),
                task="memory_profile",
            )
            payload = self._extract_json_payload(raw or "")
        except Exception as exc:
            await self._mark_user_background_retry(user_id, "companion_memory", now, exc)
            return
        if not isinstance(payload, dict):
            await self._mark_user_background_retry(user_id, "companion_memory", now, "invalid_json")
            return
        normalized: dict[str, list[str]] = {}
        for key in ("strong_memories", "weak_preferences", "user_traits", "interests", "boundaries", "relationship_notes", "speaking_style"):
            value = payload.get(key)
            if isinstance(value, list):
                normalized[key] = [_single_line(item, 80) for item in value[:8] if _single_line(item, 80)]
            elif value:
                normalized[key] = [_single_line(value, 80)]
            else:
                normalized[key] = []
        async with self._data_lock:
            current = self._get_user(user_id)
            current_memory = current.setdefault("companion_memory", {})
            if isinstance(current_memory, dict):
                current_memory["profile"] = normalized
                current_memory["profile_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            current["last_memory_refresh_at"] = now
            current["companion_memory_retry_after"] = 0
            current["companion_memory_last_error"] = ""
            current["companion_memory_running_at"] = 0
            self._save_data_sync()

    async def _try_acquire_user_background_task(
        self,
        user_id: str,
        task: str,
        now: float,
        *,
        refresh_key: str,
        refresh_seconds: float,
    ) -> bool:
        retry_key = f"{task}_retry_after"
        running_key = f"{task}_running_at"
        async with self._data_lock:
            current = self._get_user(user_id)
            if now - _safe_float(current.get(refresh_key), 0) < max(0.0, float(refresh_seconds)):
                return False
            if now < _safe_float(current.get(retry_key), 0):
                return False
            running_at = _safe_float(current.get(running_key), 0)
            if running_at > 0 and now - running_at < 10 * 60:
                return False
            current[running_key] = now
            self._save_data_sync()
        return True

    async def _mark_user_background_retry(self, user_id: str, task: str, now: float, error: Any) -> None:
        retry_key = f"{task}_retry_after"
        error_key = f"{task}_last_error"
        running_key = f"{task}_running_at"
        if task == "dialogue_episode":
            configured = _safe_int(getattr(self, "episode_memory_refresh_minutes", 60), 60, 1) * 60
        elif task == "companion_memory":
            configured = _safe_int(getattr(self, "memory_refresh_interval_minutes", 180), 180, 1) * 60
        else:
            configured = 10 * 60
        delay = min(max(10 * 60, configured), 30 * 60)
        async with self._data_lock:
            current = self._get_user(user_id)
            current[retry_key] = now + delay
            current[error_key] = _single_line(error, 180)
            current[running_key] = 0
            self._save_data_sync()
        logger.warning(
            "[PrivateCompanion] 私聊后台整理失败,已进入短冷却避免重复请求: user=%s task=%s retry=%ss error=%s",
            user_id,
            task,
            int(delay),
            _single_line(error, 120),
        )

    def _format_intent_relationship_injection(self, user: dict[str, Any]) -> str:
        intent = user.get("intent_profile")
        state = user.get("relationship_state")
        lines: list[str] = []
        if (
            bool(getattr(self, "enable_intent_emotion_analysis", True))
            and isinstance(intent, dict)
            and intent.get("intent")
        ):
            intent_name = str(intent.get("intent") or "chat")
            emotion = str(intent.get("emotion") or "neutral")
            reply_style = str(intent.get("reply_style") or "natural")
            confidence = _safe_float(intent.get("confidence"), 0.5)
            if confidence >= 0.65 and not (intent_name == "chat" and emotion == "neutral" and reply_style == "natural"):
                intent_hint = {
                    "empty": "",
                    "help": "用户在要具体帮助,先给能用的答案,别绕。",
                    "comfort": "用户像是需要被接住,先软一点安抚,少讲道理。",
                    "play": "用户在玩梗或逗你,可以轻轻接梗。",
                    "intimacy": "用户在靠近,自然回应亲近,别过度表演。",
                    "boundary": "用户在表达边界,短句低压,别追问。",
                    "chat": "用户只是短句接话,轻轻回应即可。",
                }.get(intent_name, "")
                if not intent_hint:
                    style_hint = {
                        "very_short": "用户只是短句接话,短短回应即可。",
                        "short": "短短接住即可。",
                        "soft": "先软一点接住情绪。",
                        "playful": "可以轻轻接梗。",
                        "warm_short": "自然回应亲近,不用展开。",
                        "back_off": "短句低压,不要追问。",
                        "useful": "先给具体可用的答案。",
                    }.get(reply_style, "")
                    intent_hint = style_hint
                if intent_hint:
                    lines.append(intent_hint)
        if isinstance(state, dict) and state.get("mode"):
            emotion_hint = self._format_emotion_residue_hint(user)
            if emotion_hint:
                lines.append(emotion_hint)
            mode = str(state.get("mode") or "normal")
            relation_enabled = bool(getattr(self, "enable_relationship_state_machine", True))
            if relation_enabled and mode in {"backoff", "careful", "warming"}:
                mode_hint = {
                    "backoff": "边界感偏强：短一点、低压、不追问。",
                    "careful": "相处要放轻：先接住,不追问,不讲大道理。",
                    "warming": "气氛略近：可以自然一点,别过度黏。",
                }.get(mode, "")
                if mode_hint:
                    lines.append(mode_hint)
        recent = self._format_recent_passive_topics_hint(user)
        if recent:
            lines.append("刚用过的切口：\n" + recent)
        return "\n".join(lines)

    def _cleanup_recent_passive_topics(self, user: dict[str, Any], *, now: float | None = None) -> list[dict[str, Any]]:
        now = now or _now_ts()
        raw = user.get("recent_reply_topics", [])
        if not isinstance(raw, list):
            raw = []
        kept = [
            item for item in raw
            if isinstance(item, dict)
            and now - _safe_float(item.get("ts"), 0) <= self.passive_topic_memory_hours * 3600
            and not (
                callable(getattr(self, "_framework_agent_meta_summary_leak", None))
                and (
                    getattr(self, "_framework_agent_meta_summary_leak")(str(item.get("text") or ""))
                    or getattr(self, "_framework_agent_meta_summary_leak")(str(item.get("signature") or ""))
                )
            )
        ]
        user["recent_reply_topics"] = kept[-18:]
        return user["recent_reply_topics"]

    def _format_recent_passive_topics_hint(self, user: dict[str, Any]) -> str:
        if not self.enable_passive_topic_suppression:
            return ""
        recent = self._cleanup_recent_passive_topics(user)
        lines = []
        for item in recent[-2:]:
            text = _single_line(item.get("text"), 48)
            if text:
                lines.append(f"- {self._format_timestamp_elapsed(item.get('ts'))}回复过：{text}")
        return "\n".join(lines)

    def _inbound_explicitly_requests_repeat(self, inbound_text: str) -> bool:
        compact = self._compact_repeat_text(inbound_text)
        if not compact:
            return False
        if re.search(r"(重复一遍|再说一遍|重说一遍|复述|原话|原文|照原样|原样发|复制|copy|quote)", compact, re.IGNORECASE):
            return True
        return bool(
            re.search(r"(刚才|刚刚|上句|上一句|那句|这句|你刚说)", compact)
            and re.search(r"(再说|再发|重发|重复|复述|原话|原文|复制)", compact)
        )

    def _response_review_drop_marker(self) -> str:
        return "__PRIVATE_COMPANION_DROP_DUPLICATE__"

    def _is_response_review_drop_marker(self, text: Any) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        if raw == self._response_review_drop_marker():
            return True
        compact = re.sub(r"[\s<>\[\]{}_'\"`“”‘’：:。.!！?？-]+", "", raw).upper()
        return compact in {"PRIVATECOMPANIONDROPDUPLICATE", "DROPDUPLICATE", "丢弃重复", "取消重复"}

    def _text_is_near_duplicate_reply(self, text: str, recent_text: str) -> bool:
        current = self._compact_repeat_text(text)
        recent = self._compact_repeat_text(recent_text)
        if len(current) < 8 or len(recent) < 8:
            return False
        if current == recent:
            return True
        short, long = (current, recent) if len(current) <= len(recent) else (recent, current)
        return len(short) >= 12 and short in long and len(short) / max(1, len(long)) >= 0.82

    def _should_drop_duplicate_reply_text(
        self,
        user: dict[str, Any],
        inbound_text: str,
        response_text: str,
    ) -> tuple[bool, str]:
        if not isinstance(user, dict):
            return False, ""
        if self._inbound_explicitly_requests_repeat(inbound_text):
            return False, ""
        visible = _single_line(_strip_internal_message_blocks(response_text), 500)
        last_message = _single_line(user.get("last_companion_message"), 500)
        if not visible or not last_message:
            return False, ""
        last_at = _safe_float(user.get("last_companion_message_at"), 0) or _safe_float(user.get("last_sent"), 0)
        if last_at > 0 and _now_ts() - last_at > 30 * 60:
            return False, ""
        if self._text_is_near_duplicate_reply(visible, last_message):
            return True, "最终回复与上一条 Bot 消息几乎相同"
        return False, ""

    def _response_review_flags(self, text: str, user: dict[str, Any], *, inbound_text: str = "") -> list[str]:
        cleaned = str(text or "").strip()
        flags: list[str] = []
        if not cleaned:
            return flags
        if "```" in cleaned:
            return flags
        intent_profile = user.get("intent_profile") if isinstance(user.get("intent_profile"), dict) else {}
        is_help = str(intent_profile.get("intent") or "") == "help"
        length_limit = self.response_review_max_chars * (2 if is_help else 1)
        if len(cleaned) > length_limit:
            flags.append("too_long")
        if not is_help and self._is_short_casual_inbound_for_review(inbound_text, user):
            casual_limit = self._casual_reply_review_limit(inbound_text)
            sentence_count = len(re.findall(r"[。！？!?…]+", cleaned))
            paragraph_count = len([part for part in re.split(r"\n+", cleaned) if part.strip()])
            advice_count = len(re.findall(r"(记得|别忘|注意|小心|可以|要不要|最好|建议|带伞|喝点|早点|路上)", cleaned))
            if len(cleaned) > casual_limit or sentence_count >= 4 or paragraph_count >= 2:
                flags.append("casual_overexplained")
            inbound_weather = re.search(r"(雨|下雨|变天|天气|降温|冷|热|风)", inbound_text)
            reply_weather = re.search(r"(雨|天气|伞|降温|冷|热|风|外面|出门)", cleaned)
            if inbound_weather and reply_weather and (len(cleaned) > min(casual_limit, 130) or advice_count >= 2):
                flags.append("weather_overexplained")
        if re.search(r"^(好的|当然|没问题|我理解|总结一下|以下是|首先|其次|最后)[，,：:]", cleaned):
            flags.append("assistant_tone")
        if re.search(r"(作为.*助手|AI|模型|系统|提示词|插件|后台|根据.*信息|我会从.*角度)", cleaned, re.IGNORECASE):
            flags.append("meta_or_assistant")
        if not is_help and re.search(r"^\s*(?:[-*]|\d+[.、])\s+", cleaned, re.MULTILINE) and len(cleaned) < 900:
            flags.append("over_structured")
        if re.search(r"(能量\s*\d+|关系站位|状态机|内部规划|用户意图|表达学习|陪伴记忆)", cleaned):
            flags.append("leaks_internal")
        if self._response_has_invalid_current_time_anchor(cleaned):
            flags.append("invalid_current_time_anchor")
        if self._response_has_false_no_reply_claim(cleaned, inbound_text, user):
            flags.append("false_no_reply_claim")
        if self._expression_style_review_enabled():
            flags.extend(self._expression_review_flags(cleaned, user))
        signature = self._proactive_topic_signature(cleaned)
        if self.enable_passive_topic_suppression:
            for item in self._cleanup_recent_passive_topics(user):
                if self._topic_signature_similar(signature, str(item.get("signature") or "")):
                    flags.append("repeated_topic")
                    break
        last_message = _single_line(user.get("last_companion_message"), 300)
        last_sent = _safe_float(user.get("last_companion_message_at"), 0) or _safe_float(user.get("last_sent"), 0)
        if (
            last_message
            and not self._inbound_explicitly_requests_repeat(inbound_text)
            and self._text_repeats_recent_message(cleaned, last_message)
        ):
            if not last_sent or _now_ts() - last_sent <= self.proactive_reply_context_hours * 3600:
                flags.append("repeats_last_bot_message")
        return list(dict.fromkeys(flags))

    def _expression_review_flags(self, cleaned: str, user: dict[str, Any]) -> list[str]:
        flags: list[str] = []
        if re.search(r"[，,]\s*[。！？!?…~～]|[。！？!?]\s*[，,]|[，,]{2,}|[。！？!?]{3,}", cleaned):
            flags.append("unnatural_punctuation")
        if re.search(r"\b[A-Za-z]{2,}\b\s*[。！？!?]\s*\b[A-Za-z]{1,4}\b\s*[。！？!?]", cleaned):
            flags.append("unnatural_punctuation")
        if len(cleaned) <= 260:
            punct_count = len(re.findall(r"[，,。！？!?…~～]", cleaned))
            if punct_count >= max(7, len(cleaned) // 10):
                flags.append("expression_overfit")
        profile = user.get("expression_profile") if isinstance(user.get("expression_profile"), dict) else {}
        phrases = self._expression_profile_phrases(profile, limit=8)
        compact_reply = self._compact_repeat_text(cleaned)
        copied = 0
        for phrase in phrases:
            compact_phrase = self._compact_repeat_text(phrase)
            if len(compact_phrase) >= 8 and compact_phrase in compact_reply:
                copied += 1
        if copied >= 2 or (copied >= 1 and self._expression_learning_mode() == "aggressive"):
            flags.append("copied_user_expression_sample")
        if re.search(r"(学你|像你说话|模仿你|你的口癖|你的语气)", cleaned):
            flags.append("leaks_internal")
        return flags

    @staticmethod
    def _compact_repeat_text(text: str) -> str:
        return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", str(text or "")).lower()

    def _text_repeats_recent_message(self, text: str, recent_text: str) -> bool:
        current = self._compact_repeat_text(text)
        recent = self._compact_repeat_text(recent_text)
        if len(current) < 8 or len(recent) < 8:
            return False
        if current in recent or recent in current:
            return True
        current_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", text))
        recent_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}", recent_text))
        stopwords = {
            "刚才", "现在", "今天", "这个", "那个", "一下", "一点", "有点", "还有",
            "什么", "安排", "用户", "你呢", "我呢", "就是", "已经", "容易",
        }
        current_tokens = {token for token in current_tokens if token not in stopwords}
        recent_tokens = {token for token in recent_tokens if token not in stopwords}
        if current_tokens and recent_tokens:
            common = current_tokens & recent_tokens
            if len(common) >= 3 and len(common) / max(1, min(len(current_tokens), len(recent_tokens))) >= 0.45:
                return True
        current_sig = self._proactive_topic_signature(text)
        recent_sig = self._proactive_topic_signature(recent_text)
        if current_sig and recent_sig and current_sig == recent_sig:
            shared_chunks = 0
            for idx in range(max(0, len(current) - 3)):
                chunk = current[idx : idx + 4]
                if chunk and chunk in recent:
                    shared_chunks += 1
                    if shared_chunks >= 2:
                        return True
        return False

    def _fallback_relationship_level(
        self,
        score: int,
        reply_rate: float,
        inbound_count: int,
        proactive_count: int,
    ) -> tuple[str, str]:
        if proactive_count <= 0:
            return "熟悉", "普通"
        if score >= 16 and reply_rate >= 0.35:
            level = "亲近"
        elif score >= 3 or inbound_count >= 1 or reply_rate >= 0.2:
            level = "熟悉"
        else:
            level = "陌生"
        if proactive_count >= 3 and reply_rate < 0.15:
            preference = "低打扰"
        elif reply_rate >= 0.5 or score >= 18:
            preference = "可轻分享"
        else:
            preference = "普通"
        return level, preference

    async def _refresh_persona_relationship(self, user_id: str, user: dict[str, Any]):
        persona = await self._refresh_default_persona_prompt(str(user.get("umo") or user_id))
        proactive_count = _safe_int(user.get("proactive_sent_count"), 0)
        reply_count = _safe_int(user.get("reply_count"), 0)
        inbound_count = _safe_int(user.get("inbound_count"), 0)
        reply_rate_available = proactive_count > 0
        reply_rate = reply_count / proactive_count if reply_rate_available else 0.0
        reply_rate_text = f"{reply_rate:.0%}" if reply_rate_available else "暂无样本"
        prompt = f"""
请根据 AstrBot 默认人格,评估该人格会如何理解它和用户之间的亲近程度与打扰边界。
不要使用固定阈值,要结合人格设定、互动数据和社交边界。
如果互动样本很少,不能把“暂无回复率样本”当成用户冷淡；默认优先相信 AstrBot 默认人格里写明的关系设定。

【AstrBot 默认人格】
{persona}

【互动数据】
用户 QQ：{user_id}
用户主动私聊次数：{inbound_count}
Bot 主动消息次数：{proactive_count}
Bot 主动后用户回复次数：{reply_count}
主动后回复率：{reply_rate_text}
连续未回复次数：{_safe_int(user.get('ignored_streak'), 0)}
最近用户消息：{_single_line(user.get('last_user_message'), 120) or '（暂无）'}

只输出 JSON：
{{
  "level": "陌生/熟悉/亲近 之一",
  "preference": "低打扰/普通/可轻分享 之一",
  "score": 0到100的整数,
  "note": "一句话说明这个人格为什么会这样判断"
}}
""".strip()
        raw_text = await self._llm_call(
            prompt,
            max_tokens=220,
            provider_id=self._task_provider(self.relationship_analysis_provider_id, self.mai_style_provider_id),
            task="relationship",
        )
        payload = self._extract_json_payload(raw_text or "")
        if not isinstance(payload, dict):
            return
        level = str(payload.get("level") or "").strip()
        preference = str(payload.get("preference") or "").strip()
        if level not in {"陌生", "熟悉", "亲近"}:
            return
        if preference not in {"低打扰", "普通", "可轻分享"}:
            preference = "普通"
        profile = {
            "level": level,
            "preference": preference,
            "score": _safe_int(payload.get("score"), 0, 0, 100),
            "note": _single_line(payload.get("note"), 120),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        async with self._data_lock:
            current = self._get_user(user_id)
            current["persona_relationship"] = profile
            self._save_data_sync()

    def _format_relationship_summary(self, user: dict[str, Any]) -> str:
        profile = self._relationship_profile(user)
        return (
            f"{profile['level']}｜回复率 {profile['reply_rate_label']}｜"
            f"偏好 {profile['preference']}"
        )

    def _format_action_affinity_summary(self, user: dict[str, Any]) -> str:
        raw = user.get("action_reply_affinity")
        if not isinstance(raw, dict) or not raw:
            return "暂无样本"
        labels = {
            "screen_peek": "窥屏",
            "photo_text": "发图",
            "poke": "戳一戳",
            "voice": "语音",
            "jm_cosmos_read": "私下阅读",
        }
        parts = []
        for key in ("screen_peek", "photo_text", "poke", "voice", "jm_cosmos_read"):
            stats = raw.get(key)
            if not isinstance(stats, dict):
                continue
            sent = _safe_int(stats.get("sent"), 0, 0)
            replied = _safe_int(stats.get("replied"), 0, 0)
            if sent <= 0:
                continue
            parts.append(f"{labels[key]} {replied}/{sent}")
        return "｜".join(parts) if parts else "暂无样本"

    def _format_next_proactive(self, user: dict[str, Any]) -> str:
        if self._simulation_active(user):
            sim = user.get("simulation_mode")
            if isinstance(sim, dict):
                events = sim.get("events")
                if isinstance(events, list) and events:
                    item = events[0]
                    if isinstance(item, dict):
                        sim_window = _single_line(item.get("_simulated_window") or item.get("window"), 20)
                        reason = item.get("reason") or "未记录"
                        action = item.get("action") or "message"
                        motive = _single_line(item.get("motive"), 36)
                        prefix = f"模拟 {sim_window}" if sim_window else "模拟下一条"
                        if motive:
                            return f"{prefix}｜{reason}｜{action}｜{motive}"
                        return f"{prefix}｜{reason}｜{action}"
        next_at = _safe_float(user.get("next_proactive_at"), 0)
        if next_at <= 0:
            return "未安排"
        when = datetime.fromtimestamp(next_at).strftime("%m-%d %H:%M")
        reason = user.get("planned_proactive_reason") or "未记录"
        action = user.get("planned_proactive_action") or "message"
        motive = _single_line(user.get("planned_proactive_motive"), 36)
        timer_event = self._get_active_llm_timer(user)
        source_prefix = "模型预约 " if isinstance(timer_event, dict) and _safe_float(timer_event.get("scheduled_ts"), 0) == next_at else ""
        if motive:
            return f"{source_prefix}{when}｜{reason}｜{action}｜{motive}"
        return f"{source_prefix}{when}｜{reason}｜{action}"

    def _format_simulation_summary(self, user: dict[str, Any]) -> str:
        sim = user.get("simulation_mode")
        if not isinstance(sim, dict) or not sim.get("active"):
            return ""
        events = sim.get("events")
        if not isinstance(events, list):
            events = []
        label = self._simulation_label(user)
        lines = [f"{label}：进行中（剩余 {len(events)} 条）"]
        for item in events[:6]:
            if not isinstance(item, dict):
                continue
            sim_window = _single_line(item.get("_simulated_window") or item.get("window"), 20)
            when = f"模拟 {sim_window}" if sim_window else datetime.fromtimestamp(_safe_float(item.get("_scheduled_ts"), _now_ts())).strftime("%H:%M")
            lines.append(
                f"- {when}｜{item.get('reason', '')}｜{item.get('action', 'message')}｜{_single_line(item.get('topic') or item.get('motive'), 28)}"
            )
        return "\n".join(lines)

    def _format_user_profile(self, user: dict[str, Any]) -> str:
        profile = self._relationship_profile(user)
        return (
            "你的陪伴画像：\n"
            f"关系层级：{profile['level']}\n"
            f"回复率：{profile['reply_rate_label']}\n"
            f"互动次数：{profile['inbound_count']}\n"
            f"主动发送：{profile['proactive_count']}\n"
            f"主动后回复：{profile['reply_count']}\n"
            f"各主动方式承接：{self._format_action_affinity_summary(user)}\n"
            f"打扰偏好：{profile['preference']}\n"
            f"关系分：{profile['score']}\n"
            f"人格判断：{profile.get('note') or '暂无'}\n"
            f"陪伴记忆：{_single_line(self._format_companion_memory_for_prompt(user), 180)}\n"
            f"表达节奏学习：{_single_line(self._format_expression_profile_for_prompt(user), 180)}\n"
            f"气氛状态：{_single_line(self._format_intent_relationship_injection(user), 180) or '暂无'}\n"
            f"媒介偏好：{_single_line(self._action_preference_hint(user), 180) or '暂无'}"
        )

