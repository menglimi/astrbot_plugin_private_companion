# -*- coding: utf-8 -*-
"""
ProactiveEngineMixin — 主动行为候选、决策、计划事件与动作选择
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
from .helpers import _date_key, _now_ts, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks, _today_key, normalize_legacy_tag_text
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




class ProactiveEngineMixin:
    """主动行为候选、决策、计划事件与动作选择"""

    def _proactive_candidate_pool(self) -> list[dict[str, Any]]:
        raw = self.data.setdefault("proactive_candidate_pool", [])
        if not isinstance(raw, list):
            raw = []
            self.data["proactive_candidate_pool"] = raw
        return raw

    def _pending_proactive_candidate_limit(self, user: dict[str, Any] | None = None) -> int:
        if not isinstance(user, dict):
            return 200
        override = _safe_int(user.get("pending_proactive_candidate_limit"), -1, -1)
        return override if override > 0 else 200

    def _candidate_user_id(self, item: dict[str, Any]) -> str:
        if not isinstance(item, dict):
            return ""
        return _single_line(item.get("user_id") or item.get("target_user_id") or item.get("id"), 40)

    @staticmethod
    def _pending_candidate_status(status: str) -> bool:
        normalized = _single_line(status, 24).lower()
        return normalized != "sent"

    def _planned_candidate_ids_by_user(self) -> dict[str, str]:
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        planned: dict[str, str] = {}
        for user_id, user in users.items():
            if not isinstance(user, dict):
                continue
            candidate_id = _single_line(user.get("planned_candidate_id"), 40)
            if candidate_id:
                planned[str(user_id)] = candidate_id
        return planned

    def _trim_proactive_candidate_total(self, items: list[dict[str, Any]], *, limit: int = 2000) -> list[dict[str, Any]]:
        if len(items) <= limit:
            return items
        planned_ids = set(self._planned_candidate_ids_by_user().values())
        protected = [
            item for item in items
            if _single_line(item.get("id"), 40) in planned_ids
        ]
        protected_ids = {_single_line(item.get("id"), 40) for item in protected}
        remaining = [
            item for item in items
            if _single_line(item.get("id"), 40) not in protected_ids
        ]
        keep_count = max(0, limit - len(protected))
        trimmed = remaining[-keep_count:] if keep_count else []
        result = protected + trimmed
        result.sort(
            key=lambda item: max(
                _safe_float(item.get("updated_ts"), 0),
                _safe_float(item.get("created_ts"), 0),
                _safe_float(item.get("scheduled_ts"), 0),
                _safe_float(item.get("last_seen_ts"), 0),
            )
        )
        return result[-limit:]

    def _candidate_trim_priority(self, item: dict[str, Any], *, planned_candidate_id: str = "") -> tuple[int, int, int, float]:
        status = _single_line(item.get("status"), 24).lower()
        note = _single_line(item.get("note"), 160)
        item_id = _single_line(item.get("id"), 40)
        updated = _safe_float(item.get("updated_ts"), 0)
        created = _safe_float(item.get("created_ts"), 0)
        scheduled = _safe_float(item.get("scheduled_ts"), 0)
        last_seen = _safe_float(item.get("last_seen_ts"), 0)
        repeat_count = _safe_int(item.get("repeat_count"), 1, 1)
        protected = item_id and planned_candidate_id and item_id == planned_candidate_id
        status_rank = {
            "failed": 0,
            "cancelled": 1,
            "dropped": 2,
            "blocked": 3,
            "deferred": 4,
            "accepted": 6,
        }.get(status, 5)
        note_penalty = 0 if note else 1
        freshness = max(updated, scheduled, last_seen, created)
        return (1 if protected else 0, status_rank, repeat_count + note_penalty, freshness)

    def _apply_per_user_pending_candidate_cap(
        self,
        items: list[dict[str, Any]],
        *,
        pending_cap: int | None = None,
        target_user_id: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        planned_ids = self._planned_candidate_ids_by_user()
        grouped: dict[str, list[dict[str, Any]]] = {}
        passthrough: list[dict[str, Any]] = []
        removed = 0
        target = str(target_user_id or "").strip()
        for item in items:
            if not isinstance(item, dict):
                continue
            user_id = self._candidate_user_id(item)
            if not user_id:
                passthrough.append(item)
                continue
            if target and user_id != target:
                passthrough.append(item)
                continue
            grouped.setdefault(user_id, []).append(item)
        kept: list[dict[str, Any]] = list(passthrough)
        for user_id, user_items in grouped.items():
            user = users.get(user_id) if isinstance(users, dict) else None
            limit = pending_cap if pending_cap is not None else self._pending_proactive_candidate_limit(user if isinstance(user, dict) else None)
            if limit <= 0:
                kept.extend(user_items)
                continue
            pending_items = [item for item in user_items if self._pending_candidate_status(str(item.get("status") or ""))]
            sent_items = [item for item in user_items if not self._pending_candidate_status(str(item.get("status") or ""))]
            if len(pending_items) > limit:
                planned_candidate_id = planned_ids.get(user_id, "")
                pending_items.sort(
                    key=lambda item: self._candidate_trim_priority(item, planned_candidate_id=planned_candidate_id),
                    reverse=True,
                )
                trimmed_pending = pending_items[:limit]
                removed += max(0, len(pending_items) - len(trimmed_pending))
                pending_items = sorted(
                    trimmed_pending,
                    key=lambda item: max(
                        _safe_float(item.get("updated_ts"), 0),
                        _safe_float(item.get("created_ts"), 0),
                        _safe_float(item.get("scheduled_ts"), 0),
                    ),
                )
            kept.extend(sent_items)
            kept.extend(pending_items)
        kept.sort(
            key=lambda item: max(
                _safe_float(item.get("updated_ts"), 0),
                _safe_float(item.get("created_ts"), 0),
                _safe_float(item.get("scheduled_ts"), 0),
                _safe_float(item.get("last_seen_ts"), 0),
            )
        )
        return kept, removed

    def _shrink_user_proactive_candidates(
        self,
        user_id: str,
        *,
        pending_cap: int | None = None,
        note: str = "",
    ) -> int:
        target_user_id = str(user_id or "").strip()
        if not target_user_id:
            return 0
        current = [item for item in self._proactive_candidate_pool() if isinstance(item, dict)]
        kept, removed = self._apply_per_user_pending_candidate_cap(
            current,
            pending_cap=pending_cap,
            target_user_id=target_user_id,
        )
        if removed > 0:
            self.data["proactive_candidate_pool"] = kept
            logger.info(
                "[PrivateCompanion] 主动候选自动收缩: user=%s removed=%s cap=%s note=%s",
                target_user_id,
                removed,
                pending_cap or "default",
                _single_line(note, 120),
            )
        return removed

    def _cleanup_proactive_candidate_pool(self, *, now: float | None = None) -> list[dict[str, Any]]:
        now = now or _now_ts()
        kept: list[dict[str, Any]] = []
        for item in self._proactive_candidate_pool():
            if not isinstance(item, dict):
                continue
            created = _safe_float(item.get("created_ts"), 0)
            scheduled = _safe_float(item.get("scheduled_ts"), 0)
            status = str(item.get("status") or "")
            ttl = 36 * 3600 if status in {"accepted", "sent"} else 18 * 3600
            anchor = max(created, scheduled)
            if anchor > 0 and now - anchor <= ttl:
                kept.append(item)
        kept, _ = self._apply_per_user_pending_candidate_cap(kept)
        self.data["proactive_candidate_pool"] = self._trim_proactive_candidate_total(kept, limit=2000)
        return self.data["proactive_candidate_pool"]

    def _proactive_impulse_pool(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        raw = user.get("proactive_impulses")
        if not isinstance(raw, list):
            raw = []
            user["proactive_impulses"] = raw
        return raw

    def _cleanup_proactive_impulses(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        check_now = _now_ts() if now is None else now
        kept: list[dict[str, Any]] = []
        for item in self._proactive_impulse_pool(user):
            if not isinstance(item, dict):
                continue
            created = _safe_float(item.get("created_ts"), 0)
            updated = _safe_float(item.get("updated_ts"), created)
            state = str(item.get("state") or "queued").strip().lower()
            expire_at = _safe_float(item.get("expire_at"), 0)
            if state in {"sent", "blocked", "cancelled", "dropped"}:
                if max(created, updated, expire_at) > 0 and check_now - max(created, updated, expire_at) <= 12 * 3600:
                    kept.append(item)
                continue
            if expire_at > 0 and check_now - expire_at > 2 * 3600:
                continue
            if created > 0 and check_now - created > 48 * 3600:
                continue
            kept.append(item)
        user["proactive_impulses"] = kept[-16:]
        return user["proactive_impulses"]

    def _proactive_impulse_signature(self, item: dict[str, Any]) -> str:
        return self._proactive_topic_signature(
            item.get("reason"),
            item.get("source"),
            item.get("topic"),
            item.get("motive"),
        )

    def _proactive_impulse_default_window_seconds(self, reason: str) -> tuple[float, float]:
        if reason in {"morning_greeting", "noon_greeting", "evening_greeting"}:
            return 70 * 60.0, 35 * 60.0
        if reason in {"group_share", "news_share", "bili_video_share", "web_exploration_share"}:
            return 45 * 60.0, 60 * 60.0
        if reason in {"quiet_care", "check_in", "state_share"}:
            return 55 * 60.0, 80 * 60.0
        return 50 * 60.0, 70 * 60.0

    def _event_time_window_bounds(
        self,
        event: dict[str, Any],
        *,
        reason: str,
        now: float | None = None,
    ) -> tuple[float, float, float, float]:
        check_now = _now_ts() if now is None else now
        preferred_ts = _safe_float(event.get("_scheduled_ts"), 0)
        start_ts = preferred_ts
        end_ts = 0.0
        window = str(event.get("window") or "").strip()
        if window:
            start_minute, end_minute = self._parse_window_minutes(window)
            if start_minute is not None and end_minute is not None:
                when = self._environment_fromtimestamp(check_now)
                date_text = str(event.get("date") or "").strip()
                try:
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
                        base_date = datetime.strptime(date_text, "%Y-%m-%d").date()
                    else:
                        base_date = when.date()
                except Exception:
                    base_date = when.date()
                tzinfo = when.tzinfo
                start_dt = datetime.combine(base_date, datetime.min.time(), tzinfo=tzinfo) + timedelta(minutes=start_minute)
                end_dt = datetime.combine(base_date, datetime.min.time(), tzinfo=tzinfo) + timedelta(minutes=end_minute)
                start_ts = start_dt.timestamp()
                end_ts = end_dt.timestamp()
        if preferred_ts <= 0:
            preferred_ts = start_ts if start_ts > 0 else check_now + 60
        if start_ts <= 0:
            start_ts = preferred_ts
        active_span, grace_span = self._proactive_impulse_default_window_seconds(reason)
        if end_ts <= 0:
            end_ts = max(start_ts + 60.0, preferred_ts + active_span)
        expire_at = max(end_ts + grace_span, preferred_ts + 5 * 60.0)
        return start_ts, preferred_ts, end_ts, expire_at

    def _build_proactive_impulse(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str,
        topic: str,
        source: str,
        window_start_at: float,
        preferred_ts: float,
        best_until_at: float,
        expire_at: float,
        chain: list[dict[str, Any]] | None = None,
        trigger_message_id: str = "",
        trigger_umo: str = "",
        trigger_ts: float = 0,
        quota_exempt: bool = False,
        context_key: str = "",
        context: Any = None,
        opener_mode: str = "",
        followup_kind: str = "",
    ) -> dict[str, Any]:
        role = self._private_user_role(user)
        impulse_reason = _single_line(reason, 40) or "check_in"
        impulse_action = _single_line(action, 40) or "message"
        impulse_topic = _single_line(topic, 80)
        impulse_motive = self._normalize_internal_motive_text(_single_line(motive, 180))
        salience = 0.54
        warmth = 0.46
        urgency = 0.38
        if source in {"followup", "timer", "pending_followup"}:
            salience += 0.2
            urgency += 0.12
        elif source in {"story", "event"}:
            salience += 0.12
        elif source == "random":
            warmth += 0.06
        if impulse_reason in {"morning_greeting", "noon_greeting", "evening_greeting"}:
            warmth += 0.1
            urgency += 0.08
        if impulse_reason in {"quiet_care", "important_date_share"}:
            warmth += 0.16
        if role == "friend":
            warmth = max(0.18, warmth - 0.08)
            urgency = max(0.16, urgency - 0.04)
        decay_per_hour = 0.06 if source in {"followup", "timer"} else 0.1
        persona_alignment = self._proactive_persona_alignment(
            user,
            reason=impulse_reason,
            action=impulse_action,
            motive=impulse_motive,
            topic=impulse_topic,
            source=source,
        )
        semantics = self._proactive_candidate_semantics(
            user,
            reason=impulse_reason,
            action=impulse_action,
            motive=impulse_motive,
            topic=impulse_topic,
            source=source,
            context=context,
            chain=chain,
            trigger_message_id=trigger_message_id,
            trigger_ts=trigger_ts,
        )
        return {
            "id": uuid.uuid4().hex[:12],
            "created_ts": _now_ts(),
            "updated_ts": _now_ts(),
            "state": "queued",
            "source": _single_line(source, 40) or "random",
            "reason": impulse_reason,
            "action": impulse_action,
            "topic": impulse_topic,
            "motive": impulse_motive,
            "window_start_at": max(0.0, float(window_start_at or preferred_ts or _now_ts())),
            "preferred_ts": max(0.0, float(preferred_ts or window_start_at or _now_ts())),
            "best_until_at": max(float(best_until_at or preferred_ts or _now_ts()), float(window_start_at or 0.0)),
            "expire_at": max(float(expire_at or best_until_at or preferred_ts or _now_ts()), float(best_until_at or 0.0)),
            "salience": max(0.0, min(1.0, salience)),
            "warmth": max(0.0, min(1.0, warmth)),
            "urgency": max(0.0, min(1.0, urgency)),
            "decay_per_hour": max(0.01, min(0.5, decay_per_hour)),
            "persona_fit": max(0.0, min(1.0, _safe_float(persona_alignment.get("score"), 0.5))),
            "persona_fit_note": _single_line(persona_alignment.get("note"), 160),
            "persona_fit_blocker": bool(persona_alignment.get("blocker")),
            "semantic_kind": _single_line(semantics.get("kind"), 40),
            "semantic_anchor_type": _single_line(semantics.get("anchor_type"), 40),
            "semantic_score": max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))),
            "semantic_anchor_score": max(0.0, min(1.0, _safe_float(semantics.get("anchor_score"), 0.5))),
            "semantic_pressure": max(0.0, min(1.0, _safe_float(semantics.get("pressure"), 0.4))),
            "semantic_risk": max(0.0, min(1.0, _safe_float(semantics.get("risk"), 0.0))),
            "semantic_note": _single_line(semantics.get("note"), 180),
            "semantic_blocker": bool(semantics.get("blocker")),
            "signature": self._proactive_topic_signature(impulse_reason, source, impulse_topic, impulse_motive),
            "chain": [] if role == "friend" else [dict(item) for item in (chain or []) if isinstance(item, dict)],
            "trigger_message_id": _single_line(trigger_message_id, 120),
            "trigger_umo": _single_line(trigger_umo, 160),
            "trigger_ts": _safe_float(trigger_ts, 0),
            "quota_exempt": bool(quota_exempt),
            "context_key": _single_line(context_key, 60),
            "context": dict(context) if isinstance(context, dict) else context,
            "opener_mode": _single_line(opener_mode, 24),
            "followup_kind": _single_line(followup_kind, 32),
        }

    def _queue_proactive_impulse(
        self,
        user: dict[str, Any],
        impulse: dict[str, Any],
    ) -> dict[str, Any]:
        pool = self._cleanup_proactive_impulses(user)
        signature = self._proactive_impulse_signature(impulse)
        reason = _single_line(impulse.get("reason"), 40)
        source = _single_line(impulse.get("source"), 40)
        for existing in reversed(pool):
            if not isinstance(existing, dict):
                continue
            if str(existing.get("state") or "queued") not in {"queued", "deferred"}:
                continue
            if str(existing.get("reason") or "") != reason:
                continue
            if str(existing.get("source") or "") != source:
                continue
            if not self._topic_signature_similar(signature, str(existing.get("signature") or "")):
                continue
            existing_start = _safe_float(existing.get("window_start_at"), 0)
            incoming_start = _safe_float(impulse.get("window_start_at"), 0)
            existing_preferred = _safe_float(existing.get("preferred_ts"), 0)
            incoming_preferred = _safe_float(impulse.get("preferred_ts"), 0)
            existing["updated_ts"] = _now_ts()
            if existing_start <= 0:
                existing["window_start_at"] = incoming_start
            elif incoming_start > 0:
                existing["window_start_at"] = min(existing_start, incoming_start)
            if existing_preferred <= 0:
                existing["preferred_ts"] = incoming_preferred
            elif incoming_preferred > 0:
                existing["preferred_ts"] = min(existing_preferred, incoming_preferred)
            existing["best_until_at"] = max(
                _safe_float(existing.get("best_until_at"), 0),
                _safe_float(impulse.get("best_until_at"), 0),
            )
            existing["expire_at"] = max(
                _safe_float(existing.get("expire_at"), 0),
                _safe_float(impulse.get("expire_at"), 0),
            )
            existing["salience"] = max(_safe_float(existing.get("salience"), 0.0), _safe_float(impulse.get("salience"), 0.0))
            existing["warmth"] = max(_safe_float(existing.get("warmth"), 0.0), _safe_float(impulse.get("warmth"), 0.0))
            existing["urgency"] = max(_safe_float(existing.get("urgency"), 0.0), _safe_float(impulse.get("urgency"), 0.0))
            existing_fit = _safe_float(existing.get("persona_fit"), 0.0)
            incoming_fit = _safe_float(impulse.get("persona_fit"), 0.0)
            if incoming_fit > 0:
                existing["persona_fit"] = max(existing_fit, incoming_fit)
            if incoming_fit >= existing_fit:
                existing["persona_fit_blocker"] = bool(impulse.get("persona_fit_blocker"))
            elif impulse.get("persona_fit_blocker"):
                existing["persona_fit_blocker"] = True
            if _single_line(impulse.get("persona_fit_note"), 160):
                existing["persona_fit_note"] = _single_line(impulse.get("persona_fit_note"), 160)
            existing_semantic = _safe_float(existing.get("semantic_score"), 0.0)
            incoming_semantic = _safe_float(impulse.get("semantic_score"), 0.0)
            if incoming_semantic >= existing_semantic:
                for key in (
                    "semantic_kind",
                    "semantic_anchor_type",
                    "semantic_score",
                    "semantic_anchor_score",
                    "semantic_pressure",
                    "semantic_risk",
                    "semantic_note",
                    "semantic_blocker",
                ):
                    if key in impulse:
                        existing[key] = impulse.get(key)
            elif impulse.get("semantic_blocker"):
                existing["semantic_blocker"] = True
                existing["semantic_risk"] = max(_safe_float(existing.get("semantic_risk"), 0.0), _safe_float(impulse.get("semantic_risk"), 0.0))
            if _single_line(impulse.get("topic"), 80):
                existing["topic"] = _single_line(impulse.get("topic"), 80)
            if _single_line(impulse.get("motive"), 180):
                existing["motive"] = self._normalize_internal_motive_text(_single_line(impulse.get("motive"), 180))
            if impulse.get("chain"):
                existing["chain"] = [dict(item) for item in impulse.get("chain", []) if isinstance(item, dict)]
            if _single_line(impulse.get("trigger_message_id"), 120):
                existing["trigger_message_id"] = _single_line(impulse.get("trigger_message_id"), 120)
            if _single_line(impulse.get("trigger_umo"), 160):
                existing["trigger_umo"] = _single_line(impulse.get("trigger_umo"), 160)
            if _safe_float(impulse.get("trigger_ts"), 0) > 0:
                existing["trigger_ts"] = _safe_float(impulse.get("trigger_ts"), 0)
            if impulse.get("quota_exempt"):
                existing["quota_exempt"] = True
            if _single_line(impulse.get("context_key"), 60) and isinstance(impulse.get("context"), dict):
                existing["context_key"] = _single_line(impulse.get("context_key"), 60)
                existing["context"] = dict(impulse.get("context"))
            return existing
        item = dict(impulse)
        item["id"] = _single_line(item.get("id"), 20) or uuid.uuid4().hex[:12]
        item["signature"] = signature
        item["state"] = str(item.get("state") or "queued")
        pool.append(item)
        del pool[:-16]
        return item

    def _candidate_to_impulse(
        self,
        user: dict[str, Any],
        candidate: dict[str, Any],
        *,
        source: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(candidate, dict):
            return None
        check_now = _now_ts() if now is None else now
        reason = _single_line(candidate.get("reason"), 40) or "check_in"
        action = _single_line(candidate.get("action"), 40) or "message"
        motive = _single_line(candidate.get("motive"), 180)
        topic = _single_line(candidate.get("topic"), 80)
        window_start_at = _safe_float(candidate.get("window_start_at"), 0)
        preferred_ts = _safe_float(candidate.get("preferred_ts"), 0)
        best_until_at = _safe_float(candidate.get("best_until_at"), 0)
        expire_at = _safe_float(candidate.get("expire_at"), 0)
        if any(value <= 0 for value in (window_start_at, preferred_ts, best_until_at, expire_at)):
            window_start_at, preferred_ts, best_until_at, expire_at = self._event_time_window_bounds(
                candidate,
                reason=reason,
                now=check_now,
            )
        return self._build_proactive_impulse(
            user,
            reason=reason,
            action=action,
            motive=motive,
            topic=topic,
            source=source,
            window_start_at=window_start_at,
            preferred_ts=preferred_ts,
            best_until_at=best_until_at,
            expire_at=expire_at,
            chain=candidate.get("chain") if isinstance(candidate.get("chain"), list) else [],
            trigger_message_id=self._candidate_trigger_message_id(candidate),
            trigger_umo=_single_line(candidate.get("trigger_umo") or candidate.get("umo"), 160),
            trigger_ts=_safe_float(candidate.get("trigger_ts") or candidate.get("created_ts"), 0),
            quota_exempt=bool(candidate.get("_free_screen_peek")),
            context_key=_single_line(candidate.get("context_key"), 60),
            context=candidate.get("context"),
            opener_mode="name_only" if candidate.get("_name_only_opener") else "",
            followup_kind=(
                "suspended_opener"
                if candidate.get("_opener_followup")
                else "chain_followup"
                if candidate.get("_chain_followup")
                else ""
            ),
        )

    def _impulse_ready_now(self, impulse: dict[str, Any], *, now: float | None = None) -> bool:
        check_now = _now_ts() if now is None else now
        return (
            str(impulse.get("state") or "queued") in {"queued", "deferred"}
            and check_now >= _safe_float(impulse.get("window_start_at"), 0)
            and check_now <= _safe_float(impulse.get("expire_at"), 0)
        )

    def _score_proactive_impulse(
        self,
        user: dict[str, Any],
        impulse: dict[str, Any],
        *,
        now: float | None = None,
    ) -> float:
        check_now = _now_ts() if now is None else now
        created = _safe_float(impulse.get("created_ts"), check_now)
        preferred_ts = _safe_float(impulse.get("preferred_ts"), _safe_float(impulse.get("window_start_at"), check_now))
        best_until_at = _safe_float(impulse.get("best_until_at"), preferred_ts)
        age_hours = max(0.0, check_now - created) / 3600.0
        score = (
            _safe_float(impulse.get("salience"), 0.5)
            + _safe_float(impulse.get("urgency"), 0.3) * 0.9
            + _safe_float(impulse.get("warmth"), 0.3) * 0.7
        )
        score -= age_hours * _safe_float(impulse.get("decay_per_hour"), 0.08)
        if preferred_ts > 0:
            score -= min(0.55, abs(check_now - preferred_ts) / 3600.0 * 0.14)
        if best_until_at > 0 and check_now > best_until_at:
            score -= min(0.7, (check_now - best_until_at) / 3600.0 * 0.25)
        if str(impulse.get("source") or "") in {"pending_followup", "followup"} or str(impulse.get("reason") or "") == "quiet_care":
            score += 0.05
        persona_fit = _safe_float(impulse.get("persona_fit"), -1.0)
        if persona_fit < 0:
            persona_alignment = self._proactive_persona_alignment(
                user,
                reason=_single_line(impulse.get("reason"), 40),
                action=_single_line(impulse.get("action"), 40) or "message",
                motive=_single_line(impulse.get("motive"), 180),
                topic=_single_line(impulse.get("topic"), 80),
                source=_single_line(impulse.get("source"), 40),
                now=check_now,
            )
            persona_fit = _safe_float(persona_alignment.get("score"), 0.55)
            impulse["persona_fit"] = persona_fit
            impulse["persona_fit_note"] = _single_line(persona_alignment.get("note"), 160)
            impulse["persona_fit_blocker"] = bool(persona_alignment.get("blocker"))
        score += (persona_fit - 0.6) * 0.36
        if impulse.get("persona_fit_blocker"):
            score -= 0.45
        semantic_score = _safe_float(impulse.get("semantic_score"), -1.0)
        if semantic_score < 0:
            semantics = self._proactive_candidate_semantics(
                user,
                reason=_single_line(impulse.get("reason"), 40),
                action=_single_line(impulse.get("action"), 60) or "message",
                motive=_single_line(impulse.get("motive"), 180),
                topic=_single_line(impulse.get("topic"), 100),
                source=_single_line(impulse.get("source"), 40),
                context=impulse.get("context"),
                chain=impulse.get("chain") if isinstance(impulse.get("chain"), list) else [],
                trigger_message_id=_single_line(impulse.get("trigger_message_id"), 120),
                trigger_ts=_safe_float(impulse.get("trigger_ts"), 0),
            )
            semantic_score = _safe_float(semantics.get("score"), 0.5)
            impulse["semantic_kind"] = _single_line(semantics.get("kind"), 40)
            impulse["semantic_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
            impulse["semantic_score"] = semantic_score
            impulse["semantic_anchor_score"] = _safe_float(semantics.get("anchor_score"), 0.5)
            impulse["semantic_pressure"] = _safe_float(semantics.get("pressure"), 0.4)
            impulse["semantic_risk"] = _safe_float(semantics.get("risk"), 0.0)
            impulse["semantic_note"] = _single_line(semantics.get("note"), 180)
            impulse["semantic_blocker"] = bool(semantics.get("blocker"))
        score += (semantic_score - 0.5) * 0.42
        score -= max(0.0, _safe_float(impulse.get("semantic_pressure"), 0.4) - 0.55) * 0.22
        score -= _safe_float(impulse.get("semantic_risk"), 0.0) * 0.42
        if impulse.get("semantic_blocker"):
            score -= 0.5
        readiness = self._proactive_inner_readiness(user, now=check_now)
        temperature = readiness.get("temperature") if isinstance(readiness.get("temperature"), dict) else {}
        score += (_safe_float(readiness.get("score"), 0.55) - 0.55) * 0.38
        score += (_safe_float(temperature.get("score"), 0.55) - 0.55) * 0.22
        hesitation_count = _safe_int(impulse.get("hesitation_count"), 0, 0, 8)
        if hesitation_count > 0:
            score += min(0.12, hesitation_count * 0.035)
        if _safe_int(user.get("ignored_streak"), 0, 0) >= 2:
            score -= 0.08
        return score

    def _proactive_persona_alignment(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str,
        topic: str = "",
        source: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        role = self._private_user_role(user)
        normalized_reason = str(reason or "check_in")
        normalized_action = str(action or "message").strip() or "message"
        normalized_motive = self._normalize_internal_motive_text(_single_line(motive, 180))
        normalized_topic = _single_line(topic, 80)
        normalized_source = _single_line(source, 40)
        text = f"{normalized_reason} {normalized_action} {normalized_topic} {normalized_motive}"
        profile = self._persona_action_profile()
        score = 0.66
        notes: list[str] = []
        blocker = False

        def note(text_value: str) -> None:
            clean = _single_line(text_value, 60)
            if clean and clean not in notes:
                notes.append(clean)

        intimate = (
            self._proactive_reason_is_intimate(normalized_reason)
            or self._proactive_action_is_intimate(normalized_action)
            or self._proactive_text_is_intimate(normalized_reason, normalized_action, normalized_motive, normalized_topic)
        )
        if role == "friend":
            score += 0.02
            if self._friend_sensitive_proactive_reason(normalized_reason) or self._friend_sensitive_proactive_action(normalized_action):
                blocker = True
                score -= 0.45
                note("朋友关系不适合这个主动来源/能力")
            if intimate:
                score -= 0.22
                note("朋友关系下亲密度偏高")
            if self._is_vague_seek_user_motive(normalized_reason, normalized_action, normalized_motive, normalized_topic):
                score -= 0.12
                note("朋友关系下动机太像索取回应")
        else:
            if intimate and (profile.get("clingy") or profile.get("voicey")):
                score += 0.07
                note("亲近型人格可承载这个主动")
            if self._is_vague_seek_user_motive(normalized_reason, normalized_action, normalized_motive, normalized_topic):
                score -= 0.07
                note("动机略空,需要更具体的生活钩子")

        action_parts = {part.strip() for part in normalized_action.split("+") if part.strip()}
        if "screen_peek" in action_parts:
            if profile.get("observant"):
                score += 0.07
                note("观察型人格适合轻观察")
            else:
                score -= 0.05
                note("观察能力和人格标记不强")
        if "photo_text" in action_parts:
            if profile.get("visual"):
                score += 0.08
                note("视觉表达贴合人格")
            else:
                score -= 0.04
                note("图片表达缺少人格支撑")
        if "voice" in action_parts:
            if profile.get("voicey"):
                score += 0.08
                note("语音表达贴合人格")
            else:
                score -= 0.04
                note("语音表达缺少人格支撑")
        if "poke" in action_parts:
            if profile.get("playful") or profile.get("clingy"):
                score += 0.06
                note("轻互动贴合俏皮/依恋人格")
            else:
                score -= 0.06
                note("戳一戳不像当前人格的自然动作")
        if "jm_cosmos_read" in action_parts and not any(token in str(self._get_default_persona_prompt() or "") for token in ("阅读", "书", "小说", "本子", "夹层")):
            score -= 0.08
            note("私下阅读缺少人格兴趣支撑")

        if normalized_reason in {"activity_share", "diary_share", "background_schedule"}:
            if profile.get("playful") or profile.get("visual") or profile.get("observant"):
                score += 0.04
                note("轻分享和人格气质相容")
        if not normalized_topic and not normalized_motive and normalized_reason in {"check_in", "quiet_care", "state_share"}:
            score -= 0.08
            note("念头缺少具体来源")

        leak_tokens = ("模型", "插件", "action", "模块", "接口", "提示词", "LLM", "prompt", "后台任务", "系统调度")
        if any(token in text for token in leak_tokens):
            score -= 0.28
            blocker = True
            note("内部机制泄露风险")
        worldview_mode = str(getattr(self, "worldview_adaptation_mode", "auto") or "auto")
        if worldview_mode in {"fantasy", "sci_fi", "custom"} and any(token in text for token in ("现实网络", "现实设备", "影响现实", "控制设备")):
            score -= 0.25
            blocker = True
            note("世界观边界风险")

        mode = self._current_emotion_gate_mode(user, now=now) or self._current_relationship_gate_mode(user, now=now)
        if mode in {"careful", "hurt", "refusing", "backoff"} and intimate and normalized_source != "timer":
            score -= 0.22
            if mode in {"refusing", "backoff"}:
                blocker = True
            note(f"关系状态 {mode} 不适合亲密主动")

        score = max(0.0, min(1.0, score))
        if not notes:
            note("动机、动作和当前关系基本贴合")
        return {
            "score": score,
            "note": "；".join(notes[:3]),
            "blocker": blocker,
        }

    def _proactive_candidate_semantics(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str,
        topic: str = "",
        source: str = "",
        context: Any = None,
        chain: list[dict[str, Any]] | None = None,
        trigger_message_id: str = "",
        trigger_ts: float = 0,
    ) -> dict[str, Any]:
        normalized_reason = _single_line(reason, 40) or "check_in"
        normalized_action = _single_line(action, 60) or "message"
        normalized_motive = self._normalize_internal_motive_text(_single_line(motive, 180))
        normalized_topic = _single_line(topic, 100)
        normalized_source = _single_line(source, 40)
        context_text = self._proactive_semantic_evidence_text(context)
        chain_text = self._proactive_semantic_chain_text(chain)
        has_context = bool(context_text)
        has_chain = bool(chain_text)
        has_trigger = bool(_single_line(trigger_message_id, 120))
        text = f"{normalized_reason} {normalized_action} {normalized_topic} {normalized_motive}"
        evidence_text = f"{text} {context_text} {chain_text}"
        action_parts = {part.strip() for part in normalized_action.split("+") if part.strip()}
        kind = "check_in"
        if normalized_reason in {"morning_greeting", "noon_greeting", "evening_greeting", "insomnia_night"}:
            kind = "greeting"
        elif normalized_reason in {"quiet_care", "state_share"}:
            kind = "care"
        elif normalized_reason in {"activity_share", "diary_share", "background_schedule", "creative_share"}:
            kind = "self_share"
        elif normalized_reason in {"important_date_share"}:
            kind = "reminder"
        elif normalized_reason in {"group_share", "bili_video_share", "news_share", "web_exploration_share"}:
            kind = "external_share"
        elif normalized_source in {"pending_followup", "followup"}:
            kind = "continuation"
        elif action_parts & {"screen_peek"}:
            kind = "observation"
        elif action_parts & {"poke"}:
            kind = "light_touch"

        anchor_type = "vague"
        anchor_score = 0.28
        if normalized_source in {"pending_followup", "followup"} or has_trigger or has_chain or "前面提过" in evidence_text:
            anchor_type, anchor_score = "recent_context", 0.78
        elif normalized_reason in {"group_share"} or "群" in evidence_text:
            anchor_type, anchor_score = "group_context", 0.72
        elif normalized_reason in {"diary_share", "creative_share"} or any(token in evidence_text for token in ("日记", "写到", "作品", "片段")):
            anchor_type, anchor_score = "inner_life", 0.68
        elif normalized_reason in {"background_schedule"} or any(token in evidence_text for token in ("手上", "忙到", "日程", "计划", "刚好停")):
            anchor_type, anchor_score = "current_activity", 0.62
        elif normalized_reason in {"important_date_share"} or any(token in evidence_text for token in ("生日", "纪念", "日期", "考试", "提醒")):
            anchor_type, anchor_score = "important_date", 0.78
        elif normalized_reason in {"news_share", "web_exploration_share", "bili_video_share"}:
            anchor_type, anchor_score = "external_info", 0.66
        elif normalized_reason in {"morning_greeting", "noon_greeting", "evening_greeting", "insomnia_night"}:
            anchor_type, anchor_score = "time_ritual", 0.55
        elif any(token in evidence_text for token in ("天气", "雨", "阳光", "晚霞", "天色", "风", "窗")):
            anchor_type, anchor_score = "environment", 0.58
        elif has_context:
            anchor_type, anchor_score = "topic_hint", 0.56
        elif normalized_topic:
            anchor_type, anchor_score = "topic_hint", 0.48

        pressure = 0.34
        if kind in {"greeting", "self_share", "reminder"}:
            pressure -= 0.04
        if kind in {"check_in", "care", "observation", "light_touch"}:
            pressure += 0.08
        if action_parts & {"screen_peek", "poke", "voice"}:
            pressure += 0.16
        if has_context or has_trigger:
            pressure -= 0.05
        if has_chain and normalized_source in {"pending_followup", "followup", "daily_greeting"}:
            pressure -= 0.03
        if self._is_vague_seek_user_motive(normalized_reason, normalized_action, normalized_motive, normalized_topic):
            pressure += 0.18
        if self._private_user_role(user) == "friend":
            pressure += 0.08
        if _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            pressure += min(0.22, _safe_int(user.get("ignored_streak"), 0, 0) * 0.06)

        risk = 0.0
        blocker = False
        notes: list[str] = []

        def note(value: str) -> None:
            clean = _single_line(value, 60)
            if clean and clean not in notes:
                notes.append(clean)

        if anchor_score >= 0.65:
            note(f"由头明确:{anchor_type}")
        elif anchor_score <= 0.35:
            note("由头偏虚")
        if pressure >= 0.62:
            note("打扰压力偏高")
        if self._unverified_social_relay_plan_reason(
            {
                "reason": normalized_reason,
                "action": normalized_action,
                "topic": normalized_topic,
                "motive": normalized_motive,
                "scene": context_text,
            },
            source=normalized_source,
            has_trigger=has_trigger,
        ):
            risk += 0.35
            blocker = True
            note("疑似无来源转述")
        if any(token in evidence_text for token in ("模型", "插件", "接口", "后台", "提示词", "系统调度", "action")):
            risk += 0.42
            blocker = True
            note("内部机制泄露")
        if self._friend_can_receive_proactive_reason(user, normalized_reason, normalized_action) is False:
            risk += 0.35
            blocker = True
            note("朋友关系语义越界")
        if self._proactive_text_is_intimate(normalized_reason, normalized_action, normalized_motive, normalized_topic):
            risk += 0.18
            if self._private_user_role(user) == "friend":
                risk += 0.18
                note("朋友关系亲密过量")
        score = 0.52 + (anchor_score - 0.5) * 0.42 - max(0.0, pressure - 0.45) * 0.36 - risk * 0.5
        if kind in {"continuation", "reminder"}:
            score += 0.08
        if kind == "self_share" and anchor_score >= 0.48:
            score += 0.05
        if has_context and anchor_score >= 0.5:
            score += 0.04
        if kind == "check_in" and anchor_score < 0.45:
            score -= 0.08
        score = max(0.0, min(1.0, score))
        if not notes:
            note(f"{kind}/{anchor_type}")
        return {
            "kind": kind,
            "anchor_type": anchor_type,
            "anchor_score": anchor_score,
            "pressure": max(0.0, min(1.0, pressure)),
            "risk": max(0.0, min(1.0, risk)),
            "score": score,
            "note": "；".join(notes[:4]),
            "blocker": blocker,
        }

    def _proactive_semantic_evidence_text(self, value: Any, *, limit: int = 260) -> str:
        parts: list[str] = []

        def collect(item: Any, depth: int = 0) -> None:
            if len(parts) >= 8 or depth > 2:
                return
            if isinstance(item, dict):
                priority = (
                    "title",
                    "topic",
                    "summary",
                    "text",
                    "content",
                    "reason",
                    "why",
                    "scene",
                    "impulse",
                    "tone",
                    "group_name",
                    "sender_name",
                    "share_decision",
                    "share_tone",
                    "share_boundary",
                )
                for key in priority:
                    if key in item:
                        collect(item.get(key), depth + 1)
                if len(parts) < 4:
                    for key, nested in list(item.items())[:8]:
                        if key not in priority:
                            collect(nested, depth + 1)
            elif isinstance(item, list):
                for nested in item[:5]:
                    collect(nested, depth + 1)
            else:
                text = _single_line(item, 80)
                if text and text not in parts:
                    parts.append(text)

        collect(value)
        return _single_line(" ".join(parts), limit)

    def _proactive_semantic_chain_text(self, chain: list[dict[str, Any]] | None) -> str:
        if not isinstance(chain, list):
            return ""
        parts: list[str] = []
        for step in chain[:4]:
            if not isinstance(step, dict):
                continue
            bits = [
                _single_line(step.get("kind"), 30),
                _single_line(step.get("reason"), 40),
                _single_line(step.get("topic"), 60),
                _single_line(step.get("motive"), 80),
                _single_line(step.get("tone"), 40),
            ]
            line = _single_line(" ".join(bit for bit in bits if bit), 120)
            if line:
                parts.append(line)
        return _single_line(" ".join(parts), 240)

    def _planned_proactive_semantics(self, user: dict[str, Any]) -> dict[str, Any]:
        impulse = self._planned_proactive_impulse(user)
        if isinstance(impulse, dict):
            return {
                "kind": _single_line(impulse.get("semantic_kind"), 40),
                "anchor_type": _single_line(impulse.get("semantic_anchor_type"), 40),
                "score": _safe_float(impulse.get("semantic_score"), 0.5),
                "anchor_score": _safe_float(impulse.get("semantic_anchor_score"), 0.5),
                "pressure": _safe_float(impulse.get("semantic_pressure"), 0.4),
                "risk": _safe_float(impulse.get("semantic_risk"), 0.0),
                "note": _single_line(impulse.get("semantic_note"), 180),
                "blocker": bool(impulse.get("semantic_blocker")),
            }
        return self._proactive_candidate_semantics(
            user,
            reason=self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) or "check_in",
            action=self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=60) or "message",
            motive=_single_line(user.get("planned_proactive_motive"), 180),
            topic=_single_line(user.get("planned_proactive_topic"), 100),
            source=self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40),
            chain=user.get("planned_event_chain") if isinstance(user.get("planned_event_chain"), list) else [],
            trigger_message_id=_single_line(user.get("planned_proactive_trigger_message_id"), 120),
            trigger_ts=_safe_float(user.get("planned_proactive_trigger_ts"), 0),
        )

    def _planned_proactive_persona_alignment(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        return self._proactive_persona_alignment(
            user,
            reason=self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) or "check_in",
            action=self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=40) or "message",
            motive=_single_line(user.get("planned_proactive_motive"), 180),
            topic=_single_line(user.get("planned_proactive_topic"), 80),
            source=self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40),
            now=now,
        )

    def _planned_proactive_model_judge_signature(self, user: dict[str, Any]) -> str:
        persona = _single_line(str(self._get_default_persona_prompt() or ""), 800)
        worldview = _single_line(str(self._format_worldview_adaptation_prompt() or ""), 400)
        rel_state = user.get("relationship_state") if isinstance(user.get("relationship_state"), dict) else {}
        readiness = self._proactive_inner_readiness(user)
        semantics = self._planned_proactive_semantics(user)
        parts = [
            self._private_user_role(user),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=40),
            user.get("planned_proactive_topic"),
            user.get("planned_proactive_motive"),
            user.get("planned_proactive_impulse_id"),
            _single_line(semantics.get("kind"), 40),
            _single_line(semantics.get("anchor_type"), 40),
            f"semantic={_safe_float(semantics.get('score'), 0.5):.2f}",
            f"pressure={_safe_float(semantics.get('pressure'), 0.4):.2f}",
            f"risk={_safe_float(semantics.get('risk'), 0.0):.2f}",
            rel_state.get("mode") if isinstance(rel_state, dict) else "",
            user.get("ignored_streak"),
            f"inner={_safe_float(readiness.get('score'), 0.55):.2f}",
            _single_line(readiness.get("label"), 80),
            persona,
            worldview,
        ]
        raw = "\n".join(_single_line(part, 1000) for part in parts)
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()

    def _cached_proactive_model_judgement(
        self,
        user: dict[str, Any],
        *,
        signature: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        if not signature:
            return None
        check_now = _now_ts() if now is None else now
        if _single_line(user.get("planned_proactive_model_judge_signature"), 80) != signature:
            return None
        judged_at = _safe_float(user.get("planned_proactive_model_judge_at"), 0)
        ttl = max(5, _safe_int(getattr(self, "proactive_persona_judge_cache_minutes", 180), 180, 5, 720)) * 60
        if judged_at <= 0 or check_now - judged_at > ttl:
            return None
        cached = user.get("planned_proactive_model_judge_result")
        return dict(cached) if isinstance(cached, dict) else None

    def _normalize_proactive_model_judgement(self, payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        decision = str(payload.get("decision") or "").strip().lower()
        if decision not in {"send", "rewrite", "defer", "drop"}:
            return None
        score = _safe_int(payload.get("score"), 0, 0, 100)
        threshold_getter = getattr(self, "_effective_proactive_persona_judge_send_threshold", None)
        threshold = (
            threshold_getter()
            if callable(threshold_getter)
            else _safe_int(getattr(self, "proactive_persona_judge_send_threshold", 62), 62, 0, 100)
        )
        if decision == "send" and score > 0 and score < threshold:
            decision = "defer"
        reason = self._normalize_legacy_proactive_text(payload.get("reason"), limit=140) or "模型人格判定"
        result = {
            "decision": decision,
            "score": score,
            "reason": reason,
            "delay_minutes": _safe_int(payload.get("delay_minutes"), 90, 20, 360),
            "reason_field": self._normalize_legacy_proactive_text(payload.get("planned_reason") or payload.get("reason_field"), limit=40),
            "action": self._normalize_legacy_proactive_text(payload.get("action"), limit=40),
            "topic": _single_line(payload.get("topic"), 80),
            "motive": self._normalize_internal_motive_text(_single_line(payload.get("motive"), 180)),
        }
        return result

    def _format_proactive_source_model_hint(self, user: dict[str, Any]) -> str:
        source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        if source in {"pending_followup", "followup"}:
            return "\n".join(
                [
                    "【来源专项改写：补一句】",
                    "这类来源不是重新开话题，而是前一句还有一个具体点没落地。",
                    "如果现在的 topic/motive 只是“再说一句/补一句/轻轻放一句/顺着那股劲”，优先 rewrite，不要直接 send。",
                    "rewrite 后必须补出一个实质点：提醒、遗漏信息、没说完的小重点，或前一句里还挂着的小事。",
                    "情绪可以很轻，但只允许当底色：一点点不甘心、惦记，或认真；不要把情绪本身写成内容。",
                ]
            )
        if source == "daily_greeting" or reason in {"morning_greeting", "noon_greeting", "evening_greeting"}:
            return "\n".join(
                [
                    "【来源专项改写：日常招呼】",
                    "这类来源的价值在“当天这个时段的第一句主动开口”，不是在任何空档里补一声问候。",
                    "如果今天已经发过别的主动、或者已经和对方有来回互动，就别再硬写 morning_greeting/noon_greeting/evening_greeting。",
                    "rewrite 后必须落在当前时段的一个小片段上：早晨刚醒/洗漱/出门前，中午刚吃完/发懒/准备午休，晚上收尾/回家/窝下来。",
                    "最终效果要像这个时段第一次顺手冒头，不像模板化签到，也不像聊到一半又补来的礼貌问候。",
                ]
            )
        if source == "random":
            return "\n".join(
                [
                    "【来源专项改写：轻微想念】",
                    "规则层已经判断这次“想来找一下”成立，但正文不能只剩关系姿态。",
                    "如果现在的 topic/motive 只有“想你了/来看看你/在不在/忙不忙”，优先 rewrite。",
                    "rewrite 后要补出一个很小的具体钩子：当前时段的小片段、刚冒出来的小念头，或一句能自然说给用户的话。",
                    "这类来源只能轻，不要写成索取回应，也不要写成无缘由的空泛表白。",
                ]
            )
        if source == "state" or reason == "state_share":
            return "\n".join(
                [
                    "【来源专项改写：身体小需求】",
                    "这类来源不是汇报状态，而是身体上的那点小事带出来的话头。",
                    "如果现在的 topic/motive 像“我饿了/我累了/状态不好”，优先 rewrite。",
                    "rewrite 后要把它改成一个具体可聊的小需求，比如吃什么、要不要垫一口、想不想来点甜的；不要写成状态播报。",
                    "语气要自然，不要像健康汇报、撒娇表演或硬找人陪。",
                ]
            )
        return ""

    def _format_proactive_model_judge_prompt(self, user: dict[str, Any]) -> str:
        persona = str(self._get_default_persona_prompt() or "").strip()
        worldview = str(self._format_worldview_adaptation_prompt() or "").strip()
        boundary = self._format_private_user_boundary_hint(user) if isinstance(user, dict) else ""
        rel_summary = ""
        formatter = getattr(self, "_format_relationship_summary", None)
        if callable(formatter):
            try:
                rel_summary = _single_line(formatter(user), 220)
            except Exception:
                rel_summary = ""
        local_alignment = self._planned_proactive_persona_alignment(user)
        semantics = self._planned_proactive_semantics(user)
        window_phase, window_detail = self._planned_impulse_window_phase(user)
        inner_readiness = self._proactive_inner_readiness(user)
        source_hint = self._format_proactive_source_model_hint(user)
        role = self._private_user_role(user)
        nickname = _single_line(user.get("nickname"), 40) or self.default_nickname
        return f"""
