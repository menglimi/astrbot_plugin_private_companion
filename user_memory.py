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
from .helpers import _date_key, _normalize_photo_subject_owner, _now_ts, _photo_subject_owner_prompt_label, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks, _today_key
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
        regulation_hint = self._format_gross_emotion_regulation_hint(rel_state)
        regulation = rel_state.get("emotion_regulation")
        regulation_strategy = str(regulation.get("strategy") or "") if isinstance(regulation, dict) else ""
        if mode == "refusing" and hurt_active and abs(mood_score) >= refuse_threshold:
            base = "对用户刚才的言行还有些不满,表现得回避一点；回复短一些、安静一些,先别急着贴近。"
            return f"{base} {regulation_hint}" if regulation_hint and regulation_strategy != "response_modulation" else base
        if mode == "hurt" and hurt_active and abs(mood_score) >= hurt_threshold:
            base = "被用户刚才的言行伤到心里,还没有完全恢复；语气放轻、放慢一点,别急着热情贴近。"
            return f"{base} {regulation_hint}" if regulation_hint and regulation_strategy != "response_modulation" else base
        dimension_hint = self._format_izard_emotion_dimension_hint(rel_state)
        plutchik_hint = self._format_plutchik_emotion_hint(rel_state)
        return " ".join(item for item in (dimension_hint, plutchik_hint, regulation_hint) if item).strip()

    def _emotion_dimension_baseline(self) -> dict[str, int]:
        return {"pleasantness": 0, "tension": 12, "arousal": 20, "certainty": 60}

    def _move_emotion_dimension_toward(self, value: int, target: int, amount: int) -> int:
        if value < target:
            return min(target, value + amount)
        if value > target:
            return max(target, value - amount)
        return value

    def _decay_izard_emotion_dimensions(self, state: dict[str, Any], *, now: float | None = None) -> dict[str, int]:
        now = now or _now_ts()
        baseline = self._emotion_dimension_baseline()
        raw = state.get("emotion_dimensions")
        dims = dict(raw) if isinstance(raw, dict) else {}
        last_ts = _safe_float(dims.get("updated_ts"), _safe_float(state.get("mood_updated_ts"), now))
        hours = max(0.0, (now - last_ts) / 3600.0) if last_ts > 0 else 0.0
        recovery = max(1, _safe_int(getattr(self, "emotional_gate_recovery_per_hour", 24), 24, 1, 60))
        steps = max(0, int(hours * recovery))
        result: dict[str, int] = {}
        for key, target in baseline.items():
            minimum = -100 if key == "pleasantness" else 0
            value = _safe_int(dims.get(key), target, minimum, 100)
            if steps > 0:
                amount = max(1, steps)
                if key == "pleasantness":
                    amount = max(1, int(steps * 0.75))
                elif key == "certainty":
                    amount = max(1, int(steps * 0.55))
                result[key] = self._move_emotion_dimension_toward(value, target, amount)
            else:
                result[key] = value
        result["updated_ts"] = int(now)
        state["emotion_dimensions"] = result
        return result

    def _nudge_emotion_dimension(self, dims: dict[str, int], key: str, delta: int) -> None:
        minimum = -100 if key == "pleasantness" else 0
        dims[key] = max(minimum, min(100, _safe_int(dims.get(key), 0, minimum, 100) + int(delta)))

    def _update_izard_emotion_dimensions(
        self,
        state: dict[str, Any],
        *,
        event: str,
        intensity: int,
        target: str,
        confidence: float,
        inbound_intent: str,
        pressure: int,
        mode: str,
        now: float | None = None,
    ) -> dict[str, int]:
        dims = self._decay_izard_emotion_dimensions(state, now=now)
        event = str(event or "neutral")
        target = str(target or "none")
        intensity = _safe_int(intensity, 0, 0, 100)
        confidence = max(0.0, min(1.0, _safe_float(confidence, 0.5, 0.0)))
        if event == "hurt" and target in {"bot", "ambiguous"}:
            self._nudge_emotion_dimension(dims, "pleasantness", -max(12, int(intensity * 0.65)))
            self._nudge_emotion_dimension(dims, "tension", max(18, int(24 + intensity * 0.42)))
            self._nudge_emotion_dimension(dims, "arousal", max(12, int(16 + intensity * 0.28)))
            self._nudge_emotion_dimension(dims, "certainty", 8 if target == "bot" and confidence >= 0.82 else -18)
        elif event == "apology":
            self._nudge_emotion_dimension(dims, "pleasantness", max(14, int(intensity * 0.45)))
            self._nudge_emotion_dimension(dims, "tension", -max(18, int(intensity * 0.48)))
            self._nudge_emotion_dimension(dims, "arousal", -max(8, int(intensity * 0.22)))
            self._nudge_emotion_dimension(dims, "certainty", max(12, int(intensity * 0.35)))
        elif event == "comfort":
            self._nudge_emotion_dimension(dims, "pleasantness", max(10, int(intensity * 0.36)))
            self._nudge_emotion_dimension(dims, "tension", -max(12, int(intensity * 0.42)))
            self._nudge_emotion_dimension(dims, "arousal", -max(6, int(intensity * 0.18)))
            self._nudge_emotion_dimension(dims, "certainty", max(8, int(intensity * 0.28)))
        elif event == "praise":
            self._nudge_emotion_dimension(dims, "pleasantness", max(10, int(intensity * 0.5)))
            self._nudge_emotion_dimension(dims, "tension", -max(5, int(intensity * 0.25)))
            self._nudge_emotion_dimension(dims, "arousal", max(4, int(intensity * 0.18)))
            self._nudge_emotion_dimension(dims, "certainty", max(6, int(intensity * 0.25)))
        elif event == "comfort_need":
            self._nudge_emotion_dimension(dims, "pleasantness", -14)
            self._nudge_emotion_dimension(dims, "tension", max(10, int(intensity * 0.28)))
            self._nudge_emotion_dimension(dims, "arousal", max(6, int(intensity * 0.16)))
            self._nudge_emotion_dimension(dims, "certainty", 5)
        elif event == "external_negative":
            self._nudge_emotion_dimension(dims, "pleasantness", -8)
            self._nudge_emotion_dimension(dims, "tension", max(8, int(intensity * 0.25)))
            self._nudge_emotion_dimension(dims, "arousal", max(6, int(intensity * 0.22)))
            self._nudge_emotion_dimension(dims, "certainty", 8)
        elif inbound_intent == "boundary" or mode == "backoff":
            self._nudge_emotion_dimension(dims, "pleasantness", -8)
            self._nudge_emotion_dimension(dims, "tension", 18 if pressure >= 2 else 10)
            self._nudge_emotion_dimension(dims, "arousal", -4)
            self._nudge_emotion_dimension(dims, "certainty", 18)
        elif inbound_intent in {"intimacy", "play"} and mode in {"warming", "attached"}:
            self._nudge_emotion_dimension(dims, "pleasantness", 10 if mode == "warming" else 16)
            self._nudge_emotion_dimension(dims, "tension", -8)
            self._nudge_emotion_dimension(dims, "arousal", 6)
            self._nudge_emotion_dimension(dims, "certainty", 8)
        dims["updated_ts"] = int(now or _now_ts())
        state["emotion_dimensions"] = dims
        return dims

    def _format_izard_emotion_dimension_hint(self, state: dict[str, Any]) -> str:
        dims = self._decay_izard_emotion_dimensions(state)
        pleasantness = _safe_int(dims.get("pleasantness"), 0, -100, 100)
        tension = _safe_int(dims.get("tension"), 12, 0, 100)
        arousal = _safe_int(dims.get("arousal"), 20, 0, 100)
        certainty = _safe_int(dims.get("certainty"), 60, 0, 100)
        if pleasantness <= -45 and tension >= 45:
            if arousal >= 55:
                return "情绪底色不愉快、紧张且激动度偏高；回复要短、慢、低刺激,先稳住,不要追问或开玩笑。"
            return "情绪底色不愉快且紧张,但激动度不高；回复安静一点,先接住,别急着贴近。"
        if tension >= 60 and certainty <= 38:
            return "情绪紧张且确信度偏低；不要替用户下结论,多用试探和确认,少做强判断。"
        if pleasantness >= 35 and tension <= 30 and certainty >= 55:
            return "情绪底色较愉快且放松；可以自然一点、轻一点靠近,但仍不要突然过度热情。"
        if arousal >= 65 and tension >= 40:
            return "激动度和紧张度都偏高；回复节奏收住,避免长段解释和继续加压。"
        if certainty <= 30:
            return "当前确信度偏低；语气保留余地,不要把猜测说死。"
        return ""

    def _plutchik_emotion_labels(self) -> dict[str, str]:
        return {
            "joy": "喜悦",
            "trust": "信任",
            "fear": "恐惧",
            "surprise": "惊讶",
            "sadness": "悲伤",
            "disgust": "厌恶",
            "anger": "愤怒",
            "anticipation": "期待",
        }

    def _decay_plutchik_emotions(self, state: dict[str, Any], *, now: float | None = None) -> dict[str, int]:
        now = now or _now_ts()
        labels = self._plutchik_emotion_labels()
        raw = state.get("plutchik_emotions")
        values = dict(raw) if isinstance(raw, dict) else {}
        last_ts = _safe_float(values.get("updated_ts"), _safe_float(state.get("mood_updated_ts"), now))
        hours = max(0.0, (now - last_ts) / 3600.0) if last_ts > 0 else 0.0
        recovery = max(1, _safe_int(getattr(self, "emotional_gate_recovery_per_hour", 24), 24, 1, 60))
        decay = max(0, int(hours * recovery))
        result: dict[str, int] = {}
        for key in labels:
            value = _safe_int(values.get(key), 0, 0, 100)
            result[key] = max(0, value - decay) if decay > 0 else value
        result["updated_ts"] = int(now)
        state["plutchik_emotions"] = result
        state["plutchik_profile"] = self._plutchik_profile_from_basic(result, now=now)
        return result

    def _nudge_plutchik_emotion(self, emotions: dict[str, int], key: str, delta: int) -> None:
        if key not in self._plutchik_emotion_labels():
            return
        emotions[key] = max(0, min(100, _safe_int(emotions.get(key), 0, 0, 100) + int(delta)))

    def _update_plutchik_emotions(
        self,
        state: dict[str, Any],
        *,
        event: str,
        intensity: int,
        target: str,
        inbound_intent: str,
        pressure: int,
        mode: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = now or _now_ts()
        emotions = self._decay_plutchik_emotions(state, now=now)
        event = str(event or "neutral")
        intensity = _safe_int(intensity, 0, 0, 100)
        if event == "hurt" and target in {"bot", "ambiguous"}:
            self._nudge_plutchik_emotion(emotions, "sadness", max(16, int(intensity * 0.42)))
            self._nudge_plutchik_emotion(emotions, "anger", max(14, int(intensity * 0.36)))
            self._nudge_plutchik_emotion(emotions, "disgust", max(8, int(intensity * 0.22)))
            self._nudge_plutchik_emotion(emotions, "trust", -max(10, int(intensity * 0.25)))
        elif event == "apology":
            self._nudge_plutchik_emotion(emotions, "trust", max(16, int(intensity * 0.48)))
            self._nudge_plutchik_emotion(emotions, "joy", max(8, int(intensity * 0.24)))
            self._nudge_plutchik_emotion(emotions, "sadness", -max(12, int(intensity * 0.36)))
            self._nudge_plutchik_emotion(emotions, "anger", -max(12, int(intensity * 0.42)))
            self._nudge_plutchik_emotion(emotions, "disgust", -max(8, int(intensity * 0.28)))
        elif event == "comfort":
            self._nudge_plutchik_emotion(emotions, "trust", max(12, int(intensity * 0.42)))
            self._nudge_plutchik_emotion(emotions, "joy", max(6, int(intensity * 0.2)))
            self._nudge_plutchik_emotion(emotions, "sadness", -max(8, int(intensity * 0.24)))
            self._nudge_plutchik_emotion(emotions, "fear", -max(6, int(intensity * 0.18)))
        elif event == "praise":
            self._nudge_plutchik_emotion(emotions, "joy", max(12, int(intensity * 0.52)))
            self._nudge_plutchik_emotion(emotions, "trust", max(8, int(intensity * 0.32)))
            self._nudge_plutchik_emotion(emotions, "anticipation", max(4, int(intensity * 0.18)))
        elif event == "comfort_need":
            self._nudge_plutchik_emotion(emotions, "sadness", max(14, int(intensity * 0.35)))
            self._nudge_plutchik_emotion(emotions, "fear", max(8, int(intensity * 0.2)))
            self._nudge_plutchik_emotion(emotions, "trust", 6)
        elif event == "external_negative":
            self._nudge_plutchik_emotion(emotions, "anger", max(10, int(intensity * 0.3)))
            self._nudge_plutchik_emotion(emotions, "disgust", max(8, int(intensity * 0.24)))
            self._nudge_plutchik_emotion(emotions, "surprise", 4)
        elif inbound_intent == "boundary" or mode == "backoff":
            self._nudge_plutchik_emotion(emotions, "fear", 12 if pressure >= 2 else 7)
            self._nudge_plutchik_emotion(emotions, "sadness", 8)
            self._nudge_plutchik_emotion(emotions, "trust", -8)
        elif inbound_intent in {"intimacy", "play"} and mode in {"warming", "attached"}:
            self._nudge_plutchik_emotion(emotions, "joy", 12 if mode == "attached" else 8)
            self._nudge_plutchik_emotion(emotions, "trust", 14 if mode == "attached" else 9)
            self._nudge_plutchik_emotion(emotions, "anticipation", 6)
        emotions["updated_ts"] = int(now)
        state["plutchik_emotions"] = emotions
        profile = self._plutchik_profile_from_basic(emotions, now=now)
        state["plutchik_profile"] = profile
        return profile

    def _plutchik_profile_from_basic(self, emotions: dict[str, int], *, now: float | None = None) -> dict[str, Any]:
        labels = self._plutchik_emotion_labels()
        ordered = sorted(
            ((key, _safe_int(emotions.get(key), 0, 0, 100)) for key in labels),
            key=lambda item: item[1],
            reverse=True,
        )
        dominant_key, dominant_value = ordered[0] if ordered else ("", 0)
        secondary_key, secondary_value = ordered[1] if len(ordered) > 1 else ("", 0)
        primary_dyads = {
            frozenset(("joy", "trust")): ("love", "亲近/喜欢"),
            frozenset(("trust", "fear")): ("submission", "依赖/顺从"),
            frozenset(("fear", "surprise")): ("awe", "敬畏/惊住"),
            frozenset(("surprise", "sadness")): ("disapproval", "失望/不认可"),
            frozenset(("sadness", "disgust")): ("remorse", "懊悔/难受"),
            frozenset(("disgust", "anger")): ("contempt", "轻蔑/反感"),
            frozenset(("anger", "anticipation")): ("aggressiveness", "进攻/顶回去"),
            frozenset(("anticipation", "joy")): ("optimism", "期待/乐观"),
        }
        blend_key = ""
        blend_label = ""
        if dominant_value >= 28 and secondary_value >= 22:
            blend = primary_dyads.get(frozenset((dominant_key, secondary_key)))
            if blend:
                blend_key, blend_label = blend
        active = [
            {"key": key, "label": labels.get(key, key), "value": value}
            for key, value in ordered
            if value >= 18
        ][:4]
        return {
            "dominant": dominant_key if dominant_value >= 18 else "",
            "dominant_label": labels.get(dominant_key, "") if dominant_value >= 18 else "",
            "dominant_value": dominant_value if dominant_value >= 18 else 0,
            "secondary": secondary_key if secondary_value >= 18 else "",
            "secondary_label": labels.get(secondary_key, "") if secondary_value >= 18 else "",
            "secondary_value": secondary_value if secondary_value >= 18 else 0,
            "blend": blend_key,
            "blend_label": blend_label,
            "active": active,
            "updated_ts": int(now or _now_ts()),
        }

    def _format_plutchik_emotion_hint(self, state: dict[str, Any]) -> str:
        emotions = self._decay_plutchik_emotions(state)
        profile = self._plutchik_profile_from_basic(emotions)
        blend = str(profile.get("blend") or "")
        dominant = str(profile.get("dominant") or "")
        if blend == "love":
            return "她现在更容易被亲近感带软；可以自然靠近一点,但别突然过度黏。"
        if blend == "optimism":
            return "她现在有一点期待感；语气可以轻快些,适合留一个小期待或轻松收尾。"
        if blend in {"contempt", "aggressiveness"}:
            return "她现在有反感或想顶回去的倾向；避免讽刺、争辩和升级,先短答降压。"
        if blend in {"disapproval", "remorse"}:
            return "她现在偏失望或难受；回复放慢,承认感受,不要用玩笑盖过去。"
        if blend in {"submission", "awe"}:
            return "她现在有点不安、像被压住；语气放稳,不要继续压迫或替用户强决策。"
        if dominant == "anger":
            return "她现在火气比较明显；回复避免顶撞,少解释,先让压力降下来。"
        if dominant == "sadness":
            return "她现在有点难过；回复温和一点,不要急着活跃气氛。"
        if dominant == "fear":
            return "她现在不安感更强；回复要给边界和确定感,少催促。"
        if dominant == "joy":
            return "她现在更轻松开心；可以自然一点,但仍保持角色分寸。"
        return ""

    def _gross_regulation_strategy_labels(self) -> dict[str, str]:
        return {
            "situation_selection": "避开高压",
            "situation_modification": "换低压问法",
            "attentional_deployment": "转移注意",
            "cognitive_change": "重新理解",
            "response_modulation": "短答降压",
        }

    def _derive_gross_emotion_regulation(
        self,
        state: dict[str, Any],
        *,
        event: str | None = None,
        intensity: int | None = None,
        target: str | None = None,
        inbound_intent: str | None = None,
        pressure: int | None = None,
        mode: str | None = None,
        mood_score: int | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = now or _now_ts()
        labels = self._gross_regulation_strategy_labels()
        dims = self._decay_izard_emotion_dimensions(state, now=now)
        emotions = self._decay_plutchik_emotions(state, now=now)
        event = str(event if event is not None else state.get("last_emotion_event") or "neutral")
        target = str(target if target is not None else state.get("last_emotion_target") or "none")
        inbound_intent = str(inbound_intent if inbound_intent is not None else state.get("last_intent") or "chat")
        mode = str(mode if mode is not None else state.get("mode") or "normal")
        intensity_value = _safe_int(intensity if intensity is not None else state.get("last_emotion_intensity"), 0, 0, 100)
        pressure_value = _safe_int(pressure if pressure is not None else state.get("last_pressure"), 0, 0, 5)
        mood_value = _safe_int(mood_score if mood_score is not None else state.get("mood_score"), 0, -100, 100)
        pleasantness = _safe_int(dims.get("pleasantness"), 0, -100, 100)
        tension = _safe_int(dims.get("tension"), 12, 0, 100)
        arousal = _safe_int(dims.get("arousal"), 20, 0, 100)
        certainty = _safe_int(dims.get("certainty"), 60, 0, 100)
        unpleasant = max(0, -pleasantness)
        uncertainty = max(0, 60 - certainty)
        anger_like = max(
            _safe_int(emotions.get("anger"), 0, 0, 100),
            _safe_int(emotions.get("disgust"), 0, 0, 100),
        )
        vulnerable = max(
            _safe_int(emotions.get("sadness"), 0, 0, 100),
            _safe_int(emotions.get("fear"), 0, 0, 100),
        )
        surprise = _safe_int(emotions.get("surprise"), 0, 0, 100)
        pressure_load = pressure_value * 14
        hurt_active = _safe_float(state.get("hurt_until"), 0) > now
        backoff_active = _safe_float(state.get("backoff_until"), 0) > now
        candidates: list[dict[str, Any]] = []

        def add_candidate(strategy: str, score: int, reason_text: str) -> None:
            if strategy not in labels:
                return
            score = max(0, min(100, int(score)))
            if score < 36:
                return
            candidates.append({
                "strategy": strategy,
                "strategy_label": labels[strategy],
                "intensity": score,
                "reason": reason_text,
            })

        add_candidate(
            "situation_selection",
            max(
                78 if mode in {"refusing", "backoff"} else 0,
                68 if hurt_active and mode == "hurt" else 0,
                72 if backoff_active or inbound_intent == "boundary" else 0,
                unpleasant + pressure_load,
            ),
            "边界或受伤余波较强",
        )
        add_candidate(
            "situation_modification",
            max(
                54 if pressure_value >= 2 and mode not in {"refusing", "backoff"} else 0,
                int(tension * 0.72 + max(0, intensity_value - 30) * 0.28),
                50 if event in {"comfort", "apology"} and mood_value < 0 else 0,
            ),
            "话题还能继续但需要降压改法",
        )
        add_candidate(
            "attentional_deployment",
            max(
                66 if event in {"comfort_need", "external_negative"} else 0,
                int(vulnerable * 0.85 + tension * 0.22),
                52 if inbound_intent in {"help", "comfort"} else 0,
            ),
            "更适合把注意力落到眼前小事",
        )
        add_candidate(
            "cognitive_change",
            max(
                int(uncertainty * 1.35 + surprise * 0.35),
                58 if target == "ambiguous" and event != "neutral" else 0,
                50 if certainty <= 36 and tension >= 38 else 0,
            ),
            "理解仍有不确定性",
        )
        add_candidate(
            "response_modulation",
            max(
                int(arousal * 0.76 + tension * 0.34),
                anger_like + int(pressure_load * 0.45),
                70 if mode in {"hurt", "refusing"} else 0,
            ),
            "外显反应需要先收住",
        )
        candidates.sort(key=lambda item: _safe_int(item.get("intensity"), 0, 0, 100), reverse=True)
        if not candidates:
            regulation = {
                "strategy": "none",
                "strategy_label": "无需额外调节",
                "reason": "",
                "intensity": 0,
                "strategy_stack": [],
                "updated_ts": int(now),
            }
            state["emotion_regulation"] = regulation
            return regulation
        primary = dict(candidates[0])
        stack = candidates[:3]
        regulation = {
            "strategy": primary.get("strategy") or "",
            "strategy_label": primary.get("strategy_label") or "",
            "reason": primary.get("reason") or "",
            "intensity": _safe_int(primary.get("intensity"), 0, 0, 100),
            "strategy_stack": stack,
            "updated_ts": int(now),
        }
        state["emotion_regulation"] = regulation
        return regulation

    def _format_gross_emotion_regulation_hint(self, state: dict[str, Any]) -> str:
        regulation = state.get("emotion_regulation")
        now = _now_ts()
        if not isinstance(regulation, dict) or now - _safe_float(regulation.get("updated_ts"), 0) > 900:
            regulation = self._derive_gross_emotion_regulation(state, now=now)
        strategy = str(regulation.get("strategy") or "")
        intensity = _safe_int(regulation.get("intensity"), 0, 0, 100)
        if not strategy or strategy == "none" or intensity < 42:
            return ""
        if strategy == "situation_selection":
            return "这轮更适合先避开高压点；不要继续追着刺痛处聊,必要时轻轻退开。"
        if strategy == "situation_modification":
            return "话题可以继续,但要换成低压问法；短句、少追问,给用户容易接的台阶。"
        if strategy == "attentional_deployment":
            return "更适合把注意力带回眼前小事；先接住情绪,再给一个具体、可做的小落点。"
        if strategy == "cognitive_change":
            return "先给对方留解释空间；少做定性判断,用试探语气,不要把猜测说死。"
        if strategy == "response_modulation":
            return "先控制外显反应；短一点、慢一点、少解释,避免顶回去或连续补充。"
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

    def _expression_scope_mode(self, key: str, allowed: set[str], default: str) -> str:
        value = str(getattr(self, key, default) or default).strip().lower()
        return value if value in allowed else default

    def _expression_scope_ids(self, key: str, *, group: bool = False) -> set[str]:
        raw = getattr(self, key, [])
        parser = getattr(self, "_parse_group_id_list" if group else "_parse_text_list_config", None)
        try:
            values = parser(raw) if callable(parser) else (raw if isinstance(raw, list) else [])
        except Exception:
            values = raw if isinstance(raw, list) else []
        normalized: set[str] = set()
        for item in values:
            value = _single_line(item, 80)
            if not value:
                continue
            if not group:
                value = self._expression_private_scope_id(value)
            if value:
                normalized.add(value)
        return normalized

    def _expression_private_scope_id(self, user_id: Any) -> str:
        """Normalize configured private IDs so aliases follow the same identity boundary."""
        value = _single_line(user_id, 80)
        normalizer = getattr(self, "_canonical_private_user_id", None)
        if value and callable(normalizer):
            try:
                value = _single_line(normalizer(value), 80) or value
            except Exception:
                pass
        return value

    def _expression_private_learning_source_enabled(self, user: dict[str, Any], user_id: Any = "") -> bool:
        mode = self._expression_scope_mode(
            "expression_private_learning_source_mode",
            {"owner", "selected", "all"},
            "owner",
        )
        user_id = self._expression_private_scope_id(user_id or user.get("user_id"))
        if mode == "all":
            return True
        if mode == "selected":
            return bool(user_id and user_id in self._expression_scope_ids("expression_private_learning_source_ids"))
        role_getter = getattr(self, "_private_user_role", None)
        try:
            role = role_getter(user, user_id) if callable(role_getter) else str(user.get("relationship_role") or "")
        except Exception:
            role = str(user.get("relationship_role") or "")
        return str(role or "").strip().lower() == "owner"

    def _expression_group_learning_source_enabled(self, group_id: Any) -> bool:
        mode = self._expression_scope_mode(
            "expression_group_learning_source_mode",
            {"disabled", "selected", "all"},
            "disabled",
        )
        group_id = _single_line(group_id, 80)
        if mode == "all":
            return bool(group_id)
        if mode == "selected":
            return bool(group_id and group_id in self._expression_scope_ids("expression_group_learning_source_ids", group=True))
        return False

    def _expression_private_application_enabled(self, user_id: Any) -> bool:
        mode = self._expression_scope_mode(
            "expression_private_application_mode",
            {"all", "selected"},
            "all",
        )
        user_id = self._expression_private_scope_id(user_id)
        return mode == "all" or bool(user_id and user_id in self._expression_scope_ids("expression_private_application_user_ids"))

    def _expression_group_application_enabled(self, group_id: Any) -> bool:
        mode = self._expression_scope_mode(
            "expression_group_application_mode",
            {"disabled", "all", "selected"},
            "all",
        )
        group_id = _single_line(group_id, 80)
        if mode == "all":
            return bool(group_id)
        if mode == "selected":
            return bool(group_id and group_id in self._expression_scope_ids("expression_group_application_ids", group=True))
        return False

    def _expression_scope_signature(self) -> str:
        parts = [
            self._expression_scope_mode("expression_private_learning_source_mode", {"owner", "selected", "all"}, "owner"),
            ",".join(sorted(self._expression_scope_ids("expression_private_learning_source_ids"))),
            self._expression_scope_mode("expression_group_learning_source_mode", {"disabled", "selected", "all"}, "disabled"),
            ",".join(sorted(self._expression_scope_ids("expression_group_learning_source_ids", group=True))),
            self._expression_scope_mode("expression_private_application_mode", {"all", "selected"}, "all"),
            ",".join(sorted(self._expression_scope_ids("expression_private_application_user_ids"))),
            self._expression_scope_mode("expression_group_application_mode", {"disabled", "all", "selected"}, "all"),
            ",".join(sorted(self._expression_scope_ids("expression_group_application_ids", group=True))),
        ]
        return "|".join(parts)

    @staticmethod
    def _expression_voice_actions(
        sample_count: int,
        short_ratio: float,
        feature_counts: dict[str, Any],
        *,
        limit: int = 3,
    ) -> list[str]:
        if sample_count < 2:
            return []
        actions: list[str] = []
        if short_ratio >= 0.55:
            actions.append("优先用一两句完整短句，保持即时聊天感")
        elif short_ratio <= 0.2:
            actions.append("可以说完整一点，但不要写成说明书")
        if _safe_int(feature_counts.get("casual_opener"), 0, 0) >= 2:
            actions.append("开头可以自然地随口起一句，避开客服式开场")
        if _safe_int(feature_counts.get("laugh_marker"), 0, 0) >= 2:
            actions.append("轻松时可放一个笑声式口语标记，不要连续堆叠")
        elif _safe_int(feature_counts.get("soft_wave"), 0, 0) >= 2:
            actions.append("轻松时可用一个轻微波浪号收束，不要每句都加")
        elif _safe_int(feature_counts.get("playful"), 0, 0) >= 2:
            actions.append("保留一点轻松口语感，但不要硬塞口癖")
        if _safe_int(feature_counts.get("soft_ending"), 0, 0) >= 2:
            actions.append("收尾可以放轻一点，不必强行加语气词")
        if _safe_int(feature_counts.get("reduplication"), 0, 0) >= 2:
            actions.append("亲近轻松的话题里可偶尔用一个自然叠词，不要生造")
        if _safe_int(feature_counts.get("pause"), 0, 0) >= 2:
            actions.append("允许留一点停顿感，最多一个省略号")
        return actions[: max(1, limit)]

    def _refresh_expression_voice_profile(self) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        now = _now_ts()
        cutoff = now - 30 * 86400
        refresh_day = datetime.now().strftime("%Y-%m-%d")
        total_samples = 0
        total_short = 0
        private_sources = 0
        group_sources = 0
        feature_counts: dict[str, int] = {}
        scene_profiles: dict[str, dict[str, Any]] = {}
        semantic_rules: dict[str, dict[str, Any]] = {}

        def collect(profile: Any, *, source_kind: str, source_id: str) -> None:
            nonlocal total_samples, total_short, private_sources, group_sources
            if not isinstance(profile, dict):
                return
            self._backfill_expression_rule_families(profile)
            if source_kind == "group":
                samples = [
                    item
                    for item in self._group_expression_pattern_samples(profile, now=now)
                    if _safe_int(item.get("evidence_count"), 1, 1) >= 2
                ]
            else:
                raw_samples = profile.get("samples")
                samples = [
                    item
                    for item in (raw_samples if isinstance(raw_samples, list) else [])
                    if isinstance(item, dict) and _safe_float(item.get("ts"), now) >= cutoff
                ]
            learned_rules = [
                item
                for item in (profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else [])
                if (
                    isinstance(item, dict)
                    and _safe_int(item.get("evidence_count"), 0, 0) >= 1
                    and self._expression_rule_definition_is_valid(item)
                )
            ]
            if not samples and not learned_rules:
                return
            if source_kind == "private":
                private_sources += 1
            else:
                group_sources += 1
            for item in samples:
                evidence = _safe_int(item.get("evidence_count"), 1, 1)
                weight = min(evidence, 6) if source_kind == "group" else 1
                total_samples += weight
                length = _safe_int(item.get("length"), 0, 0)
                if 0 < length <= 18:
                    total_short += weight
                scene = _single_line(item.get("scene"), 32)
                if scene not in {"acknowledgement", "question", "request", "tease", "emotion", "casual"}:
                    scene = self._expression_scene_from_text(item.get("text") or item.get("phrase"))
                bucket = scene_profiles.setdefault(scene, {"count": 0, "short_count": 0, "feature_counts": {}})
                bucket["count"] += weight
                if 0 < length <= 18:
                    bucket["short_count"] += weight
                raw_features = item.get("features")
                features = raw_features if isinstance(raw_features, list) else self._expression_style_features_from_text(item.get("text") or item.get("phrase"))
                for feature in features:
                    key = _single_line(feature, 32)
                    if not key:
                        continue
                    feature_counts[key] = _safe_int(feature_counts.get(key), 0, 0) + weight
                    bucket_features = bucket["feature_counts"]
                    bucket_features[key] = _safe_int(bucket_features.get(key), 0, 0) + weight
            for item in learned_rules:
                # 只汇总已经审核通过的规则。pattern 是脱敏后的可复用表达模板，
                # 与 evidence_examples 不同，可以进入召回；支持片段永远只留在审核页。
                kind = _single_line(item.get("kind"), 16).lower()
                situation = _single_line(item.get("situation"), 80)
                pattern = _single_line(item.get("pattern") or item.get("style"), 100)
                instruction = _single_line(item.get("instruction"), 140)
                if kind not in {"style", "grammar"} or not situation or not pattern or not instruction:
                    continue
                if not self._safe_expression_phrase(pattern, 100):
                    continue
                family_id = _single_line(item.get("family_id"), 64)
                signature_text = "|".join(
                    (
                        family_id,
                        kind,
                        re.sub(r"[\s，。！？!?、；;：:]", "", situation).lower(),
                        re.sub(r"[\s，。！？!?、；;：:]", "", pattern).lower(),
                    )
                )
                signature = hashlib.sha1(signature_text.encode("utf-8")).hexdigest()[:16]
                evidence = min(6, _safe_int(item.get("evidence_count"), 0, 0))
                bucket = semantic_rules.setdefault(
                    signature,
                    {
                        "id": signature,
                        "family_id": family_id,
                        "kind": kind,
                        "situation": situation,
                        "pattern": pattern,
                        "instruction": instruction,
                        "keywords": [],
                        "evidence_count": 0,
                        "source_kinds": [],
                        "source_refs": [],
                        "channels": [],
                        "relationship_stages": [],
                        "emotion_gates": [],
                        "intent": "",
                        "avoid": "",
                        "persona_conflict": False,
                        "positive_feedback": 0,
                        "negative_feedback": 0,
                        "use_count": 0,
                        "last_seen_ts": 0.0,
                    },
                )
                bucket["evidence_count"] = min(99, _safe_int(bucket.get("evidence_count"), 0, 0) + evidence)
                bucket["last_seen_ts"] = max(
                    _safe_float(bucket.get("last_seen_ts"), 0.0),
                    _safe_float(item.get("last_seen_ts"), now),
                )
                if source_kind not in bucket["source_kinds"]:
                    bucket["source_kinds"].append(source_kind)
                source_ref = {
                    "source_kind": source_kind,
                    "source_id": _single_line(source_id, 80),
                    "rule_id": _single_line(item.get("id"), 40),
                }
                if source_ref["source_id"] and source_ref["rule_id"] and source_ref not in bucket["source_refs"]:
                    bucket["source_refs"].append(source_ref)
                for field in ("channels", "relationship_stages", "emotion_gates"):
                    values = item.get(field) if isinstance(item.get(field), list) else []
                    for value in values:
                        normalized = _single_line(value, 24).lower()
                        if normalized and normalized not in bucket[field]:
                            bucket[field].append(normalized)
                incoming_intent = _single_line(item.get("intent"), 32).lower()
                if incoming_intent:
                    if bucket["intent"] and bucket["intent"] != incoming_intent:
                        bucket["intent"] = "any"
                    else:
                        bucket["intent"] = incoming_intent
                incoming_avoid = _single_line(item.get("avoid"), 160)
                if incoming_avoid and len(incoming_avoid) > len(bucket["avoid"]):
                    bucket["avoid"] = incoming_avoid
                bucket["persona_conflict"] = bool(bucket["persona_conflict"] or item.get("persona_conflict"))
                bucket["positive_feedback"] = min(
                    999,
                    _safe_int(bucket.get("positive_feedback"), 0, 0)
                    + _safe_int(item.get("positive_feedback"), 0, 0),
                )
                bucket["negative_feedback"] = min(
                    999,
                    _safe_int(bucket.get("negative_feedback"), 0, 0)
                    + _safe_int(item.get("negative_feedback"), 0, 0),
                )
                bucket["use_count"] = min(
                    99999,
                    _safe_int(bucket.get("use_count"), 0, 0)
                    + _safe_int(item.get("use_count"), 0, 0),
                )
                for keyword in item.get("keywords", []) if isinstance(item.get("keywords"), list) else []:
                    value = _single_line(keyword, 24)
                    if value and value not in bucket["keywords"]:
                        bucket["keywords"].append(value)
                bucket["keywords"] = bucket["keywords"][:8]

        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        for user_id, user in users.items():
            if isinstance(user, dict) and self._expression_private_learning_source_enabled(user, user_id):
                collect(user.get("expression_profile"), source_kind="private", source_id=str(user_id))
        groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
        for group_id, group in groups.items():
            if isinstance(group, dict) and self._expression_group_learning_source_enabled(group_id):
                collect(group.get("expression_profile"), source_kind="group", source_id=str(group_id))

        profile = {
            "sample_count": total_samples,
            "private_source_count": private_sources,
            "group_source_count": group_sources,
            "short_ratio": round(total_short / max(1, total_samples), 2),
            "feature_counts": feature_counts,
            "scene_profiles": {
                scene: {
                    "count": _safe_int(bucket.get("count"), 0, 0),
                    "short_ratio": round(_safe_int(bucket.get("short_count"), 0, 0) / max(1, _safe_int(bucket.get("count"), 0, 0)), 2),
                    "feature_counts": dict(bucket.get("feature_counts") or {}),
                }
                for scene, bucket in scene_profiles.items()
                if _safe_int(bucket.get("count"), 0, 0) > 0
            },
            "learned_rules": sorted(
                semantic_rules.values(),
                key=lambda item: (
                    -_safe_int(item.get("evidence_count"), 0, 0),
                    -_safe_float(item.get("last_seen_ts"), 0.0),
                ),
            )[: self.max_learned_expression_items],
            "scope_signature": self._expression_scope_signature(),
            "refresh_day": refresh_day,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        profile["actions"] = self._expression_voice_actions(
            total_samples,
            _safe_float(profile.get("short_ratio"), 0.0),
            feature_counts,
            limit=4,
        )
        data["expression_voice_profile"] = profile
        return profile

    def _expression_voice_profile(self) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        profile = data.get("expression_voice_profile")
        refresh_day = datetime.now().strftime("%Y-%m-%d")
        if (
            not isinstance(profile, dict)
            or profile.get("scope_signature") != self._expression_scope_signature()
            or profile.get("refresh_day") != refresh_day
        ):
            profile = self._refresh_expression_voice_profile()
        return profile if isinstance(profile, dict) else {}

    def _expression_companion_context(
        self,
        *,
        scope: str,
        target_id: str = "",
        inbound_text: str = "",
        context_owner: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        channel = _single_line(scope, 24).lower() or "private"
        owner = context_owner if isinstance(context_owner, dict) else None
        if owner is None and channel in {"private", "proactive", "tts"}:
            users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
            candidate = users.get(str(target_id)) if isinstance(users, dict) else None
            owner = candidate if isinstance(candidate, dict) else None

        relationship_stage = "any"
        if isinstance(owner, dict) and channel in {"private", "proactive", "tts"}:
            level = _single_line(self._relationship_profile(owner).get("level"), 24).lower()
            relationship_stage = {
                "陌生": "stranger",
                "stranger": "stranger",
                "熟悉": "familiar",
                "familiar": "familiar",
                "亲近": "close",
                "close": "close",
            }.get(level, "any")

        intent_profile: dict[str, Any] = {}
        if inbound_text:
            try:
                intent_profile = self._analyze_inbound_intent(inbound_text)
            except Exception:
                intent_profile = {}
        elif isinstance(owner, dict) and isinstance(owner.get("intent_profile"), dict):
            intent_profile = owner.get("intent_profile") or {}
        intent = _single_line(intent_profile.get("intent"), 32).lower()
        if channel == "proactive" and not inbound_text:
            intent = "proactive"
        elif channel == "qzone":
            intent = "emotion" if re.search(r"(低落|委屈|难受|emo|情绪)", inbound_text, re.IGNORECASE) else "casual"
        elif intent in {"", "chat", "empty"}:
            intent = self._expression_scene_from_text(inbound_text) if inbound_text else "casual"

        emotion_gate = "normal"
        state = owner.get("relationship_state") if isinstance(owner, dict) else {}
        state_mode = _single_line(state.get("mode"), 24).lower() if isinstance(state, dict) else ""
        intent_emotion = _single_line(intent_profile.get("emotion"), 24).lower()
        if state_mode in {"refusing", "hurt", "backoff", "careful"} or intent == "boundary" or intent_emotion == "resistant":
            emotion_gate = "guarded"
        elif intent in {"comfort", "emotion"} or intent_emotion == "low":
            emotion_gate = "low"
        elif intent in {"play", "intimacy"} or intent_emotion in {"light", "close", "positive"}:
            emotion_gate = "positive"

        return {
            "channel": channel,
            "relationship_stage": relationship_stage,
            "emotion_gate": emotion_gate,
            "intent": intent or "casual",
        }

    @staticmethod
    def _format_expression_rule_bundle_line(rule: Any) -> str:
        if not isinstance(rule, dict):
            return ""
        style_rule = rule.get("style_rule") if isinstance(rule.get("style_rule"), dict) else None
        grammar_rule = rule.get("grammar_rule") if isinstance(rule.get("grammar_rule"), dict) else None
        if style_rule is None and _single_line(rule.get("kind"), 16).lower() == "style":
            style_rule = rule
        if grammar_rule is None and _single_line(rule.get("kind"), 16).lower() == "grammar":
            grammar_rule = rule
        situation = _single_line(
            (style_rule or {}).get("situation") or (grammar_rule or {}).get("situation") or rule.get("situation"),
            80,
        )
        if not situation:
            return ""
        parts: list[str] = []
        if style_rule:
            pattern = _single_line(style_rule.get("pattern") or style_rule.get("style"), 100)
            instruction = _single_line(style_rule.get("instruction"), 140)
            if pattern and instruction:
                parts.append(f"可复用表达“{pattern}”（{instruction}）")
        if grammar_rule:
            pattern = _single_line(grammar_rule.get("pattern") or grammar_rule.get("style"), 100)
            instruction = _single_line(grammar_rule.get("instruction"), 140)
            if pattern and instruction:
                parts.append(f"句法习惯“{pattern}”（{instruction}）")
        if not parts:
            return ""
        avoid = _single_line(rule.get("avoid"), 200)
        if avoid:
            parts.append(f"边界：{avoid}")
        kind_label = "组合规则" if style_rule and grammar_rule else ("情境表达" if style_rule else "语法习惯")
        return f"- {kind_label}｜当“{situation}”时：" + "；".join(parts)

    def _expression_voice_selection(
        self,
        *,
        scope: str,
        target_id: str = "",
        inbound_text: str = "",
        context_owner: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not bool(getattr(self, "enable_expression_learning", True)):
            return {"prompt": "", "rules": [], "context": {}}
        if scope in {"private", "proactive"} and not self._expression_private_application_enabled(target_id):
            return {"prompt": "", "rules": [], "context": {}}
        if scope == "group" and not self._expression_group_application_enabled(target_id):
            return {"prompt": "", "rules": [], "context": {}}
        profile = self._expression_voice_profile()
        context = self._expression_companion_context(
            scope=scope,
            target_id=target_id,
            inbound_text=inbound_text,
            context_owner=context_owner,
        )
        learned_rules = self._select_learned_expression_rules(
            profile.get("learned_rules"),
            hint=inbound_text,
            limit=2,
            context=context,
        )
        if not learned_rules:
            return {"prompt": "", "rules": [], "context": context}
        guidance: list[str] = []
        for rule in learned_rules:
            line = self._format_expression_rule_bundle_line(rule)
            if line:
                guidance.append(line)
        if not guidance:
            return {"prompt": "", "rules": [], "context": context}
        scope_label = {"private": "私聊回复", "proactive": "私聊主动消息", "group": "群聊回复"}.get(scope, "当前回复")
        evidence_count = sum(_safe_int(item.get("evidence_count"), 0, 0) for item in learned_rules)
        prompt = (
            "【已审核的表达学习规则】\n"
            f"这些规则只来自已允许的私聊/群聊来源，共 {evidence_count} 条支持证据。当前用于{scope_label}：\n"
            + "\n".join(guidance[:4])
            + "\n执行优先级：工具与事实结果 > 安全及能力边界 > AstrBot 人格 > 当前关系与情绪 > 已审核表达规则 > 装饰性口癖/标点。"
            + "任何冲突都舍弃较低优先级；工具失败时绝不能声称已发送、已完成或已成功。"
            + "情境表达可以改写或替换占位符，语法习惯只控制句法；不要机械复读。"
            + "句尾括号或颜文字后缀必须与所属句保持同一行；规则要求括号前无标点时，不得补逗号或其他标点。"
            + "不得带出来源身份、称呼、账号、关系、事实、秘密或支持片段。"
        )
        return {"prompt": prompt, "rules": [dict(item) for item in learned_rules], "context": context}

    def _format_expression_voice_for_prompt(
        self,
        *,
        scope: str,
        target_id: str = "",
        inbound_text: str = "",
        context_owner: dict[str, Any] | None = None,
        stage_owner: dict[str, Any] | None = None,
    ) -> str:
        selection = self._expression_voice_selection(
            scope=scope,
            target_id=target_id,
            inbound_text=inbound_text,
            context_owner=context_owner,
        )
        if isinstance(stage_owner, dict) and selection.get("rules"):
            profile = stage_owner.setdefault("expression_profile", {})
            if isinstance(profile, dict):
                profile["staged_semantic_selection"] = {
                    "ts": _now_ts(),
                    "rules": [dict(item) for item in selection.get("rules", []) if isinstance(item, dict)][:2],
                    "context": dict(selection.get("context") or {}),
                }
        return str(selection.get("prompt") or "")

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

    def _update_group_expression_profile_from_message(self, group: dict[str, Any], text: str) -> None:
        if not self.enable_expression_learning:
            return
        cleaned = _single_line(_strip_internal_message_blocks(text), self._expression_sample_max_chars())
        if not cleaned or self._should_skip_expression_sample(cleaned):
            return
        profile = group.setdefault("expression_profile", {})
        if not isinstance(profile, dict):
            profile = {}
            group["expression_profile"] = profile
        now = _now_ts()
        samples = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        sample = self._expression_sample_from_text(cleaned, now)
        # Group sources retain only aggregate-safe metadata, never a group member's original phrasing.
        for key in ("text", "phrase", "ending"):
            sample.pop(key, None)
        sample["evidence_count"] = 1
        samples.insert(0, sample)
        profile["samples"] = samples
        profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._normalize_group_expression_profile(profile, now=now)

    @staticmethod
    def _expression_length_bucket(length: Any) -> str:
        value = _safe_int(length, 0, 0)
        if value <= 6:
            return "2-6"
        if value <= 12:
            return "7-12"
        if value <= 20:
            return "13-20"
        if value <= 36:
            return "21-36"
        return "37+"

    def _group_expression_pattern_signature(self, item: dict[str, Any]) -> str:
        scene = _single_line(item.get("scene"), 32)
        if scene not in {"acknowledgement", "question", "request", "tease", "emotion", "casual"}:
            scene = "casual"
        raw_features = item.get("features")
        features = sorted({
            _single_line(feature, 32)
            for feature in raw_features
            if _single_line(feature, 32)
        }) if isinstance(raw_features, list) else []
        distinctive = [feature for feature in features if feature not in {"short", "question"}]
        if scene == "casual" and not distinctive:
            return ""
        marks = item.get("punctuation") if isinstance(item.get("punctuation"), dict) else {}
        mark_keys = sorted(str(mark) for mark, count in marks.items() if _safe_int(count, 0, 0) > 0)
        length_bucket = self._expression_length_bucket(item.get("length"))
        return "|".join((scene, length_bucket, ",".join(features), ",".join(mark_keys)))

    def _group_expression_pattern_samples(self, profile: dict[str, Any], *, now: float | None = None) -> list[dict[str, Any]]:
        if not isinstance(profile, dict):
            return []
        now = now or _now_ts()
        cutoff = now - 30 * 86400
        raw_samples = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        buckets: dict[str, dict[str, Any]] = {}
        for raw in raw_samples:
            if not isinstance(raw, dict) or _safe_float(raw.get("ts"), now) < cutoff:
                continue
            signature = self._group_expression_pattern_signature(raw)
            if not signature:
                continue
            evidence = _safe_int(raw.get("evidence_count"), 1, 1, 9999)
            length = _safe_int(raw.get("length"), 0, 0)
            length_total = _safe_int(raw.get("length_total"), length * evidence, 0)
            ts = _safe_float(raw.get("ts"), now)
            first_seen_ts = _safe_float(raw.get("first_seen_ts"), ts)
            features = [
                _single_line(feature, 32)
                for feature in raw.get("features", [])
                if _single_line(feature, 32)
            ] if isinstance(raw.get("features"), list) else []
            marks = raw.get("punctuation") if isinstance(raw.get("punctuation"), dict) else {}
            bucket = buckets.setdefault(
                signature,
                {
                    "id": hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12],
                    "ts": ts,
                    "first_seen_ts": first_seen_ts,
                    "scene": _single_line(raw.get("scene"), 32) or "casual",
                    "features": list(dict.fromkeys(features)),
                    "length_bucket": self._expression_length_bucket(length),
                    "length_total": 0,
                    "evidence_count": 0,
                    "punctuation": {},
                },
            )
            bucket["ts"] = max(_safe_float(bucket.get("ts"), 0.0), ts)
            bucket["first_seen_ts"] = min(_safe_float(bucket.get("first_seen_ts"), ts), first_seen_ts)
            bucket["length_total"] += length_total
            bucket["evidence_count"] += evidence
            bucket_marks = bucket["punctuation"]
            for mark, count in marks.items():
                value = _safe_int(count, 0, 0)
                if value > 0:
                    bucket_marks[str(mark)] = _safe_int(bucket_marks.get(str(mark)), 0, 0) + value
        patterns = []
        for bucket in buckets.values():
            evidence = max(1, _safe_int(bucket.get("evidence_count"), 1, 1))
            bucket["length"] = round(_safe_int(bucket.get("length_total"), 0, 0) / evidence)
            bucket["pattern_status"] = "active" if evidence >= 2 else "observing"
            patterns.append(bucket)
        patterns.sort(key=lambda item: (-_safe_int(item.get("evidence_count"), 0, 0), -_safe_float(item.get("ts"), 0.0)))
        return patterns[: self.max_learned_expression_items]

    def _normalize_group_expression_profile(self, profile: dict[str, Any], *, now: float | None = None) -> bool:
        if not isinstance(profile, dict):
            return False
        before = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        patterns = self._group_expression_pattern_samples(profile, now=now)
        changed = before != patterns
        profile["samples"] = patterns
        profile["pattern_count"] = len(patterns)
        profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._refresh_expression_profile_legacy_summary(profile)
        return changed

    @staticmethod
    def _expression_rule_signature(item: dict[str, Any]) -> str:
        def compact(value: Any, limit: int) -> str:
            return re.sub(
                r"[\s，。！？!?、；;：:‘’“”\"']",
                "",
                _single_line(value, limit).lower(),
            )

        parts = (
            compact(item.get("kind"), 16),
            compact(item.get("situation"), 80),
            compact(item.get("pattern"), 100),
            compact(item.get("instruction"), 140),
        )
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _expression_rule_source_parts(source_text: str, *, source_kind: str) -> tuple[list[str], set[str]]:
        utterances: list[str] = []
        speaker_names: set[str] = set()
        for raw_line in str(source_text or "").splitlines():
            line = raw_line.strip()
            match = re.match(
                r"^(?:\d{2}-\d{2}\s+\d{2}:\d{2}\s+)?([^:：]{1,40})[:：]\s*(.*)$",
                line,
            )
            if not match:
                continue
            speaker, content = match.groups()
            speaker = speaker.strip()
            content = _single_line(content, 260)
            if not content:
                continue
            if source_kind == "private":
                if speaker != "用户":
                    continue
            else:
                if speaker:
                    speaker_names.add(speaker)
            utterances.append(content)
        return utterances, speaker_names

    @staticmethod
    def _expression_rule_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return _single_line(value, 16).lower() in {"1", "true", "yes", "on", "是", "有", "冲突"}

    @staticmethod
    def _normalize_expression_rule_values(
        value: Any,
        *,
        allowed: set[str],
        aliases: dict[str, str],
        defaults: list[str],
    ) -> list[str]:
        if isinstance(value, str):
            raw_values = re.split(r"[,，/、|\s]+", value)
        elif isinstance(value, list):
            raw_values = value
        else:
            raw_values = []
        normalized: list[str] = []
        for raw in raw_values:
            item = _single_line(raw, 24).lower()
            item = aliases.get(item, item)
            if item == "all":
                item = "any"
            if item in allowed and item not in normalized:
                normalized.append(item)
        return normalized or list(defaults)

    def _normalize_expression_rule_channels(self, value: Any, *, source_kind: str) -> list[str]:
        # 来源与使用范围是两件事。群聊里学到的脱敏表达默认也可以用于
        # 已配置的私聊/主动消息目标，最终仍会经过频道、关系和审核门控。
        defaults = ["private", "group", "proactive"] if source_kind == "group" else ["private", "proactive"]
        return self._normalize_expression_rule_values(
            value,
            allowed={"private", "group", "proactive", "qzone", "tts"},
            aliases={
                "私聊": "private",
                "群聊": "group",
                "主动": "proactive",
                "主动消息": "proactive",
                "空间": "qzone",
                "qq空间": "qzone",
                "说说": "qzone",
                "语音": "tts",
            },
            defaults=defaults,
        )

    def _normalize_expression_relationship_stages(self, value: Any) -> list[str]:
        return self._normalize_expression_rule_values(
            value,
            allowed={"any", "stranger", "familiar", "close"},
            aliases={"不限": "any", "任意": "any", "陌生": "stranger", "熟悉": "familiar", "亲近": "close"},
            defaults=["any"],
        )

    def _normalize_expression_emotion_gates(self, value: Any) -> list[str]:
        return self._normalize_expression_rule_values(
            value,
            allowed={"any", "normal", "positive", "low", "guarded"},
            aliases={
                "不限": "any",
                "任意": "any",
                "普通": "normal",
                "中性": "normal",
                "轻松": "positive",
                "积极": "positive",
                "低落": "low",
                "安抚": "low",
                "防备": "guarded",
                "边界": "guarded",
            },
            defaults=["any"],
        )

    @staticmethod
    def _normalize_expression_intent(value: Any) -> str:
        intent = _single_line(value, 32).lower()
        aliases = {
            "不限": "any",
            "任意": "any",
            "确认": "acknowledgement",
            "提问": "question",
            "请求": "request",
            "求助": "help",
            "安抚": "comfort",
            "玩笑": "play",
            "亲近": "intimacy",
            "边界": "boundary",
            "情绪": "emotion",
            "闲聊": "casual",
            "主动": "proactive",
        }
        intent = aliases.get(intent, intent)
        allowed = {
            "any",
            "acknowledgement",
            "question",
            "request",
            "help",
            "comfort",
            "play",
            "tease",
            "intimacy",
            "boundary",
            "emotion",
            "casual",
            "proactive",
        }
        return intent if intent in allowed else "any"

    @staticmethod
    def _expression_rule_payload_candidates(payload: Any) -> list[dict[str, Any]]:
        """兼容旧 expression_rules，并优先接收 WaifuBot 式的双分类结果。"""
        if not isinstance(payload, dict):
            return []
        result: list[dict[str, Any]] = []
        sections = (
            ("style_expressions", "style"),
            ("grammar_expressions", "grammar"),
            ("expression_rules", ""),
        )
        for key, forced_kind in sections:
            values = payload.get(key)
            if not isinstance(values, list):
                continue
            for raw in values:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                if forced_kind:
                    item["kind"] = forced_kind
                pattern = _single_line(item.get("pattern") or item.get("style"), 120)
                if pattern and not _single_line(item.get("instruction"), 160):
                    if _single_line(item.get("kind"), 16).lower() == "grammar":
                        item["instruction"] = f"在匹配情境中采用“{pattern}”的句法，内容仍按当前事实生成"
                    else:
                        item["instruction"] = f"在匹配情境中自然使用或轻微改写“{pattern}”，不要机械复读"
                if "keywords" not in item and isinstance(item.get("tags"), list):
                    item["keywords"] = list(item.get("tags") or [])
                result.append(item)
                if len(result) >= 12:
                    return result
        return result

    @staticmethod
    def _expression_style_pattern_is_reusable(pattern: str) -> bool:
        value = _single_line(pattern, 100)
        if len(value) < 2 or len(value) > 64:
            return False
        quoted = re.fullmatch(r"[“\"‘'](.{2,48})[”\"’']", value)
        if quoted:
            value = quoted.group(1).strip()
        compact = re.sub(r"[\s，。！？!?、；;：:]", "", value)
        if compact in {
            "短句", "长句", "柔和收尾", "轻松语气", "自然表达", "口语化表达",
            "先确认再补充", "先接住再延续", "简短回应", "语气自然",
        }:
            return False
        has_template_marker = bool(re.search(r"_{2,}|\[[^\]]{1,20}\]|[（(][^）)]{1,20}[）)]|[“\"].{1,30}[”\"]", value))
        meta_description = bool(
            re.search(
                r"(?:偏好|习惯|倾向|通常|经常|多用|常用|口语化|书面化|"
                r"语气|风格|句式|句法|字数|主语|拆句|铺垫|柔和收尾|"
                r"表达内容|表达方式|回应时|回复时|句子结构|长篇大论)",
                value,
            )
        )
        looks_like_instruction = bool(
            re.match(
                r"^(?:先|使用|采用|保持|表达|回复|回应|开头|结尾|收尾|"
                r"语气|句式|句法|短句|长句|直接|简短|自然|柔和)",
                value,
            )
        )
        if meta_description or looks_like_instruction:
            return False
        return has_template_marker or len(value) <= 32

    @staticmethod
    def _expression_grammar_pattern_is_specific(pattern: str) -> bool:
        value = _single_line(pattern, 100)
        if len(value) < 4 or len(value) > 80:
            return False
        if re.search(r"(?:语气自然|自然表达|口语化表达|表达简洁|说话直接)$", value):
            return False
        return bool(
            re.search(
                r"(?:主语|省略|\d+\s*[—–~-]\s*\d+\s*字|\d+\s*字|"
                r"[一二三四五六七八九十]+\s*[—–~-]\s*[一二三四五六七八九十]+\s*字|"
                r"短句|长句|单句|双句|两句|拆句|断句|反问|祈使|问句|"
                r"感叹句|陈述句|倒装|重复|叠词|标点|停顿|句首|句尾|连接词)",
                value,
            )
        )

    def _expression_rule_definition_is_valid(self, raw_rule: Any) -> bool:
        if not isinstance(raw_rule, dict):
            return False
        kind = _single_line(raw_rule.get("kind") or raw_rule.get("type"), 16).lower()
        situation = _single_line(raw_rule.get("situation"), 100)
        pattern = _single_line(raw_rule.get("pattern") or raw_rule.get("style"), 100)
        instruction = _single_line(raw_rule.get("instruction"), 160)
        if kind not in {"style", "grammar"} or not situation or not pattern or not instruction:
            return False
        if kind == "style":
            return self._expression_style_pattern_is_reusable(pattern)
        return self._expression_grammar_pattern_is_specific(pattern)

    def _prune_invalid_expression_rules(self, profile: dict[str, Any]) -> bool:
        if not isinstance(profile, dict):
            return False
        changed = False
        for storage_key in ("pending_rules", "learned_rules"):
            existing = profile.get(storage_key)
            if not isinstance(existing, list):
                continue
            kept = [
                item
                for item in existing
                if (
                    self._expression_rule_definition_is_valid(item)
                    and _safe_int(item.get("evidence_count"), 0, 0) >= 1
                )
            ]
            if kept != existing:
                profile[storage_key] = kept
                changed = True
            if self._assign_expression_rule_families(kept):
                changed = True
        return changed

    @staticmethod
    def _expression_rule_evidence_key(value: Any) -> str:
        text = _single_line(value, 96).lower()
        return re.sub(r"[\s，。！？!?、；;：:‘’“”\"'（）()【】\[\]<>《》~～…—–_-]", "", text)

    @staticmethod
    def _expression_rule_text_similarity(left: Any, right: Any) -> float:
        def grams(value: Any) -> set[str]:
            compact = re.sub(
                r"[\s，。！？!?、；;：:‘’“”\"'（）()【】\[\]<>《》~～…—–_-]",
                "",
                _single_line(value, 160).lower(),
            )
            if not compact:
                return set()
            if len(compact) == 1:
                return {compact}
            return {compact[index:index + 2] for index in range(len(compact) - 1)}

        left_grams = grams(left)
        right_grams = grams(right)
        if not left_grams or not right_grams:
            return 0.0
        return len(left_grams & right_grams) / max(1, len(left_grams | right_grams))

    def _expression_rule_pair_score(self, style: dict[str, Any], grammar: dict[str, Any]) -> float:
        style_family = _single_line(style.get("family_id"), 64)
        grammar_family = _single_line(grammar.get("family_id"), 64)
        if style_family and style_family == grammar_family and not style_family.startswith("xs-"):
            return 1000.0

        style_key = _single_line(style.get("family_key"), 80).lower()
        grammar_key = _single_line(grammar.get("family_key"), 80).lower()
        if style_key and style_key == grammar_key:
            return 900.0

        style_examples = {
            key
            for value in (style.get("evidence_examples") if isinstance(style.get("evidence_examples"), list) else [])
            if (key := self._expression_rule_evidence_key(value))
        }
        grammar_examples = {
            key
            for value in (grammar.get("evidence_examples") if isinstance(grammar.get("evidence_examples"), list) else [])
            if (key := self._expression_rule_evidence_key(value))
        }
        overlap_count = len(style_examples & grammar_examples)
        overlap_ratio = overlap_count / max(1, max(len(style_examples), len(grammar_examples)))
        situation_similarity = self._expression_rule_text_similarity(
            style.get("situation"),
            grammar.get("situation"),
        )
        style_keywords = {
            _single_line(value, 24).lower()
            for value in (style.get("keywords") if isinstance(style.get("keywords"), list) else [])
            if _single_line(value, 24)
        }
        grammar_keywords = {
            _single_line(value, 24).lower()
            for value in (grammar.get("keywords") if isinstance(grammar.get("keywords"), list) else [])
            if _single_line(value, 24)
        }
        keyword_overlap = len(style_keywords & grammar_keywords)
        same_batch = bool(
            _single_line(style.get("last_batch_key"), 80)
            and _single_line(style.get("last_batch_key"), 80)
            == _single_line(grammar.get("last_batch_key"), 80)
        )
        same_evidence_count = _safe_int(style.get("evidence_count"), 0, 0) == _safe_int(
            grammar.get("evidence_count"), 0, 0
        )

        # 旧规则没有 family_key，只在支持片段确实重叠时自动配对；
        # 同批次、情境高度相近只作为辅助，避免把同一批中的不同规则强行绑在一起。
        if overlap_ratio >= 0.45:
            return 500.0 + overlap_ratio * 100 + situation_similarity * 20 + min(2, keyword_overlap) * 5
        if overlap_count and same_batch and situation_similarity >= 0.3:
            return 430.0 + situation_similarity * 40 + min(2, keyword_overlap) * 5
        if same_batch and same_evidence_count and situation_similarity >= 0.72 and keyword_overlap:
            return 320.0 + situation_similarity * 40 + min(2, keyword_overlap) * 5
        return -1.0

    def _assign_expression_rule_families(
        self,
        rules: Any,
        *,
        batch_key: str = "",
    ) -> bool:
        if not isinstance(rules, list):
            return False
        valid_rules = [item for item in rules if isinstance(item, dict)]
        if not valid_rules:
            return False
        for item in valid_rules:
            if batch_key and not _single_line(item.get("last_batch_key"), 80):
                item["last_batch_key"] = _single_line(batch_key, 80)
            family_key = _single_line(item.get("family_key"), 80).lower()
            if family_key:
                item["family_key"] = family_key

        styles = [item for item in valid_rules if _single_line(item.get("kind"), 16).lower() == "style"]
        grammars = [item for item in valid_rules if _single_line(item.get("kind"), 16).lower() == "grammar"]
        pair_candidates: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
        for style in styles:
            for grammar in grammars:
                score = self._expression_rule_pair_score(style, grammar)
                if score >= 0:
                    pair_candidates.append((score, style, grammar))
        pair_candidates.sort(key=lambda item: item[0], reverse=True)

        paired_ids: set[int] = set()
        family_by_object: dict[int, str] = {}
        for _, style, grammar in pair_candidates:
            if id(style) in paired_ids or id(grammar) in paired_ids:
                continue
            rule_ids = sorted([
                value
                for value in (
                    _single_line(style.get("id"), 100) or self._expression_rule_signature(style),
                    _single_line(grammar.get("id"), 100) or self._expression_rule_signature(grammar),
                )
                if value
            ])
            family_seed = "|".join(rule_ids)
            family_id = f"xf-{hashlib.sha1(family_seed.encode('utf-8')).hexdigest()[:16]}"
            family_by_object[id(style)] = family_id
            family_by_object[id(grammar)] = family_id
            paired_ids.update({id(style), id(grammar)})

        changed = False
        for item in valid_rules:
            family_id = family_by_object.get(id(item))
            if not family_id:
                rule_id = _single_line(item.get("id"), 100) or self._expression_rule_signature(item)
                family_id = f"xs-{hashlib.sha1(rule_id.encode('utf-8')).hexdigest()[:16]}"
            if _single_line(item.get("family_id"), 64) != family_id:
                item["family_id"] = family_id
                changed = True
        return changed

    def _backfill_expression_rule_families(self, profile: Any) -> bool:
        if not isinstance(profile, dict):
            return False
        changed = False
        for storage_key in ("pending_rules", "learned_rules"):
            rules = profile.get(storage_key)
            if isinstance(rules, list) and self._assign_expression_rule_families(rules):
                changed = True
        return changed

    def _expression_rule_groups(self, rules: Any) -> list[list[dict[str, Any]]]:
        if not isinstance(rules, list):
            return []
        self._assign_expression_rule_families(rules)
        groups: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for item in rules:
            if not isinstance(item, dict):
                continue
            family_id = _single_line(item.get("family_id"), 64)
            if not family_id:
                continue
            if family_id not in groups:
                groups[family_id] = []
                order.append(family_id)
            groups[family_id].append(item)
        return [groups[family_id] for family_id in order]

    def _expression_rule_runtime_bundle(self, group: Any) -> dict[str, Any]:
        items = [dict(item) for item in group if isinstance(item, dict)] if isinstance(group, list) else []
        if not items:
            return {}
        items.sort(key=lambda item: 0 if _single_line(item.get("kind"), 16).lower() == "style" else 1)
        style_rule = next((item for item in items if _single_line(item.get("kind"), 16).lower() == "style"), None)
        grammar_rule = next((item for item in items if _single_line(item.get("kind"), 16).lower() == "grammar"), None)
        primary = style_rule or grammar_rule or items[0]
        family_id = _single_line(primary.get("family_id"), 64)
        bundle = dict(primary)
        bundle["id"] = family_id if len(items) > 1 else _single_line(primary.get("id"), 100)
        bundle["family_id"] = family_id
        bundle["kind"] = "combined" if style_rule and grammar_rule else _single_line(primary.get("kind"), 16).lower()
        bundle["component_kinds"] = [
            kind for kind in ("style", "grammar")
            if any(_single_line(item.get("kind"), 16).lower() == kind for item in items)
        ]
        bundle["component_count"] = len(items)
        bundle["component_rules"] = items
        bundle["style_rule"] = dict(style_rule) if style_rule else None
        bundle["grammar_rule"] = dict(grammar_rule) if grammar_rule else None
        bundle["evidence_count"] = max(_safe_int(item.get("evidence_count"), 0, 0) for item in items)
        bundle["positive_feedback"] = max(_safe_int(item.get("positive_feedback"), 0, 0) for item in items)
        bundle["negative_feedback"] = max(_safe_int(item.get("negative_feedback"), 0, 0) for item in items)
        bundle["use_count"] = max(_safe_int(item.get("use_count"), 0, 0) for item in items)
        bundle["last_seen_ts"] = max(_safe_float(item.get("last_seen_ts"), 0.0) for item in items)
        bundle["last_used_ts"] = max(_safe_float(item.get("last_used_ts"), 0.0) for item in items)
        for field, limit in (("keywords", 8), ("tags", 8), ("signals", 8), ("channels", 8), ("relationship_stages", 8), ("emotion_gates", 8)):
            values: list[str] = []
            for item in items:
                for value in item.get(field, []) if isinstance(item.get(field), list) else []:
                    normalized = _single_line(value, 32)
                    if normalized and normalized not in values:
                        values.append(normalized)
            bundle[field] = values[:limit]
        examples: list[str] = []
        refs: list[dict[str, str]] = []
        ref_keys: set[tuple[str, str, str]] = set()
        for item in items:
            for example in item.get("evidence_examples", []) if isinstance(item.get("evidence_examples"), list) else []:
                value = _single_line(example, 80)
                if value and value not in examples:
                    examples.append(value)
            for raw_ref in item.get("source_refs", []) if isinstance(item.get("source_refs"), list) else []:
                if not isinstance(raw_ref, dict):
                    continue
                ref = {
                    "source_kind": _single_line(raw_ref.get("source_kind"), 16).lower(),
                    "source_id": _single_line(raw_ref.get("source_id"), 80),
                    "rule_id": _single_line(raw_ref.get("rule_id"), 40),
                }
                key = (ref["source_kind"], ref["source_id"], ref["rule_id"])
                if all(key) and key not in ref_keys:
                    ref_keys.add(key)
                    refs.append(ref)
        bundle["evidence_examples"] = examples[:6]
        bundle["source_refs"] = refs
        avoid_values = [
            _single_line(item.get("avoid"), 160)
            for item in items
            if _single_line(item.get("avoid"), 160)
        ]
        bundle["avoid"] = "；".join(dict.fromkeys(avoid_values))[:240]
        bundle["persona_conflict"] = any(self._expression_rule_bool(item.get("persona_conflict")) for item in items)
        return bundle

    @staticmethod
    def _normalize_expression_evidence_examples(
        value: Any,
        *,
        source_kind: str,
        source_names: set[str],
    ) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for raw in value:
            example = _single_line(raw, 72)
            example = re.sub(r"^[^:：]{1,32}[:：]\s*", "", example).strip()
            if not example or re.search(r"https?://|@|\b\d{5,}\b|QQ|群号|用户ID", example, re.IGNORECASE):
                continue
            if source_kind == "group" and any(name and name in example for name in source_names):
                continue
            if example not in result:
                result.append(example)
            if len(result) >= 3:
                break
        return result

    def _normalize_expression_rule_candidates(
        self,
        raw_rules: Any,
        *,
        source_kind: str,
        source_text: str = "",
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_rules, list):
            return []
        source_utterances, source_names = self._expression_rule_source_parts(
            source_text,
            source_kind=source_kind,
        )
        compact_utterances = {
            re.sub(r"\s+", "", line).lower()
            for line in source_utterances
            if line.strip()
        }
        result: list[dict[str, Any]] = []
        for raw in raw_rules[:12]:
            if not isinstance(raw, dict):
                continue
            kind = _single_line(raw.get("kind") or raw.get("type"), 16).lower()
            if kind not in {"style", "grammar"}:
                continue
            situation = _single_line(raw.get("situation"), 80)
            pattern = _single_line(raw.get("pattern") or raw.get("style"), 100)
            instruction = _single_line(raw.get("instruction"), 140)
            avoid = _single_line(raw.get("avoid"), 160)
            evidence_examples = self._normalize_expression_evidence_examples(
                raw.get("evidence_examples") or raw.get("examples"),
                source_kind=source_kind,
                source_names=source_names,
            )
            evidence = _safe_int(raw.get("evidence_count"), len(evidence_examples) or 1, 1, 20)
            if not situation or not pattern or not instruction:
                continue
            if kind == "style" and not self._expression_style_pattern_is_reusable(pattern):
                continue
            if kind == "grammar" and not self._expression_grammar_pattern_is_specific(pattern):
                continue
            if source_utterances:
                evidence = min(evidence, len(source_utterances))
            if evidence < 1:
                continue
            combined_rule = f"{situation} {pattern} {instruction}"
            if any(marker in combined_rule for marker in ("SELF", "系统提示", "提示词", "用户ID", "群号", "QQ号")):
                continue
            if re.search(r"@|\b\d{5,}\b|QQ|昵称为|ID为", combined_rule, re.IGNORECASE):
                continue
            if source_kind == "group":
                if any(name and name in combined_rule for name in source_names):
                    continue
                # 允许保留短而有辨识度的表达或占位模板，这是 WaifuBot 式学习的核心；
                # 仍拒绝长句照搬、成员身份和账号等不可迁移内容。
                compact_pattern = re.sub(r"\s+", "", pattern).lower()
                if len(pattern) > 48 and any(len(line) >= 16 and line in compact_pattern for line in compact_utterances):
                    continue
                quoted = re.findall(r"[“\"‘']([^”\"’']{2,40})[”\"’']", combined_rule)
                if any(
                    len(quote) > 24 and re.sub(r"\s+", "", quote).lower() in line
                    for quote in quoted
                    for line in compact_utterances
                ):
                    continue
            raw_keywords = raw.get("keywords") if isinstance(raw.get("keywords"), list) else raw.get("tags")
            keywords = []
            if isinstance(raw_keywords, list):
                for keyword in raw_keywords:
                    value = _single_line(keyword, 24)
                    if source_kind == "group" and (
                        value in source_names
                        or re.search(r"\d{5,}", value)
                        or re.sub(r"\s+", "", value).lower() in compact_utterances
                    ):
                        continue
                    if len(value) >= 2 and value not in keywords:
                        keywords.append(value)
                    if len(keywords) >= 8:
                        break
            item = {
                "kind": kind,
                "situation": situation,
                "pattern": pattern,
                "instruction": instruction,
                "keywords": keywords,
                "tags": list(keywords),
                "evidence_examples": evidence_examples,
                "evidence_count": evidence,
                "source_kind": source_kind,
                "family_key": _single_line(raw.get("family_key"), 80).lower(),
                "channels": self._normalize_expression_rule_channels(raw.get("channels"), source_kind=source_kind),
                "relationship_stages": self._normalize_expression_relationship_stages(raw.get("relationship_stages")),
                "emotion_gates": self._normalize_expression_emotion_gates(raw.get("emotion_gates")),
                "intent": self._normalize_expression_intent(raw.get("intent")),
                "avoid": avoid or "事实、工具结果、安全边界或人格发生冲突时不用",
                "persona_conflict": self._expression_rule_bool(raw.get("persona_conflict")) or bool(
                    re.search(
                        r"(?:假装|谎称|声称).{0,12}(?:成功|完成|已发|发过)|"
                        r"(?:无视|覆盖|改写).{0,8}(?:人格|安全|事实|工具结果)|"
                        r"(?:必须|永远|无条件).{0,10}(?:服从|同意|答应)",
                        combined_rule,
                        re.IGNORECASE,
                    )
                ),
                "positive_feedback": 0,
                "negative_feedback": 0,
                "use_count": 0,
            }
            item["id"] = self._expression_rule_signature(item)
            duplicate = next((old for old in result if old.get("id") == item["id"]), None)
            if duplicate is not None:
                duplicate["evidence_count"] = max(
                    _safe_int(duplicate.get("evidence_count"), 0, 0),
                    evidence,
                )
                continue
            result.append(item)
        return result[:6]

    def _merge_learned_expression_rules(
        self,
        profile: dict[str, Any],
        candidates: list[dict[str, Any]],
        *,
        batch_key: str,
        now: float,
        pending: bool = False,
    ) -> bool:
        if not isinstance(profile, dict) or not candidates:
            return False
        candidates = [item for item in candidates if self._expression_rule_definition_is_valid(item)]
        if not candidates:
            return False
        self._assign_expression_rule_families(candidates, batch_key=batch_key)
        storage_key = "pending_rules" if pending else "learned_rules"
        existing = [dict(item) for item in profile.get(storage_key, []) if isinstance(item, dict)]
        families_changed = self._assign_expression_rule_families(existing)
        by_id = {_single_line(item.get("id"), 40): item for item in existing if _single_line(item.get("id"), 40)}

        def semantic_key(item: dict[str, Any]) -> str:
            kind = _single_line(item.get("kind"), 16).lower()
            situation = re.sub(
                r"[\s，。！？!?、；;：:‘’“”\"']",
                "",
                _single_line(item.get("situation"), 80).lower(),
            )
            pattern = re.sub(
                r"[\s，。！？!?、；;：:‘’“”\"']",
                "",
                _single_line(item.get("pattern") or item.get("style"), 100).lower(),
            )
            return f"{kind}|{situation}|{pattern}"

        by_semantic_key = {
            semantic_key(item): item
            for item in existing
            if semantic_key(item) != "||"
        }
        changed = families_changed
        for candidate in candidates:
            rule_id = _single_line(candidate.get("id"), 40)
            old = by_id.get(rule_id) or by_semantic_key.get(semantic_key(candidate))
            if old is None:
                old = dict(candidate)
                if pending:
                    old["review_status"] = "pending"
                old["created_ts"] = now
                old["last_seen_ts"] = now
                old["last_batch_key"] = batch_key
                existing.append(old)
                by_id[rule_id] = old
                by_semantic_key[semantic_key(old)] = old
                changed = True
                continue
            old["last_seen_ts"] = now
            incoming_family_key = _single_line(candidate.get("family_key"), 80).lower()
            if incoming_family_key and _single_line(old.get("family_key"), 80).lower() != incoming_family_key:
                old["family_key"] = incoming_family_key
            old["keywords"] = list(dict.fromkeys([
                *[str(item) for item in old.get("keywords", []) if str(item).strip()],
                *[str(item) for item in candidate.get("keywords", []) if str(item).strip()],
            ]))[:8]
            old["tags"] = list(old["keywords"])
            old["evidence_examples"] = list(dict.fromkeys([
                *[str(item) for item in old.get("evidence_examples", []) if str(item).strip()],
                *[str(item) for item in candidate.get("evidence_examples", []) if str(item).strip()],
            ]))[:3]
            for field in ("channels", "relationship_stages", "emotion_gates"):
                old_values = old.get(field) if isinstance(old.get(field), list) else []
                candidate_values = candidate.get(field) if isinstance(candidate.get(field), list) else []
                old[field] = list(dict.fromkeys([
                    *[_single_line(item, 24).lower() for item in old_values if _single_line(item, 24)],
                    *[_single_line(item, 24).lower() for item in candidate_values if _single_line(item, 24)],
                ]))[:8]
            old_intent = self._normalize_expression_intent(old.get("intent"))
            candidate_intent = self._normalize_expression_intent(candidate.get("intent"))
            old["intent"] = old_intent if old_intent == candidate_intent else "any"
            candidate_avoid = _single_line(candidate.get("avoid"), 160)
            if candidate_avoid and len(candidate_avoid) > len(_single_line(old.get("avoid"), 160)):
                old["avoid"] = candidate_avoid
            old["persona_conflict"] = bool(
                self._expression_rule_bool(old.get("persona_conflict"))
                or self._expression_rule_bool(candidate.get("persona_conflict"))
            )
            old["positive_feedback"] = _safe_int(old.get("positive_feedback"), 0, 0)
            old["negative_feedback"] = _safe_int(old.get("negative_feedback"), 0, 0)
            old["use_count"] = _safe_int(old.get("use_count"), 0, 0)
            if _single_line(old.get("last_batch_key"), 40) != batch_key:
                old["evidence_count"] = min(
                    99,
                    _safe_int(old.get("evidence_count"), 0, 0) + _safe_int(candidate.get("evidence_count"), 0, 0),
                )
                old["last_batch_key"] = batch_key
            else:
                old["evidence_count"] = max(
                    _safe_int(old.get("evidence_count"), 0, 0),
                    _safe_int(candidate.get("evidence_count"), 0, 0),
                )
            changed = True
        if self._assign_expression_rule_families(existing, batch_key=batch_key):
            changed = True
        existing.sort(
            key=lambda item: (
                -_safe_int(item.get("evidence_count"), 0, 0),
                -_safe_float(item.get("last_seen_ts"), 0.0),
            )
        )
        profile[storage_key] = existing[: self.max_learned_expression_items]
        return changed

    def _select_learned_expression_rules(
        self,
        rules: Any,
        *,
        hint: str = "",
        limit: int = 2,
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(rules, list):
            return []
        self._assign_expression_rule_families(rules)
        query = _single_line(hint, 300).lower()
        context = context if isinstance(context, dict) else {}
        channel = _single_line(context.get("channel"), 24).lower()
        relationship_stage = _single_line(context.get("relationship_stage"), 24).lower()
        emotion_gate = _single_line(context.get("emotion_gate"), 24).lower()
        current_intent = self._normalize_expression_intent(context.get("intent"))
        now = _now_ts()
        ranked: list[tuple[float, dict[str, Any]]] = []
        for raw in rules:
            if not isinstance(raw, dict) or _safe_int(raw.get("evidence_count"), 0, 0) < 1:
                continue
            if not self._expression_rule_definition_is_valid(raw):
                continue
            review_status = _single_line(raw.get("review_status"), 24).lower()
            if review_status in {"pending", "needs_review", "rejected"}:
                continue
            if self._expression_rule_bool(raw.get("persona_conflict")):
                continue
            negative_feedback = _safe_int(raw.get("negative_feedback"), 0, 0)
            positive_feedback = _safe_int(raw.get("positive_feedback"), 0, 0)
            if negative_feedback >= 2 and negative_feedback > positive_feedback:
                continue

            context_score = 0
            channels = raw.get("channels") if isinstance(raw.get("channels"), list) else []
            normalized_channels = {_single_line(item, 24).lower() for item in channels if _single_line(item, 24)}
            if normalized_channels and channel and channel not in normalized_channels:
                continue
            if normalized_channels and channel in normalized_channels:
                context_score += 4

            relationship_stages = raw.get("relationship_stages") if isinstance(raw.get("relationship_stages"), list) else []
            normalized_relationships = {
                _single_line(item, 24).lower() for item in relationship_stages if _single_line(item, 24)
            }
            if normalized_relationships and "any" not in normalized_relationships:
                if relationship_stage and relationship_stage not in normalized_relationships:
                    continue
                if relationship_stage in normalized_relationships:
                    context_score += 3

            emotion_gates = raw.get("emotion_gates") if isinstance(raw.get("emotion_gates"), list) else []
            normalized_emotions = {_single_line(item, 24).lower() for item in emotion_gates if _single_line(item, 24)}
            if normalized_emotions and "any" not in normalized_emotions:
                if emotion_gate and emotion_gate not in normalized_emotions:
                    continue
                if emotion_gate in normalized_emotions:
                    context_score += 3

            rule_intent = self._normalize_expression_intent(raw.get("intent"))
            intent_equivalents = {
                "help": {"help", "request", "question"},
                "comfort": {"comfort", "emotion"},
                "play": {"play", "tease"},
                "tease": {"play", "tease"},
                "intimacy": {"intimacy", "emotion"},
                "boundary": {"boundary"},
                "acknowledgement": {"acknowledgement", "casual"},
                "question": {"question", "help"},
                "request": {"request", "help"},
                "emotion": {"emotion", "comfort", "intimacy"},
                "casual": {"casual", "acknowledgement"},
                "proactive": {"proactive", "casual"},
            }
            if rule_intent != "any" and current_intent != "any":
                if rule_intent not in intent_equivalents.get(current_intent, {current_intent}):
                    continue
                context_score += 5
            keywords = [
                _single_line(item, 24).lower()
                for item in raw.get("keywords", [])
                if _single_line(item, 24)
            ] if isinstance(raw.get("keywords"), list) else []
            matched = sum(1 for keyword in keywords if query and keyword in query)
            if query and keywords and matched <= 0 and context_score <= 0:
                continue
            last_seen_ts = _safe_float(raw.get("last_seen_ts"), 0.0)
            age_days = max(0.0, (now - last_seen_ts) / 86400) if last_seen_ts > 0 else 0.0
            freshness = max(0.01, 1.0 - age_days / 30.0)
            feedback_score = min(8, positive_feedback * 1.5) - min(16, negative_feedback * 4)
            score = (
                matched * 10
                + context_score
                + min(9, _safe_int(raw.get("evidence_count"), 0, 0)) * freshness
                + feedback_score
            )
            ranked.append((score, raw))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        grouped_ranked: dict[str, dict[str, Any]] = {}
        group_order: list[str] = []
        for score, item in ranked:
            family_id = _single_line(item.get("family_id"), 64)
            if not family_id:
                family_id = f"xs-{hashlib.sha1(_single_line(item.get('id'), 100).encode('utf-8')).hexdigest()[:16]}"
            if family_id not in grouped_ranked:
                grouped_ranked[family_id] = {"score": score, "items": []}
                group_order.append(family_id)
            grouped_ranked[family_id]["score"] = max(_safe_float(grouped_ranked[family_id].get("score"), score), score)
            grouped_ranked[family_id]["items"].append(item)

        ranked_groups: list[tuple[float, dict[str, Any]]] = []
        for family_id in group_order:
            entry = grouped_ranked[family_id]
            bundle = self._expression_rule_runtime_bundle(entry.get("items"))
            if not bundle:
                continue
            complement_bonus = min(1.5, max(0, _safe_int(bundle.get("component_count"), 1, 1) - 1) * 0.75)
            ranked_groups.append((_safe_float(entry.get("score"), 0.0) + complement_bonus, bundle))
        ranked_groups.sort(key=lambda pair: pair[0], reverse=True)

        limit = max(1, limit)
        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        seen_kinds: set[str] = set()
        # 同源的表达与语法作为一个规则组占一个名额；独立规则仍优先覆盖两种能力。
        for _, bundle in ranked_groups:
            component_kinds = {
                _single_line(item, 16).lower()
                for item in bundle.get("component_kinds", [])
                if _single_line(item, 16).lower() in {"style", "grammar"}
            }
            if component_kinds and component_kinds.issubset(seen_kinds):
                continue
            selected.append(bundle)
            selected_ids.add(_single_line(bundle.get("family_id"), 64) or _single_line(bundle.get("id"), 100))
            seen_kinds.update(component_kinds)
            if len(selected) >= limit:
                return selected
        for _, bundle in ranked_groups:
            bundle_id = _single_line(bundle.get("family_id"), 64) or _single_line(bundle.get("id"), 100)
            if bundle_id in selected_ids:
                continue
            selected.append(bundle)
            if len(selected) >= limit:
                break
        return selected

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
            "scene": self._expression_scene_from_text(cleaned),
            "features": self._expression_style_features_from_text(cleaned),
        }

    @staticmethod
    def _expression_scene_label(scene: Any) -> str:
        labels = {
            "acknowledgement": "短确认",
            "question": "提问/追问",
            "request": "提出请求",
            "tease": "玩笑/打趣",
            "emotion": "情绪表达",
            "casual": "普通闲聊",
        }
        return labels.get(_single_line(scene, 32), "普通闲聊")

    def _expression_scene_from_text(self, text: Any) -> str:
        cleaned = _single_line(text, 180)
        if not cleaned:
            return "casual"
        stripped = cleaned.rstrip("。！？!?~～… ").lower()
        if re.search(r"(?:帮我|给我|麻烦|能不能|可不可以|要不|请你|记得|别忘|提醒我|帮忙)", cleaned):
            return "request"
        if re.search(r"(?:难过|委屈|烦|好累|累死|想哭|哭了|生气|不开心|emo|破防|崩溃|害怕|焦虑)", cleaned, re.I):
            return "emotion"
        if re.search(r"(?:笨蛋|坏蛋|哼|才不要|你又|真是你|可恶)", cleaned):
            return "tease"
        if "？" in cleaned or "?" in cleaned or re.search(r"(?:怎么|为什么|啥|什么|是不是|对吗|行吗|好不好)$", stripped):
            return "question"
        if len(stripped) <= 28 and re.search(r"^(?:嗯|好|行|可以|知道|收到|对|没事|好吧|好呀|行吧|确实|原来|懂了|哦|啊|诶)", stripped):
            return "acknowledgement"
        return "casual"

    @staticmethod
    def _expression_style_features_from_text(text: Any) -> list[str]:
        cleaned = _single_line(text, 180)
        if not cleaned:
            return []
        stripped = cleaned.rstrip("。！？!?~～… ")
        features: list[str] = []
        if len(cleaned) <= 18:
            features.append("short")
        if re.match(r"^(?:嗯|啊|诶|欸|唔|哎|哈哈|嘿嘿|哼|唉)", stripped):
            features.append("casual_opener")
        lowered = cleaned.lower()
        if any(marker in lowered for marker in ("哈哈", "嘿嘿", "hh", "www")):
            features.append("laugh_marker")
        if not any(marker in lowered for marker in ("哈哈", "嘿嘿")) and re.search(r"([\u4e00-\u9fff])\1", stripped):
            features.append("reduplication")
        if "~" in cleaned or "～" in cleaned:
            features.append("soft_wave")
        if any(marker in lowered for marker in ("哈哈", "嘿嘿", "hh", "www", "~", "～", "捏", "哼")):
            features.append("playful")
        if stripped.endswith(("吧", "呀", "啦", "嘛", "呢", "哦", "诶")):
            features.append("soft_ending")
        if "…" in cleaned or "..." in cleaned:
            features.append("pause")
        if "？" in cleaned or "?" in cleaned:
            features.append("question")
        return features

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
        if re.search(r"^\s*(?:我叫|我是|叫我)[^。！？!?\n]{1,40}", cleaned):
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
        if re.search(r"(你是|我是|他是|她是|叫我|叫你|名字|主人|主要用户|次要用户|朋友|同学|老师|室友|父母|妈妈|爸爸|哥哥|姐姐|弟弟|妹妹)", text):
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
        profile["pattern_count"] = len(samples)
        profile["sample_count"] = sum(
            _safe_int(item.get("evidence_count"), 1, 1)
            for item in samples
            if isinstance(item, dict)
        )
        profile["short_count"] = sum(
            _safe_int(item.get("evidence_count"), 1, 1)
            for item in samples
            if isinstance(item, dict) and _safe_int(item.get("length"), 0, 0) <= 18
        )
        punctuation: dict[str, int] = {}
        endings: list[str] = []
        phrases: list[str] = []
        scene_stats: dict[str, dict[str, Any]] = {}
        fingerprint_features: dict[str, int] = {}
        for item in samples:
            if not isinstance(item, dict):
                continue
            evidence = _safe_int(item.get("evidence_count"), 1, 1)
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
            sample_text = _single_line(item.get("text") or phrase, 180)
            scene = _single_line(item.get("scene"), 32)
            if scene not in {"acknowledgement", "question", "request", "tease", "emotion", "casual"}:
                scene = self._expression_scene_from_text(sample_text)
                item["scene"] = scene
            raw_features = item.get("features")
            features = [
                _single_line(feature, 32)
                for feature in raw_features
                if _single_line(feature, 32)
            ] if isinstance(raw_features, list) else self._expression_style_features_from_text(sample_text)
            item["features"] = list(dict.fromkeys(features))
            bucket = scene_stats.setdefault(
                scene,
                {"count": 0, "short_count": 0, "feature_counts": {}, "latest_ts": 0.0},
            )
            bucket["count"] += evidence
            if _safe_int(item.get("length"), len(sample_text), 0) <= 18:
                bucket["short_count"] += evidence
            bucket["latest_ts"] = max(_safe_float(bucket.get("latest_ts"), 0.0), _safe_float(item.get("ts"), 0.0))
            feature_counts = bucket["feature_counts"]
            for feature in item["features"]:
                feature_counts[feature] = _safe_int(feature_counts.get(feature), 0, 0) + evidence
                fingerprint_features[feature] = _safe_int(fingerprint_features.get(feature), 0, 0) + evidence
        profile["punctuation"] = punctuation
        profile["endings"] = endings[: self.max_learned_expression_items]
        profile["recent_phrases"] = phrases[: self.max_learned_expression_items]
        profile["scene_profiles"] = {
            scene: {
                "count": _safe_int(bucket.get("count"), 0, 0),
                "short_ratio": round(
                    _safe_int(bucket.get("short_count"), 0, 0) / max(1, _safe_int(bucket.get("count"), 0, 0)),
                    2,
                ),
                "feature_counts": dict(bucket.get("feature_counts") or {}),
                "latest_ts": _safe_float(bucket.get("latest_ts"), 0.0),
            }
            for scene, bucket in scene_stats.items()
            if _safe_int(bucket.get("count"), 0, 0) > 0
        }
        profile["style_fingerprint"] = {
            "short_ratio": round(profile["short_count"] / max(1, profile["sample_count"]), 2),
            "feature_counts": fingerprint_features,
        }
        profile["expression_rules"] = self._expression_rules_from_scene_profiles(profile["scene_profiles"])

    def _expression_rule_details_for_scene(
        self,
        scene: Any,
        scene_profile: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_scene = _single_line(scene, 32)
        count = _safe_int(scene_profile.get("count"), 0, 0)
        if normalized_scene not in {"acknowledgement", "question", "request", "tease", "emotion", "casual"} or count < 2:
            return {}
        base_rules = {
            "acknowledgement": "先用简短口语确认接住，不把一个短确认扩写成长说明",
            "question": "先直接回应核心，再自然接下去，不绕成客服式解释",
            "request": "先给明确答复或行动，再补必要说明",
            "tease": "保持轻松有来有回，不突然说教或端着",
            "emotion": "先接住情绪，短一点、慢一点，不急着讲道理",
            "casual": "从眼前话头直接接，不套客气开场",
        }
        short_ratio = _safe_float(scene_profile.get("short_ratio"), 0.0)
        raw_feature_counts = scene_profile.get("feature_counts")
        feature_counts = raw_feature_counts if isinstance(raw_feature_counts, dict) else {}
        feature_threshold = 2
        actions = [base_rules[normalized_scene]]
        signals: list[str] = []

        if short_ratio >= 0.6:
            actions.append("长度控制在一两句，保留即时聊天感")
            signals.append("short")
        elif short_ratio <= 0.2 and normalized_scene in {"emotion", "casual"}:
            actions.append("可以完整一点，但不要写成说明书")
        if _safe_int(feature_counts.get("casual_opener"), 0, 0) >= feature_threshold:
            actions.append("开头可自然地随口起一句，避开客服式开场")
            signals.append("casual_opener")
        if _safe_int(feature_counts.get("laugh_marker"), 0, 0) >= feature_threshold:
            actions.append("轻松时可放一个笑声式口语标记，不要连续堆叠")
            signals.append("laugh_marker")
        elif _safe_int(feature_counts.get("soft_wave"), 0, 0) >= feature_threshold:
            actions.append("轻松时可用一个轻微波浪号收束，不要每句都加")
            signals.append("soft_wave")
        elif _safe_int(feature_counts.get("playful"), 0, 0) >= feature_threshold:
            actions.append("保留一点轻松口语感，但不要硬塞口癖")
            signals.append("playful")
        if _safe_int(feature_counts.get("soft_ending"), 0, 0) >= feature_threshold:
            actions.append("收尾可以放轻一点，不必强行加语气词")
            signals.append("soft_ending")
        if _safe_int(feature_counts.get("reduplication"), 0, 0) >= feature_threshold:
            actions.append("亲近轻松的话题里可偶尔用一个自然叠词，不要生造")
            signals.append("reduplication")
        if _safe_int(feature_counts.get("pause"), 0, 0) >= feature_threshold:
            actions.append("允许留一点停顿感，最多一个省略号")
            signals.append("pause")

        signals = list(dict.fromkeys(signals))
        rule_id = f"{normalized_scene}:{'.'.join(signals[:3]) or 'scene'}"
        return {
            "id": rule_id,
            "scene": normalized_scene,
            "label": self._expression_scene_label(normalized_scene),
            "evidence_count": count,
            "confidence": min(0.96, round(0.45 + min(count, 8) * 0.06, 2)),
            "actions": actions[:4],
            "signals": signals[:4],
            "instruction": "；".join(actions[:4]) + "。",
        }

    def _expression_rules_from_scene_profiles(self, scene_profiles: Any) -> list[dict[str, Any]]:
        if not isinstance(scene_profiles, dict):
            return []
        rules: list[dict[str, Any]] = []
        for scene, raw_profile in scene_profiles.items():
            if not isinstance(raw_profile, dict):
                continue
            details = self._expression_rule_details_for_scene(scene, raw_profile)
            if details:
                rules.append(details)
        rules.sort(key=lambda item: (-_safe_int(item.get("evidence_count"), 0, 0), _single_line(item.get("scene"), 32)))
        return rules[:6]

    def _expression_rule_details_for_inbound(self, profile: dict[str, Any], inbound_text: Any) -> dict[str, Any]:
        scene = self._expression_scene_from_text(inbound_text)
        raw_profiles = profile.get("scene_profiles") if isinstance(profile, dict) else {}
        scene_profiles = raw_profiles if isinstance(raw_profiles, dict) else {}
        scene_profile = scene_profiles.get(scene) if isinstance(scene_profiles.get(scene), dict) else {}
        return self._expression_rule_details_for_scene(scene, scene_profile)

    def _expression_scene_rule_for_inbound(self, profile: dict[str, Any], inbound_text: Any) -> str:
        if self._expression_learning_mode() == "light":
            return ""
        details = self._expression_rule_details_for_inbound(profile, inbound_text)
        if not details:
            return ""
        return (
            f"当前场景「{details['label']}」已有 {details['evidence_count']} 条表达证据："
            f"{details['instruction']}"
        )

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
        birthday_asked_at = _safe_float(user.get("birthday_curiosity_asked_at"), 0)
        asked_recently = birthday_asked_at > 0 and _now_ts() - birthday_asked_at <= 14 * 24 * 3600
        if asked_recently and re.search(r"(?:不想|不愿|不方便|先不|暂时不|别).{0,10}(?:说|讲|提|问)?.{0,6}生日|生日.{0,12}(?:不想|不愿|不方便|别|不要)", cleaned):
            user["birthday_curiosity_opt_out"] = True
            user["birthday_curiosity_asked_at"] = 0
        else:
            birthday_match = re.search(r"(?:(农历|公历)\s*)?(\d{1,2})\s*(?:月|[-./])\s*(\d{1,2})\s*(?:日|号)?", cleaned)
            explicit_birthday = bool(re.search(r"(?:我|我的|本人).{0,6}生日(?:.{0,10}(?:是|在|：|:))?", cleaned))
            if birthday_match and (asked_recently or explicit_birthday):
                user["birthday_profile"] = {
                    "calendar": "lunar" if birthday_match.group(1) == "农历" else "solar",
                    "month": int(birthday_match.group(2)),
                    "day": int(birthday_match.group(3)),
                    "raw": birthday_match.group(0),
                    "source": "birthday_curiosity_reply" if asked_recently else "user_explicit",
                    "confirmed_at": _now_ts(),
                }
                if asked_recently:
                    user["birthday_curiosity_answered_at"] = _now_ts()
                    user["birthday_curiosity_asked_at"] = 0
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

    def _format_expression_profile_for_prompt(
        self,
        user: dict[str, Any],
        *,
        inbound_text: str = "",
        include_semantic: bool = True,
    ) -> str:
        profile = user.get("expression_profile")
        if not isinstance(profile, dict):
            return "暂无已审核表达规则。保持 AstrBot 默认人格的自然表达。"
        learned_rules = profile.get("learned_rules") if isinstance(profile.get("learned_rules"), list) else []
        if not include_semantic or not learned_rules:
            return "暂无已审核表达规则。保持 AstrBot 默认人格的自然表达。"
        semantic_matches = self._select_learned_expression_rules(
            learned_rules,
            hint=inbound_text,
            limit=2,
        )
        if not semantic_matches:
            return "暂无匹配的已审核表达规则。保持 AstrBot 默认人格的自然表达。"
        lines: list[str] = []
        for rule in semantic_matches:
            line = self._format_expression_rule_bundle_line(rule)
            if line:
                lines.append(line)
        if lines:
            lines.append(
                "只使用已经审核通过的规则；观察素材、支持片段、句长和标点统计不得直接影响回复。"
                "工具与事实、安全边界、AstrBot 人格、当前关系和情绪始终优先。"
                "句尾括号或颜文字后缀必须与所属句保持同一行；规则要求括号前无标点时，不得补逗号或其他标点。"
            )
        return "\n".join(lines) if lines else "暂无匹配的已审核表达规则。保持 AstrBot 默认人格的自然表达。"

    def _expression_visible_signals_in_reply(
        self,
        response_text: Any,
        rule_details: dict[str, Any],
    ) -> list[str]:
        cleaned = _single_line(_strip_internal_message_blocks(response_text), 500)
        if not cleaned or not isinstance(rule_details, dict):
            return []
        expected = {
            _single_line(item, 32)
            for item in rule_details.get("signals", [])
            if _single_line(item, 32)
        } if isinstance(rule_details.get("signals"), list) else set()
        if not expected:
            return []
        actual = set(self._expression_style_features_from_text(cleaned))
        if "short" in expected:
            sentence_count = len(re.findall(r"[^。！？!?\n]+[。！？!?]?", cleaned))
            if len(cleaned) <= 72 and sentence_count <= 2:
                actual.add("short")
        return [signal for signal in rule_details.get("signals", []) if signal in expected and signal in actual]

    def _record_expression_rule_injection(
        self,
        user: dict[str, Any],
        rule_details: dict[str, Any] | None,
        response_text: Any,
        *,
        semantic_rules: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        local_rule = rule_details if isinstance(rule_details, dict) and rule_details.get("id") else {}
        selected_semantic_rules = [
            dict(item)
            for item in (semantic_rules if isinstance(semantic_rules, list) else [])
            if isinstance(item, dict) and item.get("id")
        ][:2]
        if not isinstance(user, dict) or (not local_rule and not selected_semantic_rules):
            return {}
        profile = user.setdefault("expression_profile", {})
        if not isinstance(profile, dict):
            profile = {}
            user["expression_profile"] = profile
        usage = profile.setdefault("usage", {})
        if not isinstance(usage, dict):
            usage = {}
            profile["usage"] = usage
        visible_signals = self._expression_visible_signals_in_reply(response_text, local_rule)
        injected_count = _safe_int(usage.get("injected_count"), 0, 0) + 1
        visible_match_count = _safe_int(usage.get("visible_match_count"), 0, 0) + (1 if visible_signals else 0)
        now = _now_ts()
        primary_rule = local_rule or selected_semantic_rules[0]
        semantic_primary = selected_semantic_rules[0] if selected_semantic_rules else {}
        last = {
            "ts": now,
            "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "rule_id": _single_line(primary_rule.get("id"), 100),
            "scene": _single_line(primary_rule.get("scene") or primary_rule.get("intent") or primary_rule.get("kind"), 32),
            "label": _single_line(primary_rule.get("label") or primary_rule.get("situation"), 80),
            "instruction": _single_line(primary_rule.get("instruction"), 260),
            "evidence_count": _safe_int(primary_rule.get("evidence_count"), 0, 0),
            "confidence": max(
                0.0,
                min(
                    1.0,
                    _safe_float(
                        primary_rule.get("confidence"),
                        min(0.98, 0.52 + min(8, _safe_int(primary_rule.get("evidence_count"), 0, 0)) * 0.055),
                    ),
                ),
            ),
            "expected_signals": [
                _single_line(item, 32)
                for item in local_rule.get("signals", [])
                if _single_line(item, 32)
            ][:4] if isinstance(local_rule.get("signals"), list) else [],
            "visible_signals": visible_signals[:4],
            "rule_type": "heuristic" if local_rule else "semantic",
            "semantic_rule_count": len(selected_semantic_rules),
            "channel": _single_line((context or {}).get("channel"), 24),
            "relationship_stage": _single_line((context or {}).get("relationship_stage"), 24),
            "emotion_gate": _single_line((context or {}).get("emotion_gate"), 24),
            "intent": _single_line((context or {}).get("intent"), 32),
        }
        if local_rule and semantic_primary:
            last["semantic_rule_id"] = _single_line(semantic_primary.get("id"), 100)
            last["semantic_label"] = _single_line(semantic_primary.get("situation"), 80)
        usage.update(
            {
                "injected_count": injected_count,
                "visible_match_count": visible_match_count,
                "last_injection": last,
                "updated_at": last["at"],
            }
        )
        if selected_semantic_rules:
            usage["semantic_injected_count"] = _safe_int(usage.get("semantic_injected_count"), 0, 0) + 1
            feedback_rules: list[dict[str, Any]] = []
            seen_refs: set[tuple[str, str, str]] = set()
            for selected in selected_semantic_rules:
                refs = selected.get("source_refs") if isinstance(selected.get("source_refs"), list) else []
                compact_refs: list[dict[str, str]] = []
                for raw_ref in refs:
                    if not isinstance(raw_ref, dict):
                        continue
                    ref = {
                        "source_kind": _single_line(raw_ref.get("source_kind"), 16).lower(),
                        "source_id": _single_line(raw_ref.get("source_id"), 80),
                        "rule_id": _single_line(raw_ref.get("rule_id"), 40),
                    }
                    key = (ref["source_kind"], ref["source_id"], ref["rule_id"])
                    if not all(key) or key in seen_refs:
                        continue
                    seen_refs.add(key)
                    compact_refs.append(ref)
                    collection_key = "groups" if ref["source_kind"] == "group" else "users"
                    collection = self.data.get(collection_key) if isinstance(getattr(self, "data", None), dict) else {}
                    source_owner = collection.get(ref["source_id"]) if isinstance(collection, dict) else None
                    source_profile = source_owner.get("expression_profile") if isinstance(source_owner, dict) else None
                    source_rules = source_profile.get("learned_rules") if isinstance(source_profile, dict) else None
                    if not isinstance(source_rules, list):
                        continue
                    for source_rule in source_rules:
                        if not isinstance(source_rule, dict) or _single_line(source_rule.get("id"), 40) != ref["rule_id"]:
                            continue
                        source_rule["use_count"] = _safe_int(source_rule.get("use_count"), 0, 0) + 1
                        source_rule["last_used_ts"] = now
                        break
                if compact_refs:
                    feedback_rules.append(
                        {
                            "id": _single_line(selected.get("id"), 100),
                            "situation": _single_line(selected.get("situation"), 80),
                            "source_refs": compact_refs,
                        }
                    )
            feedback_channel = _single_line((context or {}).get("channel"), 24).lower()
            if feedback_rules and feedback_channel in {"private", "proactive", "group"}:
                profile["pending_semantic_feedback"] = {
                    "ts": now,
                    "channel": feedback_channel,
                    "seen_after": 0,
                    "rules": feedback_rules,
                }
        profile["last_injected_at"] = last["at"]
        return last

    def _record_staged_expression_rule_injection(
        self,
        owner: dict[str, Any],
        response_text: Any,
        *,
        channel: str,
    ) -> dict[str, Any]:
        if not isinstance(owner, dict):
            return {}
        profile = owner.get("expression_profile")
        staged = profile.pop("staged_semantic_selection", None) if isinstance(profile, dict) else None
        if not isinstance(staged, dict) or _now_ts() - _safe_float(staged.get("ts"), 0.0) > 15 * 60:
            return {}
        rules = staged.get("rules") if isinstance(staged.get("rules"), list) else []
        context = dict(staged.get("context") or {}) if isinstance(staged.get("context"), dict) else {}
        context["channel"] = _single_line(channel, 24).lower()
        return self._record_expression_rule_injection(
            owner,
            {},
            response_text,
            semantic_rules=rules,
            context=context,
        )

    @staticmethod
    def _classify_expression_rule_feedback(text: Any, *, channel: str) -> str:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return ""
        negative = bool(
            re.search(
                r"(别这么说|别这样说|别学|不像你|正常说话|好尬|尴尬|油腻|别夹|别装|"
                r"这个语气.{0,6}(?:怪|烦|恶心|不喜欢)|你怎么说话|说话怎么.{0,6}怪|闭嘴|吵死)",
                cleaned,
                re.IGNORECASE,
            )
        )
        if negative:
            return "negative"
        if channel == "group" and re.search(r"(哈哈|笑死|草|绷|乐|hhh|可以|确实|对啊)", cleaned, re.IGNORECASE):
            return "positive"
        positive = bool(
            re.search(
                r"(?:(?:这样说|这个语气|你这么说|你这样说|这个说法).{0,8}(?:好|喜欢|自然|舒服|可爱|对味))|"
                r"(?:(?:好喜欢|很喜欢).{0,8}(?:你这样|这个语气|你这么说))",
                cleaned,
                re.IGNORECASE,
            )
        )
        return "positive" if positive else ""

    def _apply_expression_rule_feedback(
        self,
        owner: dict[str, Any],
        text: Any,
        *,
        channel: str = "private",
    ) -> dict[str, Any]:
        if not isinstance(owner, dict):
            return {}
        profile = owner.get("expression_profile")
        pending = profile.get("pending_semantic_feedback") if isinstance(profile, dict) else None
        if not isinstance(pending, dict) or not pending:
            return {}
        now = _now_ts()
        if now - _safe_float(pending.get("ts"), 0.0) > 10 * 60:
            profile.pop("pending_semantic_feedback", None)
            return {}
        pending_channel = _single_line(pending.get("channel"), 24).lower()
        current_channel = _single_line(channel, 24).lower()
        if pending_channel == "group" and current_channel != "group":
            return {}
        if pending_channel in {"private", "proactive"} and current_channel != "private":
            return {}

        signal = self._classify_expression_rule_feedback(text, channel=current_channel)
        pending["seen_after"] = _safe_int(pending.get("seen_after"), 0, 0) + 1
        max_unmatched = 3 if current_channel == "group" else 1
        if not signal:
            if _safe_int(pending.get("seen_after"), 0, 0) >= max_unmatched:
                profile.pop("pending_semantic_feedback", None)
            return {}

        feedback_field = "positive_feedback" if signal == "positive" else "negative_feedback"
        updated = 0
        demoted = 0
        processed: set[tuple[str, str, str]] = set()
        for pending_rule in pending.get("rules", []) if isinstance(pending.get("rules"), list) else []:
            if not isinstance(pending_rule, dict):
                continue
            refs = pending_rule.get("source_refs") if isinstance(pending_rule.get("source_refs"), list) else []
            for ref in refs:
                if not isinstance(ref, dict):
                    continue
                source_kind = _single_line(ref.get("source_kind"), 16).lower()
                source_id = _single_line(ref.get("source_id"), 80)
                rule_id = _single_line(ref.get("rule_id"), 40)
                key = (source_kind, source_id, rule_id)
                if not all(key) or key in processed:
                    continue
                processed.add(key)
                collection_key = "groups" if source_kind == "group" else "users"
                collection = self.data.get(collection_key) if isinstance(getattr(self, "data", None), dict) else {}
                source_owner = collection.get(source_id) if isinstance(collection, dict) else None
                source_profile = source_owner.get("expression_profile") if isinstance(source_owner, dict) else None
                learned_rules = source_profile.get("learned_rules") if isinstance(source_profile, dict) else None
                if not isinstance(learned_rules, list):
                    continue
                for index, source_rule in enumerate(list(learned_rules)):
                    if not isinstance(source_rule, dict) or _single_line(source_rule.get("id"), 40) != rule_id:
                        continue
                    source_rule[feedback_field] = _safe_int(source_rule.get(feedback_field), 0, 0) + 1
                    source_rule["last_feedback"] = signal
                    source_rule["last_feedback_ts"] = now
                    updated += 1
                    if (
                        signal == "negative"
                        and _safe_int(source_rule.get("negative_feedback"), 0, 0) >= 2
                        and _safe_int(source_rule.get("negative_feedback"), 0, 0)
                        > _safe_int(source_rule.get("positive_feedback"), 0, 0)
                    ):
                        needs_review = dict(source_rule)
                        needs_review["review_status"] = "needs_review"
                        needs_review["review_reason"] = "连续收到 2 次明确负向表达反馈，已自动停用"
                        needs_review["reviewed_back_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                        learned_rules.pop(index)
                        pending_rules = source_profile.get("pending_rules") if isinstance(source_profile.get("pending_rules"), list) else []
                        pending_rules = [
                            item
                            for item in pending_rules
                            if not isinstance(item, dict) or _single_line(item.get("id"), 40) != rule_id
                        ]
                        pending_rules.insert(0, needs_review)
                        source_profile["learned_rules"] = learned_rules
                        source_profile["pending_rules"] = pending_rules[: self.max_learned_expression_items]
                        demoted += 1
                    source_profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    break

        usage = profile.setdefault("usage", {})
        if isinstance(usage, dict):
            counter = "feedback_positive" if signal == "positive" else "feedback_negative"
            usage[counter] = _safe_int(usage.get(counter), 0, 0) + 1
            usage["last_feedback"] = {
                "signal": signal,
                "ts": now,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "updated_rules": updated,
                "demoted_rules": demoted,
            }
        profile.pop("pending_semantic_feedback", None)
        if updated:
            self._refresh_expression_voice_profile()
        return {"signal": signal, "updated_rules": updated, "demoted_rules": demoted}

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
                lines.append(
                    "近期共同经历：\n"
                    + "\n".join(recent_context_parts)
                    + "\n使用方式：只在和用户当前消息相关、用户主动回到旧话题，或能一句话自然带过时使用；"
                    "不需要为了兑现旧话题打断当前话题。"
                )
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

    def _open_loop_hint_allows_topic_return(self, hint: str) -> bool:
        cleaned = _single_line(hint, 260)
        if not cleaned:
            return False
        return bool(re.search(
            r"(刚才|刚刚|前面|之前|上次|上回|昨天|昨晚|那个|这个|继续|接着|回到|再说|讲讲|说说|展开|还没|没回答|没讲完|我问的|我刚问|你刚说)",
            cleaned,
        ))

    def _select_open_loops_for_prompt(
        self,
        loops: list[dict[str, Any]],
        *,
        hint: str = "",
        limit: int = 3,
        require_relevant: bool | None = None,
    ) -> list[dict[str, Any]]:
        candidates: list[tuple[int, float, dict[str, Any]]] = []
        total = len(loops)
        hint_text = _single_line(hint, 260)
        if require_relevant is None:
            require_relevant = bool(hint_text)
        allows_topic_return = self._open_loop_hint_allows_topic_return(hint_text)
        for index, item in enumerate(loops):
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "") in {"已完成", "已取消"}:
                continue
            loop_text = _single_line(item.get("text"), 120)
            if not loop_text:
                continue
            topic_score = self._open_loop_match_score(loop_text, hint_text) if hint_text else 0.0
            if require_relevant and topic_score < 0.22 and not allows_topic_return:
                continue
            score = self._open_loop_relevance_score(item, hint=hint)
            if index >= max(0, total - 1):
                score += 2.0
            elif index >= max(0, total - 3):
                score += 1.0
            score += topic_score * 4.0
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
        affinity_tracker = getattr(self, "_note_action_affinity_sent", None)
        if callable(affinity_tracker):
            affinity_tracker(user, action)
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
        if reason in {"group_share", "news_share", "bili_video_share", "web_exploration_share", "environment_change"} or semantic_kind == "external_share":
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
        affinity_tracker = getattr(self, "_note_action_affinity_reply_feedback", None)
        if callable(affinity_tracker):
            affinity_tracker(user, action)

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
        compact = re.sub(r"\s+", "", cleaned)
        if self._user_habit_message_is_noise(cleaned):
            return "", "", ""
        category = ""
        topic = ""
        profile = self._detect_private_user_retrieval_habit(cleaned)
        if profile:
            category = "固定检索"
            topic = _single_line(profile.get("topic"), 80) or cleaned
        elif re.fullmatch(r"(?:早|早安|早上好|午安|中午好|晚上好|晚安)(?:呀|啊|哦|喔|啦|～|~|！|!)?", compact):
            category = "互动习惯"
            topic = "日常问候"
        elif re.fullmatch(r"(?:摸摸|抱抱|贴贴|亲亲){1,4}(?:呀|啊|哦|啦|～|~|！|!)?", compact):
            category = "互动习惯"
            topic = "亲昵互动"
        elif re.fullmatch(r"(?:你)?(?:在干嘛|在做什么|做什么呢|在吗)(?:呀|啊|呢|？|\?)?", compact):
            category = "互动习惯"
            topic = "询问近况"
        elif any(token in cleaned for token in ("喜欢", "讨厌", "想要", "以后", "每天", "经常", "总是", "习惯")):
            category = "偏好习惯"
            topic = cleaned
        elif self._user_habit_has_self_state(cleaned, "饮食"):
            category = "饮食节奏"
            if any(token in cleaned for token in ("还没", "没吃", "没来得及", "没饭", "没到饭点")):
                topic = "还没吃/饭点偏晚"
            elif any(token in cleaned for token in ("吃了", "刚吃", "吃完", "饱")):
                topic = "已经吃过饭"
            else:
                topic = "吃饭相关"
        elif self._user_habit_has_self_state(cleaned, "作息"):
            category = "作息节奏"
            if any(token in cleaned for token in ("还没睡", "睡不着", "熬夜")):
                topic = "夜里还没睡"
            elif any(token in cleaned for token in ("起床", "刚醒", "醒了")):
                topic = "起床/刚醒"
            else:
                topic = "睡眠相关"
        elif self._user_habit_has_self_state(cleaned, "学习工作"):
            category = "学习工作"
            topic = "学习/工作节奏"
        elif self._user_habit_has_self_state(cleaned, "娱乐"):
            category = "娱乐习惯"
            topic = "娱乐/刷内容"
        if not category or not topic:
            return "", "", ""
        signature_core = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9_]+", "", topic.casefold())[:80]
        signature = f"{category}|{signature_core}"
        return category, _single_line(topic, 80), signature

    @staticmethod
    def _user_habit_message_is_noise(text: str) -> bool:
        cleaned = _single_line(text, 240)
        lowered = cleaned.lower()
        if not cleaned or len(cleaned) > 180:
            return True
        if any(token in lowered for token in (
            "bili_live_probe", "bili状态", "photo_share", "private_companion", "<timer", "<tts",
            "我转发了一段聊天记录", "你看看里面在说什么", "合并转发", "聊天记录",
        )):
            return True
        if re.search(r"^(?:私聊|群聊)?(?:告诉|转告|提醒|叫|转发给).{1,40}", cleaned):
            return True
        if re.search(r"(?:帮我|能去|可以去).{0,12}(?:告诉|叫|转告|提醒).{1,40}", cleaned):
            return True
        return False

    @staticmethod
    def _user_habit_has_self_state(text: str, kind: str) -> bool:
        cleaned = _single_line(text, 180)
        if not cleaned or re.search(r"(?:你|他|她|它|别人|群里).{0,8}", cleaned[:20]):
            return False
        markers = {
            "饮食": r"(?:我.{0,10}(?:吃|饿|饱)|(?:还没吃|没吃|刚吃|吃完|饿了|好饿|饱了|去吃饭|准备吃))",
            "作息": r"(?:我.{0,10}(?:睡|醒|困|起床|熬夜)|(?:睡觉啦|准备睡|去睡了|刚睡醒|醒了|起床了|困了|睡不着|还没睡))",
            "学习工作": r"(?:我.{0,12}(?:学习|上班|下班|工作|上课|下课|写作业|考试|摸鱼)|(?:去上班|下班了|上课了|下课了|写作业|准备考试))",
            "娱乐": r"(?:我.{0,12}(?:玩|看|刷|追)|(?:在玩|去玩|在看|刚看|最近看|正在刷|准备看).{0,20}(?:游戏|视频|番|漫画|小说|直播)?)",
        }
        return bool(re.search(markers.get(kind, r"$^"), cleaned))

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
        sleep_delay_detector = getattr(self, "_detect_sleep_delay_request", None)
        if callable(sleep_delay_detector):
            try:
                if sleep_delay_detector(cleaned):
                    return
            except Exception:
                pass
        category, topic, signature = self._classify_user_habit_message(cleaned)
        if not category or not signature:
            return
        now_dt = datetime.now()
        day_key = now_dt.strftime("%Y-%m-%d")
        bucket, minute = self._time_bucket_for_user_habit(now_dt)
        habits = user.setdefault("behavior_habits", {})
        if not isinstance(habits, dict):
            habits = {}
            user["behavior_habits"] = habits
        patterns = habits.setdefault("patterns", [])
        if not isinstance(patterns, list):
            patterns = []
            habits["patterns"] = patterns
        self._sanitize_user_behavior_habit_patterns(user)
        patterns = habits.get("patterns") if isinstance(habits.get("patterns"), list) else []
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
        evidence_days = matched.get("evidence_days")
        if not isinstance(evidence_days, list):
            evidence_days = []
        evidence_days.append(day_key)
        matched["evidence_days"] = list(dict.fromkeys(str(item) for item in evidence_days if str(item)))[-30:]
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

    def _sanitize_user_behavior_habit_patterns(self, user: dict[str, Any]) -> bool:
        habits = user.get("behavior_habits") if isinstance(user, dict) else None
        if not isinstance(habits, dict):
            return False
        patterns = habits.get("patterns")
        if not isinstance(patterns, list):
            return False
        allowed_categories = {
            "固定检索", "互动习惯", "偏好习惯", "饮食节奏", "作息节奏", "学习工作", "娱乐习惯",
        }
        kept: list[dict[str, Any]] = []
        for item in patterns:
            if not isinstance(item, dict) or str(item.get("category") or "") not in allowed_categories:
                continue
            evidence_days = item.get("evidence_days")
            if not isinstance(evidence_days, list) or not any(str(day) for day in evidence_days):
                continue
            kept.append(item)
        if len(kept) == len(patterns):
            return False
        habits["patterns"] = kept[: self.user_habit_max_items]
        habits["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        return True

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
        evidence_days = item.get("evidence_days")
        count = (
            len(set(str(day) for day in evidence_days if str(day)))
            if isinstance(evidence_days, list)
            else 0
        )
        age_days = max(0.0, (now - _safe_float(item.get("last_seen_ts"), now)) / 86400)
        if age_days <= 7:
            recency = 1.0
        elif age_days <= 30:
            recency = max(0.2, 1.0 - (age_days - 7) / 23 * 0.8)
        else:
            recency = 0.0
        return count * recency

    def _qualified_user_behavior_habits(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        self._sanitize_user_behavior_habit_patterns(user)
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
            evidence_days = item.get("evidence_days")
            if not isinstance(evidence_days, list) or len(set(str(day) for day in evidence_days if str(day))) < min_count:
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
        if self._habit_topic_is_greeting_like(topic or category) and self._recent_activity_suppresses_habit_greeting(
            user,
            now=now,
            topic=topic or category,
        ):
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
        if re.fullmatch(r"(?:早|早安|早上好|上午好|午安|中午好|晚上好|晚安)", compact):
            return True
        if len(compact) > 16:
            return False
        return bool(
            re.search(r"(?:早安|早上好|上午好|午安|中午好|晚上好|晚安|早间|早晨|早上)", compact)
            and re.search(r"(?:问候|打招呼|招呼|寒暄|开场|醒来|起床)", compact)
        )

    def _recent_activity_suppresses_habit_greeting(self, user: dict[str, Any], *, now: float, topic: str = "") -> bool:
        compact_topic = re.sub(r"\s+", "", _single_line(topic, 80))
        try:
            current_minute = self._environment_fromtimestamp(now).hour * 60 + self._environment_fromtimestamp(now).minute
        except Exception:
            current_minute = datetime.fromtimestamp(now).hour * 60 + datetime.fromtimestamp(now).minute
        if compact_topic in {"早", "早安", "早上好"} and current_minute >= 11 * 60:
            return True
        recent_at = self._latest_private_user_activity_ts(user)
        recent_any = max(
            recent_at,
            _safe_float(user.get("last_user_message_at"), 0),
            _safe_float(user.get("last_companion_message_at"), 0),
            _safe_float(user.get("last_sent"), 0),
        )
        if recent_any > 0 and now - recent_any < max(90, self._effective_user_greeting_idle_minutes(user)) * 60:
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

    def _is_playful_or_ambiguous_boundary(self, text: str) -> bool:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        if self._is_soft_playful_boundary(cleaned):
            return True
        return bool(
            re.search(r"(开玩笑|闹着玩|不是认真的|别当真|随口|口嗨|逗你|玩梗)", cleaned)
            or re.search(r"(哈哈|呵呵|hhh|hha|笑死|绷不住|乐了|233|~|～|qwq|w$)", cleaned, re.IGNORECASE)
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
        weak_boundary = bool(re.search(r"(别|不要|讨厌|烦)", cleaned))
        soft_play_boundary = self._is_soft_playful_boundary(cleaned)
        playful_or_ambiguous = self._is_playful_or_ambiguous_boundary(cleaned)
        durable_boundary = bool(
            target_hint
            and not third_party_hint
            and not playful_or_ambiguous
            and re.search(
                r"(?:以后|之后).{0,8}(?:别|不要)|(?:别再|不要再|不许).{0,12}(?:这样|烦|吵|打扰|靠近|贴|撒娇|叫我|问我|说话)|(?:不想|不愿).{0,10}(?:理你|跟你聊|继续聊)|(?:离我远点|别打扰我|别靠近|别贴|别撒娇)",
                cleaned,
            )
        )
        single_turn_boundary = bool(
            target_hint
            and not third_party_hint
            and not playful_or_ambiguous
            and re.search(r"(别|不要|讨厌|烦|闭嘴|滚|离远点)", cleaned)
        )
        if durable_boundary:
            intent = "boundary"
            emotion = "resistant"
            pressure += 3
            reply_style = "back_off"
            confidence = 0.9
            source = "durable_boundary_rule"
            reason = "用户明确、持续地对 Bot 表达边界"
        elif single_turn_boundary:
            reply_style = "short"
            confidence = 0.58
            source = "single_turn_boundary"
            reason = "单句负向表达，先按当下语境短答，不写入长期关系状态"
        elif not playful_or_ambiguous and re.search(r"(烦|累|难受|崩溃|不想|想哭|emo|压力|焦虑|失眠|疼|委屈)", cleaned, re.IGNORECASE):
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
        emotion_event = self._classify_relationship_emotion_event(
            cleaned,
            intent_context={
                "confidence": confidence,
                "source": source,
                "boundary_durable": durable_boundary,
                "playful_or_ambiguous": playful_or_ambiguous,
            },
        )
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
            "boundary_durable": durable_boundary,
            "playful_or_ambiguous": playful_or_ambiguous,
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
        boundary_durable = bool((intent_context or {}).get("boundary_durable"))
        playful_or_ambiguous = bool((intent_context or {}).get("playful_or_ambiguous"))
        if playful_or_ambiguous or intent_source in {"soft_boundary_play_rule", "weak_boundary_ignored", "single_turn_boundary"}:
            return {"event": "neutral", "intensity": 0, "reason": "玩笑或单句边界不作为情绪余波依据", "target": "none", "rule": "playful_or_single_boundary", "confidence": 0.8}
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
        if intent_source == "durable_boundary_rule" and not direct_bot_negative and not identity_hurt:
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
            return {"event": "hurt", "intensity": 76 if boundary_durable else 60, "reason": "否定情感真实性或人格", "target": "bot", "rule": "identity_hurt", "confidence": 0.84 if boundary_durable else 0.68}
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
        source = str(intent.get("source") or "")
        if bool(intent.get("playful_or_ambiguous")) or source in {"weak_boundary_ignored", "soft_boundary_play_rule", "single_turn_boundary"}:
            return False
        mode = str(getattr(self, "emotion_judgement_mode", "suspicious") or "suspicious").lower()
        if mode in {"off", "none", "disabled"}:
            return False
        if mode in {"always", "all"}:
            return True
        confidence = _safe_float(intent.get("confidence"), 0.5)
        emotion_confidence = _safe_float(intent.get("emotion_confidence"), confidence)
        event = str(intent.get("emotion_event") or "neutral")
        return (
            event != "neutral"
            or confidence < 0.72
            or emotion_confidence < 0.72
            or source == "durable_boundary_rule"
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
        if event == "hurt" and (
            bool(base_intent.get("playful_or_ambiguous"))
            or local_source in {"weak_boundary_ignored", "soft_boundary_play_rule", "single_turn_boundary"}
        ):
            return None
        if event == "hurt" and local_source == "durable_boundary_rule":
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
You classify whether one private inbound message changes the Bot's short-term emotional afterglow. Do not write a reply.

Allowed event values: neutral, hurt, apology, comfort, praise, comfort_need, external_negative.
target must be bot, self, other, ambiguous, or none.
Only classify hurt when the message clearly targets the Bot/current character. Be conservative with jokes, flirting, logs, code, and quoted text.
A boundary such as less intimacy, no flirting, no approaching, or no interruptions should normally be neutral; relationship-distance logic handles it separately.
Return JSON only:
{{"event":"neutral|hurt|apology|comfort|praise|comfort_need|external_negative","target":"bot|self|other|ambiguous|none","intensity":0-100,"confidence":0.0-1.0,"reason":"brief reason"}}

User message:
{cleaned}

Local classifier result:
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
            logger.debug("[PrivateCompanion] Emotion judgement request failed: %s", _single_line(exc, 120))
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
                    "[PrivateCompanion] Emotion judgement completed: user=%s event=%s target=%s intensity=%s confidence=%s reason=%s",
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
        # Old versions did not mark whether a backoff came from a durable boundary.
        # Clear those legacy one-shot states on the next interaction.
        if previous_mode == "backoff" and not bool(state.get("backoff_is_durable")):
            state["backoff_until"] = 0
            previous_mode = "normal"
        current = previous_mode
        mood_score = self._decay_relationship_mood_score(state, now=now) if emotion_enabled else 0
        inbound_intent = str(intent.get("intent") or "chat")
        pressure = _safe_int(intent.get("pressure"), 0, 0, 5)
        emotion_event = str(intent.get("emotion_event") or "neutral")
        intensity = _safe_int(intent.get("emotion_intensity"), 0, 0, 100)
        intent_confidence = _safe_float(intent.get("confidence"), 0.5)
        emotion_confidence = _safe_float(intent.get("emotion_confidence"), intent_confidence)
        boundary_durable = bool(intent.get("boundary_durable"))
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
        if emotion_enabled and emotion_event == "hurt" and target == "bot" and intensity >= hurt_threshold:
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
        elif relation_enabled and inbound_intent == "boundary" and boundary_durable and intent_confidence >= 0.82:
            current = "backoff"
            state["backoff_until"] = now + 6 * 3600
            state["backoff_is_durable"] = True
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
        if current != "backoff":
            state["backoff_is_durable"] = False
        if current not in {"hurt", "refusing"} and _safe_int(state.get("silence_turns"), 0, 0, 5) > 0:
            state["silence_turns"] = max(0, _safe_int(state.get("silence_turns"), 0, 0, 5) - 1)
        emotion_dimensions = (
            self._update_izard_emotion_dimensions(
                state,
                event=emotion_event,
                intensity=intensity,
                target=target,
                confidence=emotion_confidence,
                inbound_intent=inbound_intent,
                pressure=pressure,
                mode=current,
                now=now,
            )
            if emotion_enabled
            else {}
        )
        plutchik_profile = (
            self._update_plutchik_emotions(
                state,
                event=emotion_event,
                intensity=intensity,
                target=target,
                inbound_intent=inbound_intent,
                pressure=pressure,
                mode=current,
                now=now,
            )
            if emotion_enabled
            else {}
        )
        emotion_regulation = (
            self._derive_gross_emotion_regulation(
                state,
                event=emotion_event,
                intensity=intensity,
                target=target,
                inbound_intent=inbound_intent,
                pressure=pressure,
                mode=current,
                mood_score=mood_score,
                now=now,
            )
            if emotion_enabled
            else {}
        )
        state["mode"] = current
        state["mood_score"] = mood_score
        state["mood_updated_ts"] = now
        state["last_intent"] = inbound_intent
        state["last_pressure"] = pressure
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
        if emotion_event != "neutral" and emotion_dimensions:
            logger.debug(
                "[PrivateCompanion] 情绪四维: pleasantness=%s tension=%s arousal=%s certainty=%s",
                emotion_dimensions.get("pleasantness"),
                emotion_dimensions.get("tension"),
                emotion_dimensions.get("arousal"),
                emotion_dimensions.get("certainty"),
            )
        if emotion_event != "neutral" and plutchik_profile:
            logger.debug(
                "[PrivateCompanion] 基本/复合情绪: dominant=%s(%s) blend=%s",
                plutchik_profile.get("dominant_label") or "-",
                plutchik_profile.get("dominant_value") or 0,
                plutchik_profile.get("blend_label") or "-",
            )
        if emotion_event != "neutral" and emotion_regulation:
            logger.debug(
                "[PrivateCompanion] 情绪调节策略: strategy=%s intensity=%s reason=%s",
                emotion_regulation.get("strategy_label") or emotion_regulation.get("strategy") or "-",
                emotion_regulation.get("intensity") or 0,
                emotion_regulation.get("reason") or "-",
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

    def _smart_silence_contextual_trigger_reason(
        self,
        inbound_text: str,
        response_text: str = "",
        *,
        session_kind: str = "",
    ) -> str:
        boundary = self._smart_silence_trigger_reason(inbound_text)
        if boundary:
            return boundary
        mode = str(getattr(self, "smart_silence_judge_mode", "boundary_only") or "boundary_only").strip().lower()
        if mode != "contextual":
            return ""
        inbound = _single_line(inbound_text, 260)
        response = _single_line(response_text, 600)
        compact = re.sub(r"\s+", "", inbound)
        response_compact = re.sub(r"\s+", "", response)
        if not compact or not response_compact:
            return ""
        if len(compact) <= 16 and re.fullmatch(r"(嗯+|恩+|哦+|噢+|喔+|行|好|好吧|可以|算了|没事|不用了|随便|先这样|就这样|知道了|了解了|收到|ok|OK|嗯嗯|啊这|呃|em+|额)", compact, flags=re.I):
            if re.search(r"(吗|呢|吧|要不要|需不需要|可以.*吗|要是|如果|我可以|我帮你|继续|再|还|解释|分析|建议|聊|说)", response_compact):
                return "short_disengage"
        if re.search(r"(算了|没事|不用了|先这样|就这样|不管了|随便吧|无所谓了)", compact):
            if re.search(r"(那我|我来|我帮|可以继续|继续|再说|要不要|需不需要|解释|分析|建议|追问|为什么|怎么)", response_compact):
                return "soft_disengage"
        if re.search(r"(困了|睡了|睡觉|去睡|先睡|晚安|下了|走了|忙去了|开会|上课|工作了|不方便)", compact):
            if re.search(r"(吗|呢|要不要|继续|再聊|我陪|我等|说说|聊聊|解释|分析|建议)", response_compact):
                return "leaving_or_busy"
        if session_kind == "group" and len(compact) <= 12 and re.fullmatch(r"(哈哈+|草+|笑死|乐|绷|6+|？+|\\?+|啊？|啥|什么鬼|不是吧|好家伙)", compact):
            if len(response_compact) >= 18 and re.search(r"(我觉得|可能|其实|要不|建议|可以|因为|所以|解释|分析)", response_compact):
                return "group_reaction_not_request"
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
        trigger = self._smart_silence_contextual_trigger_reason(
            inbound,
            response,
            session_kind=session_kind,
        )
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
你是聊天回复发送前的智能沉默判定器。判断用户是否在表达“不要继续这个话题/不要再追问/先别回复/换掉当前话题”，或上下文已经明显适合安静收住，从而应该直接不发这条待发送回复。

只输出 JSON：{{"decision":"send|silent","confidence":0-1,"reason":"不超过20字"}}

判定原则：
- 用户明确说别聊、别问、别继续、到此为止、算了别说了、换个话题，且待发送回复仍在确认、安慰、解释、追问或继续这个话题，decision=silent。
- 当触发词是 short_disengage、soft_disengage、leaving_or_busy 或 group_reaction_not_request 时，要结合上下文判断：用户只是短促收尾、要离开、忙了、敷衍回应，且待发送回复还在追问、解释、建议、延长话题，才 silent。
- 如果用户同一句已经开启了新请求或新问题，例如“算了，帮我看这个”“换个话题，今天吃什么”，且待发送回复是在处理新请求，decision=send。
- 如果待发送回复只是“好，那不聊这个了”“嗯我闭嘴了”这类对边界的重复确认，通常 silent；真实聊天里安静退开更自然。
- 如果待发送回复是必要的信息回答、用户明确提问的答案、工具结果、约定确认或安全提醒，decision=send。
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
        timeout_getter = getattr(self, "_model_timeout_seconds_for_call", None)
        timeout_override = (
            timeout_getter(
                task="smart_silence",
                provider_id=provider_id,
                timeout_key="SMART_SILENCE_PROVIDER_ID",
            )
            if callable(timeout_getter)
            else None
        )
        if timeout_override is not None:
            timeout_seconds = float(timeout_override)
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

    @staticmethod
    def _looks_like_private_fact_correction(text: Any) -> bool:
        cleaned = _single_line(text, 220)
        if not cleaned or len(cleaned) > 180:
            return False
        patterns = (
            r"明明(?:是|就是)",
            r"(?:你|我|他|她|它)才(?:是|没有|没|先|刚)",
            r"(?:不是|并非)(?:你|我|他|她|它)[^。！？!?]{0,24}(?:说|提|想|做|拿|问|告诉|推荐)",
            r"不是[^。！？!?]{1,40}[，,](?:而是|是)(?:你|我|他|她|它|[\u4e00-\u9fffA-Za-z0-9_]{1,16}(?:大人|主人|先生|小姐)?)[^。！？!?]{0,24}",
            r"(?:说|记|弄|搞|认|写|理解)(?:反|错|偏)了",
            r"(?:主语|对象|人|名字|称呼)(?:反了|错了|不对)",
        )
        return any(re.search(pattern, cleaned, re.IGNORECASE) for pattern in patterns)

    def _record_recent_private_fact_correction(self, user: dict[str, Any], inbound_text: str) -> bool:
        if not isinstance(user, dict) or not self._looks_like_private_fact_correction(inbound_text):
            return False
        correction = _single_line(inbound_text, 180)
        inbound_count = _safe_int(user.get("private_inbound_count"), 0, 0)
        existing = user.get("recent_fact_correction")
        if (
            isinstance(existing, dict)
            and _single_line(existing.get("text"), 180) == correction
            and inbound_count == _safe_int(existing.get("inbound_count"), -1)
        ):
            return False
        user["recent_fact_correction"] = {
            "text": correction,
            "at": _now_ts(),
            "inbound_count": inbound_count,
        }
        return True

    def _active_private_fact_correction(self, user: dict[str, Any], inbound_text: str = "") -> str:
        current = _single_line(inbound_text, 180)
        if self._looks_like_private_fact_correction(current):
            return current
        if not isinstance(user, dict):
            return ""
        record = user.get("recent_fact_correction")
        if not isinstance(record, dict):
            return ""
        text = _single_line(record.get("text"), 180)
        corrected_at = _safe_float(record.get("at"), 0)
        corrected_count = _safe_int(record.get("inbound_count"), -1)
        current_count = _safe_int(user.get("private_inbound_count"), 0, 0)
        if not text or corrected_at <= 0 or _now_ts() - corrected_at > 30 * 60:
            return ""
        if corrected_count >= 0 and current_count - corrected_count > 2:
            return ""
        return text

    @staticmethod
    def _inbound_explicitly_owns_recent_media_event(inbound_text: str) -> bool:
        """Return whether the user explicitly says the depicted event happened to/by them."""
        inbound = _single_line(inbound_text, 220)
        if not inbound:
            return False
        # Common exclamations and observation phrases contain “我” without assigning
        # the depicted action to the user (for example “我的天，洒出来了”).
        if re.match(r"^(?:我的天|我天|我去|我靠|我艹|我草|我勒个|我看(?:见|到|着)?|我觉得|我感觉|我想说)", inbound):
            return False
        return bool(
            re.search(
                r"(?:^|[，,。！？!?\s])(?:是)?我(?:自己)?"
                r"[^。！？!?\n]{0,12}"
                r"(?:把|将|弄|搞|打翻|碰倒|弄倒|洒|撒|溅|摔|掉|弄坏|打碎|"
                r"受伤|烫|割|磕|撞|做的|干的|画的|拍的|发的)",
                inbound,
            )
        )

    def _recent_proactive_media_ownership_context(
        self,
        user: dict[str, Any],
        inbound_text: str = "",
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Resolve a short reply as commentary on the Bot's latest proactive image."""
        if not isinstance(user, dict):
            return {}
        inbound = _single_line(inbound_text, 220)
        if not inbound or self._inbound_explicitly_owns_recent_media_event(inbound):
            return {}

        check_now = _now_ts() if now is None else now
        action = _single_line(user.get("last_proactive_action"), 80).lower()
        action_parts = {part.strip() for part in action.split("+") if part.strip()}
        action_is_photo = "photo_text" in action_parts or "photo_text" in action
        last_proactive_at = _safe_float(user.get("last_proactive_sent_at"), 0)

        snapshot = user.get("last_photo_share_snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        snapshot_at = _safe_float(snapshot.get("sent_at"), 0)
        snapshot_expires_at = _safe_float(snapshot.get("expires_at"), 0) or snapshot_at + 12 * 3600
        snapshot_is_live = snapshot_at > 0 and check_now < snapshot_expires_at
        newer_non_photo_proactive = (
            last_proactive_at > snapshot_at + 1
            and not action_is_photo
        )
        if not action_is_photo and (not snapshot_is_live or newer_non_photo_proactive):
            return {}

        sent_at = max(last_proactive_at if action_is_photo else 0, snapshot_at if snapshot_is_live else 0)
        age = check_now - sent_at
        if sent_at <= 0 or age < 0:
            return {}

        compact = self._compact_repeat_text(inbound)
        direct_media_reference = bool(
            re.search(r"(?:这|那|刚才|你发的)?(?:张)?(?:图|图片|照片|画面|里面|图里|照片里)", inbound)
        )
        reaction_cues = (
            "洒", "撒", "溅", "打翻", "翻了", "翻车", "摔", "掉", "倒了", "漏", "碎", "破",
            "糊", "焦", "坏", "着火", "冒烟", "脏", "湿", "好看", "漂亮", "可爱", "吓", "危险",
            "小心", "完了", "救命", "哈哈", "笑死", "啊", "怎么", "手", "疼",
        )
        short_reaction = len(compact) <= 48 and any(cue in inbound for cue in reaction_cues)
        if direct_media_reference:
            if age > 12 * 3600:
                return {}
        elif not short_reaction or age > 30 * 60:
            return {}

        caption = _single_line(snapshot.get("caption"), 260)
        if not caption:
            summary = _single_line(user.get("last_proactive_behavior_summary"), 300)
            caption = _single_line(re.split(r"[:：]", summary, maxsplit=1)[-1], 260) if summary else ""
        return {
            "sent_at": sent_at,
            "action": action or "photo_text",
            "caption": caption,
            "subject_owner": _normalize_photo_subject_owner(snapshot.get("subject_owner")) or "unknown",
            "proactive_text": _single_line(user.get("last_proactive_message"), 300),
        }

    def _format_recent_proactive_media_ownership_guard(
        self,
        user: dict[str, Any],
        inbound_text: str = "",
    ) -> str:
        context = self._recent_proactive_media_ownership_context(user, inbound_text)
        if not context:
            return ""
        caption = _single_line(context.get("caption"), 260)
        subject_owner = _normalize_photo_subject_owner(context.get("subject_owner")) or "unknown"
        owner_label = _photo_subject_owner_prompt_label(subject_owner)
        if subject_owner == "bot":
            ownership_rule = "- 结构化主体归属为 Bot：图中由“我/她/角色本人”做出的动作属于 Bot/当前人格。"
        else:
            ownership_rule = f"- 结构化主体归属为{owner_label}；动作属于该画面主体，不属于用户，也不要擅自改判成 Bot。"
        return "\n".join(
            part
            for part in (
                "【本轮主动图片归属（高优先级）】",
                "- 用户是在评价 Bot 刚才主动发出的图片，不是在报告自己做了图中的事。",
                f"- 图片发送者：Bot/当前人格；画面主体：{owner_label}",
                f"- 刚才图片画面：{caption}" if caption else "",
                ownership_rule,
                "- 除非用户明确说“我把……弄洒了/做了”，否则绝不能把图中动作安到用户身上。",
                "- 回复应从 Bot 或真实画面主体的角度承接，可以自然承认、自嘲或回应用户的担心；不得责怪用户笨手笨脚，也不得询问用户有没有被图中事件弄伤、弄湿或溅到。",
            )
            if part
        )

    def _format_private_fact_attribution_guard(self, user: dict[str, Any], inbound_text: str = "") -> str:
        correction = self._active_private_fact_correction(user, inbound_text)
        lines = [
            "【事实主语与归属边界】",
            "- 使用结构化记忆时先确认记录的叙述视角：Bot 自我/人格生活和本私聊的 Bot 视角摘要中，“我”是当前 Bot/人格，收件人昵称才是用户。",
            "- 不得把“Bot 提过、Bot 想去、Bot 看见、Bot 推荐”改写成“用户提过、用户想去、用户先拿来诱惑 Bot”，反向亦然；视角不清时省略主语，不要猜。",
            "- 当前消息和最近原始对话高于旧摘要；用户纠正事实归属后，先承认并沿用，不得在后一句又翻回原来的错误。",
        ]
        if correction:
            lines.extend(
                [
                    f"- 最近的高优先级纠正：{correction}",
                    "- 这条纠正只用于稳定眼前话题的主客体，不要扩写成用户没说过的新事实，也不要反过来埋怨用户。",
                ]
            )
        media_ownership_guard = self._format_recent_proactive_media_ownership_guard(user, inbound_text)
        if media_ownership_guard:
            lines.extend(["", media_ownership_guard])
        return "\n".join(lines)

    def _response_reverses_recent_proactive_media_ownership(
        self,
        response_text: str,
        user: dict[str, Any],
        inbound_text: str,
    ) -> bool:
        if not self._recent_proactive_media_ownership_context(user, inbound_text):
            return False
        cleaned = _single_line(response_text, 500)
        if not cleaned:
            return False
        depicted_actions = r"(?:洒|撒|溅|打翻|碰倒|弄倒|摔|掉|弄坏|打碎|受伤|烫|割|磕|撞)"
        if re.search(rf"我[^。！？!?\n]{{0,16}}{depicted_actions}", cleaned):
            return False
        return bool(
            re.search(rf"你[^。！？!?\n]{{0,18}}{depicted_actions}", cleaned)
            or re.search(r"(?:怎么|这么|也太)[^。！？!?\n]{0,10}(?:笨手笨脚|不小心|毛手毛脚)", cleaned)
            or re.search(
                r"(?:有没有|有没|没|会不会|别|记得|赶紧|快|先|小心)"
                r"[^。！？!?\n]{0,14}"
                r"(?:溅到|伤到|烫到|割到|弄到|碰到|受伤|手上|身上|衣服|疼)",
                cleaned,
            )
            or re.search(r"(?:你没事吧|没伤着吧|有没有受伤|疼不疼)", cleaned)
        )

    def _response_claims_user_prior_action(self, text: str, user: dict[str, Any]) -> bool:
        cleaned = _single_line(text, 500)
        if not cleaned:
            return False
        names = ["你"]
        if isinstance(user, dict):
            for key in ("nickname", "last_display_name", "display_name"):
                name = _single_line(user.get(key), 24)
                if name and not name.isdigit() and name not in names:
                    names.append(name)
        subject = "|".join(re.escape(name) for name in names)
        titled_name = r"[\u4e00-\u9fffA-Za-z0-9_]{1,16}(?:大人|主人|先生|小姐)"
        return bool(
            re.search(
                rf"(?:{subject}|{titled_name})[^。！？!?\n]{{0,18}}(?:上次|之前|先|早就|原来)[^。！？!?\n]{{0,18}}(?:说|提|想|拿|问|做|告诉|推荐|诱惑)",
                cleaned,
            )
            or re.search(r"明明是[^。！？!?\n]{1,24}先[^。！？!?\n]{0,18}(?:说|提|想|拿|问|做|告诉|推荐|诱惑)", cleaned)
        )

    @staticmethod
    def _response_denies_existing_creative_work(response_text: str, creative_context: str) -> bool:
        response = _single_line(response_text, 500)
        context = str(creative_context or "")
        if not response or "真实创作记录：共有" not in context:
            return False
        denial_patterns = (
            r"(?:没|没有|还没|从没|并没|未曾)[^。！？!?\n]{0,10}(?:写过|写|创作过|创作|完成)[^。！？!?\n]{0,10}(?:书|小说|作品|故事|诗|随笔|散文|剧本|手稿)",
            r"(?:没|没有|还没有|并没有)[^。！？!?\n]{0,8}(?:自己写的|自己的|成型的)?[^。！？!?\n]{0,5}(?:书|小说|作品|故事|手稿)",
            r"(?:我)?哪有[^。！？!?\n]{0,12}(?:书|小说|作品|手稿)",
        )
        return any(re.search(pattern, response, re.IGNORECASE) for pattern in denial_patterns)

    async def _review_and_rewrite_response(
        self,
        user: dict[str, Any],
        inbound_text: str,
        response_text: str,
        *,
        music_album_context: dict[str, Any] | None = None,
        creative_context: str = "",
        review_event: Any | None = None,
    ) -> str:
        # Any rewrite can break the protected voice/text correspondence for this turn.
        if "[[PCTTS:" in str(response_text or ""):
            return response_text
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
        if not self._passive_response_review_enabled():
            return self._fallback_temporal_or_continuity_confused_reply(inbound_text, response_text, user=user) or response_text
        flags = self._response_review_flags(response_text, user, inbound_text=inbound_text)
        if self._response_denies_existing_creative_work(response_text, creative_context):
            flags.append("denies_existing_creative_work")
            flags = list(dict.fromkeys(flags))
        if not flags:
            return response_text
        review_mode = self._effective_passive_review_mode()
        review_strength = self._effective_passive_review_strength()
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
                "fact_attribution_after_correction",
                "unverified_fact_attribution",
                "proactive_media_ownership_reversal",
                "denies_existing_creative_work",
            }
            if not any(flag in critical_flags for flag in effective_flags):
                return response_text
        intent = user.get("intent_profile") if isinstance(user.get("intent_profile"), dict) else {}
        allow_repeat = self._inbound_explicitly_requests_repeat(inbound_text)
        last_message = _single_line(user.get("last_companion_message"), 300)
        last_message_label = "用户本轮明确要求复述上一条,仅用于确认原文" if allow_repeat else "刚才 Bot 已经说过，禁止复述或换皮重复"
        persona = ""
        persona_resolver = getattr(self, "_resolve_proactive_persona_prompt", None)
        if callable(persona_resolver):
            try:
                persona = str(await persona_resolver(user) or "").strip()
            except Exception:
                persona = ""
        reply_style = self._format_reply_style_prompt() if callable(getattr(self, "_format_reply_style_prompt", None)) else ""
        attribution_guard = self._format_private_fact_attribution_guard(user, inbound_text)
        creative_review_context = str(creative_context or "").strip()[:3200]
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

【当前人格】
{persona[:2600] if persona else '（沿用原回复已有的人格语气，不要另造通用助手口吻）'}

【回复风格】
{reply_style or '（保持当前私聊的自然表达）'}

{attribution_guard}

【本轮真实创作记录】
{creative_review_context or '（本轮没有创作记录上下文）'}

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
- 如果问题是 fact_attribution_after_correction，必须以用户刚才的纠正和上一条 Bot 已承认的内容为准；不要换个说法再次把 Bot 的行为安到用户身上
- 如果问题是 unverified_fact_attribution，原回复正在断言“用户之前/先做过某事”，但当前短句没有提供这个归属；没有明确依据就改成中性主语或只谈那件事本身
- 如果问题是 proactive_media_ownership_reversal，用户只是在评价 Bot 刚主动发送的图片；把图中“我/她/角色本人”的动作改回 Bot/当前人格，绝不能责怪或关心用户仿佛是用户弄洒、摔倒或受伤
- 如果问题是 denies_existing_creative_work，必须依据本轮真实创作记录承认已有文本作品；不得把“未正式出版”偷换成“没写过”，也不要虚构出版、发行或实体书经历
""".strip()
        if review_event is not None:
            setattr(review_event, "_private_companion_response_review_guard_active", True)
            setattr(review_event, "_private_companion_response_review_fallback_text", response_text)
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
            if review_strength == "lenient":
                logger.info(
                    "[PrivateCompanion] 被动回复宽松复核忽略取消判定,保留原回复: flags=%s",
                    ",".join(effective_flags),
                )
                return response_text
            logger.info(
                "[PrivateCompanion] 被动回复模型自检判定重复,已标记丢弃: flags=%s before=%s",
                ",".join(effective_flags),
                _single_line(response_text, 120),
            )
            return self._response_review_drop_marker()
        meta_leak_reason = self._response_review_meta_leak_reason(cleaned)
        if meta_leak_reason:
            logger.error(
                "[PrivateCompanion] 被动回复复核模型返回内部判断，已回退复核前正文: reason=%s output=%s",
                meta_leak_reason,
                _single_line(cleaned, 180),
            )
            return self._fallback_temporal_or_continuity_confused_reply(
                inbound_text,
                response_text,
                flags=effective_flags,
                user=user,
            ) or response_text
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
            if review_strength == "lenient":
                return response_text
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

    def _passive_response_review_enabled(self) -> bool:
        return bool(
            getattr(
                self,
                "enable_passive_response_review",
                getattr(self, "enable_response_self_review", True),
            )
        )

    def _effective_passive_review_mode(self) -> str:
        mode = str(
            getattr(self, "passive_review_mode", getattr(self, "response_review_mode", "severe_only"))
            or "severe_only"
        ).strip().lower()
        return mode if mode in {"local_only", "severe_only", "full"} else "severe_only"

    def _effective_passive_review_strength(self) -> str:
        strength = str(getattr(self, "passive_review_strength", "lenient") or "lenient").strip().lower()
        return strength if strength in {"lenient", "balanced", "strict"} else "lenient"

    @staticmethod
    def _response_review_meta_leak_reason(text: Any) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        compact = re.sub(r"\s+", " ", raw).strip()
        lower = compact.lower()
        if re.search(r"\bmaybe\s+\d+(?:\.\d+)?%\s+of\s+the\s+time\b", lower):
            return "复核模型输出概率说明"
        if re.search(r"\bat\s+the\s+(?:very\s+)?end\s+of\s+(?:a|the)\s+run\b", lower):
            return "复核模型输出运行说明"
        if re.search(
            r"\b(?:decision|verdict|review result|review reason|reason)\s*[:：]",
            lower,
        ):
            return "复核模型输出判定字段"
        if re.search(
            r"\b(?:response|output|message)\b.{0,80}\b(?:needs?\s+(?:to\s+be\s+)?rewritten|"
            r"cannot\s+be\s+saniti[sz]ed|formatting\s+(?:issue|problem)|should\s+not\s+be\s+sent)\b",
            lower,
        ):
            return "复核模型输出英文审核评语"
        chinese_review_context = re.search(
            r"(?:原(?:回复|文本|输出)|这条(?:回复|消息|输出)|回复内容|输出内容|后处理|清洗|复核|审核|"
            r"格式化表达|重复标点|一字废话|最终回复|正常人无法容忍)",
            compact,
        )
        chinese_verdict = re.search(
            r"(?:无法|不能|不应|不宜|不适合|未通过|拒绝|需要|应当|建议).{0,24}"
            r"(?:清洗|规整|发送|通过|重写|改写|修正)",
            compact,
        )
        if chinese_review_context and chinese_verdict:
            return "复核模型输出中文审核评语"
        if re.search(r"(?:判定|审核|复核)(?:结果|结论|原因)?\s*[:：]", compact):
            return "复核模型输出判定字段"
        return ""

    def _strip_response_review_meta_leak(self, text: Any) -> tuple[str, str]:
        raw = str(text or "").strip()
        if not raw:
            return "", ""
        kept: list[str] = []
        reasons: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                if kept and kept[-1] != "":
                    kept.append("")
                continue
            reason = self._response_review_meta_leak_reason(stripped)
            if reason:
                reasons.append(reason)
                continue
            kept.append(stripped)
        if not reasons:
            whole_reason = self._response_review_meta_leak_reason(raw)
            if whole_reason:
                return "", whole_reason
            return raw, ""
        cleaned = "\n".join(kept).strip()
        return cleaned, "、".join(dict.fromkeys(reasons))

    def _response_review_severe_flags(self, flags: list[str]) -> list[str]:
        severe = {
            "meta_or_assistant",
            "leaks_internal",
            "repeats_last_bot_message",
            "casual_overexplained",
            "weather_overexplained",
            "invalid_current_time_anchor",
            "false_no_reply_claim",
            "fact_attribution_after_correction",
            "unverified_fact_attribution",
            "proactive_media_ownership_reversal",
            "denies_existing_creative_work",
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
        implicit_late_anchor = bool(
            re.search(
                r"(?:时间|时候|天色).{0,4}(?:不早|(?:这么|很|太)晚)|"
                r"(?:都|已经|这会儿|现在).{0,4}(?:不早|(?:这么|很|太)晚)|"
                r"(?:不早|(?:这么|很|太)晚).{0,3}(?:了|啦|咯)",
                cleaned,
            )
        )
        sleep_anchor = bool(re.search(r"(困不困|该睡|睡觉|睡了|晚安|熬夜|夜深|深夜)", cleaned))
        late_night = 22 * 60 <= current_minutes or current_minutes <= 90
        if (explicit_late_anchor or implicit_late_anchor) and not late_night:
            return True
        if sleep_anchor and re.search(r"(快|差不多|都|已经).{0,8}(?:十一|11|23)\s*[点點]", cleaned):
            return not late_night
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
            r"[，,。！？!?；;、\s]*(?:(?:[\u4e00-\u9fffA-Za-z0-9_\-]{1,12})[，,、:：]|(?:主人|宝贝|亲爱的|宝宝|老师))?\s*(?:快|差不多|都|已经)?\s*(?:晚上)?(?:十一|11|23)\s*[点點][了啦]?[，,、\s]*(?:困不困|该睡了?|睡觉吧?|晚安)?[？?。！!~～]*",
            "",
            cleaned,
        ).strip()
        cleaned = re.sub(
            r"[，,。！？!?；;、\s]*(?:那[^，,。！？!?；;]{0,12})?"
            r"(?:(?:时间|时候|天色).{0,4}(?:不早|(?:这么|很|太)晚)|(?:都|已经|这会儿|现在).{0,4}(?:不早|(?:这么|很|太)晚)|(?:不早|(?:这么|很|太)晚).{0,3}(?:了|啦|咯))"
            r"[^。！？!?\n]{0,20}(?:歇息|休息|睡觉|睡|晚安)?[^。！？!?\n]{0,6}[？?。！!~～]*",
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

    def _cancel_inbound_conflicting_greeting(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        user_id: str = "",
        trigger_umo: str = "",
    ) -> bool:
        now = now or _now_ts()
        changed = False
        planned_reason = str(user.get("planned_proactive_reason") or "")
        planned_topic = _single_line(user.get("planned_proactive_topic"), 80)
        planned_is_greeting_habit = (
            planned_reason == "habit_awareness"
            and self._habit_topic_is_greeting_like(planned_topic)
            and self._recent_activity_suppresses_habit_greeting(user, now=now, topic=planned_topic)
        )
        preserve_wakeup_greeting = self._is_initial_wakeup_greeting(user)
        if (
            (self._inbound_satisfies_greeting(planned_reason, now=now) and not preserve_wakeup_greeting)
            or planned_is_greeting_habit
        ):
            next_at = _safe_float(user.get("next_proactive_at"), 0)
            if next_at > 0:
                if self._inbound_satisfies_greeting(planned_reason, now=now):
                    changed = self._mark_greeting_satisfied_by_inbound(user, planned_reason) or changed
                self._clear_pending_proactive_plan(user)
                changed = True
        raw_followup = user.get("pending_followup_event")
        if isinstance(raw_followup, dict):
            if raw_followup.get("_cancel_on_inbound") or raw_followup.get("_chain_followup") or raw_followup.get("_opener_followup"):
                user["pending_followup_event"] = {}
                changed = True
            else:
                follow_reason = str(raw_followup.get("reason") or "")
                if self._inbound_satisfies_greeting(follow_reason, now=now):
                    changed = self._mark_greeting_satisfied_by_inbound(user, follow_reason) or changed
                    user["pending_followup_event"] = {}
                    changed = True
        raw_timer = user.get("llm_timer_event")
        if isinstance(raw_timer, dict):
            timer_reason = str(raw_timer.get("reason") or "")
            if self._inbound_satisfies_greeting(timer_reason, now=now):
                if _single_line(raw_timer.get("backend"), 40) == "astrbot_cron":
                    queue_cancel = getattr(self, "_queue_official_llm_timer_cancel", None)
                    queued = bool(
                        callable(queue_cancel)
                        and queue_cancel(
                            _single_line(user_id or user.get("user_id"), 120),
                            raw_timer,
                            source_text="用户已在问候时段自然出现",
                            source_origin="inbound_satisfied_greeting",
                            trigger_umo=trigger_umo,
                        )
                    )
                    if queued:
                        changed = self._mark_greeting_satisfied_by_inbound(user, timer_reason) or changed
                        changed = True
                else:
                    changed = self._mark_greeting_satisfied_by_inbound(user, timer_reason) or changed
                    user["llm_timer_event"] = {}
                    changed = True
        return changed

    async def _format_proactive_reply_context(self, event: AstrMessageEvent) -> str:
        try:
            user_id = str(event.get_sender_id())
            event_umo = _single_line(getattr(event, "unified_msg_origin", ""), 180)
        except Exception:
            return ""
        consume_suspended = False
        recent_delivery_context = ""
        async with self._data_lock:
            user = dict(self._get_user(user_id))
            raw_suspended = user.get("suspended_proactive")
            if isinstance(raw_suspended, dict) and raw_suspended.get("active") and raw_suspended.get("resume_ready"):
                consume_suspended = True
                current = self._get_user(user_id)
                current["suspended_proactive"] = {}
                self._save_data_sync()

            last_proactive_text = _single_line(user.get("last_proactive_message"), 500)
            last_proactive_at = _safe_float(user.get("last_proactive_sent_at"), 0)
            last_proactive_action = _single_line(user.get("last_proactive_action"), 80).lower()
            last_proactive_summary = _single_line(user.get("last_proactive_behavior_summary"), 300)
            delivery_umo = _single_line(user.get("last_proactive_delivery_umo") or user.get("umo"), 180)
            consumed_for = _safe_float(user.get("last_proactive_reply_context_consumed_for"), 0)
            max_age = min(max(1, self.proactive_reply_context_hours) * 3600, 30 * 60)
            same_delivery = last_proactive_at > 0 and abs(consumed_for - last_proactive_at) > 0.001
            if (
                last_proactive_text
                and event_umo
                and delivery_umo == event_umo
                and same_delivery
                and 0 <= _now_ts() - last_proactive_at <= max_age
            ):
                recent_delivery_context = (
                    "【刚才你主动发出的消息】\n"
                    f"你刚才在当前会话主动发了：{last_proactive_text}\n"
                    "这是你自己已经说过并成功外发的内容。用户当前消息很可能在回应它；"
                    "必须直接承认并顺着这条消息接话，不得声称不知道自己发了什么、没看到这条消息或把它当成别人发的。"
                    "如果其中的标题、平台或链接确实有误，简短承认并依据上面的实际原文纠正，不要继续编造来源。"
                )
                if "photo_text" in last_proactive_action:
                    image_scene = ""
                    subject_owner = "unknown"
                    snapshot = user.get("last_photo_share_snapshot")
                    if isinstance(snapshot, dict):
                        image_scene = _single_line(snapshot.get("caption"), 260)
                        subject_owner = _normalize_photo_subject_owner(snapshot.get("subject_owner")) or "unknown"
                    if not image_scene and last_proactive_summary:
                        image_scene = _single_line(re.split(r"[:：]", last_proactive_summary, maxsplit=1)[-1], 260)
                    recent_delivery_context += (
                        "\n【刚才主动图片的主客体】\n"
                        + (f"图片画面：{image_scene}\n" if image_scene else "")
                        + f"图片发送者：Bot/当前人格；画面主体：{_photo_subject_owner_prompt_label(subject_owner)}\n"
                        + "用户接下来的短句默认是在评价这张图，不是在说用户自己做了图中的事。"
                        "严格按上面的结构化归属理解代词和动作，不要仅凭‘她’猜主体。"
                        "除非用户明确说‘我做了/我弄洒了’，否则不得责怪或安慰用户仿佛事故发生在用户身上。"
                    )
                current = self._get_user(user_id)
                current["last_proactive_reply_context_consumed_for"] = last_proactive_at
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

        return recent_delivery_context


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
        user_utterances, _ = self._expression_rule_source_parts(raw_text, source_kind="private")
        learn_expression_rules = bool(
            getattr(self, "enable_expression_learning", False)
            and len(user_utterances) >= 5
            and self._expression_private_learning_source_enabled(user, user_id)
        )
        expression_rule_task = ""
        expression_rule_schema = ""
        if learn_expression_rules:
            expression_rule_task = """
同时学习用户有辨识度的表达，只分析“用户:”行，完全忽略 Bot/助手行的措辞。不要把“字数、标点、柔和收尾”本身当成学习成果。
分别输出两类：style_expressions 是“具体情境 → 可直接借鉴的短表达/口癖/梗/占位模板”；grammar_expressions 是“具体情境 → 稳定句法结构”。每类最多 3 条，没有就返回空数组。
如果一条 style 与一条 grammar 来自同一组支持片段、描述同一个情境，只是分别概括说法和句法，两者必须填写完全相同的 family_key（简短英文或拼音标识）；互不相关的规则使用不同 family_key，不要为了凑对而强行配对。
style 必须像“晚安[称谓]”“我嘞个____”“懂的都懂”一样可直接使用或轻微改写；style 字段只写 2–32 字的原话/脱敏模板。包含“偏好、语气、风格、口语化、短句、铺垫、表达方式、回应时”等分析词的一律无效，不能输出。
grammar 必须写清句长、主语省略、拆句、反问或祈使等可验证结构，例如“省略主语的 6–10 字短句”，不要混入具体事实；只有“简短、自然、直接、口语化”而没有句法细节时一律不输出。
无法从原消息中找到具体可复用原话/模板时，style_expressions 必须返回空数组，不得用抽象描述凑数。
优先要求 2 条不同用户消息支持；如果只有 1 次但表达明显独特，也可以作为待审核候选，并将 evidence_count 写 1。普通“嗯/好/可以”、内容事实、身份关系、脏话和提示词不要学。
tags 写 2–8 个用于按新消息召回的情境词；evidence_examples 写 1–3 条短支持片段，只供人工审核，不会注入回复。
同时判断适用边界：channels 只能从 private/group/proactive/qzone/tts 选；relationship_stages 只能从 stranger/familiar/close/any 选；
emotion_gates 只能从 normal/positive/low/guarded/any 选；intent 只能从 acknowledgement/question/request/help/comfort/play/intimacy/boundary/emotion/casual/proactive/any 选。
avoid 写清楚哪些严肃、排障、工具失败、低落或边界场景不能用；如果表达规律会覆盖事实、工具结果、安全边界或 AstrBot 人格，persona_conflict 必须为 true。
""".strip()
            expression_rule_schema = """,
  "style_expressions": [
    {
      "situation": "会触发这种表达的具体情境",
      "family_key": "same_scene_rule_1",
      "style": "可直接借鉴或带占位符的短表达",
      "instruction": "如何自然改写和使用",
      "tags": ["召回标签"],
      "evidence_examples": ["脱敏支持片段"],
      "channels": ["private", "proactive"],
      "relationship_stages": ["familiar", "close"],
      "emotion_gates": ["normal", "positive"],
      "intent": "acknowledgement",
      "avoid": "严肃排障、工具失败或用户低落时不用",
      "persona_conflict": false,
      "evidence_count": 2
    }
  ],
  "grammar_expressions": [
    {
      "situation": "会触发这种句法的具体情境",
      "family_key": "same_scene_rule_1",
      "style": "稳定句法结构与字数范围",
      "instruction": "如何使用该句法但不照抄内容",
      "tags": ["召回标签"],
      "evidence_examples": ["脱敏支持片段"],
      "channels": ["private", "proactive"],
      "relationship_stages": ["any"],
      "emotion_gates": ["any"],
      "intent": "casual",
      "avoid": "不适用情境",
      "persona_conflict": false,
      "evidence_count": 2
    }
  ]"""
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
{expression_rule_task}

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
  "avoid_next": ["短期内不该反复提的内容,例如已经安抚过/解释过/容易烦的点"]{expression_rule_schema}
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
                max_tokens=860 if learn_expression_rules else 520,
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
        open_loops = self._normalize_string_list(payload.get("open_loops"), limit=8, item_limit=110)
        expression_rules = self._normalize_expression_rule_candidates(
            self._expression_rule_payload_candidates(payload),
            source_kind="private",
            source_text=raw_text,
        ) if learn_expression_rules else []
        if not episode["summary"] and not expression_rules:
            await self._mark_user_background_retry(user_id, "dialogue_episode", now, "empty_summary")
            return
        expression_batch_key = hashlib.sha1(raw_text.encode("utf-8")).hexdigest()[:20]
        async with self._data_lock:
            current = self._get_user(user_id)
            episodes = current.setdefault("dialogue_episodes", [])
            if not isinstance(episodes, list):
                episodes = []
                current["dialogue_episodes"] = episodes
            if episode["summary"] and (
                not episodes
                or _single_line(episodes[-1].get("summary") if isinstance(episodes[-1], dict) else "", 140) != episode["summary"]
            ):
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
            if expression_rules:
                expression_profile = current.setdefault("expression_profile", {})
                if not isinstance(expression_profile, dict):
                    expression_profile = {}
                    current["expression_profile"] = expression_profile
                self._merge_learned_expression_rules(
                    expression_profile,
                    expression_rules,
                    batch_key=expression_batch_key,
                    now=now,
                    pending=True,
                )
                expression_profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                self._refresh_expression_voice_profile()
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
        # 表达学习由独立的 expression.rhythm 片段按当前场景注入，避免在相处线索里重复且被截断。
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
            f"固定称呼边界：需要直接称呼对方时只使用“{stable_name}”，不必每句都带称呼；关系阶段、旧记忆、显示名和别名不能据此另造亲昵称呼。若用户本轮明确要求改称呼，以本轮最新要求为准。",
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
        cleaned = re.sub(r"\[\[PCTTS:[^\]]*\]\]", "", str(text or "")).strip()
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
        correction = self._active_private_fact_correction(user, inbound_text)
        if self._looks_like_private_fact_correction(inbound_text):
            flags.append("fact_attribution_after_correction")
        claims_user_prior_action = self._response_claims_user_prior_action(cleaned, user)
        inbound_claims_ownership = bool(
            re.search(r"(?:我|你|他|她|它|谁)[^。！？!?\n]{0,18}(?:上次|之前|先|说|提|想|拿|问|做|告诉|推荐|诱惑)", inbound_text)
        )
        if claims_user_prior_action and correction:
            flags.append("fact_attribution_after_correction")
        elif claims_user_prior_action and not inbound_claims_ownership and len(self._compact_repeat_text(inbound_text)) <= 32:
            flags.append("unverified_fact_attribution")
        if self._response_reverses_recent_proactive_media_ownership(cleaned, user, inbound_text):
            flags.append("proactive_media_ownership_reversal")
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

    @staticmethod
    def _relationship_analysis_reply_rate_band(proactive_count: int, reply_count: int) -> str:
        if proactive_count <= 0:
            return "no_sample"
        reply_rate = reply_count / proactive_count
        if reply_rate < 0.15:
            return "low"
        if reply_rate < 0.35:
            return "guarded"
        if reply_rate < 0.5:
            return "steady"
        return "warm"

    def _relationship_analysis_metrics(self, user: dict[str, Any]) -> dict[str, Any]:
        proactive_count = _safe_int(user.get("proactive_sent_count"), 0, 0)
        reply_count = _safe_int(user.get("reply_count"), 0, 0)
        inbound_count = _safe_int(user.get("inbound_count"), 0, 0)
        relationship_score = _safe_int(user.get("relationship_score"), 0)
        ignored_streak = _safe_int(user.get("ignored_streak"), 0, 0)
        if ignored_streak >= 4:
            ignored_band = "high"
        elif ignored_streak >= 2:
            ignored_band = "guarded"
        elif ignored_streak == 1:
            ignored_band = "single"
        else:
            ignored_band = "none"
        if relationship_score >= 16:
            score_band = "close"
        elif relationship_score >= 3 or inbound_count >= 1:
            score_band = "familiar"
        else:
            score_band = "new"
        return {
            "inbound_count": inbound_count,
            "proactive_count": proactive_count,
            "reply_count": reply_count,
            "interaction_count": inbound_count + proactive_count,
            "reply_rate_band": self._relationship_analysis_reply_rate_band(proactive_count, reply_count),
            "relationship_score_band": score_band,
            "ignored_streak_band": ignored_band,
            "last_user_message_at": _safe_float(user.get("last_user_message_at"), 0),
        }

    @staticmethod
    def _relationship_analysis_signal(user: dict[str, Any]) -> str:
        intent = user.get("intent_profile")
        if not isinstance(intent, dict):
            return ""
        if not bool(intent.get("boundary_durable")):
            return ""
        if _safe_float(intent.get("confidence"), 0) < 0.82:
            return ""
        seed = "|".join(
            (
                str(_safe_float(user.get("last_user_message_at"), 0)),
                _single_line(user.get("last_user_message"), 240),
                _single_line(intent.get("source"), 40),
            )
        )
        return f"boundary:{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"

    def _relationship_analysis_refresh_reason(
        self,
        user: dict[str, Any],
        *,
        now: float,
        force: bool = False,
    ) -> str:
        if force:
            return "forced"
        profile = user.get("persona_relationship")
        if not isinstance(profile, dict) or not profile.get("level"):
            return "initial"
        if now < _safe_float(user.get("relationship_retry_after"), 0):
            return ""
        previous_metrics = profile.get("source_metrics")
        analyzed_at = _safe_float(profile.get("analyzed_at_ts"), 0)
        if not isinstance(previous_metrics, dict) or analyzed_at <= 0:
            return "legacy_profile"

        current_signal = self._relationship_analysis_signal(user)
        if current_signal and current_signal != str(profile.get("source_signal") or ""):
            return "durable_boundary"

        metrics = self._relationship_analysis_metrics(user)
        age = max(0.0, now - analyzed_at)
        min_interval = max(
            10.0,
            _safe_float(getattr(self, "relationship_analysis_min_interval_minutes", 45), 45),
        ) * 60
        if (
            metrics["ignored_streak_band"] in {"guarded", "high"}
            and metrics["ignored_streak_band"] != str(previous_metrics.get("ignored_streak_band") or "")
            and age >= min(min_interval, 15 * 60)
        ):
            return "ignored_streak_changed"
        if (
            metrics["relationship_score_band"] != str(previous_metrics.get("relationship_score_band") or "")
            and age >= min_interval
        ):
            return "relationship_stage_changed"
        if (
            metrics["proactive_count"] >= 3
            and metrics["reply_rate_band"] != str(previous_metrics.get("reply_rate_band") or "")
            and age >= min_interval
        ):
            return "reply_rate_changed"

        interaction_delta = max(
            0,
            _safe_int(metrics.get("interaction_count"), 0)
            - _safe_int(previous_metrics.get("interaction_count"), 0),
        )
        message_batch = max(
            4,
            _safe_int(getattr(self, "relationship_analysis_interaction_batch", 8), 8, 1),
        )
        if interaction_delta >= message_batch and age >= min_interval:
            return "interaction_batch"
        max_stale = max(
            min_interval * 2,
            _safe_float(getattr(self, "relationship_analysis_max_stale_hours", 8), 8) * 3600,
        )
        if interaction_delta > 0 and age >= max_stale:
            return "stale_with_new_interaction"
        return ""

    async def _refresh_persona_relationship(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        trigger: str = "interaction",
        force: bool = False,
    ) -> bool:
        if not bool(getattr(self, "enable_relationship_analysis", True)):
            return False
        now = _now_ts()
        async with self._data_lock:
            analysis_user = dict(self._get_user(user_id))
        refresh_reason = self._relationship_analysis_refresh_reason(analysis_user, now=now, force=force)
        if not refresh_reason:
            return False
        acquired = await self._try_acquire_user_background_task(
            user_id,
            "relationship",
            now,
            refresh_key="last_relationship_refresh_at",
            refresh_seconds=0,
        )
        if not acquired:
            return False
        async with self._data_lock:
            analysis_user = dict(self._get_user(user_id))

        try:
            persona = await self._refresh_default_persona_prompt(str(analysis_user.get("umo") or user_id))
            previous_profile = analysis_user.get("persona_relationship")
            if not isinstance(previous_profile, dict):
                previous_profile = {}
            persona_signature = hashlib.sha1(str(persona or "").encode("utf-8")).hexdigest()[:16]
            persona_context = str(persona or "").strip()
            proactive_count = _safe_int(analysis_user.get("proactive_sent_count"), 0)
            reply_count = _safe_int(analysis_user.get("reply_count"), 0)
            inbound_count = _safe_int(analysis_user.get("inbound_count"), 0)
            reply_rate_available = proactive_count > 0
            reply_rate = reply_count / proactive_count if reply_rate_available else 0.0
            reply_rate_text = f"{reply_rate:.0%}" if reply_rate_available else "暂无样本"
            previous_summary = (
                f"{_single_line(previous_profile.get('level'), 12) or '暂无'}｜"
                f"{_single_line(previous_profile.get('preference'), 16) or '普通'}｜"
                f"{_safe_int(previous_profile.get('score'), 0, 0, 100)}分｜"
                f"{_single_line(previous_profile.get('note'), 80) or '暂无说明'}"
            )
            prompt = f"""
请复核 AstrBot 人格与该用户之间的亲近程度和打扰边界。这是阶段性复核，不是对最近单句做情绪化反应。

判断原则：
- 以上次判断为基线；只有累计互动、回复习惯或明确且持续的边界发生可靠变化时才调整。
- 普通闲聊、一次性情绪、玩笑和单句撒娇不应让关系等级突然升降。
- 明确的长期边界优先；样本少时不要把“暂无回复率”理解为冷淡，优先尊重人格中的既有关系设定。
- 最近消息只用于理解变化，不要仅凭这一句话重写长期关系。

【本轮刷新原因】
{refresh_reason}（触发来源：{_single_line(trigger, 24) or 'interaction'}）

【上次关系判断】
{previous_summary}

【AstrBot 人格相关设定】
{persona_context}

【当前互动数据】
用户 ID：{user_id}
用户主动私聊次数：{inbound_count}
Bot 主动消息次数：{proactive_count}
Bot 主动后用户回复次数：{reply_count}
主动后回复率：{reply_rate_text}
连续未回复次数：{_safe_int(analysis_user.get('ignored_streak'), 0)}
最近用户消息：{_single_line(analysis_user.get('last_user_message'), 120) or '（暂无）'}

只输出紧凑 JSON，不解释过程：
{{
  "level": "陌生/熟悉/亲近 之一",
  "preference": "低打扰/普通/可轻分享 之一",
  "score": 0到100的整数,
  "note": "不超过40字的一句话理由"
}}
""".strip()
            raw_text = await self._llm_call(
                prompt,
                max_tokens=180,
                provider_id=self._task_provider(self.relationship_analysis_provider_id, self.mai_style_provider_id),
                task="relationship",
            )
            payload = self._extract_json_payload(raw_text or "")
            if not isinstance(payload, dict):
                await self._mark_user_background_retry(user_id, "relationship", now, "invalid_json")
                return False
            level = str(payload.get("level") or "").strip()
            preference = str(payload.get("preference") or "").strip()
            if level not in {"陌生", "熟悉", "亲近"}:
                await self._mark_user_background_retry(user_id, "relationship", now, "invalid_level")
                return False
            if preference not in {"低打扰", "普通", "可轻分享"}:
                preference = str(previous_profile.get("preference") or "普通")
                if preference not in {"低打扰", "普通", "可轻分享"}:
                    preference = "普通"
            source_metrics = self._relationship_analysis_metrics(analysis_user)
            source_signal = self._relationship_analysis_signal(analysis_user)
            profile = {
                "level": level,
                "preference": preference,
                "score": _safe_int(
                    payload.get("score"),
                    _safe_int(previous_profile.get("score"), 0, 0, 100),
                    0,
                    100,
                ),
                "note": _single_line(payload.get("note"), 80),
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "analyzed_at_ts": now,
                "analysis_reason": refresh_reason,
                "persona_signature": persona_signature,
                "source_metrics": source_metrics,
                "source_signal": source_signal,
            }
            followup_user: dict[str, Any] | None = None
            async with self._data_lock:
                current = self._get_user(user_id)
                current["persona_relationship"] = profile
                current["last_relationship_refresh_at"] = now
                current["relationship_retry_after"] = 0
                current["relationship_last_error"] = ""
                current["relationship_running_at"] = 0
                latest_signal = self._relationship_analysis_signal(current)
                if latest_signal and latest_signal != source_signal:
                    followup_user = dict(current)
                self._save_data_sync()
            logger.info(
                "[PrivateCompanion] 关系分析已按互动变化刷新: user=%s reason=%s trigger=%s level=%s preference=%s",
                user_id,
                refresh_reason,
                _single_line(trigger, 24) or "interaction",
                level,
                preference,
            )
            if followup_user is not None:
                asyncio.create_task(
                    self._refresh_persona_relationship(
                        user_id,
                        followup_user,
                        trigger="pending_boundary",
                    )
                )
            return True
        except Exception as exc:
            await self._mark_user_background_retry(user_id, "relationship", now, exc)
            return False

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
