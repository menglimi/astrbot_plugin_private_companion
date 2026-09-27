# -*- coding: utf-8 -*-
"""
UserMemoryMixin — 从 main.py 重新拆分出的用户记忆系统
"""
from __future__ import annotations

from .user_memory_render_shared import logger

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
from .user_memory_context_prompt import UserMemoryContextPromptMixin
from .user_memory_reply_review import UserMemoryReplyReviewMixin
from .user_memory_inbound_intent import UserMemoryInboundIntentMixin
from .user_memory_relationship_boundary import UserMemoryRelationshipBoundaryMixin
from .user_memory_companion_record import (
    UserMemoryCompanionRecordMixin,
    _REQ041_COMPANION_MEMORY_FIELDS,
    _REQ041_DIALOGUE_EPISODE_FIELDS,
)
from .user_memory_expression_feedback import UserMemoryExpressionFeedbackMixin
from .user_memory_expression_voice import UserMemoryExpressionVoiceMixin
from .user_memory_expression_rule import UserMemoryExpressionRuleMixin

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
from .persona_config import runtime_persona_setting
from .conversation_prompt_section import (
    PromptRenderMode,
    PromptSection,
    prompt_heading_ref,
    prompt_section,
    render_prompt_content,
    render_prompt_sections,
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
from .helpers import _date_key, _normalize_photo_subject_owner, _now_ts, _photo_subject_owner_prompt_label, _safe_float, _safe_int, _single_address, _single_line, _strip_internal_message_blocks, _today_key
from .relationship_policy import relationship_stage_for_score
from .expression_scope_ownership import (
    bind_expression_item,
    bind_expression_profile,
)
from .authoritative_private_memory import (
    AuthoritativePrivateMemoryError,
    AuthoritativePrivateMemoryStore,
    apply_private_memory_content,
    private_memory_content,
)
from .scoped_runtime_view import scoped_approved_expression_rules
from .companion_interaction_expression import (
    build_expression_decision,
    current_interaction_projection,
)
from .domains.affect.emotion_event_ledger import record_recent_emotion_event
from .domains.affect.interaction_dynamics import project_interaction_dynamics, settle_interaction_dynamics
from .domains.affect.emotion_targeting import classify_emotion_target
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
from .companion_memory_records import normalize_memory_items, relevant_memory_items
from .private_identity_policy import format_private_identity_anchor
from .user_memory_render_shared import _render_conversation_section_labeled, _render_user_memory_background_prompt, _render_user_memory_labeled_section




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

# 深夜时间锚点：11 点/23 点单独出现只是一个时间事实（行程、用药、约见等），
# 只有与睡意线索出现在同一小句里，才算「现在很晚了」的宣言。时间短语整体匹配
# （连半/一刻一起吃掉），并排除「点左右/前后」这类区间说法，避免只删掉半截。
# 隐含深夜说法（「时间不早了」「都这么晚了」）。
# 钟点与睡意线索之间的允许间隔：不跨句号（不跨句），可以跨问号/感叹号。
# 删除起点：句首或小句边界，避免从句子中间把话切走。
# REQ-041 权威私聊记忆的声明写集：每个写入者只提交自己拥有的字段，
# 其余字段由权威记录在提交事务内保留，避免并发写入互相整块覆盖。
class UserMemoryMixin(UserMemoryExpressionRuleMixin, UserMemoryExpressionVoiceMixin, UserMemoryExpressionFeedbackMixin, UserMemoryCompanionRecordMixin, UserMemoryRelationshipBoundaryMixin, UserMemoryInboundIntentMixin, UserMemoryReplyReviewMixin, UserMemoryContextPromptMixin):
    """用户记忆系统"""

    def _relationship_profile(self, user: dict[str, Any]) -> dict[str, Any]:
        """Compatibility DTO projected from the unified relationship authority."""
        proactive_count = _safe_int(user.get("proactive_sent_count"), 0)
        reply_count = _safe_int(user.get("reply_count"), 0)
        inbound_count = _safe_int(user.get("inbound_count"), 0)
        score = _safe_int(user.get("relationship_score"), 0)
        reply_rate_available = proactive_count > 0
        reply_rate = reply_count / proactive_count if reply_rate_available else 0.0
        reply_rate_label = f"{reply_rate:.0%}" if reply_rate_available else "暂无样本"
        policy = (
            runtime_persona_setting(self, "relationship_stage_policy", None)
            if bool(runtime_persona_setting(self, "enable_custom_relationship_stage_policy", False))
            else None
        )
        stage_projection = relationship_stage_for_score(
            score,
            policy,
            previous_stage_key=user.get("relationship_phase_key", ""),
        )
        stage = stage_projection["phase"]
        user["relationship_phase_key"] = stage.get("key", "acquaintance")
        stage_key = str(stage.get("key") or "acquaintance")
        if stage_key in {"deeply_distant", "strongly_distant", "distant"}:
            level = "陌生"
        elif stage_key in {"acquaintance", "familiar"}:
            level = "熟悉"
        else:
            level = "亲近"
        role_getter = getattr(self, "_private_user_role", None)
        try:
            role = role_getter(user, str(user.get("user_id") or "")) if callable(role_getter) else str(user.get("relationship_role") or "friend")
        except Exception:
            role = str(user.get("relationship_role") or "friend")
        interaction = current_interaction_projection(
            user.get("current_interaction"),
            relationship_role=role,
            relationship_mode=str(user.get("relationship_mode") or "normal"),
            relationship_score=score,
            normal_interaction_band_cap=runtime_persona_setting(self, "normal_interaction_band_cap", "warm"),
            now=_now_ts(),
        )
        return {
            "level": level,
            "reply_rate": reply_rate,
            "reply_rate_available": reply_rate_available,
            "reply_rate_label": reply_rate_label,
            "preference": str(interaction.get("label") or "放松"),
            "score": score,
            "inbound_count": inbound_count,
            "proactive_count": proactive_count,
            "reply_count": reply_count,
            "note": "由长期关系阶段与当前互动状态统一投影",
            "stage_key": stage_key,
            "stage_label": str(stage.get("label") or ""),
            "interaction_band": str(interaction.get("expression_band") or "relaxed"),
            "interaction_label": str(interaction.get("label") or "放松"),
        }

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
        recovery = max(
            1,
            _safe_int(runtime_persona_setting(self, "emotional_gate_recovery_per_hour", 24), 24, 1, 60),
        )
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
        recovery = max(
            1,
            _safe_int(runtime_persona_setting(self, "emotional_gate_recovery_per_hour", 24), 24, 1, 60),
        )
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
        history = user.setdefault("memory_corrections", [])
        if not isinstance(history, list):
            history = []
            user["memory_corrections"] = history
        correction_key = hashlib.sha1(correction.encode("utf-8")).hexdigest()[:20]
        history = [
            item
            for item in history
            if isinstance(item, dict)
            and _single_line(item.get("correction_key"), 40) != correction_key
        ]
        history.append(
            {
                "correction_key": correction_key,
                "text": correction,
                "at": _now_ts(),
                "inbound_count": inbound_count,
                "source": "explicit_user_correction",
            }
        )
        user["memory_corrections"] = history[-16:]
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

    def _recent_memory_correction_for_echo(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        if not isinstance(user, dict):
            return {}
        check_now = _now_ts() if now is None else now
        history = user.get("memory_corrections")
        if not isinstance(history, list):
            return {}
        for item in reversed(history):
            if not isinstance(item, dict):
                continue
            text = _single_line(item.get("text"), 180)
            corrected_at = _safe_float(item.get("at"), 0)
            age = check_now - corrected_at
            if text and 12 * 3600 <= age <= 30 * 24 * 3600:
                return {
                    "correction_key": _single_line(item.get("correction_key"), 40)
                    or hashlib.sha1(text.encode("utf-8")).hexdigest()[:20],
                    "text": text,
                    "at": corrected_at,
                }
        return {}


    def _format_intent_relationship_injection(self, user: dict[str, Any]) -> str:
        intent = user.get("intent_profile")
        lines: list[str] = []
        if (
            bool(runtime_persona_setting(self, "enable_intent_emotion_analysis", True))
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
                    "intimacy": "用户在靠近；只回应符合 Bot 当前身体状态、意愿和边界的亲近，不要把亲密关系或用户偏好当成本轮默认同意，也别过度表演。",
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
        recent = self._format_recent_passive_topics_hint(user)
        if recent:
            lines.append(
                "近期已用过的回复切口（仅用于避免重复，不是当前话题；除非用户主动提到，"
                "不要在正文复述或用它开启新话题）：\n" + recent
            )
        afterglow_formatter = getattr(self, "_format_game_afterglow_prompt", None)
        if callable(afterglow_formatter):
            afterglow = _single_line(afterglow_formatter(user), 520)
            if afterglow:
                lines.append(afterglow)
        return "\n".join(lines)

    def _cleanup_recent_passive_topics(self, user: dict[str, Any], *, now: float | None = None) -> list[dict[str, Any]]:
        now = now or _now_ts()
        raw = user.get("recent_reply_topics", [])
        if not isinstance(raw, list):
            raw = []
        kept = [
            item for item in raw
            if isinstance(item, dict)
            and now - _safe_float(item.get("ts"), 0)
            <= runtime_persona_setting(self, "passive_topic_memory_hours", 8) * 3600
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
        if not runtime_persona_setting(self, "enable_passive_topic_suppression", True):
            return ""
        recent = self._cleanup_recent_passive_topics(user)
        lines = []
        for item in recent[-2:]:
            signature = _single_line(item.get("signature"), 120)
            anchors = [
                token
                for token in signature.split("|")
                if 2 <= len(token.strip()) <= 24
            ][:6]
            if anchors:
                # 只给主题锚点，不暴露相对时间和完整旧句，避免模型把避重资料复述进正文。
                lines.append("- 已用主题词：" + "、".join(anchors))
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