你是“主动消息人格/世界观判定器”。只判断这个主动计划是否像当前角色会自然产生的念头,不要写最终聊天正文。

输出必须是 JSON 对象,不要 Markdown,不要解释：
{{"decision":"send|rewrite|defer|drop","score":0,"reason":"20字以内原因","delay_minutes":90,"planned_reason":"","action":"","topic":"","motive":""}}

判定含义：
- send：计划自然,可以进入生成。
- rewrite：方向有价值,但动机/话题/动作需要更贴合角色；只改 planned_reason/action/topic/motive,不要写最终聊天正文。
- defer：现在不适合或动机不够自然,稍后再看。
- drop：明显不符合角色/世界观/关系边界,或像系统任务、索取回应、无由头打扰。

硬要求：
- 不得放行内部机制泄露、工具名、模型、插件、提示词、后台任务。
- 不得新增事实、现实能力或用户没给过的关系信息。
- 朋友关系必须普通、低频、不过度亲密；主人/亲近关系也要尊重休息和拒绝。
- 世界观表达必须贴合设定；能力只能作为角色内自然动机,不能露出调用过程。
- 如果只是“想你了/来看看/在不在/忙不忙”且没有具体由头,通常 defer 或 rewrite。

【角色设定】
{_single_line(persona, 1800) or "（未读取到显式人格,按自然私聊陪伴角色处理）"}

【世界观/适配】
{_single_line(worldview, 1000) or "（无额外世界观适配）"}

【关系边界】
{boundary}

【当前对象】
- 昵称：{nickname}
- 关系角色：{role}
- 关系摘要：{rel_summary or "暂无"}
- 连续未回应：{_safe_int(user.get("ignored_streak"), 0, 0)}
- 最近用户消息：{_single_line(user.get("last_user_message"), 160) or "（无）"}
- 最近 Bot 消息：{_single_line(user.get("last_companion_message"), 160) or "（无）"}
- Bot 当前开口欲/关系温度：{_safe_float(inner_readiness.get("score"), 0.55):.2f}｜{_single_line(inner_readiness.get("label"), 60)}｜{_single_line(inner_readiness.get("detail"), 180)}

【当前主动计划】
- source：{self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) or "unknown"}
- reason：{self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) or "check_in"}
- action：{_single_line(user.get("planned_proactive_action"), 40) or "message"}
- topic：{_single_line(user.get("planned_proactive_topic"), 100) or "无"}
- motive：{_single_line(user.get("planned_proactive_motive"), 220) or "无"}
- 候选语义：{_single_line(semantics.get("kind"), 40)}/{_single_line(semantics.get("anchor_type"), 40)}｜score={_safe_float(semantics.get("score"), 0.5):.2f}｜pressure={_safe_float(semantics.get("pressure"), 0.4):.2f}｜risk={_safe_float(semantics.get("risk"), 0.0):.2f}｜{_single_line(semantics.get("note"), 140)}
- 念头窗口：{window_phase}｜{window_detail}
- 本地粗判：{_safe_float(local_alignment.get("score"), 0.0):.2f}｜{_single_line(local_alignment.get("note"), 140)}

{source_hint}
""".strip()

    async def _review_planned_proactive_with_model(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        check_now = _now_ts() if now is None else now
        if not bool(getattr(self, "enable_llm_proactive_persona_judge", True)):
            return {"decision": "send", "score": 100, "reason": "模型人格判定关闭"}
        if self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) in {"timer", "troubleshooting", "simulation"}:
            return {"decision": "send", "score": 100, "reason": "特权计划跳过模型人格判定"}
        signature = self._planned_proactive_model_judge_signature(user)
        cached = self._cached_proactive_model_judgement(user, signature=signature, now=check_now)
        if isinstance(cached, dict):
            cached["cached"] = True
            return cached
        prompt = self._format_proactive_model_judge_prompt(user)
        memory_getter = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(memory_getter):
            user_id = _single_line(user.get("user_id") or user.get("id"), 80)
            query = " ".join(
                part
                for part in (
                    "主动消息适合性",
                    _single_line(user.get("planned_proactive_reason"), 80),
                    _single_line(user.get("planned_proactive_topic"), 120),
                    _single_line(user.get("planned_proactive_motive"), 180),
                    "用户习惯 上次主动回应 边界 当前穿搭 当前日程 最近状态",
                )
                if part
            )
            memory_context = await memory_getter(
                kind="proactive_review",
                query=query,
                user=user,
                user_id=user_id,
                top_k=5,
                max_chars=800,
            )
            if memory_context:
                prompt = (
                    f"{prompt.rstrip()}\n\n"
                    "<!-- private_companion_memory_review_context_v1 -->\n"
                    "【我会牢牢记住你 相关记忆】\n"
                    f"{memory_context}\n"
                    "使用方式：只辅助判断是否适合主动、是否需要改写或延后；不要在理由里暴露检索过程。"
                )
        started = time.perf_counter()
        raw = await self._llm_call(
            prompt,
            max_tokens=260,
            provider_id=self._task_provider(
                getattr(self, "proactive_persona_judge_provider_id", ""),
                self.response_review_provider_id,
                self.mai_style_provider_id,
            ),
            task="proactive_persona_judge",
        )
        parsed = self._parse_json_object(raw)
        result = self._normalize_proactive_model_judgement(parsed)
        if not isinstance(result, dict):
            logger.info("[PrivateCompanion] 模型人格判定无有效 JSON,降级本地判定")
            return {"decision": "send", "score": 0, "reason": "模型判定失败,降级本地"}
        result["signature"] = signature
        result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        logger.info(
            "[PrivateCompanion] 主动模型人格判定: decision=%s score=%s reason=%s elapsed=%sms",
            result.get("decision"),
            result.get("score"),
            _single_line(result.get("reason"), 100),
            result.get("elapsed_ms"),
        )
        return result

    def _cache_proactive_model_judgement(
        self,
        user: dict[str, Any],
        judgement: dict[str, Any],
        *,
        now: float | None = None,
    ) -> None:
        signature = _single_line(judgement.get("signature"), 80) or self._planned_proactive_model_judge_signature(user)
        user["planned_proactive_model_judge_signature"] = signature
        user["planned_proactive_model_judge_result"] = {
            key: value
            for key, value in judgement.items()
            if key in {"decision", "score", "reason", "delay_minutes", "reason_field", "action", "topic", "motive"}
        }
        user["planned_proactive_model_judge_at"] = _now_ts() if now is None else now

    def _apply_proactive_model_rewrite(self, user: dict[str, Any], judgement: dict[str, Any]) -> bool:
        changed = False
        new_reason = self._normalize_legacy_proactive_text(judgement.get("reason_field"), limit=40)
        new_action = self._normalize_legacy_proactive_text(judgement.get("action"), limit=40)
        new_topic = _single_line(judgement.get("topic"), 80)
        new_motive = self._normalize_internal_motive_text(_single_line(judgement.get("motive"), 180))
        current_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        current_action = self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=40)
        if new_reason and new_reason != current_reason:
            user["planned_proactive_reason"] = new_reason
            changed = True
        if new_action and self._action_is_available(new_action, user) and new_action != current_action:
            user["planned_proactive_action"] = new_action
            changed = True
        if new_topic and new_topic != _single_line(user.get("planned_proactive_topic"), 80):
            user["planned_proactive_topic"] = new_topic
            changed = True
        if new_motive and new_motive != _single_line(user.get("planned_proactive_motive"), 180):
            user["planned_proactive_motive"] = new_motive
            changed = True
        if changed and self._private_user_role(user) == "friend":
            sanitized = self._sanitize_friend_proactive_plan_fields(
                user,
                reason=self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) or "check_in",
                action=self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=40) or "message",
                topic=_single_line(user.get("planned_proactive_topic"), 80),
                motive=_single_line(user.get("planned_proactive_motive"), 180),
            )
            user["planned_proactive_reason"] = sanitized["reason"]
            user["planned_proactive_action"] = sanitized["action"]
            user["planned_proactive_topic"] = sanitized["topic"]
            user["planned_proactive_motive"] = sanitized["motive"]
        return changed

    def _planned_proactive_impulse(self, user: dict[str, Any]) -> dict[str, Any] | None:
        impulse_id = _single_line(user.get("planned_proactive_impulse_id"), 20)
        if not impulse_id:
            return None
        for item in self._cleanup_proactive_impulses(user):
            if isinstance(item, dict) and _single_line(item.get("id"), 20) == impulse_id:
                return item
        return None

    def _planned_impulse_value(self, user: dict[str, Any], *, now: float | None = None) -> float:
        impulse = self._planned_proactive_impulse(user)
        if isinstance(impulse, dict):
            return self._score_proactive_impulse(user, impulse, now=now)
        reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        value = 0.62
        if source in {"timer", "troubleshooting", "simulation"}:
            value += 0.35
        if source in {"pending_followup", "daily_greeting", "story", "state"}:
            value += 0.08
        if reason in {"important_date_share", "quiet_care", "group_share", "news_share", "creative_share"}:
            value += 0.08
        if self._private_user_role(user) == "friend":
            value -= 0.06
        return value

    def _planned_impulse_window_phase(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> tuple[str, str]:
        check_now = _now_ts() if now is None else now
        start_at = _safe_float(user.get("planned_proactive_window_start_at"), 0)
        best_until = _safe_float(user.get("planned_proactive_best_until_at"), 0)
        expire_at = _safe_float(user.get("planned_proactive_expire_at"), 0)
        if expire_at > 0 and check_now > expire_at:
            return "expired", "念头窗口已过期"
        if start_at > 0 and check_now < start_at:
            return "before", f"距窗口开始还有 {self._format_elapsed(start_at - check_now)}"
        if best_until > 0 and check_now <= best_until:
            return "best", f"正处于最佳表达窗口,剩余 {self._format_elapsed(best_until - check_now)}"
        if expire_at > 0:
            return "tail", f"已过最佳窗口,距过期 {self._format_elapsed(max(0, expire_at - check_now))}"
        return "unknown", "未记录念头窗口"

    def _defer_or_replace_planned_impulse(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        note: str = "",
        delay_minutes: tuple[float, float] = (30.0, 90.0),
        block_current: bool = False,
    ) -> bool:
        check_now = _now_ts() if now is None else now
        impulse = self._planned_proactive_impulse(user)
        current_id = _single_line(user.get("planned_proactive_impulse_id"), 20)
        self._mark_planned_candidate_status(user, "blocked" if block_current else "deferred", note)
        if isinstance(impulse, dict):
            impulse["updated_ts"] = check_now
            impulse["last_note"] = _single_line(note, 160)
            if block_current:
                impulse["state"] = "blocked"
            else:
                hesitation_count = _safe_int(impulse.get("hesitation_count"), 0, 0, 8) + 1
                impulse["hesitation_count"] = hesitation_count
                impulse["hesitation_at"] = check_now
                impulse["hesitation_note"] = _single_line(note, 160)
                self._remember_proactive_hesitation(user, impulse, note=note, now=check_now)
                delay = random.uniform(max(1.0, delay_minutes[0]), max(delay_minutes[0] + 1.0, delay_minutes[1])) * 60
                next_window = check_now + delay
                impulse["state"] = "deferred"
                impulse["window_start_at"] = next_window
                impulse["preferred_ts"] = max(_safe_float(impulse.get("preferred_ts"), 0), next_window)
                impulse["best_until_at"] = max(_safe_float(impulse.get("best_until_at"), 0), next_window + 25 * 60)
                impulse["expire_at"] = max(_safe_float(impulse.get("expire_at"), 0), next_window + 90 * 60)
        self._clear_pending_proactive_plan(user)
        if not self._materialize_best_proactive_impulse(user, now=check_now):
            return False
        return bool(_single_line(user.get("planned_proactive_impulse_id"), 20) != current_id)

    def _remember_proactive_hesitation(
        self,
        user: dict[str, Any],
        impulse: dict[str, Any],
        *,
        note: str = "",
        now: float | None = None,
    ) -> None:
        check_now = _now_ts() if now is None else now
        raw = user.setdefault("recent_proactive_hesitations", [])
        if not isinstance(raw, list):
            raw = []
            user["recent_proactive_hesitations"] = raw
        item = {
            "ts": check_now,
            "reason": _single_line(impulse.get("reason"), 40),
            "source": _single_line(impulse.get("source"), 40),
            "topic": _single_line(impulse.get("topic"), 80),
            "motive": _single_line(impulse.get("motive"), 140),
            "note": _single_line(note, 140),
            "count": _safe_int(impulse.get("hesitation_count"), 1, 1, 20),
        }
        raw.append(item)
        del raw[:-8]
        user["last_proactive_hesitation_at"] = check_now
        user["last_proactive_hesitation_note"] = item["note"]

    def _motive_with_hesitation_memory(self, impulse: dict[str, Any], motive: str) -> str:
        count = _safe_int(impulse.get("hesitation_count"), 0, 0, 8)
        if count <= 0:
            return self._normalize_internal_motive_text(motive)
        source = str(impulse.get("source") or "")
        if source in {"timer", "troubleshooting", "simulation"}:
            return self._normalize_internal_motive_text(motive)
        cleaned = self._normalize_internal_motive_text(motive)
        if any(token in cleaned for token in ("刚才想说", "刚才差点", "忍住", "收住")):
            return cleaned
        topic = _single_line(impulse.get("topic"), 40)
        if count >= 2:
            prefix = "这个念头前面忍过一次,但它还没散"
        else:
            prefix = "刚才差点想说,后来又先收住了"
        if topic:
            prefix += f",这会儿又绕回“{topic}”"
        if cleaned:
            return self._normalize_internal_motive_text(f"{prefix}；{cleaned}")
        return self._normalize_internal_motive_text(prefix)

    def _materialize_best_proactive_impulse(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> bool:
        check_now = _now_ts() if now is None else now
        user_id = str(user.get("user_id") or user.get("id") or "")
        active = [
            item
            for item in self._cleanup_proactive_impulses(user, now=check_now)
            if isinstance(item, dict) and str(item.get("state") or "queued") in {"queued", "deferred"}
        ]
        if not active:
            return False
        ready = [item for item in active if self._impulse_ready_now(item, now=check_now)]
        selected: dict[str, Any] | None = None
        review_at = 0.0
        if ready:
            ready.sort(key=lambda item: self._score_proactive_impulse(user, item, now=check_now), reverse=True)
            selected = ready[0]
            review_at = check_now
        else:
            future = sorted(
                active,
                key=lambda item: (
                    _safe_float(item.get("window_start_at"), check_now + 365 * 24 * 3600),
                    -self._score_proactive_impulse(user, item, now=check_now),
                ),
            )
            if not future:
                return False
            selected = future[0]
            review_at = _safe_float(selected.get("window_start_at"), check_now)
        candidate = {
            "source": self._normalize_legacy_proactive_text(selected.get("source"), limit=40) or "impulse",
            "reason": self._normalize_legacy_proactive_text(selected.get("reason"), limit=40) or "check_in",
            "action": self._normalize_legacy_proactive_text(selected.get("action"), limit=40) or "message",
            "scheduled_ts": max(review_at, _safe_float(selected.get("window_start_at"), review_at)),
            "topic": _single_line(selected.get("topic"), 80),
            "motive": self._motive_with_hesitation_memory(selected, _single_line(selected.get("motive"), 180)),
            "score": int(max(0.0, min(1.0, self._score_proactive_impulse(user, selected, now=check_now))) * 100),
            "context_key": _single_line(selected.get("context_key"), 60),
            "context": selected.get("context"),
            "chain": selected.get("chain") if isinstance(selected.get("chain"), list) else [],
        }
        item = self._record_proactive_candidate(
            user_id,
            candidate,
            status="accepted",
            note="由潜在念头池物化为当前主动计划",
            user=user,
        )
        user["next_proactive_at"] = candidate["scheduled_ts"]
        user["planned_proactive_reason"] = self._normalize_legacy_proactive_text(candidate["reason"], limit=40) or "check_in"
        user["planned_proactive_action"] = self._normalize_legacy_proactive_text(candidate["action"], limit=40) or "message"
        user["planned_proactive_source"] = self._normalize_legacy_proactive_text(candidate["source"], limit=40) or "impulse"
        user["planned_proactive_motive"] = self._normalize_internal_motive_text(candidate["motive"])
        user["planned_proactive_topic"] = candidate["topic"]
        user["planned_proactive_impulse_id"] = _single_line(selected.get("id"), 20)
        user["planned_proactive_window_start_at"] = _safe_float(selected.get("window_start_at"), 0)
        user["planned_proactive_best_until_at"] = _safe_float(selected.get("best_until_at"), 0)
        user["planned_proactive_expire_at"] = _safe_float(selected.get("expire_at"), 0)
        user["planned_proactive_semantic_kind"] = _single_line(selected.get("semantic_kind"), 40)
        user["planned_proactive_anchor_type"] = _single_line(selected.get("semantic_anchor_type"), 40)
        user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(selected.get("semantic_score"), 0.5))) * 100)
        user["planned_proactive_semantic_note"] = _single_line(selected.get("semantic_note"), 180)
        user["planned_candidate_id"] = item.get("id", "")
        user["planned_event_chain"] = (
            []
            if self._private_user_role(user) == "friend"
            else [dict(step) for step in selected.get("chain", []) if isinstance(step, dict)]
        )
        user["planned_opener_mode"] = _single_line(selected.get("opener_mode"), 24)
        user["planned_followup_kind"] = _single_line(selected.get("followup_kind"), 32)
        user["planned_proactive_quota_exempt"] = bool(selected.get("quota_exempt"))
        self._set_planned_proactive_trigger(
            user,
            message_id=_single_line(selected.get("trigger_message_id"), 120),
            umo=_single_line(selected.get("trigger_umo"), 160),
            created_at=_safe_float(selected.get("trigger_ts"), 0),
        )
        context_key = _single_line(selected.get("context_key"), 60)
        context = selected.get("context")
        if context_key and isinstance(context, dict):
            user[context_key] = dict(context)
        selected["updated_ts"] = check_now
        selected["state"] = "queued"
        return True

    def _record_proactive_candidate(
        self,
        user_id: str,
        candidate: dict[str, Any],
        *,
        status: str,
        note: str = "",
        user: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _now_ts()
        topic = _single_line(candidate.get("topic"), 80)
        motive = _single_line(candidate.get("motive"), 160)
        action = _single_line(candidate.get("action"), 40) or "message"
        source = _single_line(candidate.get("source"), 40) or "unknown"
        reason = _single_line(candidate.get("reason"), 40) or "check_in"
        scheduled = _safe_float(candidate.get("scheduled_ts"), now)
        signature = self._proactive_topic_signature(topic, motive)
        semantics: dict[str, Any] = {}
        if isinstance(user, dict):
            semantics = self._proactive_candidate_semantics(
                user,
                reason=reason,
                action=action,
                motive=motive,
                topic=topic,
                source=source,
                context=candidate.get("context"),
                chain=candidate.get("chain") if isinstance(candidate.get("chain"), list) else [],
                trigger_message_id=self._candidate_trigger_message_id(candidate),
                trigger_ts=_safe_float(candidate.get("trigger_ts") or candidate.get("created_ts"), 0),
            )
        semantic_fields = {
            "semantic_kind": _single_line(semantics.get("kind"), 40),
            "semantic_anchor_type": _single_line(semantics.get("anchor_type"), 40),
            "semantic_score": int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.0))) * 100) if semantics else 0,
            "semantic_pressure": int(max(0.0, min(1.0, _safe_float(semantics.get("pressure"), 0.0))) * 100) if semantics else 0,
            "semantic_risk": int(max(0.0, min(1.0, _safe_float(semantics.get("risk"), 0.0))) * 100) if semantics else 0,
            "semantic_note": _single_line(semantics.get("note"), 180),
        }
        pool = self._cleanup_proactive_candidate_pool(now=now)
        if status in {"blocked", "accepted"}:
            for existing in reversed(pool):
                if not isinstance(existing, dict):
                    continue
                if str(existing.get("status") or "") != status:
                    continue
                if str(existing.get("user_id") or "") != str(user_id):
                    continue
                if status == "accepted" and str(existing.get("id") or "") == str(candidate.get("id") or ""):
                    continue
                if not self._topic_signature_similar(signature, str(existing.get("signature") or "")):
                    continue
                if now - _safe_float(existing.get("last_seen_ts") or existing.get("created_ts"), 0) > 18 * 3600:
                    continue
                existing["repeat_count"] = _safe_int(existing.get("repeat_count"), 1, 1) + 1
                existing["last_seen_ts"] = now
                existing["scheduled_ts"] = max(_safe_float(existing.get("scheduled_ts"), scheduled), scheduled)
                existing["source"] = source or _single_line(existing.get("source"), 40)
                existing["reason"] = reason or _single_line(existing.get("reason"), 40)
                existing["action"] = action or _single_line(existing.get("action"), 40)
                existing["topic"] = topic or _single_line(existing.get("topic"), 80)
                existing["motive"] = motive or _single_line(existing.get("motive"), 160)
                if note:
                    existing["note"] = _single_line(note, 160)
                existing["score"] = max(_safe_int(existing.get("score"), 0, 0, 100), _safe_int(candidate.get("score"), 0, 0, 100))
                if semantics:
                    existing.update(semantic_fields)
                return existing
        item = {
            "id": uuid.uuid4().hex[:12],
            "created_ts": now,
            "last_seen_ts": now,
            "scheduled_ts": scheduled,
            "user_id": str(user_id),
            "source": source,
            "reason": reason,
            "action": action,
            "topic": topic,
            "motive": motive,
            "score": _safe_int(candidate.get("score"), 0, 0, 100),
            "signature": signature,
            "status": status,
            "note": _single_line(note, 160),
            "repeat_count": 1,
            **(semantic_fields if semantics else {}),
        }
        pool.append(item)
        self._cleanup_proactive_candidate_pool(now=now)
        return item

    def _proactive_candidate_repeated(self, user: dict[str, Any], candidate: dict[str, Any]) -> bool:
        signature = self._proactive_topic_signature(
            candidate.get("topic"),
            candidate.get("motive"),
        )
        if not signature:
            return False
        if self._recent_proactive_topic_repeated(user, signature):
            return True
        now = _now_ts()
        user_id = str(user.get("user_id") or user.get("id") or "")
        for item in self._cleanup_proactive_candidate_pool(now=now):
            if str(item.get("user_id") or "") != user_id:
                continue
            if str(item.get("status") or "") not in {"accepted", "sent"}:
                continue
            if now - _safe_float(item.get("created_ts"), 0) > 8 * 3600:
                continue
            if self._topic_signature_similar(signature, str(item.get("signature") or "")):
                return True
        return False

    def _offer_proactive_candidate(self, user_id: str, user: dict[str, Any], candidate: dict[str, Any]) -> bool:
        user["user_id"] = str(user.get("user_id") or user_id)
        now = _now_ts()
        source = _single_line(candidate.get("source"), 40) or "unknown"
        scheduled = _safe_float(candidate.get("scheduled_ts"), now)
        social_relay_note = self._unverified_social_relay_plan_reason(
            candidate,
            source=source,
            has_trigger=bool(self._candidate_trigger_message_id(candidate)),
        )
        if social_relay_note:
            self._record_proactive_candidate(user_id, candidate, status="blocked", note=social_relay_note, user=user)
            return False
        rest_until = self._user_rest_silence_until(user, now=now)
        if rest_until > now and scheduled < rest_until and source != "timer":
            self._record_proactive_candidate(user_id, candidate, status="blocked", note="用户明确休息中", user=user)
            return False
        if not self._user_enabled_for_proactive(str(user_id), user):
            self._clear_pending_proactive_plan(user)
            return False
        if not self._friend_can_receive_proactive_reason(user, candidate.get("reason"), candidate.get("action")):
            return False
        timer_event = self._get_active_llm_timer(user)
        timer_scheduled = _safe_float(timer_event.get("scheduled_ts"), 0) if isinstance(timer_event, dict) else 0.0
        if timer_scheduled > now and scheduled < timer_scheduled and self._in_llm_timer_silence_window(user, now=now):
            self._remember_silenced_candidate_for_timer(user, candidate, now=now)
            self._record_proactive_candidate(user_id, candidate, status="blocked", note="已有聊天临时预约临近", user=user)
            return False
        if _safe_float(user.get("next_proactive_at"), 0) > 0 and self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) == "timer":
            current_timer = self._get_active_llm_timer(user)
            if self._llm_timer_can_use_internal_scheduler(current_timer if isinstance(current_timer, dict) else None):
                self._record_proactive_candidate(user_id, candidate, status="blocked", note="已有用户预约/定时主动", user=user)
                return False
            self._clear_llm_timer_internal_plan_fields(user)
        current_next = _safe_float(user.get("next_proactive_at"), 0)
        if current_next > 0 and current_next <= scheduled:
            self._record_proactive_candidate(user_id, candidate, status="blocked", note="已有更早主动候选", user=user)
            return False
        action = _single_line(candidate.get("action"), 40) or "message"
        if self._private_user_role(user, str(user_id)) == "friend" and self._action_has_photo_text(action):
            action = self._fallback_action_for_unavailable(action, user)
        if self._private_user_role(user, str(user_id)) == "friend":
            sanitized = self._sanitize_friend_proactive_plan_fields(
                user,
                reason=_single_line(candidate.get("reason"), 40) or "check_in",
                action=action,
                topic=_single_line(candidate.get("topic"), 80),
                motive=_single_line(candidate.get("motive"), 180),
            )
            action = sanitized["action"]
            candidate = dict(candidate)
            candidate["reason"] = sanitized["reason"]
            candidate["topic"] = sanitized["topic"]
            candidate["motive"] = sanitized["motive"]
        if not self._action_is_available(action, user):
            self._record_proactive_candidate(user_id, candidate, status="blocked", note="动作不可用或媒体额度不足", user=user)
            return False
        if self._proactive_candidate_repeated(user, candidate):
            self._record_proactive_candidate(user_id, candidate, status="blocked", note="近期主题过于相似", user=user)
            return False
        item = self._record_proactive_candidate(user_id, candidate, status="accepted", note="进入主动计划", user=user)
        impulse = self._candidate_to_impulse(user, candidate, source=source, now=now)
        if isinstance(impulse, dict):
            self._queue_proactive_impulse(user, impulse)
        user["next_proactive_at"] = scheduled
        user["planned_proactive_reason"] = self._normalize_legacy_proactive_text(candidate.get("reason"), limit=40) or "check_in"
        user["planned_proactive_action"] = self._normalize_legacy_proactive_text(action, limit=40) or "message"
        user["planned_proactive_source"] = self._normalize_legacy_proactive_text(source, limit=40) or "proactive"
        user["planned_proactive_motive"] = self._normalize_internal_motive_text(
            _single_line(candidate.get("motive"), 180)
        )
        user["planned_proactive_topic"] = _single_line(candidate.get("topic"), 80)
        user["planned_proactive_impulse_id"] = _single_line(impulse.get("id"), 20) if isinstance(impulse, dict) else ""
        user["planned_proactive_window_start_at"] = _safe_float(
            impulse.get("window_start_at"),
            scheduled,
        ) if isinstance(impulse, dict) else scheduled
        user["planned_proactive_best_until_at"] = _safe_float(
            impulse.get("best_until_at"),
            scheduled,
        ) if isinstance(impulse, dict) else scheduled
        user["planned_proactive_expire_at"] = _safe_float(
            impulse.get("expire_at"),
            scheduled,
        ) if isinstance(impulse, dict) else scheduled
        if isinstance(impulse, dict):
            user["planned_proactive_semantic_kind"] = _single_line(impulse.get("semantic_kind"), 40)
            user["planned_proactive_anchor_type"] = _single_line(impulse.get("semantic_anchor_type"), 40)
            user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(impulse.get("semantic_score"), 0.5))) * 100)
            user["planned_proactive_semantic_note"] = _single_line(impulse.get("semantic_note"), 180)
        else:
            semantics = self._planned_proactive_semantics(user)
            user["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
            user["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
            user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
            user["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
        user["planned_event_chain"] = [] if self._private_user_role(user) == "friend" else (
            [dict(step) for step in impulse.get("chain", []) if isinstance(step, dict)]
            if isinstance(impulse, dict)
            else []
        )
        user["planned_opener_mode"] = _single_line(impulse.get("opener_mode"), 24) if isinstance(impulse, dict) else ""
        user["planned_followup_kind"] = _single_line(impulse.get("followup_kind"), 32) if isinstance(impulse, dict) else ""
        self._clear_planned_proactive_trigger(user)
        user["planned_proactive_quota_exempt"] = False
        user["planned_candidate_id"] = item.get("id", "")
        self._set_planned_proactive_trigger(
            user,
            message_id=self._candidate_trigger_message_id(candidate),
            umo=_single_line(candidate.get("trigger_umo") or candidate.get("umo"), 160),
            created_at=_safe_float(candidate.get("trigger_ts") or candidate.get("created_ts"), 0),
        )
        context_key = _single_line(candidate.get("context_key"), 60)
        context = candidate.get("context")
        if context_key and isinstance(context, dict):
            user[context_key] = context
        return True

    def _llm_timer_pre_silence_seconds(self) -> float:
        return max(0.0, float(getattr(self, "timer_pre_silence_minutes", 20) or 0) * 60.0)

    def _upcoming_llm_timer_ts(self, user: dict[str, Any], *, now: float | None = None) -> float:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return 0.0
        scheduled_ts = _safe_float(event.get("scheduled_ts"), 0)
        check_now = _now_ts() if now is None else now
        return scheduled_ts if scheduled_ts > check_now else 0.0

    def _in_llm_timer_pre_silence_window(self, user: dict[str, Any], *, now: float | None = None) -> bool:
        lead = self._llm_timer_pre_silence_seconds()
        if lead <= 0:
            return False
        check_now = _now_ts() if now is None else now
        timer_ts = self._upcoming_llm_timer_ts(user, now=check_now)
        return timer_ts > 0 and 0 < timer_ts - check_now <= lead

    def _in_llm_timer_silence_window(self, user: dict[str, Any], *, now: float | None = None) -> bool:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return False
        check_now = _now_ts() if now is None else now
        scheduled_ts = _safe_float(event.get("scheduled_ts"), 0)
        if scheduled_ts <= check_now:
            return False
        if bool(event.get("silence_until_due")):
            return True
        return self._in_llm_timer_pre_silence_window(user, now=check_now)

    def _remember_silenced_plan_for_timer(self, user: dict[str, Any], *, now: float | None = None) -> None:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return
        planned_source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        if planned_source == "timer":
            return
        topic = _single_line(user.get("planned_proactive_topic"), 80)
        motive = _single_line(user.get("planned_proactive_motive"), 160)
        reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        action = self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=32)
        if not any((topic, motive, reason)):
            return
        existing = event.get("deferred_context")
        if isinstance(existing, dict) and existing:
            return
        event["deferred_context"] = {
            "created_at": now or _now_ts(),
            "reason": reason,
            "action": action,
            "topic": topic,
            "motive": self._normalize_internal_motive_text(motive),
            "source": planned_source,
        }

    def _remember_silenced_candidate_for_timer(
        self,
        user: dict[str, Any],
        candidate: dict[str, Any],
        *,
        now: float | None = None,
    ) -> None:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return
        existing = event.get("deferred_context")
        if isinstance(existing, dict) and existing:
            return
        topic = _single_line(candidate.get("topic"), 80)
        motive = _single_line(candidate.get("motive"), 160)
        reason = _single_line(candidate.get("reason"), 40)
        action = _single_line(candidate.get("action"), 32)
        if not any((topic, motive, reason)):
            return
        event["deferred_context"] = {
            "created_at": now or _now_ts(),
            "reason": reason,
            "action": action,
            "topic": topic,
            "motive": self._normalize_internal_motive_text(motive),
            "source": _single_line(candidate.get("source"), 40),
        }

    def _promote_due_llm_timer_plan(self, user: dict[str, Any], *, now: float | None = None) -> bool:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return False
        if not self._llm_timer_can_use_internal_scheduler(event):
            return False
        check_now = _now_ts() if now is None else now
        scheduled_ts = _safe_float(event.get("scheduled_ts"), 0)
        if scheduled_ts <= 0 or scheduled_ts > check_now:
            return False
        user["next_proactive_at"] = scheduled_ts
        user["planned_proactive_reason"] = self._normalize_legacy_proactive_text(event.get("reason"), limit=40) or "check_in"
        user["planned_proactive_action"] = self._normalize_legacy_proactive_text(event.get("action"), limit=24) or "message"
        user["planned_proactive_source"] = "timer"
        user["planned_proactive_motive"] = self._normalize_internal_motive_text(_single_line(event.get("motive"), 140))
        user["planned_proactive_topic"] = _single_line(event.get("topic"), 60)
        user["planned_proactive_impulse_id"] = ""
        user["planned_proactive_window_start_at"] = scheduled_ts
        active_span, grace_span = self._proactive_impulse_default_window_seconds(user["planned_proactive_reason"])
        user["planned_proactive_best_until_at"] = scheduled_ts + active_span
        user["planned_proactive_expire_at"] = scheduled_ts + active_span + grace_span
        semantics = self._planned_proactive_semantics(user)
        user["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
        user["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
        user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
        user["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
        user["planned_event_chain"] = [] if self._private_user_role(user) == "friend" else (
            list(event.get("chain") or []) if isinstance(event.get("chain"), list) else []
        )
        user["planned_opener_mode"] = ""
        user["planned_followup_kind"] = ""
        user["planned_proactive_quota_exempt"] = False
        self._set_planned_proactive_trigger(
            user,
            message_id=_single_line(event.get("trigger_message_id"), 120),
            umo=_single_line(event.get("trigger_umo"), 160),
            created_at=_safe_float(event.get("trigger_ts"), 0),
        )
        return True

    def _promote_upcoming_llm_timer_plan(self, user: dict[str, Any], *, now: float | None = None) -> bool:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return False
        if not self._llm_timer_can_use_internal_scheduler(event):
            return False
        check_now = _now_ts() if now is None else now
        scheduled_ts = _safe_float(event.get("scheduled_ts"), 0)
        if scheduled_ts <= check_now:
            return False
        user["next_proactive_at"] = scheduled_ts
        user["planned_proactive_reason"] = self._normalize_legacy_proactive_text(event.get("reason"), limit=40) or "check_in"
        user["planned_proactive_action"] = self._normalize_legacy_proactive_text(event.get("action"), limit=24) or "message"
        user["planned_proactive_source"] = "timer"
        user["planned_proactive_motive"] = self._normalize_internal_motive_text(_single_line(event.get("motive"), 140))
        user["planned_proactive_topic"] = _single_line(event.get("topic"), 60)
        user["planned_proactive_impulse_id"] = ""
        user["planned_proactive_window_start_at"] = scheduled_ts
        active_span, grace_span = self._proactive_impulse_default_window_seconds(user["planned_proactive_reason"])
        user["planned_proactive_best_until_at"] = scheduled_ts + active_span
        user["planned_proactive_expire_at"] = scheduled_ts + active_span + grace_span
        semantics = self._planned_proactive_semantics(user)
        user["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
        user["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
        user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
        user["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
        user["planned_event_chain"] = [] if self._private_user_role(user) == "friend" else (
            list(event.get("chain") or []) if isinstance(event.get("chain"), list) else []
        )
        user["planned_opener_mode"] = ""
        user["planned_followup_kind"] = ""
        user["planned_proactive_quota_exempt"] = False
        self._set_planned_proactive_trigger(
            user,
            message_id=_single_line(event.get("trigger_message_id"), 120),
            umo=_single_line(event.get("trigger_umo"), 160),
            created_at=_safe_float(event.get("trigger_ts"), 0),
        )
        return True

    def _should_send(self, user: dict[str, Any]) -> tuple[bool, str]:
        self._recover_stale_proactive_sending(user)
        user_id = str(user.get("user_id") or user.get("id") or "")
        planned_source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        is_troubleshooting = planned_source == "troubleshooting"
        if not self._user_enabled_for_proactive(user_id, user):
            self._clear_pending_proactive_plan(user)
            return False, "私聊对象未启用"
        if user.get("proactive_sending"):
            return False, "上一条主动消息仍在发送中"
        umo_filled = False
        filler = getattr(self, "_ensure_private_user_umo", None)
        if callable(filler):
            try:
                umo_filled = bool(filler(user_id, user))
            except Exception:
                umo_filled = False
        if not user.get("umo"):
            return False, "缺少私聊会话"
        if umo_filled:
            logger.info(
                "[PrivateCompanion] 已为主动私聊对象补全 UMO: user=%s umo=%s",
                _single_line(user_id, 40),
                _single_line(user.get("umo"), 120),
            )
        if self._simulation_active(user):
            return self._should_send_simulation(user)
        daily_limit = self._effective_user_daily_limit(user)
        if not is_troubleshooting and daily_limit <= 0:
            reason_formatter = getattr(self, "_format_daily_limit_disabled_reason", None)
            if callable(reason_formatter):
                return False, reason_formatter(user)
            return False, "每日上限为 0"
        now = _now_ts()
        due_timer_active = self._has_due_llm_timer(user, now=now)
        if not is_troubleshooting and planned_source == "timer" and not due_timer_active:
            self._clear_llm_timer_internal_plan_fields(user)
            if _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now)
            return False, "对话临时预约已交给官方定时计划"
        if (
            not is_troubleshooting
            and
            self._user_rest_silence_until(user, now=now) > now
            and not due_timer_active
            and planned_source != "timer"
        ):
            return False, "用户明确休息中"
        if not is_troubleshooting and self._is_quiet_time() and not self._can_send_insomnia_night_message(user):
            return False, "免打扰时段"
        pre_gate_next_at = _safe_float(user.get("next_proactive_at"), 0)
        if not is_troubleshooting and not due_timer_active:
            if pre_gate_next_at <= 0:
                self._schedule_next_proactive(user, now=now)
                return False, "已安排下一次候选主动时间"
            if now < pre_gate_next_at:
                return False, "未到候选主动时间"
        rel_state = user.get("relationship_state")
        relationship_blocked = (
            not is_troubleshooting
            and
            self.enable_relationship_state_machine
            and isinstance(rel_state, dict)
            and rel_state.get("mode") == "backoff"
            and _safe_float(rel_state.get("backoff_until"), 0) > _now_ts()
        )
        emotion_blocked = (
            not is_troubleshooting
            and
            bool(getattr(self, "enable_emotion_simulation", True))
            and isinstance(rel_state, dict)
            and rel_state.get("mode") in {"hurt", "refusing"}
            and _safe_float(rel_state.get("hurt_until"), 0) > _now_ts()
        )
        if relationship_blocked or emotion_blocked:
            gate_until = max(
                _safe_float(rel_state.get("hurt_until"), 0),
                _safe_float(rel_state.get("backoff_until"), 0),
            )
            before_next_at = _safe_float(user.get("next_proactive_at"), 0)
            adjuster = getattr(self, "_defer_or_clean_emotion_blocked_plan", None)
            if callable(adjuster):
                adjusted_reason = adjuster(user, now=now)
            else:
                adjusted_reason = "情绪/关系状态处于收敛期"
            after_next_at = _safe_float(user.get("next_proactive_at"), 0)
            if after_next_at <= now and gate_until > now:
                after_next_at = gate_until + random.uniform(15 * 60, 75 * 60)
                user["next_proactive_at"] = after_next_at
                user["planned_proactive_window_start_at"] = after_next_at
                user["planned_proactive_best_until_at"] = after_next_at + 45 * 60
                user["planned_proactive_expire_at"] = after_next_at + 90 * 60
            logger.info(
                "[PrivateCompanion] 情绪/关系闸门拦截主动: user=%s mode=%s score=%s gate_until=%s reason=%s",
                _single_line(user.get("user_id") or user.get("umo") or user.get("nickname"), 80),
                _single_line(rel_state.get("mode"), 24),
                _safe_int(rel_state.get("mood_score"), 0, -100, 100),
                int(gate_until),
                _single_line(
                    rel_state.get("last_hurt_reason")
                    or rel_state.get("last_backoff_reason")
                    or rel_state.get("last_emotion_reason"),
                    80,
                ),
            )
            return False, adjusted_reason

        planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        if due_timer_active and planned_source != "timer":
            self._promote_due_llm_timer_plan(user, now=now)
            planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
            planned_source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) or planned_source
        next_at = _safe_float(user.get("next_proactive_at"), 0)
        planned_impulse_id = _single_line(user.get("planned_proactive_impulse_id"), 20)
        planned_expire_at = _safe_float(user.get("planned_proactive_expire_at"), 0)
        if (
            not is_troubleshooting
            and planned_impulse_id
            and planned_expire_at > 0
            and now > planned_expire_at
            and not due_timer_active
        ):
            self._mark_planned_candidate_status(user, "blocked", "潜在念头窗口已过期")
            self._clear_pending_proactive_plan(user)
            if not self._materialize_best_proactive_impulse(user, now=now):
                self._schedule_next_proactive(user, now=now, delay_hours=(1.0, 3.0))
            return False, "原主动念头已过期,已重新挑选"
        if next_at <= 0:
            self._schedule_next_proactive(user, now=now)
            return False, "已安排下一次候选主动时间"
        impulse_value = self._planned_impulse_value(user, now=now)
        window_phase, window_detail = self._planned_impulse_window_phase(user, now=now)
        if (
            not is_troubleshooting
            and planned_impulse_id
            and window_phase == "tail"
            and impulse_value < 0.58
            and not due_timer_active
        ):
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="低价值念头已过最佳表达窗口",
                delay_minutes=(45, 120),
                block_current=True,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(1.0, 3.0))
            return False, "低价值念头已过最佳窗口,已重新挑选"
        if not is_troubleshooting and self._promote_earlier_daily_greeting_event(user, now=now):
            planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
            next_at = _safe_float(user.get("next_proactive_at"), 0)
            impulse_value = self._planned_impulse_value(user, now=now)
            window_phase, window_detail = self._planned_impulse_window_phase(user, now=now)
        if (
            not is_troubleshooting
            and
            not due_timer_active
            and planned_source != "timer"
            and self._in_llm_timer_silence_window(user, now=now)
        ):
            self._remember_silenced_plan_for_timer(user, now=now)
            self._promote_upcoming_llm_timer_plan(user, now=now)
            return False, "用户预约静默窗口"
        if now < next_at:
            return False, "未到候选主动时间"
        if not is_troubleshooting and self._is_proactive_plan_stale(user, now=now) and not due_timer_active:
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now, delay_hours=(1, 4))
            return False, "候选主动计划已过期,已重新安排"
        inner_readiness = self._proactive_inner_readiness(user, now=now)
        inner_score = _safe_float(inner_readiness.get("score"), 0.55)
        if (
            not is_troubleshooting
            and not due_timer_active
            and planned_source != "timer"
            and inner_score < 0.36
            and impulse_value < 0.72
        ):
            delay_low = 90 if self._private_user_role(user) != "friend" else 240
            self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="Bot 状态/关系温度偏低,这个念头先忍住: " + _single_line(inner_readiness.get("detail"), 120),
                delay_minutes=(delay_low, delay_low + 150),
                block_current=False,
            )
            if _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(2.0, 6.0))
            return False, "Bot 状态/关系温度偏低,主动念头先收住"
        social_relay_note = self._unverified_social_relay_plan_reason(
            user,
            source=planned_source,
            has_trigger=bool(_single_line(user.get("planned_proactive_trigger_message_id"), 120)),
        )
        if not is_troubleshooting and social_relay_note:
            self._mark_planned_candidate_status(user, "blocked", social_relay_note)
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now, delay_hours=(1.5, 4.5))
            return False, social_relay_note
        suppressed_raw = user.get("greetings_suppressed_by_inbound", [])
        suppressed_greetings: set[str] = set()
        if isinstance(suppressed_raw, list):
            suppressed_greetings = {str(item).strip() for item in suppressed_raw if str(item).strip()}
        if (
            not is_troubleshooting
            and planned_reason in suppressed_greetings
            and self._is_greeting_reason(planned_reason)
            and planned_source != "timer"
            and not due_timer_active
        ):
            self._mark_planned_candidate_status(user, "blocked", "用户在该问候窗口内已经活跃过")
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now, delay_hours=(2, 5))
            return False, "用户在该问候窗口内已经活跃过"

        self._reset_daily_counter_if_needed(user)
        sent_today = _safe_int(user.get("sent_today"), 0)
        if (
            not is_troubleshooting
            and planned_reason == "morning_greeting"
            and planned_source != "timer"
            and not due_timer_active
            and sent_today > 0
        ):
            self._mark_planned_candidate_status(user, "blocked", "早安只适合作为当天第一句主动消息")
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now, delay_hours=(2, 5))
            return False, "早安只适合作为当天第一句主动消息"
        if not is_troubleshooting and _safe_int(user.get("sent_today"), 0) >= daily_limit:
            if not due_timer_active:
                self._schedule_next_proactive(user, now=now, delay_hours=(8, 16))
            return False, "已达每日上限"
        idle_minutes = self._effective_user_idle_minutes(user)
        recent_activity_at = self._latest_private_user_activity_ts(user)
        if not is_troubleshooting and not due_timer_active and now - recent_activity_at < idle_minutes * 60:
            idle_limit = (
                self._effective_user_greeting_idle_minutes(user) * 60
                if self._is_greeting_reason(planned_reason)
                else idle_minutes * 60
            )
            if now - recent_activity_at < idle_limit:
                if self._is_sticky_greeting_reason(planned_reason):
                    self._reschedule_greeting_within_window(user, planned_reason, now=now)
                else:
                    replaced = self._defer_or_replace_planned_impulse(
                        user,
                        now=now,
                        note="用户刚活跃过,当前念头先收住",
                        delay_minutes=(max(8.0, idle_limit / 60 * 0.5), max(15.0, idle_limit / 60 + 8.0)),
                        block_current=impulse_value < 0.52,
                    )
                    if replaced:
                        return False, "用户刚活跃过,已换用更贴近当前节奏的念头"
                return False, "用户刚活跃过"
        min_interval = self._effective_min_interval_seconds(user)
        if self._is_greeting_reason(planned_reason) and self._private_user_role(user) != "friend":
            min_interval = min(min_interval, self._greeting_min_interval_seconds(planned_reason))
        if not is_troubleshooting and not due_timer_active and now - _safe_float(user.get("last_sent"), 0) < min_interval:
            if self._is_sticky_greeting_reason(planned_reason):
                self._reschedule_greeting_within_window(user, planned_reason, now=now)
            else:
                remaining_minutes = max(5.0, (min_interval - (now - _safe_float(user.get("last_sent"), 0))) / 60)
                self._defer_or_replace_planned_impulse(
                    user,
                    now=now,
                    note="距离上次主动太近,当前念头先压低",
                    delay_minutes=(remaining_minutes, remaining_minutes + 30.0),
                    block_current=False,
                )
            return False, "发送间隔不足"
        planned_action = str(user.get("planned_proactive_action") or "message")
        normalizer = getattr(self, "_normalize_existing_plan_for_emotion", None)
        if not is_troubleshooting and callable(normalizer):
            emotion_note = normalizer(user, now=now)
            if emotion_note:
                planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) or planned_reason
                planned_action = self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=40) or planned_action or "message"
                if _safe_float(user.get("next_proactive_at"), 0) > now + 1:
                    return False, emotion_note
        if not is_troubleshooting and self._private_user_role(user) == "friend":
            before_friend_sanitize = (
                planned_reason,
                planned_action,
                _single_line(user.get("planned_proactive_topic"), 80),
                _single_line(user.get("planned_proactive_motive"), 180),
            )
            sanitized = self._sanitize_friend_proactive_plan_fields(
                user,
                reason=planned_reason,
                action=planned_action,
                topic=_single_line(user.get("planned_proactive_topic"), 80),
                motive=_single_line(user.get("planned_proactive_motive"), 180),
            )
            user["planned_proactive_reason"] = sanitized["reason"]
            user["planned_proactive_action"] = sanitized["action"]
            user["planned_proactive_topic"] = sanitized["topic"]
            user["planned_proactive_motive"] = sanitized["motive"]
            planned_reason = sanitized["reason"]
            planned_action = sanitized["action"]
            after_friend_sanitize = (
                planned_reason,
                planned_action,
                sanitized["topic"],
                sanitized["motive"],
            )
            if after_friend_sanitize != before_friend_sanitize:
                user["planned_proactive_impulse_id"] = ""
                user["planned_proactive_semantic_kind"] = ""
                user["planned_proactive_anchor_type"] = ""
                user["planned_proactive_semantic_score"] = 0
                user["planned_proactive_semantic_note"] = ""
                user["planned_proactive_model_judge_signature"] = ""
                user["planned_proactive_model_judge_result"] = {}
                user["planned_proactive_model_judge_at"] = 0
                self._mark_planned_candidate_status(user, "accepted", "朋友未回应状态下已降级为低压主动")
        if not is_troubleshooting and not self._friend_can_receive_proactive_reason(user, planned_reason, planned_action):
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now, delay_hours=(2, 6))
            return False, "朋友关系不接收敏感主动"
        planned_semantics = self._planned_proactive_semantics(user)
        semantic_score = _safe_float(planned_semantics.get("score"), 0.5)
        semantic_pressure = _safe_float(planned_semantics.get("pressure"), 0.4)
        semantic_risk = _safe_float(planned_semantics.get("risk"), 0.0)
        semantic_blocked = bool(planned_semantics.get("blocker"))
        if (
            not is_troubleshooting
            and not due_timer_active
            and planned_source != "timer"
            and (semantic_blocked or semantic_risk >= 0.45 or (semantic_score < 0.32 and semantic_pressure >= 0.58))
        ):
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="候选语义不够自然: " + _single_line(planned_semantics.get("note"), 120),
                delay_minutes=(90, 240),
                block_current=semantic_blocked or semantic_risk >= 0.45,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(2, 6))
            return False, "候选语义不够自然,已重新挑选"
        persona_alignment = self._planned_proactive_persona_alignment(user, now=now)
        persona_fit = _safe_float(persona_alignment.get("score"), 0.55)
        persona_blocked = bool(persona_alignment.get("blocker"))
        persona_threshold = 0.48 if self._private_user_role(user) == "friend" else 0.42
        if (
            not is_troubleshooting
            and not due_timer_active
            and planned_source != "timer"
            and (persona_blocked or persona_fit < persona_threshold)
        ):
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="人格/世界观贴合度不足: " + _single_line(persona_alignment.get("note"), 120),
                delay_minutes=(90, 240),
                block_current=True,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(2, 6))
            return False, "人格/世界观贴合度不足,已重新挑选"
        if due_timer_active:
            return True, "ok(timer)"
        ignored_streak = _safe_int(user.get("ignored_streak"), 0, 0)
        if (
            not is_troubleshooting
            and ignored_streak >= 2
            and impulse_value < (0.72 if self._private_user_role(user) == "friend" else 0.66)
        ):
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note=f"连续 {ignored_streak} 次未回应,低压念头先不打扰",
                delay_minutes=(90, 240),
                block_current=True,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(2.5, 6.0))
            return False, "连续未回应,低价值主动已收住"
        if not is_troubleshooting and not self._is_reason_allowed_now(planned_reason):
            if self._is_sticky_greeting_reason(planned_reason):
                self._reschedule_greeting_within_window(user, planned_reason, now=now)
                return False, "问候仍在窗口内,稍后再试"
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="计划动机不适合当前时间",
                delay_minutes=(45, 150),
                block_current=window_phase == "tail" or impulse_value < 0.6,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now)
            return False, "计划动机不适合当前时间"
        if self._private_user_role(user) == "friend" and self._action_has_photo_text(planned_action):
            fallback_action = self._fallback_action_for_unavailable(planned_action, user)
            if fallback_action != planned_action:
                planned_action = fallback_action
                user["planned_proactive_action"] = planned_action
        if not self._action_is_available(planned_action, user):
            load_defer_note = self._photo_text_load_defer_note(planned_action)
            if load_defer_note:
                self._defer_planned_photo_text_for_load(user, now=now, note=load_defer_note)
                return False, load_defer_note
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="动作不可用或媒体额度不足",
                delay_minutes=(90, 240),
                block_current=True,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(2, 6))
            return False, "动作不可用或媒体额度不足"
        if not is_troubleshooting and self._planned_proactive_recently_repeated(user):
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="近期主题过于相似",
                delay_minutes=(120, 360),
                block_current=True,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(2, 6))
            return False, "近期主动主题过于相似"
        if not is_troubleshooting and self._planned_event_exceeds_daypart_cap(user, planned_reason, next_at):
            delay = self._friend_proactive_spread_delay_hours(user, now=now)
            if delay is None:
                delay = (7.5, 10.5) if self._proactive_daypart_bucket_for_timestamp(next_at) == "late_night" else (2.5, 5.0)
            self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="当前时段主动已足够",
                delay_minutes=(delay[0] * 60, delay[1] * 60),
                block_current=False,
            )
            if _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=delay)
            if self._private_user_role(user) == "friend":
                return False, "朋友主动已按日内节奏延后"
            return False, "当前时段主动已足够,已避开扎堆"
        return True, "ok"

    def _planned_proactive_signature(self, user: dict[str, Any]) -> str:
        return self._proactive_topic_signature(
            user.get("planned_proactive_topic"),
            user.get("planned_proactive_motive"),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40),
        )

    def _planned_proactive_recently_repeated(self, user: dict[str, Any]) -> bool:
        signature = self._planned_proactive_signature(user)
        if not signature:
            return False
        return self._recent_proactive_topic_repeated(user, signature)

    def _unverified_social_relay_plan_reason(
        self,
        item: dict[str, Any],
        *,
        source: str = "",
        has_trigger: bool = False,
    ) -> str:
        if not isinstance(item, dict):
            return ""
        normalized_source = self._normalize_legacy_proactive_text(source or item.get("source") or item.get("planned_proactive_source"), limit=40)
        if normalized_source in {"timer", "troubleshooting", "simulation", "group_share"}:
            return ""
        if has_trigger:
            return ""
        reason = self._normalize_legacy_proactive_text(item.get("reason") or item.get("planned_proactive_reason"), limit=40)
        if reason in {"group_share", "news_share", "bili_video_share", "web_exploration_share"}:
            return ""
        if normalized_source not in {"event", "random", "unknown", ""}:
            return ""
        text = " ".join(
            _single_line(item.get(key), 180)
            for key in (
                "topic",
                "planned_proactive_topic",
                "motive",
                "planned_proactive_motive",
                "why",
                "scene",
                "impulse",
            )
            if _single_line(item.get(key), 180)
        )
        if not text:
            return ""
        relay_markers = ("转达", "转述", "转告", "带话", "捎话")
        if any(token in text for token in relay_markers):
            return "疑似第三方转述/带话内容,缺少真实触发来源"
        invite_markers = ("约", "邀请", "要不要去", "去不去", "一起", "夜宵", "吃饭", "见面", "碰头")
        soft_message_markers = ("留言", "说一声", "说一下", "告诉你一声", "通知你一声")
        third_party_patterns = (
            r"[\u4e00-\u9fffA-Za-z0-9_]{1,12}(?:说|问|发(?:来|了|的)?(?:消息)?|留言|约|邀请)",
            r"(?:他|她|TA|ta)(?:说|问|发(?:来|了|的)?|留言|约|邀请)",
            r"(?:他的|她的|TA的|ta的).{0,8}(?:消息|留言|邀约|邀请)",
        )
        has_third_party_signal = any(re.search(pattern, text) for pattern in third_party_patterns)
        if has_third_party_signal and any(token in text for token in soft_message_markers):
            return "疑似第三方留言/带话内容,缺少真实触发来源"
        if any(token in text for token in invite_markers) and has_third_party_signal:
            return "疑似第三方邀约内容,缺少真实触发来源"
        return ""

    def _planned_proactive_status_snapshot(self, user: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(user, dict):
            return {}
        keys = (
            "planned_candidate_id",
            "planned_proactive_impulse_id",
            "planned_proactive_reason",
            "planned_proactive_action",
            "planned_proactive_source",
            "planned_proactive_motive",
            "planned_proactive_topic",
            "planned_proactive_semantic_kind",
            "planned_proactive_anchor_type",
            "planned_proactive_semantic_score",
            "planned_proactive_semantic_note",
        )
        return {key: user.get(key) for key in keys}

    def _mark_planned_candidate_status(
        self,
        user: dict[str, Any],
        status: str,
        note: str = "",
        *,
        planned_snapshot: dict[str, Any] | None = None,
    ) -> None:
        restored_values: dict[str, Any] = {}
        if isinstance(planned_snapshot, dict) and planned_snapshot:
            for key, value in planned_snapshot.items():
                if value in (None, "", {}, []):
                    continue
                restored_values[key] = user.get(key)
                user[key] = value
        try:
            outcome_recorder = getattr(self, "_note_proactive_afterglow_outcome", None)
            if callable(outcome_recorder):
                try:
                    outcome_recorder(user, status=status, note=note)
                except Exception as exc:
                    logger.debug("[PrivateCompanion] 主动结果余韵记录失败: %s", _single_line(exc, 120))
            candidate_id = str(user.get("planned_candidate_id") or "")
            user_id = str(user.get("user_id") or user.get("id") or "")
            if candidate_id:
                for item in self._cleanup_proactive_candidate_pool():
                    if str(item.get("id") or "") == candidate_id:
                        item["status"] = status
                        item["note"] = _single_line(note, 160)
                        item["updated_ts"] = _now_ts()
                        break
            impulse_id = _single_line(user.get("planned_proactive_impulse_id"), 20)
            if not impulse_id:
                return
            for impulse in self._cleanup_proactive_impulses(user):
                if _single_line(impulse.get("id"), 20) != impulse_id:
                    continue
                impulse["updated_ts"] = _now_ts()
                impulse["last_status"] = _single_line(status, 24)
                impulse["last_note"] = _single_line(note, 160)
                if status in {"sent"}:
                    impulse["state"] = "sent"
                elif status in {"blocked", "cancelled", "dropped"}:
                    impulse["state"] = "blocked"
                elif status == "deferred":
                    impulse["state"] = "deferred"
                    next_at = _safe_float(user.get("next_proactive_at"), 0)
                    if next_at > 0:
                        impulse["window_start_at"] = next_at
                        impulse["preferred_ts"] = max(_safe_float(impulse.get("preferred_ts"), 0), next_at)
                        impulse["best_until_at"] = max(_safe_float(impulse.get("best_until_at"), 0), next_at + 20 * 60)
                        impulse["expire_at"] = max(_safe_float(impulse.get("expire_at"), 0), impulse["best_until_at"] + 40 * 60)
                else:
                    impulse["state"] = "queued"
                break
            is_send_retry_deferred = status == "deferred" and (
                "已保留待重发内容" in str(note or "") or "平台发送" in str(note or "")
            )
            if user_id and status in {"blocked", "cancelled", "dropped", "failed", "deferred"} and not is_send_retry_deferred:
                self._shrink_user_proactive_candidates(user_id, note=note)
        finally:
            for key, value in restored_values.items():
                user[key] = value

    def _proactive_decision_factors(self, user: dict[str, Any], *, now: float | None = None) -> list[dict[str, Any]]:
        now = _now_ts() if now is None else now
        factors: list[dict[str, Any]] = []

        def add(
            key: str,
            label: str,
            passed: bool,
            score: int,
            detail: str = "",
            *,
            blocker: bool = False,
        ) -> None:
            factors.append(
                {
                    "key": key,
                    "label": label,
                    "passed": bool(passed),
                    "score": int(score),
                    "detail": _single_line(detail, 160),
                    "blocker": bool(blocker),
                }
            )

        user_id = str(user.get("user_id") or user.get("id") or "")
        enabled = self._user_enabled_for_proactive(user_id, user)
        add("enabled", "用户启用", enabled, 18 if enabled else -80, "已启用" if enabled else "私聊对象未启用", blocker=not enabled)

        has_session = bool(user.get("umo"))
        add("session", "私聊会话", has_session, 12 if has_session else -70, "会话可用" if has_session else "缺少私聊会话", blocker=not has_session)

        if user.get("proactive_sending"):
            add("sending", "发送占用", False, -60, "上一条主动消息仍在发送中", blocker=True)
        else:
            add("sending", "发送占用", True, 6, "当前没有发送占用")

        daily_limit = self._effective_user_daily_limit(user)
        sent_today = _safe_int(user.get("sent_today"), 0)
        under_limit = daily_limit > 0 and sent_today < daily_limit
        if daily_limit <= 0:
            add("daily_limit", "每日上限", False, -55, "每日上限为 0", blocker=True)
        else:
            add(
                "daily_limit",
                "每日上限",
                under_limit,
                8 if under_limit else -40,
                f"{sent_today}/{daily_limit}",
                blocker=not under_limit,
            )

        due_timer_active = self._has_due_llm_timer(user, now=now)
        source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        rest_until = self._user_rest_silence_until(user, now=now)
        rest_blocked = rest_until > now and not due_timer_active and source != "timer"
        add(
            "rest",
            "休息静默",
            not rest_blocked,
            5 if not rest_blocked else -45,
            "未命中静默" if not rest_blocked else "用户明确休息中",
            blocker=rest_blocked,
        )

        quiet_blocked = self._is_quiet_time() and not self._can_send_insomnia_night_message(user)
        add(
            "quiet_hours",
            "免打扰",
            not quiet_blocked,
            4 if not quiet_blocked else -42,
            "当前可发" if not quiet_blocked else "处于免打扰时段",
            blocker=quiet_blocked,
        )

        rel_state = user.get("relationship_state")
        relationship_blocked = (
            self.enable_relationship_state_machine
            and isinstance(rel_state, dict)
            and rel_state.get("mode") == "backoff"
            and _safe_float(rel_state.get("backoff_until"), 0) > now
        )
        emotion_blocked = (
            bool(getattr(self, "enable_emotion_simulation", True))
            and isinstance(rel_state, dict)
            and rel_state.get("mode") in {"hurt", "refusing"}
            and _safe_float(rel_state.get("hurt_until"), 0) > now
        )
        relation_ok = not (relationship_blocked or emotion_blocked)
        relation_detail = "状态平稳"
        if isinstance(rel_state, dict) and rel_state.get("mode"):
            relation_detail = f"mode={_single_line(rel_state.get('mode'), 24)}"
        add(
            "relationship_gate",
            "关系/情绪闸门",
            relation_ok,
            7 if relation_ok else -48,
            relation_detail,
            blocker=not relation_ok,
        )

        next_at = _safe_float(user.get("next_proactive_at"), 0)
        planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        if next_at <= 0:
            add("planned", "候选计划", False, -12, "尚未安排下一次候选")
        else:
            due = now >= next_at
            add(
                "planned",
                "候选计划",
                due,
                10 if due else -10,
                (
                    self._environment_fromtimestamp(next_at).strftime("%m-%d %H:%M:%S")
                    if next_at > 0
                    else "未安排"
                ),
                blocker=False,
            )
        impulse_value = self._planned_impulse_value(user, now=now)
        window_phase, window_detail = self._planned_impulse_window_phase(user, now=now)
        phase_labels = {
            "before": "窗口未开始",
            "best": "最佳窗口",
            "tail": "窗口尾段",
            "expired": "已经过期",
            "unknown": "未记录",
        }
        window_ok = window_phase not in {"expired"}
        add(
            "impulse_window",
            "念头窗口",
            window_ok,
            7 if window_phase == "best" else 2 if window_phase == "tail" else -6 if window_phase == "before" else -35 if window_phase == "expired" else 0,
            f"{phase_labels.get(window_phase, window_phase)}｜{window_detail}",
            blocker=window_phase == "expired",
        )
        add(
            "impulse_value",
            "念头价值",
            impulse_value >= 0.55,
            8 if impulse_value >= 0.85 else 4 if impulse_value >= 0.65 else -8,
            f"{impulse_value:.2f}｜越高越像当前角色真的想说",
            blocker=False,
        )
        inner_readiness = self._proactive_inner_readiness(user, now=now)
        drive = inner_readiness.get("drive") if isinstance(inner_readiness.get("drive"), dict) else {}
        temperature = inner_readiness.get("temperature") if isinstance(inner_readiness.get("temperature"), dict) else {}
        inner_score = _safe_float(inner_readiness.get("score"), 0.55)
        add(
            "bot_drive",
            "Bot 开口欲",
            inner_score >= 0.36,
            7 if inner_score >= 0.72 else 3 if inner_score >= 0.5 else -14,
            f"{inner_score:.2f}｜{_single_line(inner_readiness.get('label'), 40)}｜{_single_line(drive.get('detail'), 70)}",
            blocker=inner_score < 0.28,
        )
        temp_score = _safe_float(temperature.get("score"), 0.55)
        add(
            "relationship_temperature",
            "关系温度",
            temp_score >= 0.34,
            7 if temp_score >= 0.7 else 3 if temp_score >= 0.48 else -16,
            f"{temp_score:.2f}｜{_single_line(temperature.get('label'), 24)}｜{_single_line(temperature.get('detail'), 80)}",
            blocker=temp_score < 0.24,
        )
        planned_impulse = self._planned_proactive_impulse(user)
        hesitation_count = _safe_int(planned_impulse.get("hesitation_count"), 0, 0, 20) if isinstance(planned_impulse, dict) else 0
        if hesitation_count > 0:
            add(
                "hesitation_memory",
                "犹豫记忆",
                True,
                min(6, 2 + hesitation_count),
                f"这个念头前面收住过 {hesitation_count} 次｜{_single_line(planned_impulse.get('hesitation_note'), 80)}",
                blocker=False,
            )
        semantics = self._planned_proactive_semantics(user)
        semantic_score = _safe_float(semantics.get("score"), 0.5)
        semantic_pressure = _safe_float(semantics.get("pressure"), 0.4)
        semantic_risk = _safe_float(semantics.get("risk"), 0.0)
        semantic_ok = not bool(semantics.get("blocker")) and semantic_risk < 0.45 and not (semantic_score < 0.32 and semantic_pressure >= 0.58)
        add(
            "candidate_semantics",
            "候选语义",
            semantic_ok,
            8 if semantic_score >= 0.68 else 4 if semantic_score >= 0.48 else -18,
            (
                f"{_single_line(semantics.get('kind'), 30)}/{_single_line(semantics.get('anchor_type'), 30)}"
                f"｜语义{semantic_score:.2f} 压力{semantic_pressure:.2f} 风险{semantic_risk:.2f}"
                f"｜{_single_line(semantics.get('note'), 70)}"
            ),
            blocker=not semantic_ok,
        )
        persona_alignment = self._planned_proactive_persona_alignment(user, now=now)
        persona_fit = _safe_float(persona_alignment.get("score"), 0.55)
        persona_threshold = 0.48 if self._private_user_role(user) == "friend" else 0.42
        persona_blocked = bool(persona_alignment.get("blocker")) and source != "timer"
        persona_ok = due_timer_active or source == "timer" or (not persona_blocked and persona_fit >= persona_threshold)
        add(
            "persona_fit",
            "人格/世界观贴合",
            persona_ok,
            8 if persona_fit >= 0.78 else 4 if persona_fit >= 0.58 else -18,
            f"{persona_fit:.2f}｜{_single_line(persona_alignment.get('note'), 110)}",
            blocker=not persona_ok,
        )
        model_signature = self._planned_proactive_model_judge_signature(user)
        model_judgement = self._cached_proactive_model_judgement(user, signature=model_signature, now=now)
        if isinstance(model_judgement, dict):
            model_decision = str(model_judgement.get("decision") or "")
            model_score = _safe_int(model_judgement.get("score"), 0, 0, 100)
            model_ok = model_decision in {"send", "rewrite"}
            add(
                "model_persona_judge",
                "模型人格判定",
                model_ok,
                8 if model_decision == "send" else 4 if model_decision == "rewrite" else -30,
                f"{model_decision or 'unknown'}｜{model_score}/100｜{_single_line(model_judgement.get('reason'), 90)}",
                blocker=not model_ok,
            )
        else:
            add(
                "model_persona_judge",
                "模型人格判定",
                True,
                0,
                "未执行；硬规则通过且到点发送前执行",
                blocker=False,
            )

        last_seen = self._latest_private_user_activity_ts(user)
        idle_minutes = self._effective_user_idle_minutes(user)
        if self._is_greeting_reason(planned_reason):
            idle_minutes = self._effective_user_greeting_idle_minutes(user)
        idle_seconds = max(0, idle_minutes) * 60
        idle_elapsed = now - last_seen if last_seen > 0 else 999999999.0
        idle_passed = due_timer_active or idle_elapsed >= idle_seconds
        add(
            "idle",
            "用户空闲",
            idle_passed,
            9 if idle_passed else -28,
            (
                f"已空闲 {self._format_elapsed(max(0, idle_elapsed))} / 至少 {self._format_elapsed(idle_seconds)}"
                if last_seen > 0
                else "暂无活跃记录"
            ),
            blocker=not idle_passed and not due_timer_active,
        )

        last_sent = _safe_float(user.get("last_sent"), 0)
        min_interval = self._effective_min_interval_seconds(user)
        if self._is_greeting_reason(planned_reason) and self._private_user_role(user) != "friend":
            min_interval = min(min_interval, self._greeting_min_interval_seconds(planned_reason))
        send_elapsed = now - last_sent if last_sent > 0 else 999999999.0
        interval_passed = due_timer_active or send_elapsed >= min_interval
        add(
            "interval",
            "发送间隔",
            interval_passed,
            8 if interval_passed else -25,
            (
                f"已过 {self._format_elapsed(max(0, send_elapsed))} / 至少 {self._format_elapsed(min_interval)}"
                if last_sent > 0
                else "还没有主动发送记录"
            ),
            blocker=not interval_passed and not due_timer_active,
        )

        if planned_reason:
            reason_allowed = due_timer_active or self._is_reason_allowed_now(planned_reason)
            add(
                "reason_window",
                "时段适配",
                reason_allowed,
                6 if reason_allowed else -18,
                planned_reason,
                blocker=not reason_allowed and not due_timer_active,
            )

        planned_action = str(user.get("planned_proactive_action") or "message")
        action_ok = self._action_is_available(planned_action, user)
        add(
            "action",
            "动作可用",
            action_ok,
            6 if action_ok else -24,
            planned_action or "message",
            blocker=not action_ok,
        )

        repeated = self._planned_proactive_recently_repeated(user)
        add(
            "dedupe",
            "主题去重",
            not repeated,
            6 if not repeated else -20,
            "近期无重复" if not repeated else "近期主动主题过于相似",
            blocker=repeated,
        )

        total_score = 50 + sum(int(item.get("score") or 0) for item in factors)
        factors.append(
            {
                "key": "total",
                "label": "综合评分",
                "passed": total_score >= 50,
                "score": max(0, min(100, total_score)),
                "detail": "分数越高越适合现在发",
                "blocker": False,
            }
        )
        return factors

    def _proactive_decision_snapshot(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        now = _now_ts() if now is None else now
        factors = self._proactive_decision_factors(user, now=now)
        blocker_labels = [item.get("label") for item in factors if item.get("blocker")]
        total_score = 0
        for item in factors:
            if item.get("key") == "total":
                total_score = _safe_int(item.get("score"), 0, 0, 100)
                break
        return {
            "score": total_score,
            "blockers": [str(item) for item in blocker_labels if str(item or "").strip()],
            "factors": factors,
            "generated_ts": now,
        }

    def _proactive_audit_log(self) -> list[dict[str, Any]]:
        raw = self.data.setdefault("proactive_audit_log", [])
        if not isinstance(raw, list):
            raw = []
            self.data["proactive_audit_log"] = raw
        return raw

    def _proactive_visible_text_preview(self, text: str, *, limit: int = 180) -> str:
        cleaner = getattr(self, "_visible_text_without_tts_reading", None)
        if callable(cleaner):
            try:
                return _single_line(cleaner(text, limit=limit), limit)
            except Exception:
                pass
        return _single_line(_strip_internal_message_blocks(text), limit)

    def _proactive_audit_signature(self, item: dict[str, Any], *, bucket_seconds: int = 300) -> str:
        updated = _safe_float(item.get("updated_ts") or item.get("created_ts"), 0)
        bucket = int(updated // max(1, bucket_seconds)) if updated > 0 else 0
        parts = [
            item.get("user_id"),
            item.get("status"),
            item.get("source"),
            item.get("reason"),
            item.get("action"),
            item.get("topic"),
            item.get("motive"),
            item.get("note"),
            bucket,
        ]
        return "|".join(_single_line(part, 120) for part in parts)

    @staticmethod
    def _proactive_audit_note_is_obsolete_fixed_error(note: Any) -> bool:
        text = str(note or "")
        if "NameError" not in text:
            return False
        return any(
            token in text
            for token in (
                "name 'topic' is not defined",
                "name 'name' is not defined",
            )
        )

    def _compact_proactive_audit_log(self) -> None:
        log = self._proactive_audit_log()
        compacted: list[dict[str, Any]] = []
        seen: dict[str, dict[str, Any]] = {}
        for item in log:
            if not isinstance(item, dict):
                continue
            if self._proactive_audit_note_is_obsolete_fixed_error(item.get("note")):
                item["status"] = "obsolete"
                item["note"] = "旧版本主动发送变量错误，当前版本已修复"
            signature = self._proactive_audit_signature(item)
            previous = seen.get(signature)
            if previous is None:
                seen[signature] = item
                compacted.append(item)
                continue
            previous["updated_ts"] = max(
                _safe_float(previous.get("updated_ts"), 0),
                _safe_float(item.get("updated_ts"), 0),
            )
            previous["duplicate_count"] = _safe_int(previous.get("duplicate_count"), 1, 1) + 1
            for key in ("text_preview", "original_text_preview", "final_text_preview", "image_path"):
                if item.get(key):
                    previous[key] = item.get(key)
            if item.get("extra_count") is not None:
                previous["extra_count"] = max(
                    _safe_int(previous.get("extra_count"), 0, 0),
                    _safe_int(item.get("extra_count"), 0, 0),
                )
        if len(compacted) != len(log):
            log[:] = compacted[-160:]

    def _append_proactive_audit(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        status: str,
        note: str = "",
        reason: str = "",
        action: str = "",
        text: str = "",
        original_text: str = "",
        final_text: str = "",
    ) -> str:
        now = _now_ts()
        audit_id = uuid.uuid4().hex[:12]
        semantics = self._planned_proactive_semantics(user)
        item = {
            "id": audit_id,
            "created_ts": now,
            "updated_ts": now,
            "user_id": str(user_id or user.get("user_id") or user.get("id") or ""),
            "status": _single_line(status, 32) or "unknown",
            "note": _single_line(note, 180),
            "source": self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) or "proactive",
            "reason": self._normalize_legacy_proactive_text(reason or user.get("planned_proactive_reason"), limit=40),
            "action": _single_line(action or user.get("planned_proactive_action"), 60) or "message",
            "topic": _single_line(user.get("planned_proactive_topic"), 100),
            "motive": _single_line(user.get("planned_proactive_motive"), 180),
            "semantic_kind": _single_line(user.get("planned_proactive_semantic_kind"), 40) or _single_line(semantics.get("kind"), 40),
            "semantic_anchor_type": _single_line(user.get("planned_proactive_anchor_type"), 40) or _single_line(semantics.get("anchor_type"), 40),
            "semantic_score": _safe_int(user.get("planned_proactive_semantic_score"), 0, 0, 100) or int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.0))) * 100),
            "semantic_pressure": int(max(0.0, min(1.0, _safe_float(semantics.get("pressure"), 0.0))) * 100),
            "semantic_risk": int(max(0.0, min(1.0, _safe_float(semantics.get("risk"), 0.0))) * 100),
            "semantic_note": _single_line(user.get("planned_proactive_semantic_note"), 180) or _single_line(semantics.get("note"), 180),
            "scheduled_ts": _safe_float(user.get("next_proactive_at"), 0),
            "candidate_id": _single_line(user.get("planned_candidate_id"), 40),
            "umo": _single_line(user.get("umo"), 180),
            "text_preview": self._proactive_visible_text_preview(text) if text else "",
            "original_text_preview": self._proactive_visible_text_preview(original_text) if original_text else "",
            "final_text_preview": self._proactive_visible_text_preview(final_text) if final_text else "",
        }
        log = self._proactive_audit_log()
        signature = self._proactive_audit_signature(item)
        for existing in reversed(log[-30:]):
            if not isinstance(existing, dict):
                continue
            if self._proactive_audit_signature(existing) != signature:
                continue
            existing["updated_ts"] = now
            existing["duplicate_count"] = _safe_int(existing.get("duplicate_count"), 1, 1) + 1
            for key in ("text_preview", "original_text_preview", "final_text_preview"):
                if item.get(key):
                    existing[key] = item.get(key)
            return _single_line(existing.get("id"), 40) or audit_id
        log.append(item)
        self._compact_proactive_audit_log()
        del log[:-160]
        return audit_id

    def _update_proactive_audit(
        self,
        audit_id: str,
        *,
        status: str,
        note: str = "",
        text: str = "",
        image_path: str = "",
        extra_count: int | None = None,
        action: str = "",
        reason: str = "",
        original_text: str = "",
        final_text: str = "",
    ) -> None:
        if not audit_id:
            return
        for item in reversed(self._proactive_audit_log()):
            if str(item.get("id") or "") != str(audit_id):
                continue
            item["status"] = _single_line(status, 32) or item.get("status") or "unknown"
            item["updated_ts"] = _now_ts()
            if note:
                item["note"] = _single_line(note, 180)
            if text:
                item["text_preview"] = self._proactive_visible_text_preview(text)
            if original_text:
                item["original_text_preview"] = self._proactive_visible_text_preview(original_text)
            if final_text:
                item["final_text_preview"] = self._proactive_visible_text_preview(final_text)
            if image_path:
                item["image_path"] = _single_line(image_path, 260)
            if extra_count is not None:
                item["extra_count"] = max(0, int(extra_count))
            if action:
                item["action"] = _single_line(action, 60)
            if reason:
                item["reason"] = _single_line(reason, 40)
            self._compact_proactive_audit_log()
            break

    def _recover_stale_proactive_sending(self, user: dict[str, Any], *, now: float | None = None) -> bool:
        if not user.get("proactive_sending"):
            return False
        now = now or _now_ts()
        started_at = _safe_float(user.get("proactive_sending_started_at"), 0)
        if started_at > 0 and now - started_at < 8 * 60:
            return False
        user["proactive_sending"] = False
        user["proactive_sending_started_at"] = 0
        logger.warning(
            "[PrivateCompanion] 检测到残留的主动发送标记,已自动清理: user=%s started_at=%s",
            user.get("user_id") or user.get("id") or "unknown",
            self._environment_fromtimestamp(started_at).strftime("%m-%d %H:%M:%S") if started_at > 0 else "unknown",
        )
        return True

    def _is_recent_poke_echo(self, user: dict[str, Any], text: str, *, now: float | None = None) -> bool:
        now = now or _now_ts()
        suppress_until = _safe_float(user.get("poke_echo_suppress_until"), 0)
        if suppress_until <= 0 or now > suppress_until:
            return False
        return not bool(_single_line(text, 120))

    def _explain_proactive_decision(self, user: dict[str, Any]) -> str:
        probe = dict(user)
        decision, reason = self._should_send(probe)
        now = _now_ts()
        snapshot = self._proactive_decision_snapshot(probe, now=now)
        planned_reason = self._normalize_legacy_proactive_text(probe.get("planned_proactive_reason"), limit=40)
        planned_action = str(probe.get("planned_proactive_action") or "message")
        planned_source = self._normalize_legacy_proactive_text(probe.get("planned_proactive_source"), limit=40)
        planned_motive = _single_line(probe.get("planned_proactive_motive"), 48)
        next_at = _safe_float(probe.get("next_proactive_at"), 0)
        planned_impulse_id = _single_line(probe.get("planned_proactive_impulse_id"), 20)
        planned_best_until = _safe_float(probe.get("planned_proactive_best_until_at"), 0)
        timer_event = self._get_active_llm_timer(probe)
        active_impulses = [
            item for item in self._cleanup_proactive_impulses(probe, now=now)
            if isinstance(item, dict) and str(item.get("state") or "queued") in {"queued", "deferred"}
        ]
        next_at_text = (
            self._environment_fromtimestamp(next_at).strftime("%m-%d %H:%M:%S")
            if next_at > 0
            else "未安排"
        )
        sent_today = _safe_int(probe.get("sent_today"), 0)
        sent_greetings = probe.get("greetings_sent")
        if not isinstance(sent_greetings, list):
            sent_greetings = []
        suppressed_greetings = probe.get("greetings_suppressed_by_inbound")
        if not isinstance(suppressed_greetings, list):
            suppressed_greetings = []
        last_activity_at = self._latest_private_user_activity_ts(probe)
        last_sent_at = _safe_float(probe.get("last_sent"), 0)
        last_seen_gap = now - last_activity_at if last_activity_at > 0 else -1
        last_sent_gap = now - last_sent_at if last_sent_at > 0 else -1
        idle_limit = (
            self._effective_user_greeting_idle_minutes(probe) * 60
            if self._is_greeting_reason(planned_reason)
            else self._effective_user_idle_minutes(probe) * 60
        )
        min_interval = self._effective_min_interval_seconds(probe)
        if self._is_greeting_reason(planned_reason) and self._private_user_role(probe) != "friend":
            min_interval = min(min_interval, self._greeting_min_interval_seconds(planned_reason))
        reason_allowed = self._is_reason_allowed_now(planned_reason)
        moment_ok = True
        if reason == "未到候选主动时间":
            reason_allowed_text = "到点后再检查"
            moment_ok_text = "到点后再检查"
        else:
            reason_allowed_text = "通过" if reason_allowed else "不适合"
            moment_ok_text = "候选到点即发送"
        lines = [
            f"主动判定：{'会发送' if decision else '这次不发'}",
            f"原因：{reason}",
            f"综合评分：{_safe_int(snapshot.get('score'), 0, 0, 100)}/100",
            f"下次候选：{next_at_text}",
            f"计划：{planned_reason or '未记录'}｜{planned_action}"
            + (f"｜计划源：{planned_source}" if planned_source else "")
            + (f"｜念头ID：{planned_impulse_id}" if planned_impulse_id else "")
            + (f"｜话题：{_single_line(probe.get('planned_proactive_topic'), 24)}" if _single_line(probe.get("planned_proactive_topic"), 24) else "")
            + (f"｜动机：{planned_motive}" if planned_motive else "")
            + (f"｜最佳窗口到 {self._environment_fromtimestamp(planned_best_until).strftime('%H:%M:%S')}" if planned_best_until > 0 else "")
            + (f"｜来源：模型预约" if isinstance(timer_event, dict) and _safe_float(timer_event.get("scheduled_ts"), 0) == next_at else ""),
            f"潜在念头：{len(active_impulses)} 个待选",
            f"今日已发：{sent_today}/{self._effective_user_daily_limit(probe)}｜软目标约 {self._soft_daily_target(probe):.1f}",
            f"今日问候：已发 {', '.join(str(item) for item in sent_greetings) or '无'}｜被用户消息跳过 {', '.join(str(item) for item in suppressed_greetings) or '无'}",
            f"免打扰：{'是' if self._is_quiet_time() else '否'}｜失眠特例：{'可用' if self._can_send_insomnia_night_message(probe) else '不可用'}",
            f"距用户上次活跃：{self._format_elapsed(max(0, last_seen_gap)) if last_seen_gap >= 0 else '从未'}｜要求至少 {self._format_elapsed(idle_limit)}",
            f"距上次主动：{self._format_elapsed(max(0, last_sent_gap)) if last_sent_gap >= 0 else '从未'}｜要求至少 {self._format_elapsed(min_interval)}",
            f"时间窗适配：{reason_allowed_text}｜自然动机：{moment_ok_text}",
        ]
        blockers = snapshot.get("blockers") if isinstance(snapshot.get("blockers"), list) else []
        if blockers:
            lines.append("阻塞项：" + " / ".join(_single_line(item, 32) for item in blockers[:6] if _single_line(item, 32)))
        factor_lines = []
        factors = snapshot.get("factors") if isinstance(snapshot.get("factors"), list) else []
        for item in factors:
            if not isinstance(item, dict) or item.get("key") in {"total"}:
                continue
            label = _single_line(item.get("label"), 24)
            detail = _single_line(item.get("detail"), 90)
            state_text = "通过" if item.get("passed") else "未通过"
            score_text = f"{_safe_int(item.get('score'), 0, -100, 100):+d}"
            if label:
                factor_lines.append(f"- {label}：{state_text}（{score_text}）" + (f"｜{detail}" if detail else ""))
        if factor_lines:
            lines.append("判定分解：")
            lines.extend(factor_lines[:12])
        return "\n".join(lines)

    def _simulation_label(self, user: dict[str, Any]) -> str:
        raw = user.get("simulation_mode")
        if isinstance(raw, dict):
            label = _single_line(raw.get("label"), 24)
            if label:
                return label
        return "压缩测试"

    def _should_send_simulation(self, user: dict[str, Any]) -> tuple[bool, str]:
        sim = user.get("simulation_mode")
        if not isinstance(sim, dict) or not sim.get("active"):
            return False, "未处于模拟模式"
        now = _now_ts()
        self._sync_simulation_next_event(user, now=now)
        next_at = _safe_float(user.get("next_proactive_at"), 0)
        label = self._simulation_label(user)
        if next_at <= 0:
            self._finish_simulation_mode(user)
            return False, f"{label}已结束"
        if now < next_at:
            return False, f"{label}等待下一条主动消息"
        return True, "simulation"

    def _sync_simulation_next_event(self, user: dict[str, Any], *, now: float | None = None) -> None:
        sim = user.get("simulation_mode")
        if not isinstance(sim, dict):
            return
        events = sim.get("events")
        if not isinstance(events, list) or not events:
            self._finish_simulation_mode(user)
            return
        now = now or _now_ts()
        remaining = [event for event in events if isinstance(event, dict)]
        if not remaining:
            self._finish_simulation_mode(user)
            return
        remaining.sort(key=lambda item: _safe_float(item.get("_scheduled_ts"), now))
        sim["events"] = remaining
        current = remaining[0]
        user["next_proactive_at"] = _safe_float(current.get("_scheduled_ts"), now)
        user["planned_proactive_reason"] = self._normalize_legacy_proactive_text(current.get("reason"), limit=40) or "check_in"
        user["planned_proactive_action"] = self._normalize_legacy_proactive_text(current.get("action"), limit=40) or "message"
        user["planned_proactive_source"] = "simulation"
        user["planned_proactive_motive"] = _single_line(current.get("motive"), 140)
        user["planned_proactive_topic"] = _single_line(current.get("topic"), 60)
        scheduled_ts = _safe_float(user.get("next_proactive_at"), now)
        user["planned_proactive_impulse_id"] = ""
        user["planned_proactive_window_start_at"] = scheduled_ts
        active_span, grace_span = self._proactive_impulse_default_window_seconds(user["planned_proactive_reason"])
        user["planned_proactive_best_until_at"] = scheduled_ts + active_span
        user["planned_proactive_expire_at"] = scheduled_ts + active_span + grace_span
        semantics = self._planned_proactive_semantics(user)
        user["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
        user["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
        user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
        user["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
        user["planned_event_chain"] = [] if self._private_user_role(user) == "friend" else (
            list(current.get("chain") or []) if isinstance(current.get("chain"), list) else []
        )
        user["planned_opener_mode"] = ""
        user["planned_followup_kind"] = ""
        user["planned_proactive_quota_exempt"] = bool(current.get("_free_screen_peek"))

    def _consume_simulation_event(self, user: dict[str, Any]) -> None:
        sim = user.get("simulation_mode")
        if not isinstance(sim, dict):
            return
        events = sim.get("events")
        if not isinstance(events, list) or not events:
            self._finish_simulation_mode(user)
            return
        sim["events"] = [event for event in events[1:] if isinstance(event, dict)]
        sim["sent_count"] = _safe_int(sim.get("sent_count"), 0, 0) + 1
        self._sync_simulation_next_event(user)

    def _finish_simulation_mode(self, user: dict[str, Any]) -> None:
        user["simulation_mode"] = {}
        user["next_proactive_at"] = 0
        user["planned_proactive_reason"] = ""
        user["planned_proactive_action"] = ""
        user["planned_proactive_source"] = ""
        user["planned_proactive_motive"] = ""
        user["planned_proactive_topic"] = ""
        user["planned_proactive_impulse_id"] = ""
        user["planned_proactive_window_start_at"] = 0
        user["planned_proactive_best_until_at"] = 0
        user["planned_proactive_expire_at"] = 0
        user["planned_proactive_semantic_kind"] = ""
        user["planned_proactive_anchor_type"] = ""
        user["planned_proactive_semantic_score"] = 0
        user["planned_proactive_semantic_note"] = ""
        user["planned_event_chain"] = []
        user["planned_opener_mode"] = ""
        user["planned_followup_kind"] = ""
        user["planned_proactive_quota_exempt"] = False

    def _available_test_actions(self, user: dict[str, Any]) -> list[str]:
        actions = ["message"]
        if self._screen_glance_available(user):
            actions.append("screen_peek")
        if self._photo_text_available(user):
            actions.append("photo_text")
        if self._voice_available(user):
            actions.append("voice")
        if self._jm_cosmos_read_available(user):
            actions.append("jm_cosmos_read")
        actions.extend(f"external:{item['name']}" for item in self._available_external_proactive_abilities(user) if item.get("name"))
        return actions

    @staticmethod
    def _normalize_external_ability_name(value: Any) -> str:
        text = str(value or "").strip().lower()
        text = re.sub(r"[^a-z0-9_.:-]+", "_", text)
        return text[:64].strip("_")

    def _external_ability_store(self) -> dict[str, Any]:
        if not isinstance(getattr(self, "data", None), dict):
            self.data = {}
        store = self.data.setdefault("external_proactive_abilities", {})
        if not isinstance(store, dict):
            store = {}
            self.data["external_proactive_abilities"] = store
        return store

    def register_external_proactive_ability(self, spec: dict[str, Any]) -> bool:
        if not isinstance(spec, dict):
            return False
        name = self._normalize_external_ability_name(spec.get("name"))
        executor = spec.get("executor")
        if not name or not callable(executor):
            logger.warning("[PrivateCompanion] 外部主动能力注册失败: name/executor 无效")
            return False
        default_config = spec.get("default_config") if isinstance(spec.get("default_config"), dict) else {}
        config_schema = spec.get("config_schema") if isinstance(spec.get("config_schema"), dict) else {}
        meta = {
            "name": name,
            "module": _single_line(spec.get("module"), 24) or "外部主动能力",
            "label": _single_line(spec.get("label"), 32) or name,
            "description": _single_line(spec.get("description"), 160),
            "when": _single_line(spec.get("when"), 120) or "外部插件认为合适的场景",
            "use_for": _single_line(spec.get("use_for"), 120) or _single_line(spec.get("description"), 120),
            "avoid": _single_line(spec.get("avoid"), 120) or "不要暴露插件调用过程,不要硬触发",
            "default_enabled": bool(spec.get("default_enabled", False)),
            "share_probability": max(0.0, min(1.0, _safe_float(spec.get("share_probability"), _safe_float(default_config.get("share_probability"), 0.12)))),
            "min_interval_hours": max(0.0, _safe_float(spec.get("min_interval_hours"), _safe_float(default_config.get("min_interval_hours"), 12))),
            "config_schema": deepcopy(config_schema),
            "default_config": deepcopy(default_config),
        }
        self._external_proactive_abilities[name] = {**meta, "executor": executor}
        try:
            store = self._external_ability_store()
            item = store.get(name) if isinstance(store.get(name), dict) else {}
            config = item.get("config") if isinstance(item.get("config"), dict) else {}
            merged_config = {**default_config, **config}
            item.update({
                "name": name,
                "module": meta["module"],
                "label": meta["label"],
                "description": meta["description"],
                "when": meta["when"],
                "use_for": meta["use_for"],
                "avoid": meta["avoid"],
                "enabled": bool(item.get("enabled", meta["default_enabled"])),
                "share_probability": _safe_float(item.get("share_probability"), meta["share_probability"], 0.0),
                "min_interval_hours": _safe_float(item.get("min_interval_hours"), meta["min_interval_hours"], 0.0),
                "config": merged_config,
                "config_schema": deepcopy(config_schema),
                "registered": True,
                "updated_ts": _now_ts(),
            })
            store[name] = item
            self._save_data_sync()
        except Exception as exc:
            logger.debug("[PrivateCompanion] 外部主动能力状态保存失败: %s", exc)
        logger.info("[PrivateCompanion] 已注册外部主动能力: %s", name)
        return True

    def unregister_external_proactive_ability(self, name: str) -> bool:
        normalized = self._normalize_external_ability_name(name)
        removed = self._external_proactive_abilities.pop(normalized, None) is not None
        try:
            store = self._external_ability_store()
            item = store.get(normalized)
            if isinstance(item, dict):
                item["registered"] = False
                item["updated_ts"] = _now_ts()
                self._save_data_sync()
        except Exception:
            pass
        return removed

    def external_proactive_abilities(self) -> list[dict[str, Any]]:
        store = self.data.get("external_proactive_abilities") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(store, dict):
            store = {}
        names = sorted(set(store.keys()) | set(self._external_proactive_abilities.keys()))
        items: list[dict[str, Any]] = []
        for name in names:
            runtime = self._external_proactive_abilities.get(name, {})
            stored = store.get(name) if isinstance(store.get(name), dict) else {}
            merged = {**{k: v for k, v in runtime.items() if k != "executor"}, **stored}
            merged["name"] = name
            merged["available"] = callable(runtime.get("executor"))
            merged["registered"] = bool(runtime)
            merged["enabled"] = bool(merged.get("enabled", merged.get("default_enabled", False)))
            merged["share_probability"] = max(0.0, min(1.0, _safe_float(merged.get("share_probability"), 0.0)))
            merged["min_interval_hours"] = max(0.0, _safe_float(merged.get("min_interval_hours"), 0.0))
            items.append(merged)
        return items

    def _external_ability_config(self, name: str) -> dict[str, Any]:
        store = self.data.get("external_proactive_abilities") if isinstance(self.data.get("external_proactive_abilities"), dict) else {}
        item = store.get(name) if isinstance(store.get(name), dict) else {}
        config = item.get("config") if isinstance(item.get("config"), dict) else {}
        return dict(config)

    def _external_ability_enabled(self, name: str) -> bool:
        item = next((entry for entry in self.external_proactive_abilities() if entry.get("name") == name), None)
        if not isinstance(item, dict):
            return False
        return bool(item.get("enabled") and item.get("available"))

    def _available_external_proactive_abilities(self, user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        now = _now_ts()
        items: list[dict[str, Any]] = []
        for item in self.external_proactive_abilities():
            name = str(item.get("name") or "")
            if not name or not item.get("enabled") or not item.get("available"):
                continue
            last = _safe_float(item.get("last_executed_ts"), 0)
            cooldown = _safe_float(item.get("min_interval_hours"), 0) * 3600
            if cooldown > 0 and last > 0 and now - last < cooldown:
                continue
            items.append(item)
        return items

    def _available_proactive_abilities(self, user: dict[str, Any] | None = None) -> list[dict[str, str]]:
        user = user if isinstance(user, dict) else {}
        available = {"message"}
        if self._screen_glance_available(user):
            available.add("screen_peek")
        if self._photo_text_available(user):
            available.add("photo_text")
        if self._poke_available() and self._effective_user_poke_daily_limit(user) > 0:
            available.add("poke")
        if self._voice_available(user):
            available.add("voice")
        if self._jm_cosmos_read_available(user):
            available.add("jm_cosmos_read")
        items: list[dict[str, str]] = []
        for raw in PROACTIVE_ABILITY_REGISTRY:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            if not name or name not in available:
                continue
            items.append({str(key): str(value) for key, value in raw.items()})
        for raw in self._available_external_proactive_abilities(user):
            name = str(raw.get("name") or "").strip()
            if not name:
                continue
            items.append(
                {
                    "module": _single_line(raw.get("module"), 24) or "外部主动能力",
                    "name": f"external:{name}",
                    "label": _single_line(raw.get("label"), 32) or name,
                    "when": _single_line(raw.get("when"), 120) or "外部插件认为合适时",
                    "use_for": _single_line(raw.get("use_for"), 120) or _single_line(raw.get("description"), 120),
                    "avoid": _single_line(raw.get("avoid"), 120) or "不要暴露插件调用过程",
                }
            )
        return items

    def _format_proactive_ability_search_hint(self, user: dict[str, Any] | None = None) -> str:
        abilities = self._available_proactive_abilities(user)
        if not abilities:
            return "可用动作：message=普通文字。"
        terms = self._worldview_terms()
        lines = ["可用动作："]
        for item in abilities:
            name = _single_line(item.get("name"), 24)
            label = _single_line(item.get("label"), 16)
            when = _single_line(item.get("when"), 80)
            use_for = _single_line(item.get("use_for"), 80)
            if name == "screen_peek":
                label = f"观察{terms['screen']}"
                when = when.replace("轻窥屏", f"看一眼{terms['screen']}").replace("探头一下", "轻轻确认一下")
            elif name == "jm_cosmos_read":
                label = terms["private_reading"]
                when = f"有空、无聊或夜里自己想给{terms['bookshelf']}{terms['secret_drawer']}添一点阅读内容"
                use_for = "内部阅读、低频形成读后印象,是否提起交给人格"
            elif name == "photo_text" and terms.get("mode") in {"fantasy", "sci_fi"}:
                label = "画面加一句话"
            lines.append(
                "- {name}（{label}）：{when}；{use_for}".format(
                    name=name,
                    label=label,
                    when=when,
                    use_for=use_for,
                )
            )
        preference_hint = self._action_preference_hint(user)
        if preference_hint:
            lines.append("用户媒介偏好：\n" + preference_hint)
        return "\n".join(lines)

    def _format_proactive_ability_list_for_user(self, user: dict[str, Any] | None = None) -> str:
        abilities = self._available_proactive_abilities(user)
        if not abilities:
            return "当前主动能力：文字私聊。"
        terms = self._worldview_terms()
        lines = ["当前主动能力："]
        for item in abilities:
            name = str(item.get("name") or "")
            label = item.get("label")
            when = item.get("when")
            if name == "screen_peek":
                label = f"观察{terms['screen']}"
            elif name == "jm_cosmos_read":
                label = terms["private_reading"]
            lines.append(
                f"- {item.get('module')}/{name}：{label}｜{when}"
            )
        return "\n".join(lines)

    def _summarize_test_action_labels(self, actions: list[str]) -> str:
        labels = {
            "message": "文字",
            "screen_peek": "窥屏",
            "photo_text": "发图",
            "poke": "戳一戳",
            "voice": "语音",
            "jm_cosmos_read": "私下阅读",
        }
        return "、".join(labels.get(action, action) for action in actions)

    def _build_full_test_detail_prompt(
        self,
        segment: dict[str, Any],
        plan: dict[str, Any],
        state: dict[str, Any],
        actions: list[str],
        *,
        missing_actions: list[str] | None = None,
    ) -> str:
        base_prompt = self._build_detail_enhancement_prompt(segment, plan, state)
        action_text = "、".join(actions) if actions else "message"
        extra = [
            "",
            "【这次是临时完整主动链测试】",
            "请只围绕这一段日程生成一串用于真实测试的 proactive_events。",
            "要求它们仍然像正常生活里会长出来的主动消息，不要写成“这是测试”或功能演示。",
            f"这轮测试可用的主动行为有：{action_text}。",
            "请尽量让 proactive_events 覆盖每一种可用行为至少一次；如果某种行为实在不合时宜，也要优先找一个勉强自然的切入点，而不是完全放弃。",
            "这些 proactive_events 之后会被压缩成每两分钟一条真实发送，所以你只需要负责把这一整段里的多个主动契机安排出来。",
            "today_events 仍然保持正常生活感，proactive_events 要像从这段生活里自己长出来。",
            "不要把‘测试’、‘跑满能力’、‘验证功能’写进输出里。",
        ]
        if missing_actions:
            extra.extend(
                [
                    "",
                    "【补正要求】",
                    f"上一轮结果还缺少这些主动行为：{'、'.join(missing_actions)}。",
                    "这一轮请重点补齐缺失行为，同时保持整段仍然像同一个人的连续生活。",
                ]
            )
        return base_prompt + "\n" + "\n".join(extra)

    async def _generate_full_test_detail_enhancement(
        self,
        segment: dict[str, Any],
        plan: dict[str, Any],
        state: dict[str, Any],
        actions: list[str],
    ) -> tuple[dict[str, Any], list[str]]:
        required_actions = [action for action in actions if action in {"message", "screen_peek", "photo_text", "voice", "jm_cosmos_read"}]
        last_normalized = {
            "summary": "这一段按原日程慢慢推进。",
            "today_events": [],
            "proactive_events": [],
            "long_term_events": [],
        }
        missing_actions = list(required_actions)
        for _ in range(3):
            prompt = self._build_full_test_detail_prompt(
                segment,
                plan,
                state,
                required_actions,
                missing_actions=missing_actions if missing_actions and missing_actions != required_actions else None,
            )
            raw_text = await self._llm_call(
                prompt,
                max_tokens=1000,
                provider_id=self._task_provider(
                    self.detail_enhancement_provider_id,
                    self.daily_plan_provider_id,
                    self.mai_style_provider_id,
                ),
                task="full_test_detail",
            )
            payload = self._extract_json_payload(raw_text or "")
            if not isinstance(payload, dict):
                continue
            normalized = self._normalize_story_plan(
                {
                    "today_events": payload.get("today_events", []),
                    "proactive_events": payload.get("proactive_events", []),
                    "long_term_events": [],
                }
            )
            normalized["summary"] = _single_line(payload.get("summary"), 160)
            last_normalized = normalized
            present = {
                str(item.get("action") or "message")
                for item in normalized.get("proactive_events", [])
                if isinstance(item, dict)
            }
            missing_actions = [action for action in required_actions if action not in present]
            if not missing_actions:
                break
        return last_normalized, missing_actions

    def _build_full_test_events(
        self,
        detail: dict[str, Any],
        *,
        actions: list[str],
        segment: dict[str, Any] | None = None,
        spacing_seconds: int = 120,
    ) -> list[dict[str, Any]]:
        proactive_events = detail.get("proactive_events", []) if isinstance(detail, dict) else []
        if not isinstance(proactive_events, list):
            proactive_events = []
        usable = [dict(item) for item in proactive_events if isinstance(item, dict)]
        segment_start = _safe_int((segment or {}).get("start"), -1, -1)
        segment_end = _safe_int((segment or {}).get("end"), -1, -1)
        if segment_start >= 0 and segment_end > segment_start:
            scoped: list[dict[str, Any]] = []
            for item in usable:
                start, end = self._parse_window_minutes(str(item.get("window") or ""))
                if start is None or end is None:
                    continue
                if start < segment_start or end > segment_end:
                    continue
                scoped.append(item)
            usable = scoped
        required_actions = [action for action in actions if action in {"message", "screen_peek", "photo_text", "voice", "jm_cosmos_read"}]
        filtered: list[dict[str, Any]] = []
        for action in required_actions:
            matched = next((item for item in usable if str(item.get("action") or "message") == action and item not in filtered), None)
            if matched:
                filtered.append(matched)
        for item in usable:
            action = str(item.get("action") or "message")
            if action in required_actions and item not in filtered:
                filtered.append(item)
        if not filtered:
            filtered = [dict(item) for item in _SIMULATION_FALLBACK_EVENTS]
            for item in filtered:
                item["motive"] = self._normalize_event_motive(item)
        filtered.sort(
            key=lambda item: (
                (self._parse_window_minutes(str(item.get("window") or ""))[0])
                if self._parse_window_minutes(str(item.get("window") or ""))[0] is not None
                else 24 * 60
            )
        )
        start_ts = _now_ts() + 20
        events: list[dict[str, Any]] = []
        for index, item in enumerate(filtered):
            cloned = dict(item)
            cloned["_scheduled_ts"] = start_ts + index * spacing_seconds
            cloned["_simulated_window"] = str(item.get("window") or "")
            events.append(cloned)
        return events

    def _build_single_poke_test_event(
        self,
        *,
        user: dict[str, Any],
        segment: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item = (segment or {}).get("item") if isinstance(segment, dict) else {}
        item = item if isinstance(item, dict) else {}
        topic = (
            _single_line(((detail or {}).get("proactive_events") or [{}])[0].get("topic"), 60)
            if isinstance((detail or {}).get("proactive_events"), list) and (detail or {}).get("proactive_events")
            else ""
        )
        if not topic:
            topic = _single_line(item.get("activity"), 60) or "刚才那条内容"
        motive = self._normalize_internal_motive_text(
            f"关于“{topic}”，想用戳一戳提醒一下用户"
        )
        window = ""
        if isinstance(segment, dict):
            start = _safe_int(segment.get("start"), -1, -1)
            end = _safe_int(segment.get("end"), -1, -1)
            if start >= 0 and end > start:
                window = f"{self._minutes_to_hhmm(start)}-{self._minutes_to_hhmm(end)}"
        return {
            "window": window,
            "reason": "diary_share",
            "action": "poke",
            "why": _single_line(item.get("activity"), 80) or "突然很想戳你一下",
            "topic": topic,
            "motive": motive,
            "scene": _single_line(((detail or {}).get("summary")), 80) or "眼前这一小段",
            "tone": "轻轻使坏",
            "impulse": "先戳一下，再看看你会不会回头",
            "chain": [],
            "_scheduled_ts": _now_ts() + 3,
            "_simulated_window": window or "立即触发",
        }

    def _maybe_upgrade_planned_message_action(
        self,
        action: str,
        *,
        reason: str,
        user: dict[str, Any],
        motive: str = "",
        planned_event: dict[str, Any] | None = None,
    ) -> str:
        normalized = str(action or "message").strip() or "message"
        if normalized != "message":
            return self._fallback_action_for_unavailable(normalized, user)
        if isinstance(planned_event, dict) and planned_event.get("_daily_greeting"):
            return "message"
        candidates: list[tuple[str, float]] = []
        event_text = ""
        if isinstance(planned_event, dict):
            event_text = " ".join(
                _single_line(planned_event.get(key), 80)
                for key in ("topic", "why", "scene", "motive", "impulse")
            )
        combined_hint = f"{event_text} {motive}"
        if self._screen_glance_available(user) and reason in {"check_in", "quiet_care", "background_schedule"}:
            candidates.append(("screen_peek", 1.15))
        if (
            self._photo_text_available(user)
            and reason in {"activity_share", "diary_share", "background_schedule", "noon_greeting", "evening_greeting"}
            and self._strong_photo_share_intent(event_text, motive, user.get("planned_proactive_topic"))
        ):
            return "photo_text"
        photo_probability = self._proactive_photo_text_trigger_probability(
            reason,
            event_text,
            motive,
            user.get("planned_proactive_topic"),
            user=user,
        )
        if self._photo_text_available(user) and photo_probability > 0 and random.random() < photo_probability:
            return "photo_text"
        if self._photo_text_available(user) and (
            reason in {"activity_share", "diary_share", "background_schedule", "noon_greeting", "evening_greeting"}
            or any(token in combined_hint for token in self._visual_share_tokens())
        ):
            candidates.append(("photo_text", 1.05))
        if self._voice_available(user) and reason in {"quiet_care", "diary_share", "insomnia_night", "evening_greeting"}:
            candidates.append(("voice", 0.82))
        if self._poke_available() and self._effective_user_poke_daily_limit(user) > 0 and reason in {"check_in", "quiet_care", "morning_greeting", "evening_greeting"}:
            candidates.append(("poke", 0.62))
        if not candidates:
            return "message"
        candidates.append(("message", 0.38))
        return self._fallback_action_for_unavailable(self._weighted_choice(candidates), user)

    def _pick_best_planned_event(
        self, user: dict[str, Any], now: float | None = None
    ) -> dict[str, Any] | None:
        now = now or _now_ts()
        candidates = []
        for event in (
            self._pick_pending_followup_event(user, now),
            self._pick_daily_greeting_event(user, now),
            self._habit_proactive_event_for_user(user, now=now),
            self._pick_state_need_event(user, now=now),
            self._pick_story_plan_event(now, user=user),
        ):
            if not isinstance(event, dict):
                continue
            if self._unverified_social_relay_plan_reason(
                event,
                source="event",
                has_trigger=bool(_single_line(event.get("trigger_message_id"), 120)),
            ):
                continue
            reason = str(event.get("reason") or "check_in")
            event_ts = self._timestamp_from_story_event(event, reason)
            if self._friend_proactive_scheduled_too_early(user, event_ts):
                continue
            if event_ts > now or (event_ts > 0 and now - event_ts <= self.max_proactive_plan_lag_minutes * 60):
                candidates.append((event_ts, event))
        if not candidates:
            return None
        near_sticky = [
            (event_ts, event)
            for event_ts, event in candidates
            if self._is_sticky_greeting_event(event) and 0 < event_ts - now <= 90 * 60
        ]
        if near_sticky:
            near_sticky.sort(key=lambda item: (self._event_priority(item[1]), item[0]))
            return near_sticky[0][1]
        non_sticky = [
            (event_ts, event)
            for event_ts, event in candidates
            if not self._is_sticky_greeting_event(event)
        ]
        if non_sticky:
            non_sticky.sort(key=lambda item: item[0])
            weighted = []
            for index, (_, event) in enumerate(non_sticky[:3]):
                priority_tuple = self._event_priority(event)
                priority_score = float(-priority_tuple[0])
                weighted.append((event, 1.0 + priority_score * 0.05 + max(0.0, 0.35 - index * 0.1)))
            return self._weighted_choice(weighted)
        ranked = sorted(
            candidates,
            key=lambda item: (self._event_priority(item[1]), item[0]),
        )
        top = ranked[:3]
        return random.choice(top)[1]

    def _pick_state_need_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        now = now or _now_ts()
        state = self.data.get("daily_state", {})
        if not isinstance(state, dict) or state.get("date") != _today_key():
            return None
        hunger_text = _single_line(state.get("hunger"), 80)
        if hunger_text in {"", "饥饿感平稳", "该人格不适用饥饿状态"}:
            return None
        if _safe_float(user.get("last_food_prompt_at"), 0) + 5 * 3600 > now:
            return None
        if _safe_float(user.get("last_food_feedback_at"), 0) + 2 * 3600 > now:
            return None
        active_hunger = None
        for cond in self._get_active_conditions():
            if isinstance(cond, dict) and str(cond.get("kind") or "") == "hunger":
                active_hunger = cond
                break
        if not isinstance(active_hunger, dict):
            return None
        started = _safe_float(active_hunger.get("start_ts"), now)
        if now - started < 25 * 60:
            return None
        when = self._environment_fromtimestamp(now)
        minute = when.hour * 60 + when.minute
        if not (10 * 60 + 30 <= minute <= 21 * 60 + 40):
            return None
        intensity = max(0.0, min(1.0, self.humanized_state_intensity / 100))
        chance = 0.18 + 0.32 * intensity
        if random.random() > chance:
            return None
        delay_minutes = random.randint(4, 12) if now - started >= 55 * 60 else random.randint(12, 32)
        scheduled = now + delay_minutes * 60
        phase = _single_line(active_hunger.get("phase"), 24)
        topic = "吃点什么"
        if phase == "afternoon":
            topic = random.choice(["下午想吃点甜的", "下午想吃点咸的", "下午想吃点热的", "下午想吃点凉的"])
        elif phase == "late_snack":
            topic = "夜里要不要吃点东西"
        elif phase in {"lunch", "dinner"}:
            topic = "这一顿吃什么"
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(delay_minutes, width_minutes=18),
            "reason": "state_share",
            "action": "message",
            "why": "有些饿了",
            "topic": topic,
            "motive": self._normalize_internal_motive_text(
                "有些饿了，想问问用户吃什么"
            ),
            "scene": "饭点或嘴馋的小空档",
            "tone": "自然",
            "impulse": "想问问用户吃什么比较好",
            "_scheduled_ts": scheduled,
            "_state_need": "hunger",
        }

    @staticmethod
    def _is_sticky_greeting_event(event: dict[str, Any]) -> bool:
        return bool(event.get("_daily_greeting")) and str(event.get("reason") or "") in {
            "morning_greeting",
            "noon_greeting",
            "evening_greeting",
        }

    def _pick_pending_followup_event(
        self, user: dict[str, Any], now: float | None = None
    ) -> dict[str, Any] | None:
        now = now or _now_ts()
        if self._private_user_role(user) == "friend":
            return None
        if self._in_llm_timer_silence_window(user, now=now):
            return None
        opener_event = self._build_suspended_opener_followup_event(user, now=now)
        if isinstance(opener_event, dict):
            return opener_event
        raw = user.get("pending_followup_event")
        if not isinstance(raw, dict):
            return None
        raw = dict(raw)
        raw["reason"] = self._normalize_legacy_proactive_text(raw.get("reason"), limit=40) or _single_line(raw.get("reason"), 40) or "check_in"
        followup_date = str(raw.get("date") or "")
        if followup_date and followup_date != _today_key():
            return None
        scheduled = _safe_float(raw.get("_scheduled_ts"), 0)
        if scheduled <= 0:
            return None
        if scheduled <= now:
            return raw
        return raw

    def _build_suspended_opener_followup_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        raw = user.get("suspended_proactive")
        if not isinstance(raw, dict) or not raw.get("active"):
            return None
        if not raw.get("complaint_enabled") or raw.get("complaint_sent"):
            return None
        if max(_safe_float(user.get("awaiting_reply_since"), 0), _safe_float(user.get("last_sent"), 0)) <= 0:
            return None
        due_at = _safe_float(raw.get("complaint_after_ts"), 0)
        if due_at <= 0:
            return None
        now = now or _now_ts()
        if now < due_at:
            return None
        name = _single_line(user.get("nickname") or self.default_nickname, 24)
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(4, width_minutes=18),
            "reason": self._normalize_legacy_proactive_text(raw.get("complaint_reason"), limit=40) or "check_in",
            "action": "message",
            "why": "之前只叫了用户一声，因此把话说完",
            "topic": _single_line(raw.get("complaint_topic"), 80) or "刚才那句后面",
            "motive": _single_line(raw.get("complaint_motive"), 100) or f"刚刚只喊了{name}一声，现在想补一句完整的话",
            "scene": "先前那句之后又过了一阵",
            "tone": _single_line(raw.get("complaint_tone"), 30) or "耐心等待",
            "impulse": "想把刚才没说完的话补上",
            "_scheduled_ts": due_at,
            "_opener_followup": True,
            "_cancel_on_inbound": True,
        }

    def _build_followup_event_from_chain(
        self,
        chain: list[dict[str, Any]] | None,
        *,
        origin_reason: str,
        origin_action: str,
        now_ts: float | None = None,
    ) -> dict[str, Any] | None:
        steps = [dict(step) for step in (chain or []) if isinstance(step, dict)]
        if not steps:
            return None
        current = None
        remaining: list[dict[str, Any]] = []
        consumed_name_only = False
        for step in steps:
            kind = str(step.get("kind") or "")
            if kind == "name_only_opener" and not consumed_name_only:
                consumed_name_only = True
                continue
            if current is None and kind in {"if_no_reply", "if_still_no_reply"}:
                current = step
                continue
            remaining.append(step)
        if not isinstance(current, dict):
            return None
        now_ts = now_ts or _now_ts()
        after_minutes = _safe_int(current.get("after_minutes"), 18, 0, 240)
        origin_reason = self._normalize_legacy_proactive_text(origin_reason, limit=40)
        follow_reason = self._normalize_legacy_proactive_text(current.get("reason"), limit=40) or origin_reason or "check_in"
        if origin_reason == "morning_greeting" or follow_reason == "morning_greeting":
            after_minutes = max(after_minutes, 75)
        topic = _single_line(current.get("topic"), 80) or "刚才那条主动后面"
        motive = self._normalize_internal_motive_text(
            _single_line(current.get("motive"), 100) or "刚才那句话信息不够完整,所以想补充一句"
        )
        tone = _single_line(current.get("tone"), 30)
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(after_minutes, width_minutes=18),
            "reason": follow_reason,
            "action": "message",
            "why": "刚才那句话还有个具体点没说完,如果用户还没接住,就把那一点补上。",
            "topic": topic,
            "motive": motive,
            "scene": "前一条主动消息发出去后又过了一阵",
            "tone": "克制一点,把重点补上" if (origin_reason == "morning_greeting" or follow_reason == "morning_greeting") else (tone or "有点认真,顺手补上"),
            "impulse": "早上那句还差个重点,想补完整" if (origin_reason == "morning_greeting" or follow_reason == "morning_greeting") else "刚才那句话还有个点没落到实处,想补完整",
            "_scheduled_ts": now_ts + after_minutes * 60,
            "_origin_action": origin_action,
            "_origin_reason": origin_reason,
            "_cancel_on_inbound": True,
            "_chain_followup": True,
            "chain": remaining,
        }

    def _build_simulation_greeting_events(self) -> list[dict[str, Any]]:
        return [
            {
                "window": "08:15-10:10",
                "reason": "morning_greeting",
                "action": "message",
                "why": "早上醒来后想打个招呼",
                "topic": "早安",
                "scene": "一天刚醒来的时候",
                "tone": "还没完全醒",
                "impulse": "想第一时间和用户说声早",
            },
            {
                "window": "12:05-13:35",
                "reason": "noon_greeting",
                "action": "message",
                "why": "午休或午饭时想起用户",
                "topic": "午后犯困",
                "scene": "午后犯困的时候",
                "tone": "懒洋洋",
                "impulse": "想趁午后休息时和用户说一句",
            },
            {
                "window": "20:10-21:20",
                "reason": "evening_greeting",
                "action": "message",
                "why": "晚上节奏慢下来时，想和用户说一句",
                "topic": "晚间问候",
                "scene": "晚上安静下来时",
                "tone": "安静",
                "impulse": "想趁还没太晚和用户说一句",
            },
        ]

    def _build_simulation_events(self, user: dict[str, Any], *, duration_minutes: int = 60) -> list[dict[str, Any]]:
        plan = self.data.get("daily_story_plan", {})
        story_events = plan.get("proactive_events", []) if isinstance(plan, dict) else []
        candidates: list[dict[str, Any]] = []
        if isinstance(story_events, list):
            candidates.extend(event for event in story_events if isinstance(event, dict))
        candidates.extend(self._build_simulation_greeting_events())
        deduped = self._dedupe_proactive_events(candidates)
        ranked = sorted(
            deduped,
            key=lambda item: self._event_priority(item),
        )
        base_target = max(3, min(8, int(round(max(2.0, self._soft_daily_target(user) + 1.2)))))
        selected: list[dict[str, Any]] = []
        used_buckets: set[str] = set()
        for item in ranked:
            bucket = self._simulation_event_bucket(item)
            if bucket in used_buckets:
                continue
            selected.append(item)
            used_buckets.add(bucket)
            if len(selected) >= base_target:
                break
        if not selected:
            selected = [dict(item) for item in _SIMULATION_FALLBACK_EVENTS]
        selected = [dict(item) for item in selected]
        for item in selected:
            item["motive"] = self._normalize_event_motive(item)
        start_ts = _now_ts() + 30
        total = len(selected)
        if total == 1:
            schedule_points = [start_ts + 120]
        else:
            last_ts = start_ts + max(18 * 60, duration_minutes * 60 - 120)
            schedule_points = []
            for index in range(total):
                ratio = index / max(1, total - 1)
                base = start_ts + (last_ts - start_ts) * ratio
                jitter = random.uniform(-70, 95)
                schedule_points.append(max(start_ts + index * 70, base + jitter))
            schedule_points.sort()
        events: list[dict[str, Any]] = []
        for item, scheduled in zip(selected, schedule_points):
            cloned = dict(item)
            cloned["_scheduled_ts"] = scheduled
            cloned["_simulated_window"] = str(item.get("window") or "")
            events.append(cloned)
        return events

    def _simulation_event_bucket(self, item: dict[str, Any]) -> str:
        reason = str(item.get("reason") or "")
        if reason in {"morning_greeting", "noon_greeting", "evening_greeting"}:
            return reason
        window = str(item.get("window") or "")
        start, _ = self._parse_window_minutes(window)
        if start is None:
            return f"{reason}|misc"
        if start < 11 * 60:
            daypart = "morning"
        elif start < 15 * 60:
            daypart = "noon"
        elif start < 19 * 60:
            daypart = "evening"
        else:
            daypart = "night"
        topic = _single_line(item.get("topic"), 30)
        return f"{reason}|{daypart}|{topic}"

    def _pick_daily_greeting_event(
        self, user: dict[str, Any], now: float | None = None
    ) -> dict[str, Any] | None:
        if not self.enable_daily_greetings:
            return None
        self._reset_daily_counter_if_needed(user)
        sent = user.get("greetings_sent", [])
        if not isinstance(sent, list):
            sent = []
            user["greetings_sent"] = sent
        suppressed = user.get("greetings_suppressed_by_inbound", [])
        if not isinstance(suppressed, list):
            suppressed = []
            user["greetings_suppressed_by_inbound"] = suppressed
        now_dt = self._environment_fromtimestamp(now or _now_ts())
        minute = now_dt.hour * 60 + now_dt.minute
        recent_activity_at = self._latest_private_user_activity_ts(user)
        anchors = [
            ("morning_greeting", "07:45-10:20", "刚睡醒，想第一时间和用户说声早安", "早上刚醒来"),
            ("noon_greeting", "12:05-13:35", "中午有些犯困，想和用户打声招呼", "午后犯困"),
            ("evening_greeting", "20:10-21:20", "晚上闲下来时，想和用户说一句", "晚间问候"),
        ]
        today = now_dt.date()
        candidates = []
        for reason, window, why, topic in anchors:
            if reason in sent or reason in suppressed:
                continue
            start, end = self._parse_window_minutes(window)
            if start is None or end is None:
                continue
            if reason == "morning_greeting" and _safe_int(user.get("sent_today"), 0) > 0:
                continue
            if self._private_user_role(user) == "friend":
                bucket = self._proactive_daypart_bucket_for_minute(start)
                if _safe_int(self._today_proactive_daypart_counts(user).get(bucket), 0, 0) >= 1:
                    continue
            if recent_activity_at > 0 and self._inbound_satisfies_greeting(reason, now=recent_activity_at):
                if reason not in suppressed:
                    suppressed.append(reason)
                continue
            if minute >= end:
                continue
            start_dt = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo) + timedelta(minutes=start)
            end_dt = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo) + timedelta(minutes=end)
            earliest = max(now_dt + timedelta(minutes=1), start_dt)
            if earliest >= end_dt:
                continue
            if reason == "morning_greeting":
                scheduled = random.uniform(earliest.timestamp(), end_dt.timestamp())
            elif reason == "evening_greeting":
                tighten_end = min(end_dt.timestamp(), (earliest + timedelta(minutes=48)).timestamp())
                scheduled = random.uniform(earliest.timestamp(), max(earliest.timestamp() + 60, tighten_end))
            else:
                scheduled = random.uniform(earliest.timestamp(), end_dt.timestamp())
            if self._friend_proactive_scheduled_too_early(user, scheduled):
                continue
            candidates.append(
                (
                    scheduled,
                    {
                        "window": window,
                        "reason": reason,
                        "action": "message",
                        "_daily_greeting": True,
                        "why": why,
                        "topic": topic,
                        "_scheduled_ts": scheduled,
                    },
                )
            )
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _pick_story_plan_event(
        self,
        now: float | None = None,
        *,
        user: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        plan = self.data.get("daily_story_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return None
        events = plan.get("proactive_events", [])
        if not isinstance(events, list):
            return None
        now = now or _now_ts()
        future_events = []
        for event in events:
            if not isinstance(event, dict):
                continue
            if self._unverified_social_relay_plan_reason(
                event,
                source="event",
                has_trigger=bool(_single_line(event.get("trigger_message_id"), 120)),
            ):
                continue
            event_ts = self._timestamp_from_story_event(event, str(event.get("reason") or "check_in"))
            if event_ts > now or (event_ts > 0 and now - event_ts <= self.max_proactive_plan_lag_minutes * 60):
                future_events.append((event_ts, event))
        if not future_events:
            return None
        future_events.sort(key=lambda item: item[0])
        shortlist = future_events[:6]
        weighted: list[tuple[dict[str, Any], float]] = []
        daypart_counts = self._today_proactive_daypart_counts(user or {})
        friend_user = isinstance(user, dict) and self._private_user_role(user) == "friend"
        for index, (_, event) in enumerate(shortlist):
            event_ts = self._timestamp_from_story_event(event, str(event.get("reason") or "check_in"))
            if friend_user and user is not None and self._friend_proactive_scheduled_too_early(user, event_ts):
                continue
            priority_tuple = self._event_priority(event)
            priority_score = float(-priority_tuple[0])
            weight = 1.0 + priority_score * 0.08 + max(0.0, 0.45 - index * 0.06)
            bucket = self._proactive_daypart_bucket_for_event(event)
            sent_in_bucket = _safe_int(daypart_counts.get(bucket), 0, 0) if bucket else 0
            if friend_user and bucket and sent_in_bucket >= 1:
                continue
            if bucket == "late_night" and sent_in_bucket >= 1 and not self._is_sticky_greeting_event(event):
                continue
            if bucket and sent_in_bucket >= 2 and not self._is_sticky_greeting_event(event):
                continue
            if sent_in_bucket > 0:
                weight *= max(0.22, 0.56 ** sent_in_bucket)
            if bucket == "late_night":
                weight *= 0.72
            weighted.append((event, weight))
        if not weighted and shortlist:
            for _, event in shortlist:
                if self._is_sticky_greeting_event(event):
                    weighted.append((event, 1.0))
                    break
        if not weighted:
            return None
        return self._weighted_choice(weighted)

    def _today_proactive_daypart_counts(self, user: dict[str, Any]) -> dict[str, int]:
        if not isinstance(user, dict):
            return {}
        self._reset_daily_counter_if_needed(user)
        raw = user.get("proactive_daypart_counts")
        if not isinstance(raw, dict):
            raw = {}
            user["proactive_daypart_counts"] = raw
        counts: dict[str, int] = {}
        for key, value in raw.items():
            text_key = str(key or "")
            if text_key:
                counts[text_key] = _safe_int(value, 0, 0)
        return counts

    def _proactive_daypart_bucket_for_event(self, event: dict[str, Any]) -> str:
        reason = str(event.get("reason") or "check_in")
        event_ts = self._timestamp_from_story_event(event, reason)
        if event_ts <= 0:
            start, _ = self._parse_window_minutes(str(event.get("window") or ""))
            if start is None:
                return ""
            minute = start
        else:
            when = self._environment_fromtimestamp(event_ts)
            minute = when.hour * 60 + when.minute
        return self._proactive_daypart_bucket_for_minute(minute)

    def _proactive_daypart_bucket_for_timestamp(self, timestamp: float) -> str:
        if timestamp <= 0:
            return ""
        when = self._environment_fromtimestamp(timestamp)
        return self._proactive_daypart_bucket_for_minute(when.hour * 60 + when.minute)

    def _planned_event_exceeds_daypart_cap(self, user: dict[str, Any], reason: str, scheduled_at: float) -> bool:
        if reason in {"insomnia_night", "important_date_share"}:
            return False
        if self._friend_proactive_scheduled_too_early(user, scheduled_at):
            return True
        bucket = self._proactive_daypart_bucket_for_timestamp(scheduled_at)
        if not bucket:
            return False
        counts = self._today_proactive_daypart_counts(user)
        sent_in_bucket = _safe_int(counts.get(bucket), 0, 0)
        if self._private_user_role(user) == "friend":
            return sent_in_bucket >= 1
        if bucket == "late_night":
            return sent_in_bucket >= 1
        return sent_in_bucket >= 2

    @staticmethod
    def _proactive_daypart_bucket_for_minute(minute: int) -> str:
        if minute < 11 * 60:
            return "morning"
        if minute < 14 * 60 + 30:
            return "noon"
        if minute < 18 * 60:
            return "afternoon"
        if minute < 21 * 60:
            return "evening"
        return "late_night"

    def _note_proactive_daypart_sent(self, user: dict[str, Any], sent_at: float | None = None) -> None:
        self._reset_daily_counter_if_needed(user)
        when = self._environment_fromtimestamp(sent_at or _now_ts())
        bucket = self._proactive_daypart_bucket_for_minute(when.hour * 60 + when.minute)
        raw = user.setdefault("proactive_daypart_counts", {})
        if not isinstance(raw, dict):
            raw = {}
            user["proactive_daypart_counts"] = raw
        raw[bucket] = _safe_int(raw.get(bucket), 0, 0) + 1

    def _note_action_sent(self, user: dict[str, Any], action: str) -> None:
        raw = user.setdefault("action_reply_affinity", {})
        if not isinstance(raw, dict):
            raw = {}
            user["action_reply_affinity"] = raw
        today = _today_key()
        for part in [item.strip() for item in str(action or "message").split("+") if item.strip()]:
            if part == "message":
                continue
            stats = raw.setdefault(part, {"sent": 0, "replied": 0})
            if not isinstance(stats, dict):
                stats = {"sent": 0, "replied": 0}
                raw[part] = stats
            stats["sent"] = _safe_int(stats.get("sent"), 0, 0) + 1
            if part == "photo_text":
                if self._private_user_role(user) == "friend":
                    continue
                photo_sent_day = str(user.get("photo_sent_day") or "")
                if photo_sent_day != today:
                    user["photo_sent_day"] = today
                    user["photo_sent_today"] = 1
                else:
                    user["photo_sent_today"] = _safe_int(user.get("photo_sent_today"), 0) + 1

    def _note_photo_generation_attempt(self, user_id: str, image_path: str = "") -> None:
        if not str(user_id or "").strip():
            return
        today = _today_key()
        user = self._get_user(str(user_id or ""))
        if user.get("photo_generated_day") != today:
            user["photo_generated_day"] = today
            user["photo_generated_today"] = 0
        user["photo_generated_today"] = _safe_int(user.get("photo_generated_today"), 0) + 1
        user["last_generated_photo_path"] = _single_line(image_path, 260)
        user["last_generated_photo_at"] = _now_ts()

    def _note_screen_peek_attempt(self, user_id: str, reason: str = "", *, count_daily: bool = True) -> None:
        if not str(user_id or "").strip():
            return
        today = _today_key()
        user = self._get_user(str(user_id or ""))
        if user.get("screen_peek_day") != today:
            user["screen_peek_day"] = today
            user["screen_peek_today"] = 0
        if count_daily:
            user["screen_peek_today"] = _safe_int(user.get("screen_peek_today"), 0) + 1
        user["screen_peek_last_at"] = _now_ts()
        user["last_screen_peek_reason"] = _single_line(reason, 120)
        if not count_daily:
            user["last_unanswered_screen_peek_at"] = _now_ts()

    def _screen_peek_failure_cooldown_active(self, user: dict[str, Any] | None = None, *, now: float | None = None) -> bool:
        if not isinstance(user, dict):
            return False
        check_now = _now_ts() if now is None else now
        return _safe_float(user.get("screen_peek_failure_until"), 0.0) > check_now

    def _note_screen_peek_failure(self, user: dict[str, Any] | None, reason: str = "", *, cooldown_minutes: int = 60) -> None:
        if not isinstance(user, dict):
            return
        now = _now_ts()
        user["screen_peek_failure_until"] = now + max(5, _safe_int(cooldown_minutes, 60, 5)) * 60
        user["screen_peek_failure_reason"] = _single_line(reason, 180)
        user["screen_peek_failure_count"] = _safe_int(user.get("screen_peek_failure_count"), 0, 0) + 1
        try:
            self._save_data_sync()
        except Exception:
            pass

    def _note_action_reply_feedback(self, user: dict[str, Any], action: str) -> None:
        raw = user.setdefault("action_reply_affinity", {})
        if not isinstance(raw, dict):
            raw = {}
            user["action_reply_affinity"] = raw
        for part in [item.strip() for item in str(action or "message").split("+") if item.strip()]:
            if part == "message":
                continue
            stats = raw.setdefault(part, {"sent": 0, "replied": 0})
            if not isinstance(stats, dict):
                stats = {"sent": 0, "replied": 0}
                raw[part] = stats
            stats["replied"] = _safe_int(stats.get("replied"), 0, 0) + 1

    def _maybe_make_followup_event(self, user: dict[str, Any], reason: str, action: str) -> dict[str, Any] | None:
        if _safe_int(user.get("sent_today"), 0) >= max(0, self._effective_user_daily_limit(user) - 1):
            return None
        if action not in {"photo_text", "poke", "voice", "screen_peek"} and "+" not in action:
            return None
        chance = 0.12
        if "voice" in action:
            chance += 0.06
        if "photo_text" in action:
            chance += 0.05
        if "poke" in action:
            chance += 0.03
        if random.random() > chance:
            return None
        delay_minutes = random.randint(22, 95)
        follow_reason = "check_in" if action in {"poke", "screen_peek"} else "diary_share"
        topic = {
            "photo_text": "对发送的图片进行补充说明",
            "poke": "刚才戳完之后进行补充说明",
            "voice": "发完语音后的互动",
            "screen_peek": "偷看用户屏幕后的互动",
        }.get(action.split("+")[0], "刚刚那条主动后面")
        motive = {
            "photo_text": "刚才发完图以后，想和{name}聊聊",
            "poke": "刚才戳完以后，想和{name}聊聊",
            "voice": "刚才发完语音消息以后，想和{name}聊聊",
            "screen_peek": "刚才看过屏幕后，想问问{name}现在还忙不忙",
        }.get(action.split("+")[0], "刚才那条主动后面，还有一句话想补上")
        display_name = _single_line(user.get("nickname") or self.default_nickname, 24)
        if display_name:
            motive = motive.replace("{name}", display_name)
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(delay_minutes, width_minutes=26),
            "reason": follow_reason,
            "action": "message",
            "why": "上一条主动消息之后进行自然的接话",
            "topic": topic,
            "motive": motive,
            "scene": "上一条主动消息发出去之后的互动",
            "tone": "自然",
            "impulse": "想接着刚才的话继续聊聊",
            "_scheduled_ts": _now_ts() + delay_minutes * 60,
            "_origin_action": action,
            "_origin_reason": reason,
            "_cancel_on_inbound": True,
        }

    def _bot_currently_bored_for_unanswered_peek(self, user: dict[str, Any]) -> bool:
        text_parts = [
            user.get("last_proactive_reason"),
            user.get("last_proactive_action"),
            user.get("last_proactive_motive"),
            user.get("planned_proactive_reason"),
            user.get("planned_proactive_motive"),
        ]
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        if isinstance(current_item, dict):
            text_parts.extend(
                [
                    current_item.get("activity"),
                    current_item.get("mood"),
                    current_item.get("message_seed"),
                ]
            )
        snapshot = self._current_story_plan_snapshot()
        if isinstance(snapshot, dict):
            text_parts.extend(snapshot.values())
        text = " ".join(_single_line(part, 80) for part in text_parts if part)
        bored_tokens = (
            "无聊", "发呆", "摸鱼", "闲", "空", "没事", "百无聊赖", "松下来",
            "喘口气", "空档", "空隙", "刷视频", "短视频", "休息",
        )
        if any(token in text for token in bored_tokens):
            return True
        reason = self._normalize_legacy_proactive_text(user.get("last_proactive_reason") or user.get("planned_proactive_reason"), limit=40)
        return reason in {"check_in", "quiet_care", "background_schedule"} and _safe_int(user.get("ignored_streak"), 0) >= 1

    def _maybe_make_unanswered_screen_peek_event(
        self,
        user: dict[str, Any],
        reason: str,
        action: str,
    ) -> dict[str, Any] | None:
        if not self.enable_unanswered_screen_peek_followup:
            return None
        if "screen_peek" in str(action or ""):
            return None
        if not self._screen_glance_available(user, ignore_daily_limit=True):
            return None
        now = _now_ts()
        cooldown = max(30, self.unanswered_screen_peek_cooldown_minutes) * 60
        last_at = _safe_float(user.get("last_unanswered_screen_peek_at"), 0)
        if last_at > 0 and now - last_at < cooldown:
            return None
        if not self._bot_currently_bored_for_unanswered_peek(user):
            return None
        delay_minutes = max(10, self.unanswered_screen_peek_after_minutes)
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(delay_minutes, width_minutes=18),
            "reason": "check_in",
            "action": "screen_peek",
            "why": "用户很久没有回复消息，想确认用户是不是还在忙。",
            "topic": "偷看用户是不是还在忙",
            "motive": "刚才主动找用户之后那边一直安静着，想确认用户是不是还在忙",
            "scene": "上一条主动消息之后的安静空档",
            "tone": "好奇",
            "impulse": "想确认用户是不是还在忙",
            "_scheduled_ts": now + delay_minutes * 60,
            "_cancel_on_inbound": True,
            "_unanswered_screen_peek": True,
            "_free_screen_peek": True,
            "_origin_action": action,
            "_origin_reason": reason,
        }

    def _window_from_delay_minutes(self, delay_minutes: int, width_minutes: int = 24) -> str:
        start_dt = self._environment_fromtimestamp(_now_ts() + max(5, delay_minutes) * 60)
        end_dt = start_dt + timedelta(minutes=max(12, width_minutes))
        return f"{start_dt.strftime('%H:%M')}-{end_dt.strftime('%H:%M')}"

    def _timestamp_from_story_event(self, event: dict[str, Any], reason: str) -> float:
        scheduled_ts = _safe_float(event.get("_scheduled_ts"), 0)
        if scheduled_ts > 0:
            return scheduled_ts
        window = str(event.get("window") or "").strip()
        match = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", window)
        now_dt = self._environment_now()
        today = now_dt.date()
        if match:
            sh, sm, eh, em = [int(part) for part in match.groups()]
            start = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo).replace(hour=sh % 24, minute=sm)
            end = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo).replace(hour=eh % 24, minute=em)
            if end <= start:
                end = end + timedelta(days=1)
            if now_dt >= end:
                return 0
            earliest = max(start.timestamp(), (now_dt + timedelta(seconds=45)).timestamp())
            latest = end.timestamp()
            if earliest >= latest:
                return 0
            scheduled = random.uniform(earliest, latest)
            event["_scheduled_ts"] = scheduled
            return scheduled
        scheduled = self._move_timestamp_into_reason_window(_now_ts() + random.uniform(2 * 3600, 10 * 3600), reason)
        event["_scheduled_ts"] = scheduled
        return scheduled

    def _parse_window_minutes(self, window: str) -> tuple[int | None, int | None]:
        normalized = (
            str(window or "")
            .replace("：", ":")
            .replace("—", "-")
            .replace("–", "-")
            .replace("－", "-")
            .replace("~", "-")
            .replace("～", "-")
            .replace("至", "-")
            .replace("到", "-")
        )
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", normalized)
        if not match:
            return None, None
        sh, sm, eh, em = [int(part) for part in match.groups()]
        start = (sh % 24) * 60 + sm
        end = (eh % 24) * 60 + em
        if end <= start:
            end += 24 * 60
        return start, end

    def _choose_planned_reason(self) -> str:
        state = self.data.get("daily_state", {})
        can_do = self.data.get("can_do", [])
        diaries = self.data.get("bot_diaries", [])
        important_dates = self._get_relevant_important_dates()
        users = self.data.get("users", {})
        has_recent_user_message = False
        if isinstance(users, dict):
            for raw_user in users.values():
                if not isinstance(raw_user, dict):
                    continue
                if _single_line(raw_user.get("last_user_message"), 24):
                    has_recent_user_message = True
                    break
        has_contextual_source = bool(
            (isinstance(can_do, list) and can_do)
            or (isinstance(diaries, list) and diaries)
            or important_dates
            or self.include_schedule_in_messages
        )
        reasons = ["activity_share", "activity_share", "diary_share"]
        if not has_contextual_source:
            reasons.append("check_in")
        if self._has_active_insomnia_state():
            reasons.extend(["insomnia_night"] * 2)
        if isinstance(state, dict) and state.get("conditions"):
            reasons.extend(["quiet_care"])
        if isinstance(can_do, list) and can_do:
            reasons.extend(["activity_share"] * 3)
        if isinstance(diaries, list) and diaries:
            reasons.extend(["diary_share"] * 2)
        if important_dates:
            reasons.extend(["important_date_share"] * 2)
        if self.include_schedule_in_messages:
            reasons.extend(["background_schedule"] * 2)
        state_note = _single_line(state.get("note"), 80) if isinstance(state, dict) else ""
        state_mood = _single_line(state.get("mood_bias"), 20) if isinstance(state, dict) else ""
        if any(token in state_note for token in ("疲惫", "收声", "安静", "慢一点")) or state_mood in {"安静", "疲惫"}:
            reasons.extend(["quiet_care"])
        if has_recent_user_message:
            reasons.extend(["quiet_care"])
        return random.choice(reasons)

    def _is_greeting_reason(self, reason: str) -> bool:
        return self._normalize_legacy_proactive_text(reason, limit=40) in {"morning_greeting", "noon_greeting", "evening_greeting"}

    def _is_sticky_greeting_reason(self, reason: str) -> bool:
        return self._normalize_legacy_proactive_text(reason, limit=40) in {"morning_greeting", "noon_greeting", "evening_greeting"}

    def _greeting_min_interval_seconds(self, reason: str) -> int:
        if reason == "morning_greeting":
            return 45 * 60
        if reason == "evening_greeting":
            return 60 * 60
        if reason == "noon_greeting":
            return 60 * 60
        return 120 * 60

    def _reschedule_greeting_within_window(
        self,
        user: dict[str, Any],
        reason: str,
        *,
        now: float | None = None,
    ) -> bool:
        if not self._is_sticky_greeting_reason(reason):
            return False
        now_dt = self._environment_fromtimestamp(now or _now_ts())
        windows = self._reason_windows(reason)
        if not windows:
            return False
        today = now_dt.date()
        for start, end in windows:
            start_dt = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo) + timedelta(minutes=start)
            end_dt = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo) + timedelta(minutes=end)
            if now_dt >= end_dt:
                continue
            earliest = max(now_dt + timedelta(minutes=random.randint(6, 14)), start_dt)
            latest = end_dt - timedelta(minutes=3)
            if earliest >= latest:
                continue
            user["next_proactive_at"] = random.uniform(earliest.timestamp(), latest.timestamp())
            return True
        return False

    def _is_now_in_reason_window(self, reason: str, now: float | None = None) -> bool:
        if not reason:
            return False
        now_dt = self._environment_fromtimestamp(now or _now_ts())
        minute_of_day = now_dt.hour * 60 + now_dt.minute
        for start, end in self._reason_windows(reason):
            if start <= minute_of_day <= end:
                return True
        return False

    def _inbound_satisfies_greeting(self, reason: str, *, now: float | None = None) -> bool:
        if not self._is_greeting_reason(reason):
            return False
        now_dt = self._environment_fromtimestamp(now or _now_ts())
        minute_of_day = now_dt.hour * 60 + now_dt.minute
        lead_minutes = {
            "morning_greeting": 10,
            "noon_greeting": 10,
            "evening_greeting": 10,
        }.get(reason, 10)
        for start, end in self._reason_windows(reason):
            if start - lead_minutes <= minute_of_day < end:
                return True
        return False

    def _proactive_text_greeting_reason(self, text: str, *, now: float | None = None) -> str:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return ""
        compact = re.sub(r"\s+", "", cleaned)
        # Allow a short address before the greeting, e.g. "比折，早……" or "主人早".
        compact = re.sub(r"^[\u4e00-\u9fffA-Za-z0-9_\-]{1,12}[,，、:：]+", "", compact, count=1)
        for marker in ("早", "午安", "中午", "晚上", "晚好"):
            index = compact.find(marker)
            if 0 < index <= 6:
                compact = compact[index:]
                break
        now_dt = self._environment_fromtimestamp(now or _now_ts())
        minute = now_dt.hour * 60 + now_dt.minute
        if compact == "早" or (
            compact.startswith("早")
            and (
                compact[1:2] in {"", ".", "。", "…", "·", "~", "～", "!", "！", ",", "，", "、", "呀", "啊", "安", "上", "哇", "哦", "欸", "诶"}
            )
        ):
            return "morning_greeting"
        if compact.startswith(("午安", "中午好", "午好")) or (compact.startswith("中午") and compact[2:3] in {"，", ",", "。", ".", "!", "！", "~", "～"}):
            return "noon_greeting"
        if compact.startswith(("晚上好", "晚好")) or (compact.startswith("晚上") and compact[2:3] in {"，", ",", "。", ".", "!", "！", "~", "～"}):
            return "evening_greeting"
        if re.search(r"(?:早晨|早上).{0,12}(?:安静|洗漱|刚醒|醒来|开机|早安|问候)", compact) and minute < 11 * 60:
            return "morning_greeting"
        return ""

    def _textual_greeting_duplicate_reason(
        self,
        user: dict[str, Any],
        text: str,
        *,
        now: float | None = None,
    ) -> str:
        reason = self._proactive_text_greeting_reason(text, now=now)
        if not reason:
            return ""
        self._reset_daily_counter_if_needed(user)
        sent = user.get("greetings_sent", [])
        if not isinstance(sent, list):
            sent = []
            user["greetings_sent"] = sent
        suppressed = user.get("greetings_suppressed_by_inbound", [])
        if not isinstance(suppressed, list):
            suppressed = []
            user["greetings_suppressed_by_inbound"] = suppressed
        if reason in sent:
            return "该问候时段今天已经主动问候过"
        if reason in suppressed:
            return "该问候时段已被用户自然互动占掉"
        if reason == "morning_greeting" and _safe_int(user.get("sent_today"), 0, 0) > 0:
            return "早安只适合作为当天第一句主动消息"
        return ""

    def _mark_textual_greeting_sent(
        self,
        user: dict[str, Any],
        text: str,
        *,
        sent_at: float | None = None,
    ) -> bool:
        reason = self._proactive_text_greeting_reason(text, now=sent_at)
        if not reason:
            return False
        self._reset_daily_counter_if_needed(user)
        sent = user.setdefault("greetings_sent", [])
        if not isinstance(sent, list):
            sent = []
            user["greetings_sent"] = sent
        if reason in sent:
            return False
        sent.append(reason)
        return True

    def _mark_greeting_satisfied_by_inbound(self, user: dict[str, Any], reason: str) -> bool:
        if not self._is_greeting_reason(reason):
            return False
        self._reset_daily_counter_if_needed(user)
        suppressed = user.setdefault("greetings_suppressed_by_inbound", [])
        if not isinstance(suppressed, list):
            suppressed = []
            user["greetings_suppressed_by_inbound"] = suppressed
        if reason in suppressed:
            return False
        suppressed.append(reason)
        return True

    def _mark_greetings_satisfied_by_recent_activity(
        self,
        user: dict[str, Any],
        *,
        activity_ts: float,
    ) -> bool:
        if not isinstance(user, dict) or activity_ts <= 0:
            return False
        changed = False
        for reason in ("morning_greeting", "noon_greeting", "evening_greeting"):
            if self._inbound_satisfies_greeting(reason, now=activity_ts):
                changed = self._mark_greeting_satisfied_by_inbound(user, reason) or changed
        return changed

    def _parse_action_list(self, raw: Any) -> set[str]:
        if raw is None:
            return set()
        if isinstance(raw, str):
            parts = re.split(r"[,\s,、;；]+", raw)
        elif isinstance(raw, list):
            parts = raw
        else:
            parts = []
        return {str(part).strip() for part in parts if str(part).strip()}

    @staticmethod
    def _parse_json_object(raw: Any) -> dict[str, Any] | None:
        text = str(raw or "").strip()
        if not text:
            return None
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
        candidates = [text]
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            candidates.append(match.group(0))
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    def _visual_share_tokens(self) -> tuple[str, ...]:
        # Broad visual anchors only. Specific subjects should be chosen by the model from context.
        return (
            "看", "拍", "图", "照片", "画面", "颜色", "形状", "光", "影", "反光",
            "桌", "纸", "书", "本", "笔", "杯", "饭", "饮", "路", "窗", "镜",
            "小物", "随手", "涂", "画", "包装", "屏幕", "边角",
        )

    def _strong_photo_share_intent(self, *parts: Any) -> bool:
        text = " ".join(_single_line(part, 160) for part in parts if _single_line(part, 160))
        if not text:
            return False
        strong_tokens = ("拍了张照", "拍了照", "拍照", "照片", "图片", "发你看", "给你看", "你看看")
        if any(token in text for token in strong_tokens):
            return True
        visual_tokens = (
            "花", "颜色", "蓝紫", "矮牵牛", "雨", "小雨", "毛毛雨", "路边", "校门",
            "晚霞", "阳光", "云", "窗边", "倒影", "影子", "小猫", "桌面", "杯", "包装",
        )
        return sum(1 for token in visual_tokens if token in text) >= 2

    def _days_since_last_photo_sent(self, user: dict[str, Any] | None = None) -> int | None:
        if not isinstance(user, dict):
            return None
        day_text = str(user.get("photo_sent_day") or "").strip()
        if not day_text:
            return None
        try:
            last_day = datetime.strptime(day_text[:10], "%Y-%m-%d").date()
            today = datetime.strptime(_today_key(), "%Y-%m-%d").date()
            return max(0, (today - last_day).days)
        except Exception:
            return None

    def _photo_text_overdue_boost(self, user: dict[str, Any] | None = None) -> float:
        days = self._days_since_last_photo_sent(user)
        if days is None:
            return 0.18
        if days >= 10:
            return 0.22
        if days >= 5:
            return 0.12
        if days >= 2:
            return 0.05
        return 0.0

    def _photo_text_plan_field_patch(
        self,
        *,
        reason: str,
        topic: str = "",
        motive: str = "",
        planned_event: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        current_text = self._format_plan_item_for_prompt(current_item)
        event_text = ""
        if isinstance(planned_event, dict):
            event_text = " ".join(
                _single_line(planned_event.get(key), 80)
                for key in ("topic", "scene", "why", "motive", "impulse")
            )
        seed = _single_line(topic, 60) or _single_line(event_text, 60) or _single_line(current_text, 60)
        if not seed:
            seed = {
                "morning_greeting": "早上眼前那点小画面",
                "noon_greeting": "中午手边的小东西",
                "evening_greeting": "晚一点的光线",
                "diary_share": "今天记下来的画面",
                "background_schedule": "手边这一小段",
            }.get(reason, "眼前这个小画面")
        cleaned_seed = re.sub(r"^(?:刚刚|刚才|现在|这会儿)\s*", "", seed).strip(" ，,。")
        patched_topic = _single_line(cleaned_seed, 60) or "眼前这个小画面"
        text = " ".join([motive, topic, event_text, current_text])
        selfie_tokens = ("自拍", "穿搭", "衣服", "校服", "镜子", "发型", "脸", "表情")
        if any(token in text for token in selfie_tokens):
            patched_motive = f"看到“{patched_topic}”那一下,第一反应是想自拍一张给你看"
        else:
            patched_motive = f"看到“{patched_topic}”那一下,第一反应是想拍下来发给你"
        return {
            "topic": self._soften_topic_hook(patched_topic),
            "motive": self._normalize_internal_motive_text(patched_motive),
        }

    def _pick_life_thought_topic(self, reason: str = "") -> str:
        terms = self._worldview_terms()
        if reason == "group_share":
            return f"{terms['group_chat']}里那段片段"
        if reason == "bili_video_share":
            return f"刚看到的{terms['video']}"
        if reason == "news_share":
            return "刚看到的一条新闻"
        if reason == "creative_share":
            return "刚写到的小说片段"
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        activity = _single_line((current_item or {}).get("activity"), 36)
        if activity:
            return f"{activity}里自然冒出来的小内容"
        if reason == "diary_share":
            return "今天记录里想给用户看看的一小段"
        return "当前时段里自然冒出来的小内容"

    def _format_content_choice_options_for_prompt(self, action: Any = None) -> str:
        terms = self._worldview_terms()
        if terms.get("mode") == "fantasy":
            object_examples = "营火边、行囊、靴扣、地图角、药草包、酒馆杯沿、委托纸、斗篷边、旅店窗、书页边缘"
            record_examples = "旅记、委托备忘、魔法笔记、读到的藏书里的一小句,或某个没写完的标题"
            photo_examples = "适合用水晶映像或随手画面递给熟人的具体场景"
        elif terms.get("mode") == "sci_fi":
            object_examples = "终端边、舱窗、随身包、杯沿、数据板、照明条、维修工具、航行日志、制服边角、资料页边缘"
            record_examples = "航行日志、终端备忘、读到的资料/影像流里的一小句,或某个没写完的标题"
            photo_examples = "适合用终端快照递给熟人的具体画面"
        else:
            object_examples = "桌边、手边、路上、食物、衣物、门口、杯沿、包装、车窗、书页边缘"
            record_examples = "日记、备忘录、作业、阅读/刷到内容里的一小句,或某个没写完的标题"
            photo_examples = "任何当前场景里适合顺手拍给熟人的具体画面"
        has_action_limit = action is not None and bool(str(action).strip())
        normalized_action = str(action or "").strip().lower()
        is_photo_action = "photo" in normalized_action or "image" in normalized_action or normalized_action in {"selfie", "text2img"}
        is_touch_action = "poke" in normalized_action
        is_voice_action = "voice" in normalized_action or "tts" in normalized_action
        options: list[str] = []
        if not has_action_limit:
            options.extend(
                [
                    f"- 眼前物：从当前{terms['schedule']}里的{object_examples}等具体物件里自选一个。",
                    "- 脑内念头：一句突然冒出来的短想法、吐槽、联想或没头没尾的小结论。",
                    "- 输入残留：上一轮聊天留下的余味、没接完的话、想补但没正式补的一点。",
                    f"- 记录碎片：{record_examples}。",
                    f"- 可拍画面：{photo_examples},不限定天气。",
                    "- 关系试探：想靠近但不直说的半句、说完就停，不追问。",
                ]
            )
        elif is_photo_action:
            options.extend(
                [
                    f"- 眼前物：从当前{terms['schedule']}里的{object_examples}等具体物件里自选一个。",
                    f"- 可拍画面：{photo_examples},不限定天气。",
                ]
            )
        elif is_touch_action:
            options.extend(
                [
                    "- 脑内念头：一句突然冒出来的短想法、吐槽、联想或没头没尾的小结论。",
                    "- 输入残留：上一轮聊天留下的余味、没接完的话、想补但没正式补的一点。",
                    "- 关系试探：想靠近但不直说的半句、说完就停，不追问。",
                ]
            )
        elif is_voice_action:
            options.extend(
                [
                    "- 脑内念头：一句突然冒出来的短想法、吐槽、联想或没头没尾的小结论。",
                    "- 输入残留：上一轮聊天留下的余味、没接完的话、想补但没正式补的一点。",
                    f"- 记录碎片：{record_examples}。",
                    "- 关系试探：想靠近但不直说的半句、说完就停，不追问。",
                ]
            )
        else:
            options.extend(
                [
                    f"- 眼前物：从当前{terms['schedule']}里的{object_examples}等具体物件里自选一个。",
                    "- 脑内念头：一句突然冒出来的短想法、吐槽、联想或没头没尾的小结论。",
                    "- 输入残留：上一轮聊天留下的余味、没接完的话、想补但没正式补的一点。",
                    f"- 记录碎片：{record_examples}。",
                    "- 关系试探：想靠近但不直说的半句、说完就停，不追问。",
                ]
            )
        if has_action_limit and not is_photo_action:
            options.append("- 可拍画面：本轮不是发图动作时不能选；不要在正文里声称拍照、发图或递照片。")
        return (
            "给模型的内容选择菜单,只供内部单选,不要把类别名写进正文：\n"
            + "\n".join(options)
            + "\n单选规则：先选且只选一个正文锚点；正文只围绕这个锚点展开,不要把两个以上动机、画面、旧话题或关系试探并列拼接。"
            "人格、当前时间段、日程和聊天历史只能用于筛选锚点和调整语气,不能各自贡献一段内容。"
            "如果动机、话题、日程、聊天历史指向不同内容,优先保留最贴近本次动作和当前日程的一项,其余全部舍弃。"
            "避免复用示例词。不要反复使用草稿纸、小画、画圆圈、笔尖划来划去这类廉价重复桥段。"
        )

    def _motive_action_bias(self, motive: str) -> dict[str, float]:
        text = str(motive or "")
        return {
            "screen_peek": 0.32 if any(token in text for token in ("还在忙", "埋进去", "看你", "确认", "忙太久", "偷看一眼")) else 0.0,
            "photo_text": 0.34 if any(token in text for token in ("顺手拍", "拍给你", "发你看", "光", "雨", "窗边", "晚霞", "小猫", "桌上", "一幕", "书页", "食堂", "饮料", "便利店", "影子", "倒影", "杯", "包装", "车窗", "门口")) else 0.0,
            "poke": 0.24 if any(token in text for token in ("戳", "碰你一下", "冒头", "轻轻叫你一下", "刷存在感")) else 0.0,
            "voice": 0.3 if any(token in text for token in ("懒得打字", "留句语音", "小声说", "睡不着", "不想敲字")) else 0.0,
        }

    def _soften_topic_hook(self, text: str) -> str:
        cleaned = _single_line(text, 60)
        if not cleaned:
            return ""
        cleaned = re.sub(r"[“”\"'《》<>]", "", cleaned).strip("，,。！？；： ")
        cleaned = re.sub(r"^(?:关于|有关|一种|一些|那个|这段|这一段)", "", cleaned).strip()
        replacements = {
            "劳动节的黄昏": "刚刚那点黄昏天色",
            "夜色温柔": "窗外那点夜色",
            "剧情共鸣": "刚刚那段剧情",
            "剧情吐槽": "刚刚那段也太离谱了",
            "偷看一眼": "你这会儿在干嘛",
            "早晨试探": "刚醒那会儿",
            "面包推荐": "刚咬到的那口面包",
            "路上随手拍": "路上那一下光",
            "念头分享": "刚冒出来的那句话",
            "突然想到": "刚刚突然想到的事",
        }
        if cleaned in replacements:
            return replacements[cleaned]
        if any(token in cleaned for token in ("晚霞", "黄昏", "天色")):
            return "刚刚那点天色"
        if "夜色" in cleaned:
            return "窗外那点夜色"
        if "剧情" in cleaned:
            return "刚刚那段剧情"
        if any(token in cleaned for token in ("雨", "雨声")):
            return "外面那阵雨声"
        if "照片" in cleaned:
            return "刚翻到的那张照片"
        if "风" in cleaned and "吹" not in cleaned:
            return "刚刚那阵风"
        if len(cleaned) <= 10 and not re.search(r"(刚|这|那|窗外|外面|手里|眼前|楼下|路上|桌上|耳边)", cleaned):
            return f"刚刚那点{cleaned}"
        return cleaned

    def _choose_proactive_topic(self, reason: str, user: dict[str, Any]) -> str:
        if reason == "group_share":
            share = user.get("group_share_context") if isinstance(user.get("group_share_context"), dict) else {}
            return _single_line(share.get("topic"), 48) or _single_line(share.get("text"), 48) or "群里那段片段"
        if reason == "bili_video_share":
            video = user.get("bilibili_video_context") if isinstance(user.get("bilibili_video_context"), dict) else {}
            return _single_line(video.get("title"), 48) or "刚刷到的 B 站视频"
        if reason == "news_share":
            news = user.get("news_context") if isinstance(user.get("news_context"), dict) else {}
            return _single_line(news.get("topic") or news.get("headline"), 48) or "刚看到的一条新闻"
        if reason == "web_exploration_share":
            exploration = user.get("web_exploration_context") if isinstance(user.get("web_exploration_context"), dict) else {}
            return _single_line(exploration.get("topic") or exploration.get("query"), 48) or "刚查到的新东西"
        if reason == "creative_share":
            creative = user.get("creative_share_context") if isinstance(user.get("creative_share_context"), dict) else {}
            return _single_line(creative.get("title"), 48) or "刚写到的小说片段"
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        snapshot = self._current_story_plan_snapshot()
        weather = self._weather_summary_text(self.data.get("daily_weather", {}))
        last_user_message = _single_line(user.get("last_user_message"), 24)
        snapshot_topic = self._soften_topic_hook(snapshot.get("topic")) if isinstance(snapshot, dict) else ""
        if snapshot_topic:
            return snapshot_topic
        snapshot_event = self._soften_topic_hook(snapshot.get("event")) if isinstance(snapshot, dict) else ""
        if snapshot_event:
            return snapshot_event
        if isinstance(current_item, dict):
            activity = _single_line(current_item.get("activity"), 30)
            if activity:
                activity = re.sub(r"[,、]?\s*想起了[^,。]+", "", activity).strip(",。 ")
                activity = re.sub(r"[,、]?\s*突然想到[^,。]+", "", activity).strip(",。 ")
                if activity:
                    return self._soften_topic_hook(activity)
        if reason in {"activity_share", "diary_share"}:
            if random.random() < 0.72:
                return self._pick_life_thought_topic(reason)
            if any(token in weather for token in ("雨", "小雨", "阵雨")):
                return "外面那阵雨声"
            if any(token in weather for token in ("晴", "阳光", "晚霞", "多云")):
                return "刚刚那点天色"
        if reason == "morning_greeting":
            return "刚醒那会儿"
        if reason == "noon_greeting":
            return "中午这会儿有点懒"
        if reason == "evening_greeting":
            return "晚一点的这会儿"
        if reason == "quiet_care" and last_user_message:
            return last_user_message
        return ""

    def _action_affinity_bias(self, user: dict[str, Any] | None = None) -> dict[str, float]:
        base = {"screen_peek": 0.0, "photo_text": 0.0, "poke": 0.0, "voice": 0.0}
        if not isinstance(user, dict):
            return base
        raw = user.get("action_reply_affinity")
        if not isinstance(raw, dict):
            return base
        for action in base:
            stats = raw.get(action)
            if not isinstance(stats, dict):
                continue
            sent = _safe_int(stats.get("sent"), 0, 0)
            replied = _safe_int(stats.get("replied"), 0, 0)
            if sent <= 0:
                continue
            rate = replied / max(1, sent)
            base[action] = max(-0.08, min(0.28, (rate - 0.35) * 0.55))
        return base

    def _screen_glance_available(
        self,
        user: dict[str, Any] | None = None,
        *,
        ignore_daily_limit: bool = False,
    ) -> bool:
        if not self.enable_screen_glance_action:
            return False
        if isinstance(user, dict) and self._private_user_role(user) == "friend":
            return False
        daily_limit = self._effective_user_screen_peek_daily_limit(user)
        if daily_limit <= 0 and not ignore_daily_limit:
            return False
        if isinstance(user, dict):
            if self._screen_peek_failure_cooldown_active(user):
                return False
            today = _today_key()
            used_today = (
                _safe_int(user.get("screen_peek_today"), 0)
                if str(user.get("screen_peek_day") or "") == today
                else 0
            )
            if not ignore_daily_limit and used_today >= daily_limit:
                return False
            cooldown_seconds = max(0, self.screen_peek_cooldown_minutes) * 60
            last_at = _safe_float(user.get("screen_peek_last_at"), 0.0)
            if cooldown_seconds > 0 and last_at > 0 and _now_ts() - last_at < cooldown_seconds:
                return False
        try:
            plugin = self._get_screen_companion_plugin()
            return plugin is not None and callable(getattr(plugin, "_invoke_screen_skill", None))
        except Exception:
            return False

    def _comfyui_photo_available(self) -> bool:
        if not self.enable_photo_text_action:
            return False
        if not self.comfyui_text2img_workflow_name and not self.comfyui_selfie_workflow_name:
            return False
        try:
            module = self._get_comfyui_module()
            return module is not None
        except Exception:
            return False

    def _external_photo_available(self) -> bool:
        if not self.enable_photo_text_action:
            return False
        configured = bool(
            self.external_image_api_base_url
            and self.external_image_api_key
            and self.external_image_api_model
        )
        if configured:
            checker = getattr(self, "_external_image_model_misconfiguration_note", None)
            if callable(checker):
                try:
                    if not checker():
                        return True
                except Exception:
                    return True
            else:
                return True
        return self._backup_external_photo_available()

    def _backup_external_photo_available(self) -> bool:
        if not self.enable_photo_text_action:
            return False
        if not bool(getattr(self, "enable_backup_external_image_api", False)):
            return False
        if not (
            getattr(self, "backup_external_image_api_base_url", "")
            and getattr(self, "backup_external_image_api_key", "")
            and getattr(self, "backup_external_image_api_model", "")
        ):
            return False
        checker = getattr(self, "_external_image_model_misconfiguration_note", None)
        if not callable(checker):
            return True
        keys = (
            "external_image_api_platform",
            "external_image_api_base_url",
            "external_image_api_key",
            "external_image_api_model",
            "external_image_api_size",
            "external_image_api_timeout_seconds",
        )
        backup_values = {
            "external_image_api_platform": getattr(self, "backup_external_image_api_platform", "auto"),
            "external_image_api_base_url": getattr(self, "backup_external_image_api_base_url", ""),
            "external_image_api_key": getattr(self, "backup_external_image_api_key", ""),
            "external_image_api_model": getattr(self, "backup_external_image_api_model", ""),
            "external_image_api_size": getattr(self, "backup_external_image_api_size", "1024x1024"),
            "external_image_api_timeout_seconds": getattr(self, "backup_external_image_api_timeout_seconds", 180),
        }
        old_values = {key: getattr(self, key, None) for key in keys}
        try:
            for key, value in backup_values.items():
                setattr(self, key, value)
            return not bool(checker())
        except Exception:
            return True
        finally:
            for key, value in old_values.items():
                setattr(self, key, value)

    def _sdgen_photo_available(self) -> bool:
        if not self.enable_photo_text_action:
            return False
        try:
            getter = getattr(getattr(self, "context", None), "get_registered_star", None)
            if callable(getter):
                for name in ("SDGen", "astrbot_plugin_sdgen"):
                    plugin = getter(name)
                    if plugin is not None and callable(getattr(plugin, "_call_t2i_api", None)):
                        return True
        except Exception:
            pass
        for obj in gc.get_objects():
            try:
                cls = obj.__class__
                module = str(getattr(cls, "__module__", ""))
                if "astrbot_plugin_sdgen" not in module:
                    continue
                if callable(getattr(obj, "_call_t2i_api", None)):
                    return True
            except Exception:
                continue
        return False

    def _local_photo_generation_load_state(self, *, force_refresh: bool = False) -> dict[str, Any]:
        now = _now_ts()
        if not self.enable_local_photo_load_guard:
            return {"enabled": False, "busy": False, "reason": "负载保护未启用", "sampled_at": now}
        cached = getattr(self, "_local_photo_load_cache", {}) if isinstance(getattr(self, "_local_photo_load_cache", {}), dict) else {}
        if cached and not force_refresh and now - _safe_float(cached.get("sampled_at"), 0) < 20:
            return dict(cached)
        state: dict[str, Any] = {
            "enabled": True,
            "available": False,
            "busy": False,
            "cpu_percent": 0.0,
            "memory_percent": 0.0,
            "reason": "",
            "sampled_at": now,
        }
        try:
            psutil = importlib.import_module("psutil")
            cpu_percent = float(psutil.cpu_percent(interval=0.05))
            memory_percent = float(getattr(psutil.virtual_memory(), "percent", 0.0) or 0.0)
            state.update(
                {
                    "available": True,
                    "cpu_percent": round(cpu_percent, 1),
                    "memory_percent": round(memory_percent, 1),
                }
            )
            reasons = []
            if cpu_percent >= self.local_photo_cpu_busy_percent:
                reasons.append(f"CPU {cpu_percent:.0f}%")
            if memory_percent >= self.local_photo_memory_busy_percent:
                reasons.append(f"内存 {memory_percent:.0f}%")
            state["busy"] = bool(reasons)
            state["reason"] = "、".join(reasons) if reasons else "负载正常"
        except Exception as exc:
            state["reason"] = f"无法读取系统负载:{exc.__class__.__name__}"
        self._local_photo_load_cache = dict(state)
        return state

    def _local_photo_generation_busy_state(self, *, force_refresh: bool = False) -> dict[str, Any] | None:
        state = self._local_photo_generation_load_state(force_refresh=force_refresh)
        if bool(state.get("enabled")) and bool(state.get("available")) and bool(state.get("busy")):
            return state
        return None

    def _action_has_photo_text(self, action: str) -> bool:
        return "photo_text" in {part.strip() for part in str(action or "").split("+") if part.strip()}

    def _proactive_photo_text_trigger_probability(
        self,
        reason: str,
        *parts: Any,
        user: dict[str, Any] | None = None,
    ) -> float:
        base = max(0.0, min(1.0, float(getattr(self, "proactive_photo_text_probability", 0.18))))
        if base <= 0:
            return 0.0
        base = max(base, min(0.45, base + self._photo_text_overdue_boost(user)))
        hard_reasons = {"activity_share", "diary_share", "background_schedule", "noon_greeting", "evening_greeting"}
        soft_reasons = {"check_in", "quiet_care", "state_share"}
        text = " ".join(_single_line(part, 180) for part in parts if _single_line(part, 180))
        has_visual_cut = any(token in text for token in self._visual_share_tokens())
        if reason in hard_reasons:
            return base if has_visual_cut else base * 0.45
        if reason in soft_reasons and has_visual_cut:
            return base * 0.55
        return 0.0

    def _photo_text_load_defer_note(self, action: str = "photo_text", *, force_refresh: bool = False) -> str:
        if not self._action_has_photo_text(action):
            return ""
        if self._daily_token_soft_limit_should_defer("photo_prompt"):
            return (
                "每日 Token 软限额已暂缓主动生图"
                f"（今日已用约 {self._today_llm_token_total()} Token；软限额 {self.daily_token_soft_limit}）"
            )
        if self.photo_generation_backend == "external":
            return ""
        if self.photo_generation_backend == "sdgen":
            local_available = self._sdgen_photo_available()
        else:
            local_available = self._comfyui_photo_available() or (
                self.photo_generation_backend == "auto" and self._sdgen_photo_available()
            )
        if not local_available:
            return ""
        state = self._local_photo_generation_busy_state(force_refresh=force_refresh)
        if not state:
            return ""
        if self.photo_generation_backend == "auto" and self._external_photo_available():
            return ""
        return (
            "电脑高负荷,已延后本地生图"
            f"（{state.get('reason') or '负载偏高'}；{self.local_photo_defer_minutes} 分钟后重试）"
        )

    def _defer_planned_photo_text_for_load(self, user: dict[str, Any], *, now: float, note: str) -> None:
        delay_seconds = max(60, int(self.local_photo_defer_minutes) * 60)
        jitter_seconds = random.uniform(0, min(300, delay_seconds * 0.2))
        user["next_proactive_at"] = now + delay_seconds + jitter_seconds
        user["proactive_sending"] = False
        user["proactive_sending_started_at"] = 0
        self._mark_planned_candidate_status(user, "accepted", note)

    def _photo_text_available(self, user: dict[str, Any] | None = None) -> bool:
        if not self.enable_photo_text_action:
            return False
        if isinstance(user, dict) and self._private_user_role(user) == "friend":
            return False
        if self._daily_token_soft_limit_should_defer("photo_prompt"):
            return False
        if self.photo_generation_backend == "comfyui":
            if not self._comfyui_photo_available():
                return False
            if self._local_photo_generation_busy_state():
                return False
        elif self.photo_generation_backend == "sdgen":
            if not self._sdgen_photo_available():
                return False
            if self._local_photo_generation_busy_state():
                return False
        elif self.photo_generation_backend == "external":
            if not self._external_photo_available():
                return False
        else:
            comfyui_available = self._comfyui_photo_available() and not self._local_photo_generation_busy_state()
            sdgen_available = self._sdgen_photo_available() and not self._local_photo_generation_busy_state()
            if not (comfyui_available or sdgen_available or self._external_photo_available()):
                return False
        photo_limit = self._effective_user_photo_daily_limit(user)
        if user and photo_limit <= 0:
            return False
        if user and photo_limit > 0:
            today = _today_key()
            photo_sent_day = str(user.get("photo_sent_day") or "")
            photo_sent_today = _safe_int(user.get("photo_sent_today"), 0)
            photo_generated_day = str(user.get("photo_generated_day") or "")
            photo_generated_today = _safe_int(user.get("photo_generated_today"), 0)
            used_today = max(
                photo_sent_today if photo_sent_day == today else 0,
                photo_generated_today if photo_generated_day == today else 0,
            )
            if used_today >= photo_limit:
                return False
        return True

    def _photo_text_planning_available(self, user: dict[str, Any] | None = None) -> bool:
        try:
            return bool(self._photo_text_available(user))
        except Exception as exc:
            logger.debug("[PrivateCompanion] 主动生图规划可用性检查失败: %s", _single_line(exc, 120))
            return False

    def _poke_available(self) -> bool:
        if not self.enable_poke_action:
            return False
        if self._resolve_aiocqhttp_client() is None:
            return False
        try:
            from data.plugins.astrbot_plugin_pokepro.core.send_poke import PokeSender  # noqa: F401
            return True
        except Exception:
            try:
                from astrbot_plugin_pokepro.core.send_poke import PokeSender  # noqa: F401
                return True
            except Exception:
                return False

    def _voice_available(self, user: dict[str, Any] | None = None) -> bool:
        if not self.enable_voice_action:
            return False
        target = ""
        if isinstance(user, dict):
            target = str(user.get("umo") or "").strip()
        if not target:
            return False
        try:
            config = self.context.get_config(target)
        except Exception:
            try:
                config = self.context.get_config()
            except Exception:
                return False
        provider_settings = dict(config.get("provider_tts_settings", {}) or {})
        if not provider_settings.get("enable", False):
            return False
        try:
            return bool(self.context.get_using_tts_provider(target))
        except Exception:
            return False

    def _action_is_available(self, action: str, user: dict[str, Any] | None = None) -> bool:
        normalized = str(action or "message").strip()
        if not normalized or normalized == "message":
            return True
        if self._friend_sensitive_proactive_action(normalized) and isinstance(user, dict) and self._private_user_role(user) == "friend":
            return False
        parts = [part.strip() for part in normalized.split("+") if part.strip()]
        if not parts:
            return True
        screen_quota_exempt = bool(isinstance(user, dict) and user.get("planned_proactive_quota_exempt"))
        for part in parts:
            if part == "screen_peek" and not self._screen_glance_available(user, ignore_daily_limit=screen_quota_exempt):
                return False
            if part == "photo_text" and not self._photo_text_available(user):
                return False
            if part == "poke" and (not self._poke_available() or self._effective_user_poke_daily_limit(user) <= 0):
                return False
            if part == "voice" and not self._voice_available(user):
                return False
            if part == "jm_cosmos_read" and not self._jm_cosmos_read_available(user):
                return False
            if part.startswith("external:") and not self._external_ability_enabled(part.split(":", 1)[1]):
                return False
        return True

    def _fallback_action_for_unavailable(self, action: str, user: dict[str, Any] | None = None) -> str:
        normalized = str(action or "message").strip() or "message"
        if self._action_is_available(normalized, user):
            return normalized
        parts = [part.strip() for part in normalized.split("+") if part.strip()]
        available_parts = [part for part in parts if self._action_is_available(part, user)]
        if not available_parts:
            return "message"
        return "+".join(available_parts)

    def _choose_action_for_reason(
        self,
        reason: str,
        user: dict[str, Any] | None = None,
        motive: str = "",
    ) -> str:
        weather = self._weather_summary_text(self.data.get("daily_weather", {}))
        state = self.data.get("daily_state", {})
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        action_profile = self._persona_action_profile()
        motive_bias = self._motive_action_bias(motive)
        affinity_bias = self._action_affinity_bias(user)
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        current_item_text = self._format_plan_item_for_prompt(current_item)

        weighted: list[tuple[str, float]] = [("message", 0.82)]
        if self._screen_glance_available(user) and reason in {"check_in", "quiet_care", "state_share", "background_schedule"}:
            weight = 0.9 + (0.45 if action_profile["observant"] else 0.0) + motive_bias["screen_peek"] + affinity_bias["screen_peek"]
            if energy < 50:
                weight += 0.12
            weighted.append(("screen_peek", weight))
        if (
            self._photo_text_available(user)
            and reason in {"activity_share", "diary_share", "background_schedule", "noon_greeting", "evening_greeting"}
            and self._strong_photo_share_intent(motive, user.get("planned_proactive_topic") if isinstance(user, dict) else "")
        ):
            return "photo_text"
        photo_probability = self._proactive_photo_text_trigger_probability(
            reason,
            motive,
            user.get("planned_proactive_topic") if isinstance(user, dict) else "",
            weather,
            current_item_text,
            user=user,
        )
        if self._photo_text_available(user) and photo_probability > 0 and random.random() < photo_probability:
            return "photo_text"
        visual_hint = any(token in motive for token in self._visual_share_tokens())
        if self._photo_text_available(user) and (
            reason in {"activity_share", "diary_share", "background_schedule", "noon_greeting", "evening_greeting"}
            or photo_probability > 0
        ):
            weight = 0.38 + (0.18 if action_profile["visual"] else 0.0) + motive_bias["photo_text"] * 0.65 + affinity_bias["photo_text"]
            if any(token in weather for token in ("晴", "阳光", "多云", "晚霞", "雨", "阵雨", "小雨")):
                weight += 0.04
            if visual_hint:
                weight += 0.14
            if reason in {"activity_share", "diary_share"}:
                weight += 0.05
            if reason in {"check_in", "quiet_care", "state_share"}:
                weight *= 0.45
            weighted.append(("photo_text", weight))
        if self._poke_available() and self._effective_user_poke_daily_limit(user) > 0 and reason in {"check_in", "quiet_care", "state_share", "important_date_share", "morning_greeting", "evening_greeting"}:
            weight = 0.38 + motive_bias["poke"] + affinity_bias["poke"]
            if action_profile["playful"]:
                weight += 0.22
            if action_profile["clingy"]:
                weight += 0.12
            weighted.append(("poke", weight))
        if self._voice_available(user) and reason in {"state_share", "diary_share", "insomnia_night", "evening_greeting", "quiet_care"}:
            weight = 0.5 + (0.55 if action_profile["voicey"] else 0.0) + motive_bias["voice"] + affinity_bias["voice"]
            if action_profile["clingy"]:
                weight += 0.2
            if reason == "insomnia_night":
                weight += 0.28
            weighted.append(("voice", weight))
        for ability in self._available_external_proactive_abilities(user):
            ability_name = str(ability.get("name") or "")
            if not ability_name:
                continue
            probability = max(0.0, min(1.0, _safe_float(ability.get("share_probability"), 0.0)))
            if probability <= 0:
                continue
            when_text = " ".join(str(ability.get(key) or "") for key in ("when", "use_for", "description"))
            weight = max(0.02, probability) * 0.9
            if reason in {"activity_share", "diary_share", "background_schedule", "state_share", "quiet_care"}:
                weight += probability * 0.35
            if motive and any(token and token in motive for token in re.split(r"[\s,，、/]+", when_text)[:16]):
                weight += probability * 0.25
            weighted.append((f"external:{ability_name}", weight))

        primary = self._weighted_choice(weighted)
        if primary != "message":
            combined = self._maybe_combine_actions(primary, reason, weather=weather, action_profile=action_profile, user=user)
            if combined:
                return self._fallback_action_for_unavailable(combined, user)
        return self._fallback_action_for_unavailable(primary, user)

    def _choose_proactive_motive(
        self,
        reason: str,
        user: dict[str, Any],
        *,
        action: str = "message",
        planned_event: dict[str, Any] | None = None,
    ) -> str:
        state = self.data.get("daily_state", {})
        weather = self._weather_summary_text(self.data.get("daily_weather", {}))
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        snapshot = self._current_story_plan_snapshot()
        last_user_message = _single_line(user.get("last_user_message"), 48)
        can_do = self.data.get("can_do", [])
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)

        topic = ""
        scene = ""
        tone = ""
        impulse = ""
        event_hint = _single_line(snapshot.get("event"), 60) if isinstance(snapshot, dict) else ""
        summary_hint = _single_line(snapshot.get("summary"), 60) if isinstance(snapshot, dict) else ""
        if isinstance(planned_event, dict):
            topic = self._soften_topic_hook(planned_event.get("topic"))
            scene = _single_line(planned_event.get("scene"), 60)
            tone = _single_line(planned_event.get("tone"), 24)
            impulse = _single_line(planned_event.get("impulse"), 60)
        if not topic and isinstance(snapshot, dict):
            topic = self._soften_topic_hook(snapshot.get("topic") or snapshot.get("event"))
        if not scene and isinstance(snapshot, dict):
            scene = _single_line(snapshot.get("scene"), 60)
        if not tone and isinstance(snapshot, dict):
            tone = _single_line(snapshot.get("tone"), 24)
        if not impulse and isinstance(snapshot, dict):
            impulse = _single_line(snapshot.get("impulse"), 60)
        if not topic and current_item:
            topic = _single_line(current_item.get("title"), 36)
        if not topic and isinstance(can_do, list) and can_do and reason == "activity_share":
            topic = _single_line(random.choice(can_do), 28)
        if not topic:
            topic = self._choose_proactive_topic(reason, user)
        if self._private_user_role(user) == "friend":
            if reason in {"quiet_care", "check_in", "state_share"}:
                return random.choice([
                    "作为朋友想到对方可能正忙，只问一句，不要求立刻回复",
                    "朋友之间顺手关心一下近况,说完就把空间留给对方",
                    "看到前面的话题还有一点余味,礼貌地补一句就停",
                ])
            if reason in {"morning_greeting", "noon_greeting", "evening_greeting"}:
                return random.choice([
                    "按朋友关系顺手打个招呼,语气轻一点,不显得黏人",
                    "这个时间点刚好想起对方,只发一句普通问候",
                ])
            if reason in {"activity_share", "diary_share", "background_schedule"}:
                if topic:
                    return self._normalize_internal_motive_text(f"有个和“{topic}”有关的小片段,觉得可以像朋友一样顺手分享一下")
                return "有个不太打扰人的小片段,想像朋友一样顺手分享一下"
            if reason == "group_share":
                return "共同群里有个和对方可能有关的小片段,只做轻量转告,不扩大解读"
        if impulse:
            return self._normalize_internal_motive_text(impulse)
        if scene or tone or event_hint or summary_hint:
            mood_fragment = ""
            if tone in {"安静", "柔和", "松弛", "轻快", "迷糊", "慵懒"}:
                mood_fragment = f",整个人有点{tone}"
            lived_line = ""
            if topic and event_hint:
                lived_line = f"刚刚{event_hint}之后，还想着“{topic}”{mood_fragment}"
            elif scene and topic:
                lived_line = f"在{scene}的时候，想到“{topic}”{mood_fragment}"
            elif event_hint:
                lived_line = f"刚刚{event_hint}的时候，想和用户说一句{mood_fragment}"
            elif scene:
                lived_line = f"刚刚在{scene}的时候，想和用户说一句{mood_fragment}"
            elif summary_hint:
                lived_line = f"这一小段安静下来时，想和用户说一句{mood_fragment}"
            if lived_line:
                return self._normalize_internal_motive_text(lived_line)

        if reason == "insomnia_night":
            motives = [
                "夜里一直没睡着，想和用户说一句",
                "睡不着，想看看用户是不是也还醒着",
                "已经很晚了，但还是想给用户留一句话",
            ]
            if action == "voice":
                motives.append("夜里不想打太多字，想给用户留一句语音")
            return random.choice(motives)
        if reason == "state_share":
            motives = [
                "这会儿说话可能慢一点，但还是想和用户说一句",
                "这会儿不太想说太多，但还是想问用户一句",
                "这一会儿比较安静，想慢慢和用户说一句",
            ]
            if energy < 45:
                motives.append("不太想说长句，但想确认用户还在")
            return random.choice(motives)
        if reason == "quiet_care":
            motives = [
                "刚刚有点在意用户是不是又忙太久了",
                "想起用户最近的状态，想问一句现在怎么样",
                "本来不想打扰，但还是想确认用户那边还好不好",
            ]
            if last_user_message:
                motives.append(f"想起用户前面提过“{last_user_message}”，有点放心不下")
            elif topic:
                motives.append(f"刚刚想到“{topic}”的时候，也想起用户那边")
            return random.choice(motives)
        if reason == "group_share":
            share = user.get("group_share_context") if isinstance(user.get("group_share_context"), dict) else {}
            group_id = _single_line(share.get("group_id"), 24)
            speaker = _single_line(share.get("speaker"), 24) or "群友"
            text = _single_line(share.get("text"), 70)
            if _single_line(share.get("kind"), 32) == "bot_harassment":
                if text:
                    return self._normalize_internal_motive_text(
                        f"共同群 {group_id} 里有人持续提到 Bot,{speaker} 那句“{text}”还挺扎眼,但只想很轻地跟你提一下"
                    )
                return self._normalize_internal_motive_text(f"共同群 {group_id} 里有人持续提到 Bot,只想很轻地跟你提一下")
            if text:
                return self._normalize_internal_motive_text(
                    f"共同群 {group_id} 里有个小转折,{speaker} 那句“{text}”还留着点余味,想顺手给你递一下"
                )
            return self._normalize_internal_motive_text("共同群里有个小片段还有点余味,想顺手给你递一下")
        if reason == "activity_share":
            motives = [
                "刚刚碰到一个小片段，想和用户说一句",
                "刚刚看到一个小东西，想分享一下",
                "刚刚有个小想法，想告诉用户",
                "脑子里忽然冒出一句没头没尾的话，想和用户说一下",
                "刚刚那点小想法放着也没什么用，想和用户说一句",
                "手边的小东西有点好笑，想给用户看",
            ]
            if topic:
                motives.append(f"刚碰到“{topic}”时，想和用户说一句")
            if any(token in weather for token in ("雨", "小雨", "阵雨")):
                motives.append("听见外面下雨，想和用户说一句")
            if any(token in weather for token in ("晴", "阳光", "晚霞")):
                motives.append("看到外面的光线不错，想和用户说一句")
            return random.choice(motives)
        if reason == "diary_share":
            return random.choice([
                "翻到今天记下来的小片段时，想和用户说一句",
                "看到今天写下来的那句话，觉得可以给用户看看",
                "今天有个小片段还记着，想和用户说一下",
                "有句话不算重要，但一直记着，想给用户看看",
                "今天有个小念头还没散，想和用户说一句",
            ])
        if reason == "important_date_share":
            return random.choice([
                "怕用户转头又忘，就先提醒一句",
                "今天这个时间点该提醒一下用户",
                "还记着这件事，所以想提醒用户一句",
            ])
        if reason == "background_schedule":
            motives = [
                "手上的事告一段落时，想和用户说一句",
                "忙到能休息一小会儿时，想和用户说句话",
                "眼前这一小段缓下来以后，想和用户说一句",
            ]
            if topic:
                motives.append(f"手上这点“{topic}”还没结束，想和用户说一句")
            return random.choice(motives)
        if reason == "morning_greeting":
            return random.choice([
                "人还没太清醒,但先想和用户说声早安",
                "醒来还带着一点睡意,先想去打个招呼",
            ])
        if reason == "noon_greeting":
            return random.choice([
                "中午这会儿人有点懒，想和用户说一句",
                "午间一下子松下来，想和用户说句话",
            ])
        if reason == "evening_greeting":
            return random.choice([
                "晚一点安静下来以后，想和用户说一句",
                "白天快结束时，想和用户说句话",
            ])
        motives = [
            "刚好能休息一小会儿，想和用户说一句",
            "眼前这点小事还没散掉，想和用户说一句",
            "刚松一口气的时候，想和用户说句话",
        ]
        return self._normalize_internal_motive_text(random.choice(motives))

    def _normalize_internal_motive_text(self, text: str) -> str:
        cleaned = _single_line(text, 80)
        if not cleaned:
            return ""
        replacements = {
            "突然想起你": "刚好想到你",
            "顺手冒了个头": "想跟你说一句",
            "冒个头": "想跟你说一句",
            "冒个泡": "想跟你说一句",
            "刷一下存在感": "想跟你说一句",
            "没什么大道理,就是": "",
            "没什么大不了的,就是": "",
            "顺手晃到你这边了": "想跟你说一句",
            "顺手晃到你这边": "想跟你说一句",
            "一直不理我": "那边还安静着",
            "不理我": "那边还安静着",
            "怎么一点动静都没有": "那边还没什么动静",
            "怎么还没动静": "那边还没什么动静",
            "一点动静都没有": "那边还没什么动静",
        }
        for src, dst in replacements.items():
            cleaned = cleaned.replace(src, dst)
        cleaned = cleaned.replace("忽然想", "有点想")
        cleaned = re.sub(r"(?:来找你一下){2,}", "来找你一下", cleaned)
        cleaned = cleaned.replace("碰你一下", "跟你说一句")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(",。 ")
        return cleaned

    def _normalize_legacy_proactive_text(self, value: Any, *, limit: int = 40) -> str:
        return _single_line(normalize_legacy_tag_text(value), limit)

    def _is_vague_seek_user_motive(self, reason: str, action: str, motive: str, topic: str = "") -> bool:
        if str(action or "message") != "message":
            return False
        if str(reason or "") not in {
            "check_in",
            "quiet_care",
            "state_share",
            "morning_greeting",
            "noon_greeting",
            "evening_greeting",
        }:
            return False
        text = f"{_single_line(motive, 140)} {_single_line(topic, 80)}"
        if not text.strip():
            return True
        concrete_tokens = (
            "前面提过", "刚刚想到“", "天气", "雨", "阳光", "晚霞", "日记", "群", "照片",
            "新闻", "日期", "生日", "纪念", "考试", "作业", "吃饭", "睡", "生病", "压力",
            "低压关心", "收敛情绪",
        )
        if any(token in text for token in concrete_tokens):
            return False
        vague_tokens = (
            "想跟你说一句", "想确认你还在", "确认用户在不在", "确认一下用户状态",
            "想看你在不在", "来看看你", "想来看看你", "来找你", "想找你",
            "只是想", "就是想", "没什么事", "没什么动机", "普通问候",
            "想到你了", "先想到你", "晃到了你", "拐到了你", "碰一下你",
            "轻轻问你一句", "先冒出来的是你", "就想顺手跟你说句话",
        )
        return any(token in text for token in vague_tokens)

    def _should_use_name_only_opener(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str,
    ) -> bool:
        if self._private_user_role(user) == "friend":
            return False
        if action != "message":
            return False
        if str(user.get("planned_followup_kind") or "") == "suspended_opener":
            return False
        chain = user.get("planned_event_chain")
        if isinstance(chain, list) and chain:
            first = chain[0] if isinstance(chain[0], dict) else {}
            if str(first.get("kind") or "") == "name_only_opener":
                return True
        if reason not in {"check_in", "quiet_care", "state_share", "evening_greeting", "insomnia_night"}:
            return False
        if _safe_float(user.get("awaiting_reply_since"), 0) > 0:
            return False
        profile = self._persona_action_profile()
        chance = 0.09
        if reason in {"quiet_care", "evening_greeting", "insomnia_night"}:
            chance += 0.05
        if profile.get("clingy"):
            chance += 0.06
        if profile.get("observant"):
            chance += 0.03
        if profile.get("playful"):
            chance += 0.02
        if any(token in motive for token in ("来找你", "确认一下用户状态", "想和用户说一句", "放心不下", "想看你在不在")):
            chance += 0.05
        if self._is_vague_seek_user_motive(reason, action, motive):
            chance *= 0.45
        return random.random() < min(0.32, chance)

    def _build_name_only_opener(self, name: str) -> str:
        clean_name = _single_line(name, 24) or self.default_nickname
        return f"{clean_name}……"

    def _build_suspended_proactive_payload(
        self,
        *,
        opener_text: str,
        reason: str,
        action: str,
        motive: str,
        action_summary: str,
        chain: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        profile = self._persona_action_profile()
        delay_minutes = random.randint(26, 95)
        complaint_chance = 0.18
        if profile.get("clingy"):
            complaint_chance += 0.16
        if profile.get("playful"):
            complaint_chance += 0.08
        if reason in {"quiet_care", "insomnia_night", "evening_greeting"}:
            complaint_chance += 0.08
        if reason == "morning_greeting":
            delay_minutes = random.randint(80, 150)
            complaint_chance = min(complaint_chance, 0.08)
        chain = list(chain or [])
        no_reply_step = None
        still_no_reply_step = None
        for step in chain:
            if not isinstance(step, dict):
                continue
            kind = str(step.get("kind") or "")
            if kind == "if_no_reply" and no_reply_step is None:
                no_reply_step = step
            elif kind == "if_still_no_reply" and still_no_reply_step is None:
                still_no_reply_step = step
        complaint_after_minutes = _safe_int((no_reply_step or {}).get("after_minutes"), delay_minutes, 0, 240)
        if reason == "morning_greeting":
            complaint_after_minutes = max(complaint_after_minutes, 75)
        return {
            "active": True,
            "resume_ready": False,
            "created_at": _now_ts(),
            "opener_text": _single_line(opener_text, 60),
            "reason": reason,
            "action": action,
            "motive": self._normalize_internal_motive_text(motive),
            "summary": _single_line(action_summary, 60),
            "complaint_enabled": bool(no_reply_step) or random.random() < min(0.55, complaint_chance),
            "complaint_sent": False,
            "complaint_after_ts": _now_ts() + complaint_after_minutes * 60,
            "complaint_reason": _single_line((no_reply_step or {}).get("reason"), 40),
            "complaint_topic": _single_line((no_reply_step or {}).get("topic"), 80),
            "complaint_motive": self._normalize_internal_motive_text(_single_line((no_reply_step or {}).get("motive"), 100)),
            "complaint_tone": "克制一点,把重点补上" if reason == "morning_greeting" else _single_line((no_reply_step or {}).get("tone"), 30),
            "second_followup": still_no_reply_step if isinstance(still_no_reply_step, dict) else {},
        }

    def _weighted_choice(self, items: list[tuple[str, float]]) -> str:
        filtered = [(name, max(0.0, float(weight))) for name, weight in items if name]
        if not filtered:
            return "message"
        total = sum(weight for _, weight in filtered)
        if total <= 0:
            return filtered[0][0]
        point = random.random() * total
        upto = 0.0
        for name, weight in filtered:
            upto += weight
            if point <= upto:
                return name
        return filtered[-1][0]

    def _maybe_combine_actions(
        self,
        primary: str,
        reason: str,
        *,
        weather: str = "",
        action_profile: dict[str, bool] | None = None,
        user: dict[str, Any] | None = None,
    ) -> str:
        profile = action_profile or self._persona_action_profile()
        candidates: list[tuple[str, float]] = []
        if primary == "photo_text" and self._voice_available(user) and reason in {"activity_share", "diary_share", "background_schedule"}:
            weight = 0.06
            if profile["clingy"] or profile["voicey"]:
                weight += 0.06
            if any(token in weather for token in ("晚霞", "雨", "晴", "阳光")):
                weight += 0.04
            candidates.append(("photo_text+voice", weight))
        if not candidates:
            return primary
        candidates.append((primary, 1.0))
        return self._weighted_choice(candidates)

    def _persona_action_profile(self) -> dict[str, bool]:
        text = str(self._get_default_persona_prompt() or "")
        playful_markers = ("恶作剧", "小恶魔", "腹黑", "俏皮", "捉弄", "欺负", "调皮")
        clingy_markers = ("依赖", "依恋", "特殊的情感", "知心朋友", "关心", "体贴", "想念", "共犯")
        observant_markers = ("看透", "观察", "温柔", "安静", "留意", "敏锐")
        visual_markers = ("自拍", "照片", "景色", "表情包", "外观", "外形", "穿搭", "发型", "发饰")
        voice_markers = ("悄悄说", "口语化", "抽空回复", "亲切感", "温柔", "顺从")
        return {
            "playful": any(marker in text for marker in playful_markers),
            "clingy": any(marker in text for marker in clingy_markers),
            "observant": any(marker in text for marker in observant_markers),
            "visual": any(marker in text for marker in visual_markers),
            "voicey": any(marker in text for marker in voice_markers),
        }

    def _reason_windows(self, reason: str) -> list[tuple[int, int]]:
        reason = self._normalize_legacy_proactive_text(reason, limit=40)
        return {
            "insomnia_night": [(23 * 60, 24 * 60), (0, 6 * 60)],
            "group_share": [(9 * 60, 23 * 60)],
            "bili_video_share": [(10 * 60, 23 * 60)],
            "news_share": [(8 * 60, 23 * 60)],
            "web_exploration_share": [(9 * 60, 23 * 60)],
            "jm_cosmos_recommendation_request": [(10 * 60, 23 * 60)],
            "creative_share": [(10 * 60, 23 * 60)],
            "state_share": [(8 * 60, 22 * 60 + 30)],
            "quiet_care": [(9 * 60, 22 * 60 + 30)],
            "activity_share": [(10 * 60, 18 * 60 + 30)],
            "diary_share": [(19 * 60, 23 * 60)],
            "important_date_share": [(8 * 60 + 30, 22 * 60)],
            "background_schedule": [(9 * 60, 22 * 60)],
            "check_in": [(9 * 60, 22 * 60 + 30)],
            "morning_greeting": [(7 * 60 + 45, 10 * 60 + 20)],
            "noon_greeting": [(12 * 60 + 5, 13 * 60 + 35)],
              "evening_greeting": [(20 * 60 + 10, 21 * 60 + 20)],
        }.get(reason, [(9 * 60, 22 * 60)])

    def _is_reason_allowed_now(self, reason: str) -> bool:
        reason = self._normalize_legacy_proactive_text(reason, limit=40)
        now = self._environment_now()
        minute = now.hour * 60 + now.minute
        for start, end in self._reason_windows(reason):
            if start <= minute < end:
                if reason == "insomnia_night":
                    return self._has_active_insomnia_state()
                if reason == "diary_share":
                    return bool(self.data.get("bot_diaries"))
                if reason == "important_date_share":
                    return bool(self._get_relevant_important_dates())
                return True
        return False

    def _move_timestamp_into_reason_window(self, timestamp: float, reason: str) -> float:
        dt = self._environment_fromtimestamp(timestamp)
        minute = dt.hour * 60 + dt.minute
        windows = self._reason_windows(reason)
        for start, end in windows:
            if start <= minute < end:
                return timestamp + random.randint(0, 17 * 60)
        first_start = windows[0][0]
        target_date = dt.date()
        if all(minute >= end for _, end in windows):
            target_date = target_date + timedelta(days=1)
        hour, minute_part = divmod(first_start, 60)
        target = datetime.combine(target_date, datetime.min.time(), tzinfo=dt.tzinfo).replace(
            hour=hour % 24,
            minute=minute_part,
        )
        return target.timestamp() + random.randint(0, 59 * 60)

    def _can_send_insomnia_night_message(self, user: dict[str, Any]) -> bool:
        if not self.allow_insomnia_night_message:
            return False
        if not self._has_active_insomnia_state():
            return False
        hour = self._environment_now().hour
        if not (0 <= hour <= 5 or hour >= 23):
            return False
        if _safe_int(user.get("sent_today"), 0) >= max(1, self._effective_user_daily_limit(user)):
            return False
        if _safe_float(user.get("last_sent"), 0) > 0:
            elapsed = _now_ts() - _safe_float(user.get("last_sent"), 0)
            if elapsed < max(6 * 3600, self._effective_user_min_interval_minutes(user) * 60):
                return False
        return random.random() < 0.35

    def _has_active_insomnia_state(self) -> bool:
        state = self.data.get("daily_state", {})
        conditions = state.get("conditions", []) if isinstance(state, dict) else []
        if not isinstance(conditions, list):
            return False
        keywords = ("失眠", "睡得很浅", "睡得断断续续", "睡眠延续")
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            text = f"{cond.get('title', '')} {cond.get('label', '')}"
            if any(keyword in text for keyword in keywords):
                return True
        return False

    def _passes_proactive_moment(self, user: dict[str, Any]) -> bool:
        hour = self._environment_now().hour
        state = self.data.get("daily_state", {})
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        active_conditions = state.get("conditions", []) if isinstance(state, dict) else []
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        can_do = self.data.get("can_do", [])
        important_dates = self._get_relevant_important_dates()
        ignored_streak = _safe_int(user.get("ignored_streak"), 0)

        probability = 0.32
        if 8 <= hour <= 11:
            probability += 0.16
        elif 14 <= hour <= 17:
            probability += 0.16
        elif 19 <= hour <= 22:
            probability += 0.18
        else:
            probability -= 0.05

        if energy < 40:
            probability += 0.12
        elif energy > 80:
            probability += 0.06
        if active_conditions:
            probability += min(0.18, len(active_conditions) * 0.06)
        if current_item:
            probability += 0.08
        if isinstance(can_do, list) and can_do:
            probability += 0.12
        if current_item and _single_line(current_item.get("message_seed"), 80):
            probability += 0.12
        if important_dates:
            probability += 0.1 if _safe_int(important_dates[0].get("_days_until"), 0) == 0 else 0.05
        probability -= min(0.18, ignored_streak * 0.07)
        probability *= self._daily_intensity_factor(user)
        probability = max(0.12, min(0.9, probability))
        return random.random() < probability

    async def _render_message(self, user: dict[str, Any]) -> tuple[str, str, str, list[Any], str, str]:
        name = str(user.get("nickname") or self.default_nickname)
        user["planned_opener_mode"] = ""
        planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        planned_action = str(user.get("planned_proactive_action") or "message")
        planned_motive = _single_line(user.get("planned_proactive_motive"), 140)
        due_timer_active = self._has_due_llm_timer(user)
        troubleshooting_active = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) == "troubleshooting"
        reason = planned_reason if planned_reason and (troubleshooting_active or due_timer_active or self._is_reason_allowed_now(planned_reason)) else ""
        if not reason:
            reason, _ = self._choose_proactive_message(user, name, planned_reason)
            planned_motive = self._choose_proactive_motive(reason, user, action=planned_action)
            planned_action = self._choose_action_for_reason(reason, user, motive=planned_motive)
        if self._should_use_name_only_opener(
            user,
            reason=reason,
            action=planned_action,
            motive=planned_motive,
        ):
            user["planned_opener_mode"] = "name_only"
            return reason, self._build_name_only_opener(name), "", [], "先轻轻叫了你一声", "message"
        action_payload = await self._execute_proactive_action(planned_action, user, name, reason)
        effective_action = _single_line(action_payload.get("effective_action") or planned_action, 60) or "message"
        raw_action_context = str(action_payload.get("context") or "")
        if reason == "group_share":
            share_context = self._format_group_share_action_context(user)
            raw_action_context = "\n".join(part for part in (raw_action_context, share_context) if part).strip()
        if reason == "bili_video_share":
            video_context = self._format_bilibili_video_action_context(user)
            raw_action_context = "\n".join(part for part in (raw_action_context, video_context) if part).strip()
        if reason == "news_share":
            news_context = self._format_news_action_context(user)
            raw_action_context = "\n".join(part for part in (raw_action_context, news_context) if part).strip()
        if reason == "web_exploration_share":
            exploration_context = self._format_web_exploration_action_context(user)
            raw_action_context = "\n".join(part for part in (raw_action_context, exploration_context) if part).strip()
        if reason == "jm_cosmos_share":
            jm_context = self._format_jm_cosmos_action_context(user)
            raw_action_context = "\n".join(part for part in (raw_action_context, jm_context) if part).strip()
        if reason == "jm_cosmos_recommendation_request":
            ask_context = user.get("jm_cosmos_recommendation_context") if isinstance(user.get("jm_cosmos_recommendation_context"), dict) else {}
            ask_text = _single_line(ask_context.get("hint"), 160) or "想向用户问有没有适合私下看的阅读素材推荐。"
            raw_action_context = "\n".join(part for part in (raw_action_context, f"夹层阅读推荐征求：{ask_text}") if part).strip()
        if reason == "creative_share":
            creative_context = self._format_creative_share_action_context(user)
            raw_action_context = "\n".join(part for part in (raw_action_context, creative_context) if part).strip()
        extra_components = list(action_payload.get("extra_components") or [])
        action_summary = _single_line(action_payload.get("summary") or planned_action, 80)
        if not bool(action_payload.get("success", True)):
            return reason, "", "", [], action_summary, effective_action
        image_path = self._extract_action_image_path(raw_action_context)
        action_context = await self._narrate_action_context(effective_action, raw_action_context)
        if image_path:
            action_context = f"{action_context}\n真实图片文件：{image_path}".strip()
        text = await self._generate_proactive_message_with_llm(
            user, name, reason, action_context, action=effective_action, motive=planned_motive
        )
        pre_poke_count, pre_poke_context = await self._maybe_run_pre_message_poke(
            user,
            name,
            reason,
            action=effective_action,
            motive=planned_motive,
        )
        if pre_poke_context and not pre_poke_context.startswith("poke：已"):
            logger.info("[PrivateCompanion] 消息前置戳一戳失败,跳过本次前置戳: %s", _single_line(pre_poke_context, 120))
        captured_text, captured_image_path, captured_extra_components = self._pop_framework_captured_send_payload(
            str(user.get("umo") or "")
        )
        if "photo_text" in effective_action or planned_action == "photo_text":
            if captured_text:
                text = captured_text
            if captured_image_path:
                image_path = captured_image_path
            if self._contains_inline_image_tag(text):
                image_path = ""
                extra_components = []
        if captured_extra_components:
            extra_components = list(captured_extra_components)
        if "photo_text" in planned_action and self._contains_inline_image_tag(text):
            image_path = ""
            extra_components = []
        if not image_path and not extra_components:
            text = self._remove_unbacked_media_claims(text)
        text = self._visible_text_without_tts_reading(text, limit=1000)
        text = self._normalize_proactive_sentence_flow(text)
        if reason == "group_share":
            recency_repair = getattr(self, "_repair_group_share_recency_text", None)
            if callable(recency_repair):
                text = recency_repair(user, text)
        if not text:
            return reason, "", "", [], action_summary, effective_action
        if pre_poke_count > 0:
            action_summary = f"先戳了 {pre_poke_count} 下 + {action_summary}"
            effective_action = f"poke+{effective_action}" if effective_action != "poke" else "poke"
        return reason, text, image_path, extra_components, action_summary, effective_action

    async def _test_proactive_action(
        self,
        user: dict[str, Any],
        *,
        action_name: str,
        reason: str,
    ) -> tuple[str, str, list[Any]]:
        name = str(user.get("nickname") or self.default_nickname)
        motive = self._choose_proactive_motive(reason, user, action=action_name)
        action_payload = await self._execute_proactive_action(action_name, user, name, reason)
        action_context = str(action_payload.get("context") or "")
        extra_components = list(action_payload.get("extra_components") or [])
        image_path = self._extract_action_image_path(action_context)
        narrated = await self._narrate_action_context(action_name, action_context)
        if image_path:
            narrated = f"{narrated}\n真实图片文件：{image_path}".strip()
        text = await self._generate_proactive_message_with_llm(
            user, name, reason, narrated, action=action_name, motive=motive
        )
        if not text:
            text = ""
        failure_note = ""
        if not bool(action_payload.get("success", True)):
            failure_note = "\n结果：本次真实主动行为失败；后台正常触发时会直接放弃,不会硬发。"
        return (
            "测试完成：\n"
            f"行为：{action_name}\n"
            f"动机：{motive}\n"
            f"转述：{_single_line(narrated, 180)}\n"
            f"最终消息：\n{text}{failure_note}"
        ), image_path, extra_components

