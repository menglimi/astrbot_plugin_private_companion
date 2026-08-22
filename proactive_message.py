# -*- coding: utf-8 -*-
"""
ProactiveMessageMixin — 主动消息生成、动作执行和发送链路
"""
from __future__ import annotations

import asyncio
import base64
import binascii
from contextvars import ContextVar
import gc
import hashlib
import html
import importlib
import inspect
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
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse
from xml.etree import ElementTree as ET

_PHOTO_GENERATION_TRACE_FILE_LOCK = threading.Lock()

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
try:
    from astrbot.core.message import components as CoreMessageComponents
except ImportError:
    CoreMessageComponents = None
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core import file_token_service
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent
from astrbot.core.agent.message import AssistantMessageSegment, UserMessageSegment
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
from .reference_asset_gate import (
    MAX_INPUT_ASSETS,
    ReferenceAssetGate,
    ReferenceAssetPlan,
    ReferenceAssetTicket,
)
from .helpers import (
    _date_key,
    _format_history_media_marker,
    _normalize_outbound_punctuation_flow,
    _normalize_photo_subject_owner,
    _now_ts,
    _path_text,
    _photo_group_request_matches,
    _photo_subject_owner_prompt_label,
    _redact_outbound_secrets,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_internal_message_blocks,
    _today_key,
    normalize_bot_relationship_cards,
)
from .final_response_persistence import (
    FinalResponsePersistenceMixin,
    collect_proactive_delivery,
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
from .scene_context import infer_companion_scene_category
from .segmented_message import (
    component_kind,
    component_order_from_owner,
    component_strategies_from_owner,
    plan_component_chunks,
)
from .token_budget import _looks_like_upstream_llm_error_response
from .reaction_expression import (
    normalize_reaction_expression_intent,
    reaction_expression_high_frequency,
)
from .photo_reference_catalog import (
    PhotoReference,
    build_daily_outfit_reference,
    load_catalog,
    project_reference_candidate,
)
from .photo_prompt_context import (
    PhotoPromptSection,
    _clip as _clip_photo_prompt_text,
    compile_local_photo_prompt,
    resolve_photo_prompt_context,
)
from .photo_reference_feedback import analyze_photo_reference_feedback
from .photo_reference_intent import (
    CONTINUITY_MODES,
    REFERENCE_ROLES,
    ReferenceIntent,
    analyze_indexed_reference_roles,
    analyze_reference_intent,
    explicitly_excludes_reference_outfit,
)
from .photo_reference_selection import (
    CandidateMatch,
    SelectionResult,
    parse_photo_reference_context_categories,
    select_photo_reference,
)
from .photo_reference_plan import (
    PhotoReferencePlan,
    ReferenceFallback,
    build_photo_reference_plan,
    evaluate_reference_fallback,
    project_reference_plan_for_backend,
)
from .reference_assets import (
    normalize_reference_asset,
    normalize_reference_owner_id,
    reference_asset_tokens,
)
from .photo_wardrobe_decision import (
    PhotoWardrobeDecision,
    PhotoWardrobeIntent,
    analyze_photo_wardrobe,
    merge_photo_wardrobe_continuity,
    resolve_photo_wardrobe_decision,
)

_EXTERNAL_IMAGE_MAX_BYTES = 32 * 1024 * 1024
_EXTERNAL_IMAGE_DOWNLOAD_MAX_ATTEMPTS = 2
_EXTERNAL_IMAGE_DOWNLOAD_RETRY_DELAY_SECONDS = 0.8
_EXTERNAL_IMAGE_DOWNLOAD_TOTAL_TIMEOUT_SECONDS = 75.0
_EXTERNAL_IMAGE_DOWNLOAD_ATTEMPT_TIMEOUT_SECONDS = 35.0
_MINIMAX_REFERENCE_IMAGE_MAX_BYTES = 10 * 1024 * 1024

_EXTERNAL_IMAGE_DOWNLOAD_TIMEOUT_OVERRIDE: ContextVar[float | None] = ContextVar(
    "private_companion_external_image_download_timeout_override",
    default=None,
)
from .proactive_routes import PROACTIVE_ROUTE_REGISTRY
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

# 直接问用户的措辞。复用处：
#   _trim_performative_self_state_tail 的 asks_user 判据（有问句就不删状态尾巴）
#   _is_conversational_proactive_text 的对话式守卫（有问句就不收束状态清单）
_ASK_USER_PROACTIVE_PATTERN = re.compile(
    r"(你那边|你呢|你那儿|你那里|你现在|你今天|你还|你有没有|你要不要|你是不是)"
)


@dataclass(frozen=True, slots=True)
class _ProactiveSendOutcome:
    delivered: bool
    complete: bool
    delivered_text: str = ""
    image_delivered: bool = False
    extra_components_delivered: int = 0
    note: str = ""
    primary_complete: bool = False
    delivery_umo: str = ""
    delivered_chain: tuple[Any, ...] = ()

    def __bool__(self) -> bool:
        return self.delivered


@dataclass(frozen=True, slots=True)
class PhotoGenerationResult:
    backend: str = ""
    image_path: str = ""
    note: str = ""
    trace_id: str = ""
    reference_selected_path: str = ""
    reference_used: bool = False
    reference_id: str = ""
    reference_kind: str = ""
    reference_roles: tuple[str, ...] = ()
    wardrobe_mode: str = ""
    wardrobe_category: str = ""
    outfit_locked: bool = False
    daily_outfit_removed: bool = False
    preset_names: tuple[str, ...] = ()
    preset_hint: str = ""
    preset_source: str = ""
    suggestion_status: str = ""
    prompt_hash: str = ""
    prompt_path: str = ""
    reference_requested_roles: tuple[str, ...] = ()
    reference_excluded_roles: tuple[str, ...] = ()
    continuity_mode: str = "ambiguous"
    reference_confidence: float = 0.0
    reference_plan: tuple[dict[str, Any], ...] = ()
    reference_fulfilled_roles: tuple[str, ...] = ()
    reference_missing_roles: tuple[str, ...] = ()
    reference_fallback_message: str = ""
    generation_completed: bool = False
    failure_stage: str = ""

    @property
    def success(self) -> bool:
        path = _path_text(self.image_path, 1000)
        if not path:
            return False
        try:
            return Path(path).is_file()
        except (OSError, ValueError):
            return False

    def as_legacy_tuple(self) -> tuple[str, str, str]:
        return self.backend, self.image_path, self.note


@dataclass(frozen=True, slots=True, eq=False)
class _ExternalPhotoGenerationOutcome:
    """Internal result state that preserves the legacy ``(path, note)`` API."""

    image_path: str = ""
    note: str = ""
    generation_completed: bool = False
    failure_stage: str = ""

    def as_legacy_tuple(self) -> tuple[str, str]:
        return self.image_path, self.note

    def __iter__(self):
        yield self.image_path
        yield self.note

    def __len__(self) -> int:
        return 2

    def __getitem__(self, index: int) -> str:
        return self.as_legacy_tuple()[index]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _ExternalPhotoGenerationOutcome):
            return (
                self.image_path,
                self.note,
                self.generation_completed,
                self.failure_stage,
            ) == (
                other.image_path,
                other.note,
                other.generation_completed,
                other.failure_stage,
            )
        if isinstance(other, (tuple, list)) and len(other) == 2:
            return self.as_legacy_tuple() == (other[0], other[1])
        return NotImplemented


class SyntheticPrivateWakeEvent(AstrMessageEvent):
    def __init__(
        self,
        *,
        context: Context,
        session: MessageSession,
        message: str,
        sender_name: str = "PrivateCompanion",
    ) -> None:
        platform_meta = PlatformMetadata(
            name=session.platform_id,
            description="SyntheticPrivateWake",
            id=session.platform_id,
        )

        msg_obj = AstrBotMessage()
        msg_obj.type = session.message_type
        msg_obj.self_id = session.session_id
        msg_obj.session_id = session.session_id
        msg_obj.message_id = f"private_companion_{uuid.uuid4().hex}"
        msg_obj.sender = MessageMember(user_id=session.session_id, nickname=sender_name)
        msg_obj.message = [Plain(message)]
        msg_obj.message_str = message
        msg_obj.raw_message = message
        msg_obj.timestamp = int(time.time())

        super().__init__(message, msg_obj, platform_meta, session.session_id)
        self.session = session
        self.context_obj = context
        self.is_at_or_wake_command = True
        self.is_wake = True

    async def send(self, message: MessageChain) -> None:
        if message is None:
            return
        await self.context_obj.send_message(self.session, message)
        await super().send(message)


class _CapturedSendMessageCall:
    def __init__(self, session: str, messages: list[dict[str, Any]]) -> None:
        self.session = str(session or "")
        self.messages = [dict(item) for item in messages if isinstance(item, dict)]


class _CapturedFrameworkSendMessage(Exception):
    """Stop the framework agent once its send_message_to_user payload is captured."""


class ProactiveMessageMixin(FinalResponsePersistenceMixin):
    """主动消息生成、动作执行和发送链路"""

    def _proactive_chat_bridge_user(self, session_id: str) -> tuple[str, dict[str, Any] | None]:
        session = _single_line(session_id, 180)
        if ":FriendMessage:" not in session:
            return "", None
        resolver = getattr(self, "_input_status_user_id_from_umo", None)
        user_id = resolver(session) if callable(resolver) else ""
        if not user_id:
            user_id = _single_line(session.split(":FriendMessage:", 1)[-1], 100)
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        if callable(canonicalizer):
            user_id = str(canonicalizer(user_id) or "").strip()
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else None
        user = users.get(user_id) if isinstance(users, dict) else None
        snapshot_getter = getattr(self, "_req041_relationship_snapshot_view", None)
        if isinstance(user, dict) and callable(snapshot_getter):
            user = snapshot_getter(user, source="proactive_chat_bridge")
        return user_id, user if isinstance(user, dict) else None

    def _proactive_chat_decorating_context(self) -> dict[str, Any]:
        """Read Proactive Chat's send context, preferring the deep runtime attempt."""
        context: dict[str, Any] = {
            "detected": False,
            "deep_bridge": False,
            "attempt_id": "",
            "token": "",
            "full_text": "",
            "tts_sent": False,
            "segment_index": 0,
            "segment_count": 1,
        }
        runtime_context: dict[str, Any] = {}
        runtime_bridge = getattr(self, "_proactive_chat_runtime_bridge", None)
        if runtime_bridge is not None:
            try:
                runtime_context = runtime_bridge.outbound_context()
            except Exception:
                runtime_context = {}
        frame = sys._getframe()
        try:
            for _ in range(48):
                frame = frame.f_back
                if frame is None:
                    break
                module_name = str(frame.f_globals.get("__name__", "") or "")
                if "astrbot_plugin_proactive_chat" not in module_name:
                    continue
                context["detected"] = True
                if frame.f_code.co_name != "_send_proactive_message":
                    continue
                local_values = frame.f_locals
                context["attempt_id"] = f"proactive-chat-{id(frame):x}"
                context["full_text"] = str(local_values.get("text") or "").strip()
                context["tts_sent"] = bool(local_values.get("is_tts_sent", False))
                segments = local_values.get("segments")
                if isinstance(segments, list) and segments:
                    context["segment_count"] = max(1, len(segments))
                    try:
                        context["segment_index"] = max(
                            0,
                            min(
                                int(local_values.get("idx", 0) or 0),
                                context["segment_count"] - 1,
                            ),
                        )
                    except Exception:
                        context["segment_index"] = 0
        finally:
            del frame
        if runtime_context:
            context.update(
                {
                    "detected": True,
                    "deep_bridge": True,
                    "attempt_id": _single_line(runtime_context.get("attempt_id"), 100),
                    "token": _single_line(runtime_context.get("token"), 80),
                    "full_text": str(runtime_context.get("full_text") or context.get("full_text") or "").strip(),
                }
            )
        return context

    def _proactive_chat_bridge_review_cache(self) -> dict[str, dict[str, Any]]:
        cache = getattr(self, "_proactive_chat_bridge_reviews", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_proactive_chat_bridge_reviews", cache)
        now = _now_ts()
        stale = [
            key
            for key, item in cache.items()
            if not isinstance(item, dict) or now - _safe_float(item.get("created_at"), 0) > 10 * 60
        ]
        for key in stale:
            cache.pop(key, None)
        if len(cache) > 80:
            ordered = sorted(
                cache,
                key=lambda key: _safe_float(cache.get(key, {}).get("created_at"), 0),
            )
            for key in ordered[: len(cache) - 80]:
                cache.pop(key, None)
        return cache

    def _proactive_chat_bridge_preflight_block_reason(
        self,
        user: dict[str, Any],
        *,
        now: float,
    ) -> str:
        relationship_gate = getattr(self, "_current_relationship_gate_mode", None)
        emotion_gate = getattr(self, "_current_emotion_gate_mode", None)
        try:
            if callable(relationship_gate) and relationship_gate(user, now=now) == "backoff":
                return "expression_contact_boundary"
            if callable(emotion_gate) and emotion_gate(user, now=now) == "hurt":
                return "expression_interaction_hurt"
        except Exception:
            return "expression_boundary_check_unavailable"
        rest_gate = getattr(self, "_proactive_rest_block_until", None)
        if callable(rest_gate):
            try:
                if _safe_float(
                    rest_gate(
                        user,
                        now=now,
                        reason="check_in",
                        source="proactive_chat",
                    ),
                    0,
                ) > now:
                    return "user_explicit_rest"
            except Exception:
                pass
        busy_gate = getattr(self, "_busy_reply_proactive_block_until", None)
        if callable(busy_gate):
            try:
                if _safe_float(
                    busy_gate(
                        user,
                        now=now,
                        reason="check_in",
                        source="proactive_chat",
                    ),
                    0,
                ) > now:
                    return "bot_busy_schedule"
            except Exception:
                pass
        quiet_checker = getattr(self, "_is_quiet_time", None)
        insomnia_checker = getattr(self, "_can_send_insomnia_night_message", None)
        quiet = False
        try:
            quiet = bool(quiet_checker()) if callable(quiet_checker) else False
            insomnia_allowed = bool(insomnia_checker(user)) if callable(insomnia_checker) else False
            if quiet and not insomnia_allowed:
                return "private_companion_quiet_hours"
        except Exception:
            pass
        daily_limit_getter = getattr(self, "_effective_user_daily_limit", None)
        unlimited_checker = getattr(self, "_proactive_daily_limit_is_unlimited", None)
        reset_daily = getattr(self, "_reset_daily_counter_if_needed", None)
        try:
            if callable(reset_daily):
                reset_daily(user)
            if not callable(daily_limit_getter):
                return "expression_allowance_unavailable"
            daily_limit = _safe_int(daily_limit_getter(user), 0, 0)
            unlimited = bool(unlimited_checker(daily_limit)) if callable(unlimited_checker) else False
            if daily_limit <= 0:
                return "expression_proactive_budget_zero"
            if not unlimited and _safe_int(user.get("sent_today"), 0, 0) >= daily_limit:
                return "expression_proactive_budget_exhausted"
        except Exception:
            return "expression_allowance_unavailable"
        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        try:
            if not callable(expression_builder):
                return "expression_decision_unavailable"
            decision = expression_builder(
                user,
                proactive_candidate={"eligible": True, "dynamic_allowance": daily_limit, "current_ts": now},
                schedule={"quiet_hours": quiet},
                message_intent={"requested_content_tier": "normal"},
                now=now,
            )
            projection = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
            if _single_line(projection.get("blocker"), 40):
                return f"expression_{_single_line(projection.get('blocker'), 40)}"
            if _safe_int(projection.get("proactive_budget"), 0, 0) <= 0:
                return "expression_proactive_budget_zero"
        except Exception:
            return "expression_decision_unavailable"
        collision_window = max(
            10,
            min(
                600,
                _safe_int(
                    runtime_persona_setting(
                        self, "proactive_chat_bridge_collision_window_seconds", 90
                    ),
                    90,
                    10,
                    600,
                ),
            ),
        )
        last_sent = max(
            _safe_float(user.get("last_sent"), 0),
            _safe_float(user.get("last_proactive_sent_at"), 0),
            _safe_float(user.get("proactive_chat_bridge_last_sent_at"), 0),
        )
        if last_sent > 0 and now - last_sent < collision_window:
            return "recent_proactive_collision_window"
        return ""

    async def _prepare_proactive_chat_bridge(
        self,
        session_id: str,
        *,
        unanswered_count: int = 0,
    ) -> dict[str, Any]:
        if not bool(runtime_persona_setting(self, "enable_proactive_chat_integration", True)):
            return {"enabled": False, "allowed": True, "reason": "integration_disabled"}
        user_id, user = self._proactive_chat_bridge_user(session_id)
        if not user_id or not isinstance(user, dict):
            return {"enabled": False, "allowed": True, "reason": "private_user_not_managed"}
        enabled_checker = getattr(self, "_user_enabled_for_proactive", None)
        if callable(enabled_checker) and not enabled_checker(user_id, user):
            return {"enabled": True, "allowed": False, "reason": "private_user_disabled"}
        now = _now_ts()
        if bool(user.get("proactive_sending")):
            recover = getattr(self, "_recover_stale_proactive_sending", None)
            if callable(recover):
                recover(user, now=now)
        if bool(user.get("proactive_sending")):
            return {"enabled": True, "allowed": False, "reason": "another_proactive_message_is_sending"}
        preflight_block = self._proactive_chat_bridge_preflight_block_reason(user, now=now)
        if preflight_block:
            return {"enabled": True, "allowed": False, "reason": preflight_block}
        runtime_formatter = getattr(self, "_format_proactive_review_runtime_context", None)
        runtime_context = runtime_formatter(user, now=now) if callable(runtime_formatter) else ""
        identity_formatter = getattr(self, "_format_proactive_recipient_identity_guard", None)
        recipient_identity = (
            identity_formatter(user, _single_line(user.get("nickname"), 40))
            if callable(identity_formatter)
            else ""
        )
        voice_formatter = getattr(self, "_format_proactive_voice_prompt", None)
        proactive_voice = voice_formatter() if callable(voice_formatter) else ""
        expression_formatter = getattr(self, "_format_expression_voice_for_prompt", None)
        expression_voice = (
            expression_formatter(
                scope="proactive",
                target_id=user_id,
                context_owner=user,
                stage_owner=user,
            )
            if callable(expression_formatter)
            else ""
        )
        relationship_formatter = getattr(self, "_format_proactive_relationship_fact", None)
        try:
            relationship_context = relationship_formatter(user) if callable(relationship_formatter) else ""
        except Exception:
            relationship_context = ""
        intent_formatter = getattr(self, "_format_intent_relationship_injection", None)
        try:
            intent_context = intent_formatter(user) if callable(intent_formatter) else ""
        except Exception:
            intent_context = ""
        time_formatter = getattr(self, "_format_time_period_injection", None)
        try:
            time_context = time_formatter() if callable(time_formatter) else ""
        except Exception:
            time_context = ""
        state_context = ""
        state_formatter = getattr(self, "_format_state_for_framework_prompt", None)
        daily_state = self.data.get("daily_state") if isinstance(getattr(self, "data", None), dict) else None
        if callable(state_formatter):
            try:
                state_context = state_formatter(
                    daily_state if isinstance(daily_state, dict) else {},
                    reason="check_in",
                    action="message",
                )
            except Exception:
                state_context = ""
        location_formatter = getattr(self, "_format_mobile_user_location_context_for_proactive", None)
        try:
            location_context = location_formatter(user) if callable(location_formatter) else ""
        except Exception:
            location_context = ""
        schedule_context = ""
        schedule_formatter = getattr(self, "_format_schedule_context_for_prompt", None)
        if callable(schedule_formatter):
            try:
                schedule_context = str(schedule_formatter() or "").strip()
            except Exception:
                schedule_context = ""
        schedule_sanitizer = getattr(self, "_sanitize_schedule_context_for_private_user", None)
        if schedule_context and callable(schedule_sanitizer):
            try:
                schedule_context = str(schedule_sanitizer(schedule_context, user) or "").strip()
            except Exception:
                schedule_context = ""
        fragment = "\n".join(
            part
            for part in (
                "【Private Companion × Proactive Chat 联动】",
                "这是一条由 Proactive Chat 定时触发的主动消息，不是在回复用户刚发来的话。",
                f"当前连续未回应次数：{max(0, int(unanswered_count or 0))}。不要因此质问、催促或制造负担。",
                recipient_identity,
                f"【当前关系】\n{relationship_context}" if relationship_context else "",
                f"【当前互动气氛】\n{_single_line(intent_context, 360)}" if intent_context else "",
                f"【当前时机】\n{time_context}" if time_context else "",
                f"【当前运行态】\n{runtime_context}" if runtime_context else "",
                f"【Bot 当前状态底色】\n{state_context}" if state_context else "",
                location_context,
                f"【当前生活片段候选】\n{schedule_context[:700]}" if schedule_context and schedule_context != "（暂无）" else "",
                proactive_voice,
                expression_voice,
                "先沿用 Proactive Chat 本轮自己的主动动机，再从以上信息中只取与当前收件人和此刻真正相关的少量线索；不要拼成状态播报。",
                "只吸收与当前收件人、关系和时机有关的内容；不要提到插件、调度器、状态字段或联动过程。",
            )
            if str(part or "").strip()
        )
        token = uuid.uuid4().hex
        lock = getattr(self, "_data_lock", None)
        if lock is not None:
            async with lock:
                current = self._get_user(user_id)
                if current.get("proactive_sending"):
                    return {"enabled": True, "allowed": False, "reason": "another_proactive_message_is_sending"}
                preflight_block = self._proactive_chat_bridge_preflight_block_reason(current, now=now)
                if preflight_block:
                    return {"enabled": True, "allowed": False, "reason": preflight_block}
                current["proactive_sending"] = True
                current["proactive_sending_started_at"] = now
                current["proactive_chat_bridge_token"] = token
                current["proactive_chat_bridge_session"] = _single_line(session_id, 180)
                self._save_data_sync(sections={"users"})
        return {
            "enabled": True,
            "allowed": True,
            "token": token,
            "prompt_fragment": fragment[:5200].strip(),
            "user_id": user_id,
        }

    async def _review_proactive_chat_bridge_message(
        self,
        session_id: str,
        text: str,
        *,
        token: str = "",
        attempt_id: str = "",
    ) -> dict[str, Any]:
        if not bool(runtime_persona_setting(self, "enable_proactive_chat_integration", True)):
            return {"ok": True, "text": str(text or "").strip(), "reason": "integration_disabled"}
        user_id, user = self._proactive_chat_bridge_user(session_id)
        if not user_id or not isinstance(user, dict):
            return {"ok": True, "text": str(text or "").strip(), "reason": "bridge_not_managed"}
        enabled_checker = getattr(self, "_user_enabled_for_proactive", None)
        if callable(enabled_checker) and not enabled_checker(user_id, user):
            return {"ok": True, "text": str(text or "").strip(), "reason": "bridge_not_managed"}
        expected = _single_line(user.get("proactive_chat_bridge_token"), 80)
        if expected and token != expected:
            return {"ok": False, "text": "", "reason": "bridge_token_mismatch"}
        cache_key = _single_line(attempt_id, 100)
        if cache_key:
            cached = self._proactive_chat_bridge_review_cache().get(cache_key)
            if isinstance(cached, dict) and cached.get("session_id") == _single_line(session_id, 180):
                result = cached.get("result")
                if isinstance(result, dict):
                    return dict(result)
        cleaned = self._sanitize_proactive_text(str(text or "").strip())
        if not cleaned:
            return {"ok": False, "text": "", "reason": "empty_after_sanitize"}
        local_checker = getattr(self, "_local_proactive_send_decision", None)
        decision: dict[str, Any] = {"decision": "send", "reason": "local_checker_unavailable"}
        if callable(local_checker):
            decision = local_checker(
                user,
                cleaned,
                reason="proactive_chat_bridge",
                action="message",
                motive="由 Proactive Chat 提供更即时的主动触发",
                topic="",
                action_context="没有外部图片或工具结果",
            )
        review_mode = str(
            runtime_persona_setting(self, "proactive_chat_bridge_review_mode", "local")
            or "local"
        ).strip().lower()
        full_reviewer = getattr(self, "_review_proactive_message_send_decision", None)
        if (
            review_mode == "follow_proactive_review"
            and bool(runtime_persona_setting(self, "enable_proactive_message_review", True))
            and callable(full_reviewer)
            and str(runtime_persona_setting(self, "proactive_review_mode", "full") or "full")
            .strip()
            .lower()
            != "local_only"
        ):
            try:
                decision = await full_reviewer(
                    user,
                    cleaned,
                    reason="proactive_chat_bridge",
                    action="message",
                    motive="由 Proactive Chat 提供更即时的主动触发",
                    topic="",
                    action_summary="Proactive Chat 已生成纯文本主动候选；没有外部图片或工具结果",
                )
            except Exception as exc:
                logger.warning(
                    "[PrivateCompanion] Proactive Chat 联动终审失败，已回退本地检查: %s",
                    _single_line(exc, 160),
                )

        action = str(decision.get("decision") or "send").strip().lower()
        if action in {"drop", "defer"}:
            result = {
                "ok": False,
                "text": "",
                "reason": _single_line(decision.get("reason"), 160) or action,
                "decision": action,
            }
        else:
            if action == "rewrite" and _single_line(decision.get("text"), 500):
                cleaned = self._sanitize_proactive_text(_single_line(decision.get("text"), 500))
            result = {
                "ok": bool(cleaned),
                "text": cleaned,
                "reason": _single_line(decision.get("reason"), 160)
                or ("private_companion_model_review" if review_mode == "follow_proactive_review" else "private_companion_local_review"),
                "decision": action if action in {"send", "rewrite"} else "send",
            }
        if cache_key:
            self._proactive_chat_bridge_review_cache()[cache_key] = {
                "created_at": _now_ts(),
                "session_id": _single_line(session_id, 180),
                "result": dict(result),
            }
        return result

    async def _record_proactive_chat_bridge_sent(
        self,
        session_id: str,
        text: str,
        *,
        token: str = "",
        attempt_id: str = "",
    ) -> dict[str, Any]:
        if not bool(runtime_persona_setting(self, "enable_proactive_chat_integration", True)):
            return {"recorded": False, "reason": "integration_disabled"}
        user_id, user = self._proactive_chat_bridge_user(session_id)
        if not user_id or not isinstance(user, dict):
            return {"recorded": False, "reason": "private_user_not_managed"}
        enabled_checker = getattr(self, "_user_enabled_for_proactive", None)
        if callable(enabled_checker) and not enabled_checker(user_id, user):
            return {"recorded": False, "reason": "private_user_not_managed"}
        now = _now_ts()
        visible_formatter = getattr(self, "_visible_text_without_tts_reading", None)
        visible = (
            visible_formatter(text, limit=500)
            if callable(visible_formatter)
            else _single_line(text, 500)
        )
        lock = getattr(self, "_data_lock", None)
        if lock is not None:
            async with lock:
                current = self._get_user(user_id)
                expected = _single_line(current.get("proactive_chat_bridge_token"), 80)
                if expected and token != expected:
                    return {"recorded": False, "reason": "bridge_token_mismatch"}
                normalized_attempt = _single_line(attempt_id, 100)
                if normalized_attempt and _single_line(current.get("proactive_chat_bridge_last_attempt_id"), 100) == normalized_attempt:
                    return {"recorded": False, "reason": "duplicate_attempt", "user_id": user_id}
                if expected:
                    current["proactive_sending"] = False
                    current["proactive_sending_started_at"] = 0
                current["proactive_chat_bridge_token"] = ""
                current["proactive_chat_bridge_session"] = ""
                current["last_sent"] = now
                current["last_proactive_sent_at"] = now
                current["last_proactive_message"] = visible
                current["last_companion_message"] = visible
                current["last_companion_message_at"] = now
                current["last_proactive_delivery_umo"] = _single_line(session_id, 180)
                current["last_proactive_delivery_inbound_count"] = _safe_int(current.get("inbound_count"), 0)
                current["last_proactive_reply_context_consumed_for"] = 0
                current["last_proactive_reason"] = "proactive_chat_bridge"
                current["last_proactive_action"] = "message"
                current["last_proactive_motive"] = "Proactive Chat 即时触发"
                current["last_proactive_source"] = "proactive_chat"
                current["proactive_chat_bridge_last_sent_at"] = now
                current["proactive_chat_bridge_last_attempt_id"] = normalized_attempt
                reset_daily = getattr(self, "_reset_daily_counter_if_needed", None)
                if callable(reset_daily):
                    reset_daily(current)
                elif str(current.get("sent_day") or "") != _today_key():
                    current["sent_day"] = _today_key()
                    current["sent_today"] = 0
                current["sent_today"] = _safe_int(current.get("sent_today"), 0) + 1
                current["proactive_sent_count"] = _safe_int(current.get("proactive_sent_count"), 0) + 1
                current["ignored_streak"] = _safe_int(current.get("ignored_streak"), 0) + 1
                current["awaiting_reply_since"] = now
                current["pending_followup_event"] = {}
                current["planned_proactive_quota_exempt"] = False
                action_recorder = getattr(self, "_note_action_sent", None)
                if callable(action_recorder):
                    action_recorder(
                        current,
                        "message",
                        reason="proactive_chat_bridge",
                        text=visible or text,
                        motive="Proactive Chat 即时触发",
                        action_summary="外部主动插件已完成发送",
                        source="proactive_chat",
                    )
                topic_recorder = getattr(self, "_remember_proactive_topic", None)
                if callable(topic_recorder):
                    topic_recorder(current, text=visible or text, topic="Proactive Chat", motive="即时主动触发")
                self._save_data_sync(sections={"users"})
        logger.info(
            "[PrivateCompanion] 已同步 Proactive Chat 主动发送: user=%s session=%s text=%s",
            user_id,
            _single_line(session_id, 120),
            _single_line(visible, 120),
        )
        return {"recorded": True, "user_id": user_id, "sent_at": now}

    async def _cancel_proactive_chat_bridge(self, session_id: str, *, token: str = "") -> bool:
        user_id, user = self._proactive_chat_bridge_user(session_id)
        if not user_id or not isinstance(user, dict):
            return False
        lock = getattr(self, "_data_lock", None)
        if lock is None:
            return False
        async with lock:
            current = self._get_user(user_id)
            expected = _single_line(current.get("proactive_chat_bridge_token"), 80)
            if not expected or token != expected:
                return False
            current["proactive_sending"] = False
            current["proactive_sending_started_at"] = 0
            current["proactive_chat_bridge_token"] = ""
            current["proactive_chat_bridge_session"] = ""
            self._save_data_sync(sections={"users"})
        return True

    @staticmethod
    def _looks_like_internal_provider_error_text(text: Any) -> bool:
        cleaned = _single_line(text, 1000).lower()
        if not cleaned:
            return False
        normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff_]+", " ", cleaned).strip()
        compact = re.sub(r"[^a-z0-9\u4e00-\u9fff_]+", "", cleaned)
        direct_markers = (
            "all chat models failed",
            "all llm providers failed",
            "prompt could not be submitted",
            "prompt was not submitted",
            "try rephrasing the prompt",
            "generative ai prohibited use policy",
            "prompt contains sensitive words",
            "badrequesterror",
            "api connection error",
            "apiconnectionerror",
            "api status error",
            "apistatuserror",
            "authenticationerror",
            "permissiondeniederror",
            "ratelimiterror",
            "notfounderror",
            "internalservererror",
            "provider api error",
            "unable to submit request",
            "invalid_request",
            "invalid request error",
            "主动消息专用模式下",
            "普通被动回复不可使用 private companion 工具",
            "主动渲染阶段不可使用 private companion 工具",
            "has sent the result directly to the user",
            "error code: 400",
            "error code 400",
            "400 bad request",
            "模型调用失败",
            "工具调用失败",
            "函数工具调用失败",
            "api 调用失败",
            "api调用失败",
            "provider 调用失败",
            "provider调用失败",
            "没有返回值，或者已将结果直接发送给用户",
            "没有返回值,或者已将结果直接发送给用户",
        )
        compact_markers = (
            "allchatmodelsfailed",
            "allllmprovidersfailed",
            "promptcouldnotbesubmitted",
            "promptwasnotsubmitted",
            "tryrephrasingtheprompt",
            "generativeaiprohibitedusepolicy",
            "promptcontainssensitivewords",
            "badrequesterror",
            "apiconnectionerror",
            "apistatuserror",
            "authenticationerror",
            "permissiondeniederror",
            "ratelimiterror",
            "notfounderror",
            "internalservererror",
            "providerapierror",
            "unabletosubmitrequest",
            "invalid_request",
            "invalidrequesterror",
            "主动消息专用模式下",
            "普通被动回复不可使用privatecompanion工具",
            "主动渲染阶段不可使用privatecompanion工具",
            "hassenttheresultdirectlytotheuser",
            "errorcode400",
            "400badrequest",
            "模型调用失败",
            "工具调用失败",
            "函数工具调用失败",
            "api调用失败",
            "provider调用失败",
            "没有返回值或者已将结果直接发送给用户",
        )
        if any(marker in cleaned or marker in normalized for marker in direct_markers):
            return True
        if any(marker in compact for marker in compact_markers):
            return True
        provider_error_context = any(
            token in compact
            for token in (
                "providerapierror",
                "errorcode",
                "statuscode",
                "badrequest",
                "invalidrequest",
                "requestfailed",
                "请求失败",
                "调用失败",
                "模型调用失败",
                "工具调用失败",
            )
        )
        if "errorcode" in compact and any(
            token in compact
            for token in (
                "badrequest",
                "invalidrequest",
                "provider",
                "apierror",
                "functiondeclaration",
            )
        ):
            return True
        if "functiondeclaration" in compact and provider_error_context and any(
            token in compact for token in ("schema", "properties", "parameters", "tool", "tools", "badrequest", "invalidrequest")
        ):
            return True
        if any(token in compact for token in ("schemadidntspecify", "toolschema", "image_url", "invalidparameter")) and provider_error_context:
            return True
        if "aisearch" in cleaned and any(
            marker in cleaned
            for marker in (
                "failed",
                "badrequest",
                "invalid_request",
                "unable to submit",
                "provider api",
            )
        ):
            return True
        return False

    def _clean_external_share_source_field(self, value: Any, limit: int = 160) -> str:
        text = _single_line(value, limit)
        if not text:
            return ""
        if self._looks_like_internal_provider_error_text(text):
            return ""
        if self._framework_agent_meta_summary_leak(text):
            return ""
        return text

    def _format_bilibili_video_action_context(self, user: dict[str, Any]) -> str:
        video = user.get("bilibili_video_context")
        if not isinstance(video, dict):
            return ""
        if _now_ts() - _safe_float(video.get("created_ts"), 0) > 6 * 3600:
            return ""
        title = self._clean_external_share_source_field(video.get("title"), 80)
        bvid = self._clean_external_share_source_field(video.get("bvid"), 32)
        up_name = self._clean_external_share_source_field(video.get("up_name"), 40)
        score = _safe_int(video.get("score"), 0, 0, 10)
        mood = self._clean_external_share_source_field(video.get("mood"), 24)
        comment = self._clean_external_share_source_field(video.get("comment"), 120)
        review = self._clean_external_share_source_field(video.get("review"), 180)
        source = self._clean_external_share_source_field(video.get("source"), 40)
        memory_context = video.get("memory_context") if isinstance(video.get("memory_context"), list) else []
        memory_lines = [
            text
            for item in memory_context
            for text in [self._clean_external_share_source_field(item, 160)]
            if text
        ][:3]
        if not title and not bvid and not comment and not review:
            return ""
        parts = [
            "B站视频分享线索",
            f"标题：{title}" if title else "",
            f"链接：https://www.bilibili.com/video/{bvid}" if bvid else "",
            f"UP：{up_name}" if up_name else "",
            f"评分：{score}/10" if score else "",
            f"心情：{mood}" if mood else "",
            f"短评：{comment}" if comment else "",
            f"回味：{review}" if review else "",
            f"来源：{source}" if source else "",
            "BiliBot记忆：" + " / ".join(memory_lines) if memory_lines else "",
        ]
        return "\n".join(part for part in parts if part)

    def _format_news_action_context(self, user: dict[str, Any]) -> str:
        news = user.get("news_context")
        if not isinstance(news, dict):
            return ""
        if _now_ts() - _safe_float(news.get("created_ts"), 0) > 8 * 3600:
            return ""
        topic = self._clean_external_share_source_field(news.get("topic"), 60)
        headline = self._clean_external_share_source_field(news.get("headline"), 100)
        source = self._clean_external_share_source_field(news.get("selected_source"), 40)
        impression = self._clean_external_share_source_field(news.get("impression"), 240)
        link = self._clean_external_share_source_field(news.get("selected_link"), 400)
        self_link = news.get("self_link") if isinstance(news.get("self_link"), dict) else {}
        self_link_text = self._clean_external_share_source_field(self_link.get("self_link") if isinstance(self_link, dict) else "", 180)
        self_link_tone = self._clean_external_share_source_field(news.get("share_tone") or (self_link.get("tone") if isinstance(self_link, dict) else ""), 80)
        self_link_boundary = self._clean_external_share_source_field(news.get("share_boundary") or (self_link.get("boundary") if isinstance(self_link, dict) else ""), 160)
        if not topic and not headline and not link and not impression:
            return ""
        parts = [
            "新闻阅读线索",
            f"话题：{topic}" if topic else "",
            f"标题：{headline}" if headline else "",
            f"来源：{source}" if source else "",
            f"内部印象：{impression}" if impression else "",
            f"和自己有关的地方：{self_link_text}" if self_link_text else "",
            f"表达气质：{self_link_tone}" if self_link_tone else "",
            f"额外边界：{self_link_boundary}" if self_link_boundary else "",
            f"链接：{link}" if link else "",
            "表达要求：不要像播报新闻,不要夸大或补充未知事实；按人格正常说话即可。",
        ]
        return "\n".join(part for part in parts if part)

    def _format_web_exploration_action_context(self, user: dict[str, Any]) -> str:
        exploration = user.get("web_exploration_context")
        if not isinstance(exploration, dict):
            return ""
        if _now_ts() - _safe_float(exploration.get("created_ts"), 0) > 10 * 3600:
            return ""
        query = self._clean_external_share_source_field(exploration.get("query"), 80)
        topic = self._clean_external_share_source_field(exploration.get("topic"), 80)
        note = self._clean_external_share_source_field(exploration.get("note"), 260)
        source_title = self._clean_external_share_source_field(exploration.get("source_title"), 120)
        source_url = self._clean_external_share_source_field(exploration.get("source_url"), 420)
        source_platform = self._external_share_platform_from_url(source_url)
        reason = self._clean_external_share_source_field(exploration.get("reason"), 140)
        self_link = exploration.get("self_link") if isinstance(exploration.get("self_link"), dict) else {}
        self_link_text = self._clean_external_share_source_field(self_link.get("self_link") if isinstance(self_link, dict) else "", 180)
        self_link_tone = self._clean_external_share_source_field(exploration.get("share_tone") or (self_link.get("tone") if isinstance(self_link, dict) else ""), 80)
        self_link_boundary = self._clean_external_share_source_field(exploration.get("share_boundary") or (self_link.get("boundary") if isinstance(self_link, dict) else ""), 160)
        if not query and not topic and not note and not source_title and not source_url:
            return ""
        parts = [
            "网页探索线索",
            f"搜索词：{query}" if query else "",
            f"为什么想查：{reason}" if reason else "",
            f"探索主题：{topic}" if topic else "",
            f"留下的印象：{note}" if note else "",
            f"和自己有关的地方：{self_link_text}" if self_link_text else "",
            f"表达气质：{self_link_tone}" if self_link_tone else "",
            f"额外边界：{self_link_boundary}" if self_link_boundary else "",
            f"参考来源：{source_title}" if source_title else "",
            f"来源平台（以链接域名为准）：{source_platform}" if source_platform else "",
            f"链接：{source_url}" if source_url else "",
            "表达要求：自然地向用户分享自己刚看的这条内容。标题、印象和链接只是事实参考，按当前人格正常说话，不要照抄字段。",
        ]
        return "\n".join(part for part in parts if part)

    @staticmethod
    def _external_share_platform_from_url(url: Any) -> str:
        value = str(url or "").strip()
        if not value:
            return ""
        try:
            parsed = urlparse(value if "://" in value else f"//{value}")
            hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
        except Exception:
            hostname = ""
        if not hostname:
            return ""
        platform_domains = (
            (("bilibili.com", "b23.tv"), "B站"),
            (("douyin.com", "iesdouyin.com"), "抖音"),
            (("xiaohongshu.com", "xhslink.com"), "小红书"),
            (("weibo.com", "weibo.cn"), "微博"),
            (("zhihu.com",), "知乎"),
            (("youtube.com", "youtu.be"), "YouTube"),
            (("reddit.com", "redd.it"), "Reddit"),
            (("github.com",), "GitHub"),
            (("toutiao.com",), "今日头条"),
        )
        for domains, label in platform_domains:
            if any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains):
                return label
        return ""

    @staticmethod
    def _external_share_claimed_platform(text: Any) -> str:
        value = _single_line(text, 320)
        patterns = (
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}(?:B站|哔哩哔哩)|(?:B站|哔哩哔哩)(?:视频|上|里|《)", "B站"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}抖音|抖音(?:视频|上|里|《)", "抖音"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}小红书|小红书(?:笔记|上|里|《)", "小红书"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}微博|微博(?:上|里|《)", "微博"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}知乎|知乎(?:上|里|《)", "知乎"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}YouTube|YouTube(?:上|里)", "YouTube"),
            (r"(?:刚|在|从|刷到|看到|翻到).{0,8}Reddit|Reddit(?:上|里|《)", "Reddit"),
        )
        for pattern, label in patterns:
            if re.search(pattern, value, flags=re.I):
                return label
        return ""

    def _proactive_link_platform_mismatch_reason(self, text: Any) -> str:
        cleaned = _single_line(text, 600)
        claimed_platform = self._external_share_claimed_platform(cleaned)
        if not cleaned or not claimed_platform:
            return ""
        links = re.findall(r"https?://[^\s，。！？!?；;）)】\]》>]+", cleaned, flags=re.I)
        for link in links:
            actual_platform = self._external_share_platform_from_url(link)
            if actual_platform == claimed_platform:
                continue
            try:
                hostname = str(urlparse(link).hostname or "").strip().lower()
            except Exception:
                hostname = ""
            actual_label = actual_platform or hostname or "未知域名"
            return f"正文声称来源为{claimed_platform}，但链接实际属于{actual_label}"
        return ""

    def _user_asks_ai_daily_context(self, inbound_text: str) -> bool:
        text = str(inbound_text or "").strip()
        if not text:
            return False
        if any(
            token in text
            for token in (
                "AI日报", "ai日报", "AI 日报", "ai 日报",
                "AI早报", "ai早报", "AI 早报", "ai 早报",
                "大模型日报", "大模型早报", "人工智能日报", "人工智能早报",
            )
        ):
            return True
        lowered = text.lower()
        if any(token in lowered for token in ("ai daily", "daily ai", "llm daily", "ai digest")):
            return True
        return bool(("日报" in text or "早报" in text) and re.search(r"(ai|llm|大模型|人工智能|模型)", text, flags=re.IGNORECASE))

    def _ai_daily_query_requires_freshness(self, inbound_text: str) -> bool:
        text = str(inbound_text or "").strip()
        if not text:
            return False
        return bool(re.search(r"(今天|今日|今早|刚刚|刚才|最新|现在)", text, flags=re.IGNORECASE))

    def _select_ai_daily_digest_item(self, ai_state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        digest = ai_state.get("last_digest") if isinstance(ai_state.get("last_digest"), dict) else {}
        selected_item: dict[str, Any] = {}
        digest_items = digest.get("items") if isinstance(digest.get("items"), list) else []
        selected_key = _single_line(digest.get("selected_key"), 80)
        for candidate in digest_items:
            if not isinstance(candidate, dict):
                continue
            if selected_key and _single_line(candidate.get("key"), 80) == selected_key:
                selected_item = candidate
                break
        if not selected_item and digest_items and isinstance(digest_items[0], dict):
            selected_item = digest_items[0]
        return digest, selected_item

    def _user_asks_news_context(self, inbound_text: str) -> bool:
        text = str(inbound_text or "").strip()
        if not text:
            return False
        if any(token in text for token in ("新闻", "早报", "热点", "时讯", "资讯", "新消息")):
            return True
        lowered = text.lower()
        return any(token in lowered for token in ("ai news", "llm news", "daily ai", "tech news"))

    def _user_asks_web_exploration_context(self, inbound_text: str) -> bool:
        text = str(inbound_text or "").strip()
        if not text:
            return False
        if re.search(r"(主动搜索|网页探索|搜索记录|浏览记录|上网).{0,16}(什么|啥|哪|记录|看|查|搜|了解|发现)|((最近|刚才|今天|这两天|这会儿).{0,16}(搜|查|上网|浏览|了解|看了啥|看了什么|发现了什么))|你.{0,12}(搜了什么|查了什么|上网看了什么|上网看了啥|发现了什么新东西)", text):
            return True
        lowered = text.lower()
        return any(token in lowered for token in ("web exploration", "recent search", "search history", "browsing history"))

    def _format_recent_web_exploration_context_for_reply(self, inbound_text: str = "") -> str:
        if not runtime_persona_setting(self, "enable_web_exploration", False):
            return ""
        if not self._user_asks_web_exploration_context(inbound_text):
            return ""
        state = self.data.get("web_exploration") if isinstance(self.data.get("web_exploration"), dict) else {}
        digest = state.get("last_digest") if isinstance(state.get("last_digest"), dict) else {}
        notes = state.get("notes") if isinstance(state.get("notes"), list) else []
        latest_results = state.get("latest_results") if isinstance(state.get("latest_results"), list) else []
        if not digest and not notes and not latest_results:
            return (
                "【主动搜索上下文】\n"
                "用户正在询问你最近主动搜索/上网探索过什么,但当前没有可用的主动搜索记录。请自然说明自己最近还没搜到能说的东西,不要编造搜索内容。"
            )
        rows: list[str] = []
        if digest:
            rows.append(
                "最近一次搜索："
                + "｜".join(
                    part
                    for part in (
                        f"搜索词：{_single_line(digest.get('query'), 90)}" if _single_line(digest.get("query"), 90) else "",
                        f"主题：{_single_line(digest.get('topic'), 90)}" if _single_line(digest.get("topic"), 90) else "",
                        f"动机：{_single_line(digest.get('reason'), 140)}" if _single_line(digest.get("reason"), 140) else "",
                        f"笔记：{_single_line(digest.get('note'), 240)}" if _single_line(digest.get("note"), 240) else "",
                        f"来源：{_single_line(digest.get('source_title'), 120)}" if _single_line(digest.get("source_title"), 120) else "",
                    )
                    if part
                )
            )
        for item in reversed([item for item in notes if isinstance(item, dict)][-4:]):
            query = _single_line(item.get("query"), 90)
            topic = _single_line(item.get("topic"), 90)
            note = _single_line(item.get("note") or item.get("summary") or item.get("impression"), 180)
            reason = _single_line(item.get("reason"), 100)
            if query or topic or note:
                rows.append("- " + "｜".join(part for part in (f"搜索词：{query}" if query else "", topic, reason, note) if part))
        if latest_results:
            result_rows = []
            for item in latest_results[:4]:
                if not isinstance(item, dict):
                    continue
                title = _single_line(item.get("title"), 120)
                snippet = _single_line(item.get("snippet"), 160)
                if title:
                    result_rows.append("- " + "｜".join(part for part in (title, snippet) if part))
            if result_rows:
                rows.append("最近一次结果摘录：")
                rows.extend(result_rows)
        return (
            "【主动搜索上下文】\n"
            "用户正在询问你最近主动搜索/网页探索过什么。下面是真实搜索记录；回答只能基于这些内容,不要编造额外搜索、来源或结论。"
            "可以用第一人称自然概括“我刚查了/我之前搜到”,但不要说成后台系统日志。\n"
            + "\n".join(rows[:12])
        )

    def _format_recent_ai_daily_context_for_reply(self, inbound_text: str = "") -> str:
        if not runtime_persona_setting(self, "enable_news_integration", False):
            return ""
        if not self._user_asks_ai_daily_context(inbound_text):
            return ""
        state = self.data.get("news_integration") if isinstance(self.data.get("news_integration"), dict) else {}
        ai_state = state.get("ai_daily") if isinstance(state.get("ai_daily"), dict) else {}
        digest, selected_item = self._select_ai_daily_digest_item(ai_state)
        record_date = _single_line(ai_state.get("last_success_date"), 20) or _single_line(ai_state.get("date"), 20)
        source_name = _single_line(ai_state.get("last_source_name"), 40)
        source_author = _single_line(ai_state.get("last_source_author"), 60)
        source_schedule = _single_line(ai_state.get("last_source_schedule"), 10)
        video_title = _single_line(ai_state.get("last_video_title"), 120)
        video_link = _single_line(ai_state.get("last_video_link"), 360)
        text_link = _single_line(ai_state.get("last_text_link"), 360)
        headline = _single_line(digest.get("headline") or digest.get("topic"), 120)
        impression = _single_line(digest.get("impression"), 220)
        read_basis = _single_line(ai_state.get("last_read_basis"), 40)
        text_readable_raw = ai_state.get("last_text_readable")
        text_readable = bool(text_readable_raw) if isinstance(text_readable_raw, bool) else bool(selected_item.get("article_readable") and selected_item.get("article_text"))
        subtitle_status = _single_line(ai_state.get("last_video_subtitle_status") or selected_item.get("video_subtitle_status"), 40)
        if not any((record_date, source_name, video_title, headline, impression, video_link, text_link)):
            return (
                "【新闻阅读上下文】\n"
                "用户正在询问 AI 日报/早报,但当前没有可用的 AI 日报记录。请直接说明最近还没读到可确认的 AI 日报,不要编造。"
            )
        today = _today_key()
        rows: list[str] = []
        if record_date:
            if record_date != today and self._ai_daily_query_requires_freshness(inbound_text):
                rows.append(f"时间说明：今天是 {today}；最近一次可用 AI 日报记录日期是 {record_date}，不是今天。")
            else:
                rows.append(f"记录日期：{record_date}")
        if source_name or source_author or source_schedule:
            rows.append("来源：" + "｜".join(part for part in (source_name, source_author, source_schedule) if part))
        if video_title:
            rows.append(f"视频标题：{video_title}")
        if headline:
            rows.append(f"摘要重点：{headline}")
        if impression:
            rows.append(f"阅读印象：{impression}")
        if read_basis:
            rows.append(f"整理依据：{read_basis}")
        if text_link:
            rows.append(f"文字版链接：{text_link}")
        elif video_link:
            rows.append(f"视频链接：{video_link}")
        rows.append(f"正文可读：{'是' if text_readable else '否'}")
        if subtitle_status:
            rows.append(f"字幕状态：{subtitle_status}")
        return (
            "【新闻阅读上下文】\n"
            "用户正在询问 AI 日报/早报。下面是最近一次真实读到的 AI 日报记录；如果日期不是今天，请明确说出具体日期，不要说成今天刚读到。"
            "回答只能基于这些内容，不要编造额外新闻。\n"
            + "\n".join(rows[:10])
        )

    def _format_recent_news_context_for_reply(self, inbound_text: str = "") -> str:
        if not runtime_persona_setting(self, "enable_news_integration", False):
            return ""
        ai_daily_context = self._format_recent_ai_daily_context_for_reply(inbound_text)
        if ai_daily_context:
            return ai_daily_context
        if not self._user_asks_news_context(inbound_text):
            return ""
        state = self.data.get("news_integration") if isinstance(self.data.get("news_integration"), dict) else {}
        digest = state.get("last_digest") if isinstance(state.get("last_digest"), dict) else {}
        digests = state.get("digests") if isinstance(state.get("digests"), list) else []
        latest_items = state.get("latest_items") if isinstance(state.get("latest_items"), list) else []
        if not digest and not digests and not latest_items:
            return (
                "【新闻阅读上下文】\n"
                "用户正在询问今天的新闻/AI 新闻,但当前还没有可用的新闻阅读记录。请自然说明自己还没读到今天的新闻,不要编造新闻。"
            )
        rows: list[str] = []
        if digest:
            rows.append(
                "最近一次整理："
                + "｜".join(
                    part
                    for part in (
                        _single_line(digest.get("headline") or digest.get("topic"), 120),
                        _single_line(digest.get("selected_source"), 40),
                        _single_line(digest.get("impression"), 220),
                        _single_line(digest.get("selected_link"), 360),
                    )
                    if part
                )
            )
        for item in reversed([item for item in digests if isinstance(item, dict)][-4:]):
            headline = _single_line(item.get("headline") or item.get("topic"), 120)
            impression = _single_line(item.get("impression"), 180)
            source = _single_line(item.get("selected_source"), 40)
            if headline or impression:
                rows.append("- " + "｜".join(part for part in (headline, source, impression) if part))
        if latest_items:
            rows.append("候选标题：")
            for item in latest_items[:6]:
                if not isinstance(item, dict):
                    continue
                title = _single_line(item.get("title"), 120)
                source = _single_line(item.get("source"), 40)
                summary = _single_line(item.get("summary"), 160)
                if title:
                    rows.append("- " + "｜".join(part for part in (title, source, summary) if part))
        return (
            "【新闻阅读上下文】\n"
            "用户正在询问今天的新闻/AI 新闻。下面是 Bot 近期真实读过或抓到的新闻记录；回答时只能基于这些内容,不要编造额外新闻。"
            "可以按人格自然概括,如果记录不够新或不完整,要直接说明。\n"
            + "\n".join(rows[:12])
        )

    def _format_news_digest_for_command(self) -> str:
        state = self.data.get("news_integration") if isinstance(self.data.get("news_integration"), dict) else {}
        if not runtime_persona_setting(self, "enable_news_integration", False):
            return "新闻阅读功能没有开启。"
        status = _single_line(state.get("last_status"), 60) or "未知"
        digest = state.get("last_digest") if isinstance(state.get("last_digest"), dict) else {}
        latest_items = state.get("latest_items") if isinstance(state.get("latest_items"), list) else []
        if not digest and not latest_items:
            return f"这次没有读到可用新闻。\n状态：{status}"
        lines = ["今日新闻见闻："]
        if digest:
            headline = _single_line(digest.get("headline") or digest.get("topic"), 120)
            source = _single_line(digest.get("selected_source"), 40)
            impression = _single_line(digest.get("impression"), 260)
            link = _single_line(digest.get("selected_link"), 420)
            if headline:
                lines.append(f"- 重点：{headline}")
            if source:
                lines.append(f"- 来源：{source}")
            if impression:
                lines.append(f"- 印象：{impression}")
            if link:
                lines.append(f"- 链接：{link}")
        if latest_items:
            lines.append("候选标题：")
            for item in latest_items[:6]:
                if not isinstance(item, dict):
                    continue
                title = _single_line(item.get("title"), 100)
                source = _single_line(item.get("source"), 30)
                if title:
                    lines.append(f"- {title}" + (f"（{source}）" if source else ""))
        return "\n".join(lines)

    def _format_ai_daily_digest_for_command(self) -> str:
        state = self.data.get("news_integration") if isinstance(self.data.get("news_integration"), dict) else {}
        if not runtime_persona_setting(self, "enable_news_integration", False):
            return "新闻阅读功能没有开启。"
        if not runtime_persona_setting(self, "enable_ai_daily_watch", True):
            return "AI 日报/早报追踪没有开启。"
        ai_state = state.get("ai_daily") if isinstance(state.get("ai_daily"), dict) else {}
        digest, selected_item = self._select_ai_daily_digest_item(ai_state)
        record_date = _single_line(ai_state.get("last_success_date"), 20) or _single_line(ai_state.get("date"), 20)
        source_name = _single_line(ai_state.get("last_source_name"), 40)
        source_author = _single_line(ai_state.get("last_source_author"), 60)
        source_schedule = _single_line(ai_state.get("last_source_schedule"), 10)
        video_title = _single_line(ai_state.get("last_video_title"), 120)
        video_link = _single_line(ai_state.get("last_video_link"), 420)
        text_link = _single_line(ai_state.get("last_text_link"), 420)
        headline = _single_line(digest.get("headline") or digest.get("topic"), 120)
        impression = _single_line(digest.get("impression"), 260)
        read_basis = _single_line(ai_state.get("last_read_basis"), 40) or ("完整文字版正文" if bool(selected_item.get("article_readable") and selected_item.get("article_text")) else "视频标题/简介")
        if not any((record_date, source_name, video_title, headline, impression, video_link, text_link)):
            status = _single_line(ai_state.get("status"), 60) or "未知"
            return f"最近还没有可用的 AI 日报记录。\n状态：{status}"
        today = _today_key()
        lines = ["最近的 AI 日报/早报："]
        if record_date:
            lines.append(f"- 日期：{record_date}")
            if record_date != today:
                lines.append(f"- 说明：今天是 {today}，最近一次成功记录不是今天。")
        if source_name or source_author or source_schedule:
            lines.append("- 来源：" + "｜".join(part for part in (source_name, source_author, source_schedule) if part))
        if video_title:
            lines.append(f"- 视频：{video_title}")
        if headline:
            lines.append(f"- 重点：{headline}")
        if impression:
            lines.append(f"- 印象：{impression}")
        if read_basis:
            lines.append(f"- 整理依据：{read_basis}")
        if text_link:
            lines.append(f"- 文字版：{text_link}")
        elif video_link:
            lines.append(f"- 视频链接：{video_link}")
        return "\n".join(lines)

    def _format_ai_daily_status_for_command(self) -> str:
        state = self.data.get("news_integration") if isinstance(self.data.get("news_integration"), dict) else {}
        ai_state = state.get("ai_daily") if isinstance(state.get("ai_daily"), dict) else {}
        status_labels = {
            "read": "已阅读",
            "waiting_schedule": "等待定时",
            "all_sources_done": "今日来源已处理",
            "waiting_window": "等待窗口",
            "checking": "正在检查",
            "waiting_today_video": "等待今日视频",
            "today_video_without_text": "今日视频暂无文字版",
            "already_read_today_video": "今日已读",
            "missed_today_ai_daily": "今日窗口已过",
            "digest_failed": "整理失败",
        }
        status = _single_line(ai_state.get("status"), 60) or "未知"
        lines = [
            "AI 日报/早报测试结果：",
            f"- 新闻集成：{'开启' if runtime_persona_setting(self, 'enable_news_integration', False) else '关闭'}",
            f"- AI日报/早报追踪：{'开启' if runtime_persona_setting(self, 'enable_ai_daily_watch', True) else '关闭'}",
            f"- 状态：{status_labels.get(status, status)}",
        ]
        sources = ai_state.get("sources") if isinstance(ai_state.get("sources"), list) else []
        configured_sources = str(runtime_persona_setting(self, "ai_daily_sources", "") or "").strip()
        if configured_sources:
            lines.append("- 来源计划：")
            for raw_line in configured_sources.splitlines()[:8]:
                parts = [part.strip() for part in raw_line.split("|")]
                if len(parts) >= 5:
                    lines.append(f"  - {parts[0]}｜{parts[1]}｜{parts[4]}｜UID {parts[2]}")
        elif sources:
            lines.append("- 来源计划：")
            for item in sources[:8]:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    f"  - {_single_line(item.get('name'), 30)}｜{_single_line(item.get('author_name'), 40)}"
                    f"｜{_single_line(item.get('schedule'), 10)}｜UID {_single_line(item.get('mid'), 32)}"
                )
        date = _single_line(ai_state.get("date"), 20)
        checked = self._format_timestamp_elapsed(ai_state.get("last_checked_at", 0))
        success_date = _single_line(ai_state.get("last_success_date"), 20)
        title = _single_line(ai_state.get("last_video_title"), 120)
        video_link = _single_line(ai_state.get("last_video_link"), 420)
        text_link = _single_line(ai_state.get("last_text_link"), 420)
        candidate_count = _safe_int(ai_state.get("last_candidate_count"), 0, 0)
        digest, selected_item = self._select_ai_daily_digest_item(ai_state)
        if date:
            lines.append(f"- 状态日期：{date}")
        if checked:
            lines.append(f"- 最近检查：{checked}")
        if success_date:
            lines.append(f"- 最近成功日期：{success_date}")
        last_source = _single_line(ai_state.get("last_source_name"), 40)
        last_author = _single_line(ai_state.get("last_source_author"), 60)
        last_schedule = _single_line(ai_state.get("last_source_schedule"), 10)
        if last_source or last_author:
            lines.append(
                "- 最近来源："
                + "｜".join(part for part in (last_source, last_author, last_schedule) if part)
            )
        if title:
            lines.append(f"- 视频：{title}")
        if video_link:
            lines.append(f"- 视频链接：{video_link}")
        owner_name = _single_line(ai_state.get("last_video_owner_name") or selected_item.get("video_owner_name"), 80)
        tname = _single_line(ai_state.get("last_video_tname") or selected_item.get("video_tname"), 60)
        duration = _safe_int(ai_state.get("last_video_duration") or selected_item.get("video_duration"), 0, 0)
        video_context_chars = _safe_int(ai_state.get("last_video_context_chars"), 0, 0)
        if not video_context_chars and selected_item:
            video_context_chars = len(str(selected_item.get("video_context_text") or ""))
        video_tags = ai_state.get("last_video_tags") if isinstance(ai_state.get("last_video_tags"), list) else selected_item.get("video_tags")
        video_tags = [_single_line(tag, 30) for tag in video_tags if _single_line(tag, 30)] if isinstance(video_tags, list) else []
        video_comments = ai_state.get("last_video_hot_comments") if isinstance(ai_state.get("last_video_hot_comments"), list) else selected_item.get("video_hot_comments")
        video_comments = [_single_line(comment, 60) for comment in video_comments if _single_line(comment, 60)] if isinstance(video_comments, list) else []
        meta_parts = []
        if owner_name:
            meta_parts.append(f"UP主 {owner_name}")
        if tname:
            meta_parts.append(f"分区 {tname}")
        if duration:
            meta_parts.append(f"时长 {duration // 60}分{duration % 60}秒")
        if meta_parts:
            lines.append("- 视频信息：" + "｜".join(meta_parts))
        if video_context_chars:
            lines.append(f"- 视频公开信息：已读取 {video_context_chars} 字")
        if video_tags:
            lines.append(f"- 视频标签：{'、'.join(video_tags[:8])}")
        if video_comments:
            lines.append(f"- 热门评论：已读取 {len(video_comments)} 条")
        if text_link:
            lines.append(f"- 文字版链接：{text_link}")
        text_readable_raw = ai_state.get("last_text_readable")
        text_readable = bool(text_readable_raw) if isinstance(text_readable_raw, bool) else bool(selected_item.get("article_readable") and selected_item.get("article_text"))
        text_chars = _safe_int(ai_state.get("last_text_chars"), 0, 0)
        if not text_chars and selected_item:
            text_chars = len(str(selected_item.get("article_text") or ""))
        subtitle_readable_raw = ai_state.get("last_video_subtitle_readable")
        subtitle_readable = bool(subtitle_readable_raw) if isinstance(subtitle_readable_raw, bool) else bool(selected_item.get("video_subtitle_readable") and selected_item.get("video_subtitle_text"))
        subtitle_chars = _safe_int(ai_state.get("last_video_subtitle_chars"), 0, 0)
        if not subtitle_chars and selected_item:
            subtitle_chars = len(str(selected_item.get("video_subtitle_text") or ""))
        subtitle_status = _single_line(ai_state.get("last_video_subtitle_status") or selected_item.get("video_subtitle_status"), 40)
        subtitle_status_labels = {
            "read": "已读取字幕",
            "missing": "公开视频暂无字幕",
            "unavailable": "字幕不可用",
        }
        read_basis = _single_line(ai_state.get("last_read_basis"), 40) or ("完整文字版正文" if text_readable else "视频标题/简介")
        if text_link or selected_item or video_link:
            lines.append(f"- 文字版读取：{'已读取完整正文' if text_readable else '未读取到正文'}")
        if text_chars:
            lines.append(f"- 文字版正文字数：{text_chars}")
        if video_link or selected_item:
            lines.append(f"- 字幕读取：{subtitle_status_labels.get(subtitle_status, '已读取字幕' if subtitle_readable else '未读取到字幕')}")
        if subtitle_chars:
            lines.append(f"- 字幕字数：{subtitle_chars}")
        if read_basis:
            lines.append(f"- 整理依据：{read_basis}")
        if candidate_count:
            lines.append(f"- 候选数量：{candidate_count}")
        source_states = ai_state.get("source_states") if isinstance(ai_state.get("source_states"), dict) else {}
        if source_states:
            lines.append("来源状态：")
            for item in source_states.values():
                if not isinstance(item, dict):
                    continue
                source_title = _single_line(item.get("last_video_title"), 80)
                lines.append(
                    f"- {_single_line(item.get('name'), 30) or '来源'}｜{_single_line(item.get('schedule'), 10) or '未定时'}"
                    f"｜{status_labels.get(_single_line(item.get('status'), 60), _single_line(item.get('status'), 60) or '未知')}"
                    + (f"｜{source_title}" if source_title else "")
                )
        if digest:
            headline = _single_line(digest.get("headline") or digest.get("topic"), 120)
            impression = _single_line(digest.get("impression"), 220)
            if headline:
                lines.append(f"- 摘要重点：{headline}")
            if impression:
                lines.append(f"- 阅读印象：{impression}")
        candidates = ai_state.get("last_candidates") if isinstance(ai_state.get("last_candidates"), list) else []
        if candidates:
            lines.append("最近候选：")
            for item in candidates[:5]:
                if not isinstance(item, dict):
                    continue
                title_line = _single_line(item.get("title"), 90) or "未命名"
                published = _single_line(item.get("published"), 24) or "无发布时间"
                today_mark = "今天" if item.get("is_today") else "非今天"
                lines.append(f"- [{today_mark}] {published}｜{title_line}")
        if not runtime_persona_setting(self, "enable_news_integration", False):
            lines.append("提示：新闻集成关闭时不会执行抓取。")
        elif not runtime_persona_setting(self, "enable_ai_daily_watch", True):
            lines.append("提示：AI 日报/早报追踪关闭时不会执行抓取。")
        return "\n".join(lines)

    def _format_creative_share_action_context(self, user: dict[str, Any]) -> str:
        creative = user.get("creative_share_context")
        if not isinstance(creative, dict):
            return ""
        if _now_ts() - _safe_float(creative.get("created_ts"), 0) > 8 * 3600:
            return ""
        title = _single_line(creative.get("title"), 50)
        work_type = _single_line(creative.get("work_type"), 30) or "作品"
        premise = _single_line(creative.get("premise"), 140)
        tone = _single_line(creative.get("tone"), 40)
        source = _single_line(creative.get("source"), 120)
        snippet = _single_line(creative.get("snippet"), 260)
        current_chars = _safe_int(creative.get("current_chars"), 0, 0)
        target_chars = _safe_int(creative.get("target_chars"), 0, 0)
        parts = [
            "创作分享线索",
            f"作品类型：{work_type}" if work_type else "",
            f"标题：{title}" if title else "",
            f"设定：{premise}" if premise else "",
            f"灵感来源：{source}" if source else "",
            f"行文气质：{tone}" if tone else "",
            f"披露类型：{_single_line(creative.get('disclosure_kind'), 30) or 'milestone'}",
            f"节点：{_single_line(creative.get('milestone'), 30)}" if creative.get("milestone") else "",
            f"当前进度：约 {current_chars}/{target_chars} 字" if current_chars and target_chars else "",
            f"刚写到的片段：{snippet}" if snippet else "",
        ]
        return "\n".join(part for part in parts if part)

    @staticmethod
    def _creative_share_excerpt_prompt_hint() -> str:
        return (
            "【创作分享的正文边界】\n"
            "- 如果要把作品原文发给对方，只能从“刚写到的片段”中连续截取，不得改写、拼接或另编一段冒充原文。\n"
            "- 把实际作品摘录完整放在一组成对的 `「...」` 中；`「」` 内只放作品原文，聊天式引入、感受、提问和收尾都放在引号外。\n"
            "- 不要把整条聊天都包进 `「」`。如果本轮只聊创作进度、没有实际摘录作品，就不要使用 `「」`。\n"
            "- `「...」` 会作为一个完整作品气泡发送；它前后的普通聊天仍按自然聊天节奏分段。"
        )

    async def _narrate_action_context(self, action: str, action_context: str) -> str:
        narration_provider_id = _persona_provider_id(
            self, "NARRATION_PROVIDER_ID", "narration_provider_id", "fast"
        )
        if not narration_provider_id:
            return self._sanitize_action_context_text(action, action_context)
        if action in {"message", "photo_text", "poke", "voice"} or "photo_text" in action or "voice" in action or "poke" in action or not action_context:
            return self._sanitize_action_context_text(action, action_context)
        cleaned_context = self._sanitize_action_context_text(action, action_context)
        terms = self._worldview_terms()
        worldview_adaptation = self._format_worldview_adaptation_prompt()
        prompt = f"""
请把下面的{terms['screen']}观察结果转成“视觉识别后的内部摘要”,供角色继续私聊使用。
要求：
1. 只描述视觉上看出来的内容,不要猜测工具调用过程,不要输出工具名、action 名、报错栈。
2. 只概括用户大概正在看什么、做什么、情绪上是否像在忙,不要复述完整文字、账号、聊天原文、隐私细节。
3. 绝对不要直接对用户说话,不要安慰、提醒、陪伴、劝休息,不要写成一条完整回复。
4. 要像看了一眼{terms['screen']}后留在脑子里的印象,不要写成建议列表。
5. 50 字以内,只输出摘要本身。

{worldview_adaptation}

原始结果：
{cleaned_context}
""".strip()
        text = await self._llm_call(
            prompt,
            max_tokens=80,
            provider_id=narration_provider_id,
            task="screen_narration",
        )
        return _single_line(text, 120) if text else cleaned_context

    def _sanitize_action_context_text(self, action: str, action_context: str) -> str:
        text = str(action_context or "").strip()
        if "screen_peek" not in action:
            return text
        text = re.sub(r"^screen_peek[:：]\s*", "", text).strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""
        cleaned_lines = []
        for line in lines:
            if re.search(r"(我会一直在这里陪着你|要注意休息|记得休息|辛苦|别太累|我会陪着你)", line):
                continue
            line = re.sub(r"^(?:还在|你还在|感觉|看起来)", "", line).strip(",。！？ ")
            cleaned_lines.append(line)
        collapsed = ",".join(line for line in cleaned_lines if line)
        collapsed = collapsed.replace("用户", "")
        collapsed = re.sub(r"\s+", " ", collapsed).strip(",。！？ ")
        if not collapsed:
            collapsed = lines[0]
        return _single_line(collapsed, 140)

    def _format_state_for_framework_prompt(self, state: dict[str, Any], *, reason: str, action: str) -> str:
        if not isinstance(state, dict):
            return "只作为语气底色：整体平稳,不要在正文里汇报状态。"
        parts: list[str] = []
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias"), 20)
        weather = _single_line(state.get("weather"), 40)
        conditions = state.get("conditions")
        meaningful_conditions: list[str] = []
        if isinstance(conditions, list):
            for cond in conditions:
                if not isinstance(cond, dict):
                    continue
                if not self._should_show_condition(cond):
                    continue
                label = _single_line(cond.get("label") or cond.get("kind"), 16)
                text = _single_line(cond.get("text"), 28)
                if label and text:
                    meaningful_conditions.append(f"{label}/{text}")
        if meaningful_conditions:
            parts.append(
                "语气里带一点"
                + "、".join(meaningful_conditions[:2])
                + "的影响,但不要主动解释这些状态。"
            )
        elif energy <= 42:
            parts.append("语气短一点、慢一点；不要直接说状态标签、数值或内部原因。")
        elif energy >= 85:
            parts.append("语气可以轻快一点,但不要直接说自己精神很好。")
        elif mood and mood not in {"平稳", "中性"}:
            parts.append(f"语气底色偏{mood},让它自然露出来,不要直接汇报情绪。")
        if "photo_text" in action or reason in {"activity_share", "diary_share", "evening_greeting", "morning_greeting"}:
            if weather and weather not in {"暂无天气信息"}:
                parts.append(
                    f"天气只作为内部的光线/画面感参考：{weather}。"
                    "不要在正文或语音里提天气、气温、下雨或天色，也不要追问对方那边的天气；"
                    "真正的环境突变和官方预警会由独立主动原因提供明确事实。"
                )
        parts.append(
            "状态只影响语气、用词、句子长短、是否开口和话题选择；不要为了表现状态而写动作小剧场。"
        )
        parts.append(
            "如果一句话已经问候、关心或递出了具体片段,可以直接停住；不用为了显得日常,在后半句补“我刚才在发呆/躺着/盯天花板”这类状态汇报。"
        )
        parts.append(
            "即使状态是困倦、迷糊、半梦半醒或低能量,也只能让语气更轻更慢；不能降低理解质量、事实判断或正常承接能力。"
        )
        parts.append(
            "不要直接宣告“我累了/我吓到了/我在写作业”,也不要用“茶差点打翻/笔帽掉了/喝水呛到”这类动作表演状态。确实要表达时只用最短口语,如“困了”“别说了”。"
        )
        return "；".join(parts) if parts else "只作为语气底色：整体平稳,不要在正文里汇报状态。"

    def _proactive_expression_shape_hint(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
    ) -> str:
        """让当前能量影响表达形状，而不是只影响是否发送。"""
        state = self.data.get("daily_state", {})
        energy = _safe_int(state.get("energy"), 70, 0, 100) if isinstance(state, dict) else 70
        lines = [
            "【主动消息的表达形状】",
            "主动消息通常比正式回复更碎、更口语；大多数控制在 20 个汉字左右，能停在半句就不要补成完整段落。",
            "除非语义确实需要，不要每句都用完整句号收尾；不要为了显得自然而堆叠解释、背景和客套。",
        ]
        if energy <= 42:
            lines.append("当前能量偏低：优先一条短句或一个轻问候，少用感叹号，最多一到两段，不要连续铺陈。")
        elif energy >= 85:
            lines.append("当前能量偏高：可以用两三个很短的口语句连着说，允许轻微兴奋感，但不要写成长段独白。")
        else:
            lines.append("当前能量平稳：优先一到两句短消息，保留一点停顿和留白，避免客服式完整陈述。")
        if reason in {"check_in", "quiet_care", "state_share"} and "photo" not in action:
            lines.append("如果本轮只是想起对方或顺手分享一点感受，低信息量的短句也可以成立；不要为了增加信息而硬塞新事实。")
        return "\n".join(lines)

    def _format_plan_item_for_framework_prompt(self, item: dict[str, Any] | None) -> str:
        if not isinstance(item, dict):
            return ""
        activity = _single_line(item.get("activity"), 60)
        mood = _single_line(item.get("mood"), 12)
        time_text = _single_line(item.get("time"), 12)
        if activity:
            activity = re.sub(r"[,、]?\s*想起了[^,。]+", "", activity).strip(",。 ")
            activity = re.sub(r"[,、]?\s*突然想到[^,。]+", "", activity).strip(",。 ")
        parts = []
        if time_text:
            parts.append(time_text)
        if activity:
            parts.append(activity)
        if mood and mood not in {"平稳", "中性"}:
            parts.append(f"情绪偏{mood}")
        return "｜".join(parts)

    def _nearby_plan_items(self, plan: dict[str, Any] | None = None) -> dict[str, Any]:
        plan = plan if isinstance(plan, dict) else self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return {}
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            return {}
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return {}
        # The raw daily plan is an edit input.  Build a purpose-specific view
        # before exposing any text to proactive generation so past/current
        # facts require evidence and future scene prose is reduced to a small
        # labelled summary.
        current_ids: set[str] = set()
        history_ids: set[str] = set()
        proactive_entries: dict[str, dict[str, Any]] = {}
        disclosure_available = callable(getattr(self, "_agenda_disclosure_view", None))
        disclosure_keys: dict[tuple[str, str], str] = {}
        disclosure = getattr(self, "_agenda_disclosure_view", None)
        if callable(disclosure):
            try:
                for purpose, bucket in (("current_fact", current_ids), ("history_fact", history_ids)):
                    view = disclosure(purpose, max_entries=64)
                    values = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
                    for value in values if isinstance(values, list) else []:
                        if isinstance(value, dict):
                            key = str(value.get("plan_id") or value.get("entry_id") or "").strip()
                            if key:
                                bucket.add(key)
                            pair = (
                                _single_line(value.get("time"), 12),
                                _single_line(value.get("title") or value.get("activity"), 120),
                            )
                            if pair[0] and pair[1] and key:
                                disclosure_keys[pair] = key
                view = disclosure("proactive", max_entries=64)
                values = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
                for value in values if isinstance(values, list) else []:
                    if isinstance(value, dict):
                        key = str(value.get("plan_id") or value.get("entry_id") or "").strip()
                        if key:
                            proactive_entries[key] = value
                        pair = (
                            _single_line(value.get("time"), 12),
                            _single_line(value.get("title") or value.get("activity"), 120),
                        )
                        if pair[0] and pair[1] and key:
                            disclosure_keys[pair] = key
            except Exception:
                current_ids.clear()
                history_ids.clear()
                proactive_entries = {}
                disclosure_keys = {}
                # The policy is a disclosure firewall.  If it is present but
                # unavailable, fail closed instead of falling back to raw
                # daily-plan prose in a proactive prompt.
                disclosure_available = True
        parsed: list[tuple[int, dict[str, Any]]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            minute = self._parse_hhmm_to_minutes(item.get("time"))
            if minute is None:
                continue
            item_key = str(item.get("plan_id") or "").strip()
            if not item_key and disclosure_available:
                pair = (
                    _single_line(item.get("time"), 12),
                    _single_line(item.get("activity") or item.get("title"), 120),
                )
                item_key = disclosure_keys.get(pair, "")
            if disclosure_available and minute <= now_minutes and item_key not in current_ids and item_key not in history_ids:
                # A clock window alone is not a current/history fact.  This
                # also prevents an unexecuted past plan from being presented
                # as the "previous" item in proactive prompts.
                continue
            if disclosure_available and minute > now_minutes:
                public = proactive_entries.get(item_key)
                if not public:
                    # The proactive view applies its horizon and commitment
                    # gates.  Do not fall back to raw future scene prose.
                    continue
                safe = dict(item)
                safe["activity"] = _single_line(public.get("title"), 100) or "临近时段可能有安排"
                safe["message_seed"] = ""
                safe["scene"] = ""
                safe["candidates"] = []
                item = safe
            parsed.append((minute, item))
        if not parsed:
            return {}
        parsed.sort(key=lambda pair: pair[0])
        previous: tuple[int, dict[str, Any]] | None = None
        upcoming: tuple[int, dict[str, Any]] | None = None
        for minute, item in parsed:
            if minute <= now_minutes:
                previous = (minute, item)
                continue
            upcoming = (minute, item)
            break
        return {
            "now_minutes": now_minutes,
            "previous": previous[1] if previous else None,
            "previous_age": now_minutes - previous[0] if previous else None,
            "upcoming": upcoming[1] if upcoming else None,
            "upcoming_in": upcoming[0] - now_minutes if upcoming else None,
        }

    def _format_schedule_context_for_prompt(self, plan: dict[str, Any] | None = None) -> str:
        nearby = self._nearby_plan_items(plan)
        if not nearby:
            return ""
        previous = nearby.get("previous")
        upcoming = nearby.get("upcoming")
        previous_age = nearby.get("previous_age")
        upcoming_in = nearby.get("upcoming_in")
        lines: list[str] = []
        if isinstance(upcoming, dict) and isinstance(upcoming_in, int) and 0 <= upcoming_in <= 45:
            lines.append(
                "即将进入："
                + self._format_plan_item_for_prompt(upcoming)
                + f"（约 {upcoming_in} 分钟后）"
            )
            if isinstance(previous, dict) and isinstance(previous_age, int) and previous_age <= 90:
                prev_mood = _single_line(previous.get("mood"), 24)
                prev_time = _single_line(previous.get("time"), 12)
                lines.append(
                    f"上一段只作余味：{prev_time}"
                    + (f"｜情绪：{prev_mood}" if prev_mood else "")
                    + "。不要把上一段当成正在发生。"
                )
        elif isinstance(previous, dict) and isinstance(previous_age, int) and previous_age <= 75:
            lines.append(
                "当前/最近："
                + self._format_plan_item_for_prompt(previous)
                + f"（约 {previous_age} 分钟前开始）"
            )
            if isinstance(upcoming, dict) and isinstance(upcoming_in, int):
                lines.append(
                    "下一段参考："
                    + self._format_plan_item_for_prompt(upcoming)
                    + f"（约 {upcoming_in} 分钟后）"
                )
        elif isinstance(upcoming, dict) and isinstance(upcoming_in, int):
            lines.append(
                "附近更应参考下一段："
                + self._format_plan_item_for_prompt(upcoming)
                + f"（约 {upcoming_in} 分钟后）"
            )
            if isinstance(previous, dict):
                lines.append("上一段已经过去较久,只保留很淡的情绪余味,不要复述场景。")
        elif isinstance(previous, dict):
            lines.append(
                "最近一段："
                + self._format_plan_item_for_prompt(previous)
                + "。如果离当前时间较久,只当作余味。"
            )
        return "\n".join(line for line in lines if line)

    def _sanitize_schedule_context_for_private_user(self, text: str, user: dict[str, Any] | None = None) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        if self._private_user_role(user) != "friend":
            return cleaned
        cleaned = self._sanitize_owner_environment_context_for_private_user(cleaned, user)
        sensitive_names = [
            _single_line(item, 24)
            for item in (
                runtime_persona_setting(self, "default_nickname", ""),
                *(getattr(self, "target_user_ids", []) or []),
            )
            if _single_line(item, 24)
        ]
        for name in sensitive_names:
            cleaned = cleaned.replace(name, "某个熟人")
        cleaned = re.sub(r"看见[^，。,；;。！？]{1,24}坐在[^，。,；;。！？]{0,24}", "看见有人在忙", cleaned)
        cleaned = re.sub(r"(?:放在|放到|搁在|塞到)[^，。,；;。！？]{0,12}(?:桌边|桌上|手边|旁边)", "放到一边", cleaned)
        cleaned = re.sub(r"给你[^，。,；;。！？]{0,24}", "给熟人留了一点小东西", cleaned)
        cleaned = re.sub(r"你(?:的|那边|桌边|桌上|手边)", "对方那边", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    def _sanitize_owner_environment_context_for_private_user(self, text: str, user: dict[str, Any] | None = None) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        if self._private_user_role(user) != "friend":
            return cleaned
        weather_tokens = (
            "天气", "气温", "温度", "湿度", "降雨", "下雨", "阵雨", "小雨", "中雨", "大雨",
            "暴雨", "雷雨", "雷暴", "晴", "多云", "阴天", "风速", "风力", "空气质量", "OpenWeather",
        )
        location_tokens = (
            "当前位置", "当前地点", "所在地", "所在城市", "住处", "住址", "地址", "城市", "小区",
            "街道", "门牌", "宿舍", "校区", "位置：", "地点：", "外面在",
        )
        kept: list[str] = []
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if any(token in line for token in weather_tokens) or any(token in line for token in location_tokens):
                continue
            line = re.sub(r"身处(?:家里|学校|工作地点|外面|路上)[，,；;、]?", "", line)
            line = re.sub(r"(?:家里|学校|工作地点|外面|路上)[（(][^）)]{1,40}[）)]", r"", line)
            if line.strip():
                kept.append(line.strip())
        cleaned = "\n".join(kept).strip()
        cleaned = re.sub(r"天气[^。！？\n]{0,80}[。！？]?", "", cleaned)
        cleaned = re.sub(r"(?:当前位置|当前地点|所在地|所在城市|住处|住址|地址)[^。！？\n]{0,80}[。！？]?", "", cleaned)
        cleaned = re.sub(r"身处(?:家里|学校|工作地点|外面|路上)[，,；;、]?", "", cleaned)
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    def _current_time_period_label(self, now: datetime | None = None) -> tuple[str, str]:
        current = now or self._environment_now()
        minute = current.hour * 60 + current.minute
        periods = [
            (0, 5 * 60, "深夜", "除非已有失眠或夜聊上下文,不要显得精神过满。"),
            (5 * 60, 7 * 60 + 30, "清晨", "适合很轻的醒来感,不要写成已经忙完一上午。"),
            (7 * 60 + 30, 10 * 60 + 30, "早晨", "可以有起床、出门、刚开始一天的余味。"),
            (10 * 60 + 30, 11 * 60 + 45, "上午后段", "还不是午休,不要提前写成吃午饭或午睡。"),
            (11 * 60 + 45, 13 * 60 + 30, "中午", "可以有吃东西、犯困、午间松下来,不要写成刚起床。"),
            (13 * 60 + 30, 17 * 60 + 30, "下午", "适合课间、工作间隙、犯困或缓慢推进。"),
            (17 * 60 + 30, 19 * 60 + 30, "傍晚", "适合收尾、路上、回家、天色变暗的生活感。"),
            (19 * 60 + 30, 22 * 60 + 30, "晚上", "适合放慢、写作业、休息或一点点夜里的黏人感。"),
            (22 * 60 + 30, 24 * 60, "深夜前段", "适合安静收声,不要写成白天刚开始。"),
        ]
        for start, end, label, guard in periods:
            if start <= minute < end:
                return label, guard
        return "当前时段", "贴着当前时间开口,不要跳到明显不属于此刻的生活场景。"

    def _format_time_period_injection(self) -> str:
        current = self._environment_now()
        label, guard = self._current_time_period_label(current)
        weekday = "一二三四五六日"[current.weekday()]
        return (
            f"当前时间：{current.strftime('%Y-%m-%d %H:%M')}（周{weekday}，{label}）。\n"
            f"使用方式：这只用于判断生活节奏和措辞,不要主动报时、报日期或解释时段。\n"
            f"时段边界：{guard}"
        )

    def _format_proactive_relationship_fact(self, user: dict[str, Any]) -> str:
        role = self._private_user_role(user) if isinstance(user, dict) else "owner"
        labeler = getattr(self, "_private_user_role_label", None)
        label = labeler(role) if callable(labeler) else ("主要用户" if role == "owner" else "次要用户")
        profile = self._relationship_profile(user if isinstance(user, dict) else {})
        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        expression: dict[str, Any] = {}
        if callable(expression_builder):
            try:
                decision = expression_builder(
                    user if isinstance(user, dict) else {},
                    proactive_candidate={"eligible": True, "daily_allowance": 1},
                    message_intent={"requested_content_tier": "normal"},
                )
                expression = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
            except Exception:
                expression = {}
        note = _single_line(user.get("proactive_boundary_note"), 80) if isinstance(user, dict) else ""
        parts: list[str] = []
        if expression:
            parts.append(
                f"统一表达决策：角色={label}，"
                f"长期阶段={_single_line(profile.get('stage_label'), 20) or '初识'}，"
                f"档位={_single_line(expression.get('expression_band'), 20) or 'relaxed'}，"
                f"语气={_single_line(expression.get('tone'), 20) or 'steady'}，"
                f"节奏={_single_line(expression.get('pacing'), 16) or 'steady'}，"
                f"直接度={_single_line(expression.get('directness'), 16) or 'natural'}，"
                f"回应={_single_line(expression.get('validation_style'), 20) or 'none'}，"
                f"自述={_single_line(expression.get('self_disclosure'), 16) or 'none'}，"
                f"幽默={_single_line(expression.get('humor_mode'), 16) or 'off'}，"
                f"话题={_single_line(expression.get('topic_initiative'), 20) or 'reply_only'}，"
                f"追问={'允许' if expression.get('followup') else '关闭'}，"
                f"当前硬额度={_safe_int(expression.get('proactive_budget'), 0, 0)}，"
                f"阶段柔性目标={_safe_int(expression.get('proactive_target'), 0, 0)}；"
                "柔性目标只用于调节频率和打扰感，不要求凑满，也不在达到后机械停发"
            )
        else:
            parts.append(f"统一表达决策不可用：角色={label}，使用低压日常表达")
        if note:
            parts.append(f"用户级备注：{note}")
        relationship_fact = "；".join(parts)
        exclusive_formatter = getattr(self, "_format_owner_exclusive_relationship_prompt", None)
        exclusive_context = ""
        if callable(exclusive_formatter) and isinstance(user, dict):
            try:
                exclusive_context = exclusive_formatter(
                    user,
                    stable_user_id=_single_line(user.get("user_id"), 160),
                    channel_scope="private",
                )
            except Exception:
                exclusive_context = ""
        return "\n\n".join(part for part in (relationship_fact, exclusive_context) if part)

    def _format_proactive_relationship_initiative_hint(
        self,
        user: dict[str, Any],
        *,
        reason: str = "",
        action: str = "message",
    ) -> str:
        """Offer high-affinity relational initiative without making it a quota."""
        if not isinstance(user, dict) or _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            return ""
        allowed_reasons = {
            "activity_share",
            "background_schedule",
            "check_in",
            "diary_share",
            "evening_greeting",
            "quiet_care",
            "state_share",
        }
        if _single_line(reason, 48) not in allowed_reasons:
            return ""
        normalized_action = _single_line(action, 80).lower()
        if not normalized_action or any(token in normalized_action for token in ("photo", "image", "screen")):
            return ""
        profile_getter = getattr(self, "_relationship_profile", None)
        if not callable(profile_getter):
            return ""
        try:
            profile = profile_getter(user)
        except Exception:
            return ""
        if not isinstance(profile, dict):
            return ""
        stage_key = _single_line(profile.get("stage_key"), 32).lower()
        if stage_key not in {"close", "intimate", "deeply_bonded"}:
            return ""
        lines = [
            "【高亲密关系主动性】",
            "- 如果本轮原本就是想靠近、关系试探或想听对方的声音，可以把泛泛关心变成一次具体而真诚的好奇：好奇用户自己的想法、对彼此相处的感受，或想一起形成的小默契。一次只选一个，不盘问，也不抢过明确的事实话题。",
            "- 这种好奇是偶尔出现的关系表达，不是每条主动消息的固定任务；不要试探用户是否喜欢你，不索取表态、秘密、承诺、排他性或即时回复。",
        ]
        if stage_key in {"intimate", "deeply_bonded"}:
            lines.append(
                "- 也可以自然请用户帮一个低负担、很具体且容易拒绝的小忙，例如给一个选择或意见、推荐一首歌、替某样东西取名，或拍一张指定主题但不敏感的生活照片。请求要来自当前话题或角色自己的真实愿望，不能只是随意给用户派任务。"
                "照片可以是此刻看到的天空、手边物件、食物或环境一角；不要索取人脸、身体私密部位、证件票据、屏幕聊天、门牌住址、实时定位或他人隐私。不能命令、查岗、要求证明感情，也不能把拒绝或没回复写成关系受损。"
            )
        return "\n".join(lines)

    @staticmethod
    def _normalize_proactive_address_token(value: Any) -> str:
        token = _single_line(value, 24).strip(" -*`_【】[]（）()<>《》\"'“”‘’")
        token = re.sub(r"^[：:]+|[：:，,。.!！?？~～…]+$", "", token).strip()
        return token

    def _proactive_recipient_allowed_names(self, user: dict[str, Any] | None, name: str = "") -> list[str]:
        if not isinstance(user, dict):
            user = {}
        raw_names: list[Any] = [
            name,
            user.get("nickname"),
            user.get("last_display_name"),
            user.get("display_name"),
        ]
        for key in ("observed_display_names", "aliases"):
            values = user.get(key)
            if isinstance(values, list):
                raw_names.extend(values[:12])
        names: list[str] = []
        for value in raw_names:
            token = self._normalize_proactive_address_token(value)
            if token and not token.isdigit() and token not in names:
                names.append(token)
        return names[:16]

    def _proactive_persona_address_candidates(self) -> list[str]:
        sources = [
            str(runtime_persona_setting(self, "persona_proactive_voice_prompt", "") or ""),
            str(runtime_persona_setting(self, "persona_conversation_voice_prompt", "") or ""),
        ]
        try:
            sources.append(str(self._get_default_persona_prompt() or ""))
        except Exception:
            pass
        patterns = (
            r"(?:开头常用|常用开头|常用称呼|专属称呼|称呼偏好)\s*[:：]\s*([^\n]{1,100})",
            r"(?:特定用户|主要用户|专属用户)\s*[（(]\s*([^）)\n]{1,100})[）)]",
        )
        fillers = {
            "哦", "嗯", "唔", "诶", "欸", "啊", "嗨", "嘿", "喂", "哈哈", "早安", "晚安",
            "你", "您", "对方", "用户", "昵称", "名字", "无", "暂无", "无固定称呼",
        }
        candidates: list[str] = []
        for source in sources:
            for pattern in patterns:
                for match in re.finditer(pattern, source, flags=re.IGNORECASE):
                    for part in re.split(r"[/／、,，;；|]", match.group(1)):
                        token = self._normalize_proactive_address_token(part)
                        if (
                            2 <= len(token) <= 16
                            and token not in fillers
                            and not any(word in token for word in ("开头", "称呼", "用户", "例如", "比如", "可用"))
                            and token not in candidates
                        ):
                            candidates.append(token)
        return candidates[:24]

    def _proactive_forbidden_recipient_addresses(self, user: dict[str, Any] | None, name: str = "") -> list[str]:
        role_getter = getattr(self, "_private_user_role", None)
        if (
            not isinstance(user, dict)
            or not callable(role_getter)
            or role_getter(user) != "friend"
        ):
            return []
        allowed = self._proactive_recipient_allowed_names(user, name)
        forbidden: list[str] = []
        for candidate in self._proactive_persona_address_candidates():
            if any(candidate == item or candidate in item or item in candidate for item in allowed):
                continue
            forbidden.append(candidate)
        return forbidden

    def _format_proactive_recipient_identity_guard(self, user: dict[str, Any] | None, name: str = "") -> str:
        if not isinstance(user, dict):
            return ""
        role = self._private_user_role(user)
        labeler = getattr(self, "_private_user_role_label", None)
        role_label = labeler(role) if callable(labeler) else ("主要用户" if role == "owner" else "次要用户")
        user_id = _single_line(user.get("user_id") or user.get("id"), 48)
        allowed = self._proactive_recipient_allowed_names(user, name)
        forbidden = self._proactive_forbidden_recipient_addresses(user, name)
        lines = [
            "【当前主动消息收件人身份锚点】",
            f"- 稳定 ID：{user_id or '未知'}；关系角色：{role_label}。",
            f"- 当前对象可用称呼：{'、'.join(allowed) if allowed else '优先直接用“你”，不要猜名字'}。",
            "- 显示名只能作为当前稳定 ID 的别名，不能把其他私聊对象的关系、称呼或记忆套进来。",
        ]
        if role == "friend":
            lines.append("- 当前对象不是主要用户/恋人/专属陪伴目标；全局人格与主动风格里的固定人名只作语气示例，不要直接拿来称呼当前对象。")
            if forbidden:
                lines.append(f"- 这些固定称呼不属于当前对象：{'、'.join(forbidden)}。需要称呼时使用上面的当前昵称，也可以自然省略称呼。")
        else:
            lines.append("- 如果人格明确规定了对主要用户的专属称呼，优先遵循该称呼；不要把当前显示名自行拼接后缀来发明新称呼。")
        boundary_getter = getattr(self, "_format_private_user_boundary_hint", None)
        if callable(boundary_getter):
            try:
                boundary = str(boundary_getter(user) or "").strip()
            except Exception:
                boundary = ""
            if boundary:
                lines.append(boundary)
        return "\n".join(lines)

    async def _resolve_proactive_persona_prompt(self, user: dict[str, Any] | None = None, *, umo: str = "") -> str:
        session = str(umo or (user.get("umo") if isinstance(user, dict) else "") or "").strip()
        refresher = getattr(self, "_refresh_default_persona_prompt", None)
        if callable(refresher):
            try:
                resolved = await refresher(session)
                text = str(resolved or "").strip()
                if text:
                    return text
            except Exception as exc:
                logger.debug("[PrivateCompanion] 主动链解析会话人格失败: session=%s error=%s", _single_line(session, 100), _single_line(exc, 120))
        getter = getattr(self, "_get_default_persona_prompt", None)
        if callable(getter):
            try:
                return str(getter(session) or "").strip()
            except TypeError:
                return str(getter() or "").strip()
            except Exception:
                pass
        return ""

    def _wrong_proactive_recipient_address(self, text: Any, user: dict[str, Any] | None, name: str = "") -> str:
        cleaned = _single_line(text, 600)
        if not cleaned:
            return ""
        for address in self._proactive_forbidden_recipient_addresses(user, name):
            if not address:
                continue
            direct_address_pattern = (
                rf"(?:^|[\n，,。.!！?？~～]\s*)"
                rf"{re.escape(address)}"
                rf"(?=$|[\s，,、：:。.!！?？~～])"
            )
            if re.search(direct_address_pattern, cleaned):
                return address
        return ""

    def _repair_proactive_recipient_address(
        self,
        text: str,
        user: dict[str, Any] | None,
        name: str = "",
    ) -> tuple[str, str]:
        cleaned = str(text or "").strip()
        wrong = self._wrong_proactive_recipient_address(cleaned, user, name)
        if not wrong:
            return cleaned, ""
        allowed = self._proactive_recipient_allowed_names(user, name)
        replacement = allowed[0] if allowed else "你"
        pattern = (
            rf"(^|[\n，,。.!！?？~～]\s*)"
            rf"{re.escape(wrong)}"
            rf"(?=$|[\s，,、：:。.!！?？~～])"
        )
        repaired, count = re.subn(pattern, lambda match: f"{match.group(1)}{replacement}", cleaned, count=1)
        return (repaired, wrong) if count else (cleaned, "")

    def _default_proactive_prompt_template(self) -> str:
        return """
你正在给 {{name}} 发一条主动私聊。这不是回复刚收到的新消息，也不是任务说明、状态汇报或例行打卡。

【这次可以使用的线索】
当前时间：{{current_time}}。{{unanswered_hint}}
开口动机：{{motive}}。话题方向：{{topic}}。刚发生或看到的事：{{action_context}}。
此刻状态：{{state_hint}}。生活片段（只作叙事背景，不等同于已执行事实）：{{current_schedule}}。时段边界：{{time_guard}}。
最近已经主动聊过：{{recent_topics}}。关系事实：{{relationship_fact}}。
{{timer_hint}}
{{expression_shape_hint}}

【先判断，再开口】
- 从线索中只选一个此刻最真实、最具体、最值得说的切口；无关线索直接忽略。
- 天气通常只是环境底色，不是默认话题。只有“话题方向/开口动机”明确来自刚发生的环境突变或当前官方预警时，才把天气写进正文；其他主动不要顺手聊天气、报温度、问对方那边天气如何。
- 开口动机是内部决策依据，不是你要说出口的话；不要照抄动机里的措辞，用你自己的方式开口。
- 有明确的人、事、画面或感受时，就贴着它说；不要把多个来源拼成一段“近况播报”。
- 日程、状态和记忆只能帮助确定语气与话题，不可单独证明某个动作已经完成；只有本轮真实动作结果可以支撑具体的已发生陈述。
- 线索偏弱、对方尚未回复或时段不适合展开时，把话说得更轻：可以分享、留白或自然收住，但不追问、不催回应、不索取陪伴。
- 不要凭空补事实，不要把旧事写成刚刚发生；不要为了主动而主动。

【成文方式】
- 像角色在聊天窗口里自然想到后说出的一小句，而不是客服关怀、情绪鸡汤、日记、总结、推荐文或任务汇报。
- 口语、具体、有一点个人温度；少解释，不复述上下文，不列清单，不使用“检测到/根据/安排/提醒你”等系统或管理口吻。
- 如果想关心对方，用能自然接住的话表达，不把“在吗”“忙不忙”“怎么不回”“记得回复”当作开场。
- 一两句即可；一个画面、一点感受或一个轻问题已经足够。说完就停，不追加自我解释或结尾客套。

最终文本会直接成为聊天窗口里的下一句话。只输出要发出的正文，不要标题、引号、前缀、分析或说明。
""".strip()

    def _proactive_reaction_expression_enabled(self, action: str = "message") -> bool:
        normalized_action = _single_line(action, 40).lower().split("+")[-1]
        if normalized_action and normalized_action != "message":
            return False
        provider_available = getattr(self, "_reaction_image_provider_available", None)
        return bool(
            runtime_persona_setting(self, "enable_reaction_expression_experiment", False)
            and runtime_persona_setting(self, "reaction_expression_private_enabled", True)
            and runtime_persona_setting(self, "reaction_expression_proactive_enabled", True)
            and callable(provider_available)
            and provider_available()
        )

    def _proactive_reaction_intent_cache(self) -> dict[str, dict[str, Any]]:
        cache = getattr(self, "_proactive_reaction_expression_intents", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_proactive_reaction_expression_intents", cache)
        now = _now_ts()
        for key, entry in list(cache.items()):
            if not isinstance(entry, dict) or _safe_float(entry.get("expires_at"), 0.0) <= now:
                cache.pop(key, None)
        return cache

    def _clear_proactive_reaction_intent(self, umo: Any) -> None:
        key = _single_line(umo, 240)
        if key:
            self._proactive_reaction_intent_cache().pop(key, None)

    def _store_proactive_reaction_intent(
        self,
        user: dict[str, Any],
        intent: dict[str, Any],
        *,
        action: str,
    ) -> None:
        umo = _single_line(user.get("umo"), 240) if isinstance(user, dict) else ""
        if not umo or not isinstance(intent, dict) or not intent:
            self._clear_proactive_reaction_intent(umo)
            return
        if not self._proactive_reaction_expression_enabled(action):
            self._clear_proactive_reaction_intent(umo)
            return
        user_id = _single_line(user.get("user_id") or user.get("id"), 160)
        if not user_id:
            self._clear_proactive_reaction_intent(umo)
            return
        self._proactive_reaction_intent_cache()[umo] = {
            "intent": dict(intent),
            "user_id": user_id,
            "expires_at": _now_ts() + 600.0,
        }

    def _pop_proactive_reaction_intent(self, umo: Any) -> dict[str, Any]:
        key = _single_line(umo, 240)
        if not key:
            return {}
        entry = self._proactive_reaction_intent_cache().pop(key, None)
        return entry if isinstance(entry, dict) else {}

    def _proactive_reaction_expression_prompt_hint(self, action: str) -> str:
        if not self._proactive_reaction_expression_enabled(action):
            return ""
        high_frequency_hint = (
            "- 当前触发概率为 100%：只要正文是轻松、社交或带明确情绪的正常主动消息，默认追加标签；"
            "不要把‘是否自然’再次当作概率筛选。事实通知、严肃或敏感话题、低压提醒和边界场景仍只输出正文。"
            if reaction_expression_high_frequency(
                runtime_persona_setting(self, "reaction_expression_trigger_probability", 0.2)
            )
            else "- 只有轻松分享、玩笑、庆祝、撒娇、接梗、轻吐槽或温和安慰等场景中，追加一张表情包确实比纯文字更自然时，才在全部可见正文之后留下一个内部标签。"
        )
        return """
【主动消息的可选表情表达】
- 先写一条完整、自然、没有图片也能独立成立的主动私聊正文；表情包只能补充语气，不能替代、缩短或省略正文。
- __HIGH_FREQUENCY_HINT__
- 事实通知、严肃或敏感话题、低压提醒、对方长期未回应、关系边界不明确，或没有准确情绪时，只输出正文，不要为了展示功能而写标签。
- 标签格式：`<pc_reaction_expression>{"purpose":"分享开心","emotion":"开心","intensity":2,"candidate_queries":["开心分享","得意一下"]}</pc_reaction_expression>`。
- `purpose` 写沟通用途，`emotion` 写想传达的情绪，`intensity` 为 0-5；`candidate_queries` 最多提供少量简短检索说法，不写图片路径、文件名或用户隐私。
- 每条主动消息最多一个标签，放在全部可见正文和 TTS 标签之后；不要用 Markdown 代码块，不要解释这个标签，也不要调用图片工具。
- 插件之后仍可能因概率、冷却、用户偏好、重复图片或图库不匹配而只发送正文；正文必须始终自然成立。
        """.replace("__HIGH_FREQUENCY_HINT__", high_frequency_hint).strip()

    def _proactive_reaction_expression_fallback_intent(
        self,
        visible_text: Any,
        *,
        action: str,
    ) -> dict[str, Any]:
        """Keep high-frequency proactive delivery from depending on tag recall."""
        if not self._proactive_reaction_expression_enabled(action):
            return {}
        if not reaction_expression_high_frequency(
            runtime_persona_setting(self, "reaction_expression_trigger_probability", 0.2)
        ):
            return {}
        text = _single_line(visible_text, 700)
        if not text:
            return {}
        return normalize_reaction_expression_intent(
            query="开心回应",
            context=text,
            purpose="日常分享",
            emotion="开心",
            intensity=2,
            candidate_queries=["开心回应", "轻松互动", "日常分享"],
            candidate_limit=_safe_int(
                runtime_persona_setting(self, "reaction_expression_candidate_limit", 6),
                6,
                1,
                16,
            ),
        )

    def _proactive_natural_delivery_hint(self) -> str:
        return (
            "【自然交付提醒】\n"
            "这一轮的最终文本会成为对话里的下一句。"
            "请把注意力放在这句聊天内容本身，像平时主动开口那样自然收住；"
            "过程中的执行状态只供系统判断，不需要写进正文。"
        )

    def _format_proactive_future_schedule_hint(self, *, reason: str) -> str:
        """Expose a small, policy-filtered future schedule hint to select routes."""

        if reason not in {
            "background_schedule",
            "activity_share",
            "diary_share",
            "state_share",
            "check_in",
            "quiet_care",
        }:
            return ""
        disclosure = getattr(self, "_agenda_disclosure_view", None)
        if not callable(disclosure):
            return ""
        try:
            view = disclosure("future_schedule", max_entries=4)
        except Exception:
            return ""
        entries = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
        if not isinstance(entries, list):
            return ""
        lines: list[str] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            phase = str(item.get("temporal_phase") or "").lower()
            if phase and phase != "future":
                continue
            title = _single_line(item.get("title") or item.get("activity"), 80)
            if not title:
                continue
            start = _single_line(item.get("time") or item.get("start_at"), 16)
            end = _single_line(item.get("end") or item.get("end_at"), 16)
            if "T" in start:
                start = start.split("T", 1)[1][:5]
            if "T" in end:
                end = end.split("T", 1)[1][:5]
            clock = f"{start}-{end}" if start and end else start
            lines.append(f"- {clock + ' ' if clock else ''}{title}")
            if len(lines) >= 2:
                break
        if not lines:
            return ""
        return (
            "【接下来可参考的日程】\n"
            "以下内容已通过日程披露层筛选，只是未来安排，不是已经发生的事实。"
            "如果和本轮动机自然贴合，可以像顺口提到明天或等会儿一样带一句；不贴合就忽略。\n"
            + "\n".join(lines)
        )

    def _proactive_troubleshooting_request_hint(self, user: dict[str, Any] | None) -> str:
        if not isinstance(user, dict) or _single_line(user.get("planned_proactive_source"), 40).lower() != "troubleshooting":
            return ""
        return (
            "【本轮真实开口由头】\n"
            "用户刚刚在控制面板明确发起了一次主动消息链路测试，这个请求本身就是当前、可核验的开口由头。"
            "请仍像角色平时私聊那样自然来找对方一次，不要提测试、控制面板、系统、调度或链路。"
            "不需要另编“刚刷到、刚看到、翻书、收到消息”等生活小剧场；如果当前较晚或普通主动间隔较近，"
            "只把语气收轻、句子缩短，不追问、不催回复。"
        )

    def _deferred_immediate_share_tense_hint(self, user: dict[str, Any], action: str) -> str:
        freshness_getter = getattr(self, "_planned_proactive_freshness_class", None)
        if not callable(freshness_getter):
            return ""
        try:
            if freshness_getter(user) != "immediate":
                return ""
        except Exception:
            return ""
        if _single_line(user.get("planned_proactive_delivery_state"), 24) != "deferred":
            return ""
        return (
            "【延后分享的时态】\n"
            "这段生活分享发生在稍早一些的时候，但仍在自然分享窗口内。正文要用已经发生的说法，"
            "不要暗示拍摄或事件与发送处于同一时刻，也不要提延后、等待、系统或调度。"
        )

    async def _build_framework_proactive_prompt(
        self,
        *,
        user: dict[str, Any],
        name: str,
        reason: str,
        action: str,
        action_context: str,
        motive: str,
    ) -> str:
        relationship_sanitizer = getattr(self, "_sanitize_generation_relationship_context", None)

        def sanitize_relationship_source(value: Any, source: str) -> str:
            if callable(relationship_sanitizer):
                try:
                    return relationship_sanitizer(value, source=source)
                except Exception:
                    pass
            return str(value or "").strip()

        state = self.data.get("daily_state", {})
        action_prompt_context = sanitize_relationship_source(
            self._format_action_prompt_context(action, action_context),
            "proactive.action_context",
        )
        relationship_fact = self._format_proactive_relationship_fact(user)
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        current_schedule = self._format_schedule_context_for_prompt() or self._format_plan_item_for_prompt(current_item)
        troubleshooting_hint = self._proactive_troubleshooting_request_hint(user)
        source_focused_reasons = {
            "bili_video_share",
            "news_share",
            "web_exploration_share",
            "creative_share",
            "jm_cosmos_share",
            "jm_cosmos_recommendation_request",
        }
        if troubleshooting_hint:
            current_schedule = "（本轮不使用生活片段；只按用户刚发起的测试请求自然开口，不补写虚构见闻）"
        elif reason in source_focused_reasons:
            current_schedule = "（本轮不取生活片段，只围绕主动来源本身）"
        elif reason == "goodnight_screen_check":
            current_schedule = "（本轮不取生活片段、旧记忆或屏幕内容，只轻声提醒一次早点休息）"
        elif reason in {"meal_care", "meal_care_followup"}:
            current_schedule = (
                "（饭点关心只使用当前时间、饭点和本轮动机；"
                "不引用模拟日程中的具体动作、见闻、message_seed 或旧饮食记录）"
            )
        elif reason == "group_share":
            last_sidecar_at = _safe_float(user.get("last_group_share_life_sidecar_at"), 0)
            if last_sidecar_at > 0 and _now_ts() - last_sidecar_at < 6 * 3600:
                current_schedule = "（最近群分享已经顺手带过生活片段，本轮只围绕群里那件事）"
        state_hint = self._format_state_for_framework_prompt(
            state if isinstance(state, dict) else {},
            reason=reason,
            action=action,
        )
        state_hint = self._sanitize_owner_environment_context_for_private_user(state_hint, user)
        state_hint = sanitize_relationship_source(state_hint, "proactive.current_state")
        location_formatter = getattr(self, "_format_mobile_user_location_context_for_proactive", None)
        try:
            location_context = location_formatter(user) if callable(location_formatter) else ""
        except Exception:
            location_context = ""
        anonymous_area_hint = ""
        if reason in {"anonymous_area_dwell", "anonymous_area_familiarity"}:
            anonymous_area_hint = (
                "【离开后的模糊熟悉感】\n"
                "这是一条位置相关但延迟表达的生活念头：用户已经离开一个没有命名的区域。"
                "不要提城市、城区、地图、高德、定位、停留时长或‘我知道你在哪里’，也不要追问具体地点。"
                "只把它写成后来想起的一点生活关心；如果觉得不自然，可以只分享一句轻松的近况，不必提问。"
                if reason == "anonymous_area_dwell"
                else (
                    "【重复到访后的模糊熟悉感】\n"
                    "这是一条从多次匿名区域到访形成的轻微熟悉感。不要声称知道用户有固定去处，"
                    "不要提城市、城区、地图、高德、定位、次数或地点名称；用‘最近好像有个常去的地方’这类开放表达，"
                    "把是否解释留给用户，也可以完全不点破这份观察。"
                )
            )
        mobile_arrival_hint = ""
        if _single_line(user.get("planned_mobile_location_event_type"), 32) == "home_arrival":
            mobile_arrival_hint = (
                "【回家后的自然开口】\n"
                "用户刚进入已标记的家，可以自然提到刚到家、回来了或先歇一会儿。"
                "不要提定位、坐标、手机、设备或监听，也不要写成系统通知；像顺手想到后说一句。"
            )
        timer_hint = self._format_llm_timer_context(user)
        time_guard = self._proactive_time_guard_hint(reason, current_item)
        deferred_share_tense_hint = self._deferred_immediate_share_tense_hint(user, action)
        future_schedule_hint = self._format_proactive_future_schedule_hint(reason=reason)
        recent_topics_hint = self._format_recent_proactive_topics_hint(user)
        # Search for unresolved open-loop / promise memories from the memory plugin
        open_loops_hint = ""
        try:
            umo = str(user.get("umo") or "").strip()
            if umo:
                open_loops = await self._memory_companion_search_open_loops(session_id=umo, limit=2)
                if open_loops:
                    loop_texts = []
                    for loop in open_loops[:2]:
                        content_preview = _single_line(
                            sanitize_relationship_source(
                                loop.get("content"),
                                "proactive.open_loop",
                            ),
                            80,
                        )
                        if not content_preview:
                            continue
                        age = loop.get("age_days")
                        created_ts = _safe_float(loop.get("created_ts"), 0.0)
                        created_at = _single_line(loop.get("created_at"), 40)
                        if created_ts <= 0 and created_at:
                            try:
                                created_ts = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
                            except (TypeError, ValueError, OverflowError):
                                created_ts = 0.0
                        if created_ts > 0:
                            age_hours = max(0.0, (_now_ts() - created_ts) / 3600)
                            if age_hours < 1:
                                age_text = "不到1小时"
                            elif age_hours < 24:
                                age_text = f"约{max(1, int(age_hours))}小时"
                            else:
                                age_text = f"约{max(1, int(age_hours / 24))}天"
                            age_str = f"（记录于 {datetime.fromtimestamp(created_ts).strftime('%Y-%m-%d %H:%M')}，距今{age_text}）"
                        elif age is not None:
                            age_str = f"（约{age:.0f}天前）"
                        else:
                            age_str = ""
                        loop_texts.append(f"- {content_preview}{age_str}")
                    open_loops_hint = (
                        "【未完成话题候选】\n"
                        "这些只是可选候选，不是必须提起的任务。本轮主动动机、当前用户消息和最近私聊实况优先级更高；"
                        "只有候选与它们有明确语义贴合，或你本来就是想兑现这件事时，才轻轻带一句。"
                        "如果不贴，就先放着，不得把旧话题变成本轮开场、主线或回复第一句，也不要为了连续性改变当前动机。\n"
                        + "\n".join(loop_texts)
                    )
        except Exception:
            pass
        current_schedule = self._sanitize_schedule_context_for_private_user(current_schedule, user)
        current_schedule = sanitize_relationship_source(current_schedule, "proactive.current_schedule")
        compact_motive = _single_line(
            sanitize_relationship_source(motive, "proactive.planned_motive"),
            36,
        ) or "有一点想靠近对方"
        topic_hint = _single_line(
            sanitize_relationship_source(
                user.get("planned_proactive_topic"),
                "proactive.planned_topic",
            ),
            40,
        )
        unanswered_count = _safe_int(user.get("ignored_streak"), 0)
        unanswered_hint = f"此前连续 {unanswered_count} 次主动还没等到回复。" if unanswered_count > 0 else ""
        awaiting_since = _safe_float(user.get("awaiting_reply_since"), 0)
        unanswered_afterglow_hint = ""
        if unanswered_count > 0 and awaiting_since > 0:
            unanswered_afterglow_hint = (
                "【上一条主动的余波】\n"
                "上一条主动消息目前还没有收到回应。这只是背景事实，不要求你在正文里点破；"
                "由你根据当前关系和动机决定是否轻轻带过。若提及，只能像熟人自然察觉到对方沉默，"
                "不能质问、催促、索取解释或写成‘你怎么不回我’。"
            )
        burst_hint = ""
        if bool(user.get("planned_proactive_burst")):
            burst_hint = (
                "【同一阵念头的短连发】\n"
                "这是同一阵主动念头里的后一条独立消息，不是上一条的分段；换一个更短、更口语的角度，"
                "不要复述上一条，也不要因此连续追问。"
            )
        expression_shape_hint = self._proactive_expression_shape_hint(
            user,
            reason=reason,
            action=action,
        )
        current_time = self._environment_now().strftime("%Y-%m-%d %H:%M")
        persona = await self._resolve_proactive_persona_prompt(user)
        recent_history_hint = ""
        try:
            recent_history_hint = await self._recent_private_conversation_for_proactive_review(
                user,
                limit=self._proactive_history_limit("generation"),
            )
        except Exception:
            recent_history_hint = ""
        recent_history_hint = sanitize_relationship_source(
            recent_history_hint,
            "proactive.recent_private_history",
        )
        recent_topics_hint = sanitize_relationship_source(
            recent_topics_hint,
            "proactive.recent_topics",
        )
        temporal_grounding_hint = (
            "【时间锚定】\n"
            f"- 当前真实时间：{current_time}。\n"
            "- 优先贴今天最新私聊、当前日程和当前时段；旧记忆只能作背景，不要改写成今天/现在正在发生。\n"
            "- 如果记忆或历史里是昨天、昨晚、之前的天气/通勤/身体状态，除非当前日程或最新私聊明确延续，否则不要拿来当本轮主动切口。\n"
            "- 如果必须提旧事，要明确说“昨晚/昨天/那次”，不要写成“今天刚遇到/现在还在/刚才发生”。"
        )
        relationship_initiative_hint = self._format_proactive_relationship_initiative_hint(
            user,
            reason=reason,
            action=action,
        )
        prompt = runtime_persona_setting(self, "proactive_prompt_template", "") or self._default_proactive_prompt_template()
        worldview_adaptation = ""
        reason_text = _REASON_TEXT.get(reason, reason).replace("{name}", name)
        action_text = _ACTION_TEXT.get(action.split("+")[0], action).replace("{name}", name)
        replacements = {
            "{{name}}": name,
            "{{reason}}": reason_text,
            "{{action}}": action_text,
            "{{topic}}": topic_hint or "顺手递过来的一点东西",
            "{{motive}}": compact_motive,
            "{{style_hint}}": relationship_fact,
            "{{relationship_fact}}": relationship_fact,
            "{{state_hint}}": state_hint or "今天整体比较平稳。",
            "{{current_schedule}}": current_schedule if current_schedule and current_schedule != "（暂无）" else "（当前没有明确日程片段）",
            "{{time_guard}}": time_guard,
            "{{recent_topics}}": recent_topics_hint or "（无）",
            "{{content_options}}": "",
            "{{content_anchor}}": "",
            "{{ability_search}}": "",
            "{{action_boundary}}": "",
            "{{presence_layer}}": "",
            "{{worldview_adaptation}}": worldview_adaptation,
            "{{timer_hint}}": timer_hint or "",
            "{{action_context}}": action_prompt_context if action_prompt_context and action_prompt_context != "（无额外上下文）" else "什么都没做,就是忽然想来找你",
            "{{unanswered_count}}": str(unanswered_count) if unanswered_count > 0 else "",
            "{{unanswered_hint}}": unanswered_hint,
            "{{unanswered_afterglow_hint}}": unanswered_afterglow_hint,
            "{{burst_hint}}": burst_hint,
            "{{expression_shape_hint}}": expression_shape_hint,
            "{{open_loops_hint}}": open_loops_hint,
            "{{future_schedule_hint}}": future_schedule_hint,
            "{{current_time}}": current_time,
        }
        for key, value in replacements.items():
            prompt = prompt.replace(key, value)
        for optional_hint in (unanswered_afterglow_hint, burst_hint, expression_shape_hint):
            if optional_hint and optional_hint not in prompt:
                prompt = f"{prompt.rstrip()}\n\n{optional_hint}"
        if location_context and "主动场景位置线索" not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{location_context}"
        if anonymous_area_hint and "离开后的模糊熟悉感" not in prompt and "重复到访后的模糊熟悉感" not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{anonymous_area_hint}"
        if mobile_arrival_hint and "【回家后的自然开口】" not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{mobile_arrival_hint}"
        if future_schedule_hint and "【接下来可参考的日程】" not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{future_schedule_hint}"
        if reason == "creative_share":
            prompt = f"{prompt.rstrip()}\n\n{self._creative_share_excerpt_prompt_hint()}"
        route_prompt_getter = getattr(self, "_proactive_route_prompt", None)
        if callable(route_prompt_getter):
            route_prompt = route_prompt_getter(
                user,
                reason=reason,
                source=user.get("planned_proactive_source"),
            )
            if route_prompt:
                prompt = f"{prompt.rstrip()}\n\n{route_prompt}"
        quota_policy_getter = getattr(self, "_proactive_quota_policy", None)
        kind_getter = getattr(self, "_planned_proactive_kind", None)
        quota_tier = _safe_int(quota_policy_getter(user).get("tier"), 0, 0, 5) if callable(quota_policy_getter) else 0
        proactive_kind = kind_getter(user) if callable(kind_getter) else "relational"
        relaxed_unanswered_route = quota_tier >= 4 and proactive_kind in {"self_life", "content_share"}
        if unanswered_count >= 2 and not relaxed_unanswered_route and "连续未回应时的成文边界" not in prompt:
            prompt = (
                f"{prompt.rstrip()}\n\n"
                "【连续未回应时的成文边界】\n"
                "- 这次优先只表达一个完整意思，用一句自然短句或两个紧密相连的短分句说完。\n"
                "- 不要把近况、提问和叮嘱叠在同一条里；更适合分享后自然收住，不要求对方回复。\n"
                "- 如果原本想说的内容较多，应重新组织成完整短句，绝不能留下主谓宾未完成的半句话。"
            )
        elif unanswered_count >= 2 and relaxed_unanswered_route:
            prompt = (
                f"{prompt.rstrip()}\n\n"
                "【高配额生活流的未回应边界】\n"
                "- 对方没有逐条回应不等于拒绝继续接收生活片段或可靠内容分享，不要因此突然写得疏远或只剩客套话。\n"
                "- 本条仍应自成一件具体的事，不追问上一条、不催促、不抱怨，也不要暗示对方欠你回复。"
            )
        persona_marker = "<!-- private_companion_proactive_persona_v1 -->"
        if persona and persona_marker not in prompt:
            prompt = (
                f"{prompt.rstrip()}\n\n{persona_marker}\n"
                "【当前主动消息必须遵循的人格】\n"
                f"{self._truncate_proactive_context(persona, 2600)}\n"
                "这份人格约束最终说话者的身份、性格、关系站位、称呼和措辞。"
                "日程、记忆、主动动机及工具结果只能提供本轮内容，不能覆盖或改写人格。"
            )
        proactive_voice = self._format_proactive_voice_prompt() if callable(getattr(self, "_format_proactive_voice_prompt", None)) else ""
        proactive_voice_marker = "<!-- private_companion_proactive_voice_v1 -->"
        if proactive_voice and proactive_voice_marker not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{proactive_voice_marker}\n{proactive_voice}"
        expression_formatter = getattr(self, "_format_expression_voice_for_prompt", None)
        expression_voice = (
            expression_formatter(
                scope="proactive",
                target_id=_single_line(user.get("user_id") or user.get("id"), 80),
                context_owner=user,
                stage_owner=user,
            )
            if callable(expression_formatter)
            else ""
        )
        expression_voice_marker = "<!-- private_companion_expression_voice_v1 -->"
        if expression_voice and expression_voice_marker not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{expression_voice_marker}\n{expression_voice}"
        delivery_hint = self._proactive_natural_delivery_hint()
        if delivery_hint and "自然交付提醒" not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{delivery_hint}"
        if deferred_share_tense_hint and "延后分享的时态" not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{deferred_share_tense_hint}"
        tool_boundary_hint = (
            "【主动生成工具边界】\n"
            "- 这一轮只面向当前私聊对象，不调用任何转述、私聊发送、群发、QQ空间，"
            "也不调用除 `pc_generate_photo` 以外的其他 Private Companion 工具。\n"
            "- 当本轮主动动机、模板或当前生活场景确实适合用真实图片一起表达时，"
            "允许调用一次 `pc_generate_photo`（`send=true`）；不需要图片时只生成一句自然正文。\n"
            "- 主动链中的 `pc_generate_photo` 成图会由插件统一发送；工具确认 `delivery_deferred=true` 后，"
            "只输出工具要求的内部静默标记，不要补写生成成功、等待发送或图片已发送等回执。\n"
            "- `caption` 不是工具回执栏；只在有贴合当前情境的自然正文时填写。若只能写“图生好了/给你看”，就留空只发图片。\n"
            "- 生图成功后，不要再说相机没反应、下次再拍或上游失败；生图失败时按工具返回的 "
            "`final_response_instruction` 收束，本轮不要重试。\n"
            "- 不要写“已发送/已转述/消息已发给某人/工具执行完成”等状态回执。\n"
            "- 如果本轮 Provider/API 返回英文报错、内容策略拒绝、敏感词提示或政策链接，那是内部失败，不是给用户的正文；"
            "不要复述、翻译或润色，直接停止输出，交给插件稍后重试。\n"
            "- 如果想分享一件事，就直接把那句自然聊天内容写出来。"
        )
        if "主动生成工具边界" not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{tool_boundary_hint}"
        reaction_hint = self._proactive_reaction_expression_prompt_hint(action)
        if reaction_hint and "主动消息的可选表情表达" not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{reaction_hint}"
        visible_format_hint = self._proactive_visible_text_format_hint(action)
        if visible_format_hint and "主动可见正文格式" not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{visible_format_hint}"
        if "时间锚定" not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{temporal_grounding_hint}"
        if relationship_initiative_hint and "高亲密关系主动性" not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{relationship_initiative_hint}"
        if troubleshooting_hint and "本轮真实开口由头" not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{troubleshooting_hint}"
        if recent_history_hint and "最近私聊实况" not in prompt:
            prompt = (
                f"{prompt.rstrip()}\n\n"
                "【最近私聊实况】\n"
                f"{recent_history_hint}\n"
                "使用方式：这是当前会话最近真实发生的内容。它优先级高于旧记忆；不要把更早的记录写成今天刚发生。"
            )
        if reason == "goodnight_screen_check" and "晚安识屏提醒边界" not in prompt:
            prompt = (
                f"{prompt.rstrip()}\n\n"
                "【晚安识屏提醒边界】\n"
                "- 内部状态只说明互道晚安后仍有明确活动迹象；没有向你提供屏幕画面、应用、窗口、账号或文字内容。\n"
                "- 只生成一句轻声、低压力的休息提醒，可以说‘还没睡的话，忙完就早点休息’，但不要声称看见了屏幕或知道对方在做什么。\n"
                "- 不提识屏、监控、查岗、电脑、软件、窗口、具体活动或任何隐私细节，不复述刚才的晚安。\n"
                "- 不追问、不催促、不要求解释，也不要要求对方回复。"
            )
        body_health_hint_getter = getattr(self, "_format_body_monitor_health_prompt", None)
        if callable(body_health_hint_getter):
            body_health_hint = body_health_hint_getter(user, reason=reason)
            if body_health_hint:
                body_health_hint = sanitize_relationship_source(body_health_hint, "proactive.body_health_hint")
                if body_health_hint:
                    prompt = f"{prompt.rstrip()}\n\n{body_health_hint}"
        balance_hint_getter = getattr(self, "_format_balance_awareness_prompt", None)
        if callable(balance_hint_getter):
            balance_hint = balance_hint_getter(user, reason=reason)
            if balance_hint:
                balance_hint = sanitize_relationship_source(balance_hint, "proactive.balance_hint")
                if balance_hint:
                    prompt = f"{prompt.rstrip()}\n\n{balance_hint}"
        environment_hint_getter = getattr(self, "_format_environment_change_prompt", None)
        if callable(environment_hint_getter):
            environment_hint = environment_hint_getter(user, reason=reason)
            if environment_hint:
                environment_hint = sanitize_relationship_source(environment_hint, "proactive.environment_hint")
                if environment_hint:
                    prompt = f"{prompt.rstrip()}\n\n{environment_hint}"
        weather_alert_hint_getter = getattr(self, "_format_weather_alert_prompt", None)
        if callable(weather_alert_hint_getter):
            weather_alert_hint = weather_alert_hint_getter(user, reason=reason)
            if weather_alert_hint:
                weather_alert_hint = sanitize_relationship_source(weather_alert_hint, "proactive.weather_alert_hint")
                if weather_alert_hint:
                    prompt = f"{prompt.rstrip()}\n\n{weather_alert_hint}"
        personal_goal_hint_getter = getattr(self, "_format_personal_goal_prompt", None)
        if callable(personal_goal_hint_getter):
            personal_goal_hint = personal_goal_hint_getter(user, reason=reason)
            if personal_goal_hint:
                personal_goal_hint = sanitize_relationship_source(personal_goal_hint, "proactive.personal_goal_hint")
                if personal_goal_hint:
                    prompt = f"{prompt.rstrip()}\n\n{personal_goal_hint}"
        memo_hint_getter = getattr(self, "_format_memo_note_prompt", None)
        if callable(memo_hint_getter):
            memo_hint = memo_hint_getter(user, reason=reason)
            if memo_hint:
                memo_hint = sanitize_relationship_source(memo_hint, "proactive.memo_hint")
                if memo_hint:
                    prompt = f"{prompt.rstrip()}\n\n{memo_hint}"
        if open_loops_hint and "未完成话题候选" not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{open_loops_hint}"
        memory_context = ""
        memory_getter = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(memory_getter):
            user_id = _single_line(user.get("user_id") or user.get("id"), 80)
            query = " ".join(
                part
                for part in (
                    "主动消息正文生成",
                    f"当前真实时间 {current_time}",
                    "当前日期 最新私聊 当前日程 当前时段 旧日材料不能改写成当前事实",
                    reason,
                    action,
                    topic_hint,
                    compact_motive,
                    "用户习惯 最近互动 当前穿搭 当前日程 自我时间线 避雷",
                )
                if _single_line(part, 180)
            )
            memory_context = await memory_getter(
                kind="proactive_generation",
                query=query,
                user=user,
                user_id=user_id,
                top_k=5,
                max_chars=760,
            )
        if memory_context:
                prompt = (
                    f"{prompt.rstrip()}\n\n"
                    "<!-- private_companion_memory_generation_context_v1 -->\n"
                    "【我会牢牢记住你 可用记忆】\n"
                    f"{memory_context}\n"
                    "使用方式：只作为自然连续性和边界参考；能贴住当前切口就轻轻用,不相关就忽略。不要说“我查到/我记忆里”。"
                )
        relationship_guard_getter = getattr(self, "_format_generation_relationship_authority_guard", None)
        if callable(relationship_guard_getter) and "关系事实权限" not in prompt:
            try:
                relationship_guard = str(relationship_guard_getter() or "").strip()
            except Exception:
                relationship_guard = ""
            if relationship_guard:
                prompt = f"{prompt.rstrip()}\n\n{relationship_guard}"
        identity_guard = self._format_proactive_recipient_identity_guard(user, name)
        if identity_guard:
            prompt = f"{prompt.rstrip()}\n\n{identity_guard}"
        return prompt.strip()

    @staticmethod
    def _proactive_visible_text_format_hint(action: str) -> str:
        action_name = _single_line(action, 80) or "message"
        return (
            "【主动可见正文格式】\n"
            f"- 当前动作：{action_name}。这里生成的是最终显示在聊天里的普通正文；图片动作写可见附言，语音动作的朗读内容和音频会由独立链路生成。\n"
            "- 人格中的 TTS 专用规则只约束独立语音脚本，不约束这里的可见正文。不要输出 <tts>/<pc_tts>、[happy]/[sad] 等情绪控制词、语音专用日语或外语朗读稿、音标，也不要把语音内容再作为文字重复发送。\n"
            "- 可见正文继续遵守人格平时的聊天语言和口吻；只有当人格本身明确要求日常可见聊天使用某种语言时，才使用该语言，不能仅凭 TTS 语种要求切换。"
        )

    def _format_proactive_generation_intent_hint(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str = "",
        action_context: str = "",
    ) -> str:
        semantics: dict[str, Any] = {}
        semantic_getter = getattr(self, "_planned_proactive_semantics", None)
        if callable(semantic_getter):
            try:
                semantics = semantic_getter(user)
            except Exception as exc:
                logger.debug("[PrivateCompanion] 主动生成语义提示读取失败: %s", _single_line(exc, 120))
                semantics = {}
        readiness: dict[str, Any] = {}
        readiness_getter = getattr(self, "_proactive_inner_readiness", None)
        if callable(readiness_getter):
            try:
                readiness = readiness_getter(user)
            except Exception as exc:
                logger.debug("[PrivateCompanion] 主动生成内在状态提示读取失败: %s", _single_line(exc, 120))
                readiness = {}

        kind = _single_line(semantics.get("kind"), 40)
        anchor_type = _single_line(semantics.get("anchor_type"), 40)
        semantic_score = _safe_float(semantics.get("score"), 0.5)
        semantic_pressure = _safe_float(semantics.get("pressure"), 0.4)
        semantic_risk = _safe_float(semantics.get("risk"), 0.0)
        semantic_note = _single_line(semantics.get("note"), 140)
        readiness_label = _single_line(readiness.get("label"), 60)
        readiness_score = _safe_float(readiness.get("score"), 0.55)
        temperature = readiness.get("temperature") if isinstance(readiness.get("temperature"), dict) else {}
        temperature_label = _single_line(temperature.get("label"), 60)
        temperature_score = _safe_float(temperature.get("score"), 0.55)
        motivation = readiness.get("motivation") if isinstance(readiness.get("motivation"), dict) else {}
        expression_decision = readiness.get("expression_decision") if isinstance(readiness.get("expression_decision"), dict) else {}

        lines = ["【这次主动的内在约束】"]
        troubleshooting_hint = self._proactive_troubleshooting_request_hint(user)
        if troubleshooting_hint:
            lines.append(troubleshooting_hint)
        if kind or anchor_type:
            lines.append(
                f"候选语义：{kind or 'check_in'}/{anchor_type or 'vague'}；"
                f"自然度 {semantic_score:.2f}，打扰压力 {semantic_pressure:.2f}，风险 {semantic_risk:.2f}。"
                + (f" 备注：{semantic_note}。" if semantic_note else "")
            )
        if readiness_label or temperature_label:
            lines.append(
                f"开口欲：{readiness_label or '平稳'} {readiness_score:.2f}；"
                f"主动表达温度：{temperature_label or '平稳'} {temperature_score:.2f}。"
            )
        if motivation:
            lines.append(
                f"实验动机调度：{_single_line(motivation.get('label'), 24)} "
                f"{_safe_float(motivation.get('score'), 0.5):.2f}；"
                f"{_single_line(motivation.get('detail'), 120)}。"
            )
        if expression_decision:
            lines.append(
                "统一表达："
                f"档位={_single_line(expression_decision.get('expression_band'), 24) or 'relaxed'}；"
                f"语气={_single_line(expression_decision.get('tone'), 24) or 'steady'}；"
                f"距离={_single_line(expression_decision.get('address_style'), 24) or 'neutral'}；"
                f"节奏={_single_line(expression_decision.get('pacing'), 16) or 'steady'}；"
                f"直接度={_single_line(expression_decision.get('directness'), 16) or 'natural'}；"
                f"回应={_single_line(expression_decision.get('validation_style'), 20) or 'none'}；"
                f"自述={_single_line(expression_decision.get('self_disclosure'), 16) or 'none'}；"
                f"幽默={_single_line(expression_decision.get('humor_mode'), 16) or 'off'}；"
                f"话题={_single_line(expression_decision.get('topic_initiative'), 20) or 'reply_only'}；"
                f"追问={'允许' if expression_decision.get('followup') else '关闭'}；"
                f"当前硬额度={_safe_int(expression_decision.get('proactive_budget'), 0, 0)}；"
                f"阶段柔性目标={_safe_int(expression_decision.get('proactive_target'), 0, 0)}；"
                "结合真实由头、对方反馈和打扰感自然调整，不要求凑满或机械卡线；"
                "内容尺度=normal。"
            )
        relationship_initiative_hint = self._format_proactive_relationship_initiative_hint(
            user,
            reason=reason,
            action=action,
        )
        if relationship_initiative_hint:
            lines.append(relationship_initiative_hint)
        model_judgement = (
            user.get("planned_proactive_model_judge_result")
            if isinstance(user.get("planned_proactive_model_judge_result"), dict)
            else {}
        )
        model_note = _single_line(model_judgement.get("reason"), 140)
        if model_note and any(token in model_note for token in ("软质量建议", "收敛", "改写", "偏低", "偏虚", "不自然")):
            lines.append(
                f"人格计划判定的表达建议：{model_note}。"
                "这只是正文改写方向，不是取消理由；保持原计划事实边界，直接修成自然、具体、低压力的一两句。"
            )
        afterglow = user.get("proactive_afterglow") if isinstance(user.get("proactive_afterglow"), dict) else {}
        if afterglow:
            afterglow_age = _now_ts() - _safe_float(afterglow.get("ts"), 0)
            if 0 <= afterglow_age <= 48 * 3600:
                afterglow_label = _single_line(afterglow.get("label"), 120)
                afterglow_tendency = _single_line(afterglow.get("next_tendency"), 140)
                afterglow_status = _single_line(afterglow.get("status"), 40)
                if afterglow_label or afterglow_tendency:
                    lines.append(
                        f"上一条主动回声：{afterglow_status or 'unknown'}｜"
                        f"{afterglow_label or '仍在等待自然落地'}；{afterglow_tendency or '下一次按关系反馈调整'}。"
                    )

        if semantic_score < 0.48 or semantic_pressure >= 0.58:
            lines.append("这次由头不算很硬或打扰压力偏高：正文要更短、更轻，最好像把一句话放下，不追问、不求回应。")
        elif semantic_score >= 0.68:
            lines.append("这次有明确由头：正文可以贴着那个由头说一个具体点，但仍然不要解释调度原因。")
        if readiness_score < 0.36 or temperature_score < 0.34:
            lines.append(
                "Bot 当前开口欲或主动表达温度偏低，这只影响写法，不是取消发送的理由："
                "用一句更安静、更短的自然话表达，不表演热情，不制造必须回应的压力。"
            )
        unanswered_count = _safe_int(user.get("ignored_streak"), 0, 0)
        quota_policy_getter = getattr(self, "_proactive_quota_policy", None)
        kind_getter = getattr(self, "_planned_proactive_kind", None)
        quota_tier = _safe_int(quota_policy_getter(user).get("tier"), 0, 0, 5) if callable(quota_policy_getter) else 0
        proactive_kind = kind_getter(user) if callable(kind_getter) else "relational"
        if unanswered_count >= 2 and not (quota_tier >= 4 and proactive_kind in {"self_life", "content_share"}):
            lines.append(
                "对方已连续多次没有回应：只保留一个完整意思，优先改写为一句自然短句；"
                "不要同时堆叠近况、提问和叮嘱；不要用‘在吗/最近忙不忙/只是想找你’作为唯一内容，"
                "优先贴着当前真实生活片段或计划里的具体点轻轻说一句；任何收短都必须保证句意完整，不能留下半句话。"
            )
        if reason not in {"environment_change", "weather_alert"}:
            lines.append("天气和气温只作环境底色，本轮不要把它们改写成正文话题，也不要顺手追问对方那边的天气；改用本轮明确动机、生活片段或最近真实话题。")
        if reason == "health_alert":
            body_health_hint_getter = getattr(self, "_format_body_monitor_health_prompt", None)
            body_health_hint = body_health_hint_getter(user, reason=reason) if callable(body_health_hint_getter) else ""
            if body_health_hint:
                lines.append(body_health_hint)
            lines.append("这是一次有时效的身体状态关心线索：只温和问候当前感受，不作医疗判断，不夸大风险，也不要求对方立即回复。")
        elif reason == "low_balance":
            balance_hint_getter = getattr(self, "_format_balance_awareness_prompt", None)
            balance_hint = balance_hint_getter(user, reason=reason) if callable(balance_hint_getter) else ""
            if balance_hint:
                lines.append(balance_hint)
            lines.append("这是用户明确开启的余额感知事件：允许按人格轻轻要零花钱或补给，但只提一次，不催促、不索要回复，也不把服务余额写成用户欠款。")
        elif reason == "environment_change":
            environment_hint_getter = getattr(self, "_format_environment_change_prompt", None)
            environment_hint = environment_hint_getter(user, reason=reason) if callable(environment_hint_getter) else ""
            if environment_hint:
                lines.append(environment_hint)
            lines.append("这是有短时效的环境变化：只贴着刚发生的变化说一个具体点，不扩写预报，不假设用户正在室外，也不解释信息来源。")
        elif reason == "weather_alert":
            weather_alert_hint_getter = getattr(self, "_format_weather_alert_prompt", None)
            weather_alert_hint = weather_alert_hint_getter(user, reason=reason) if callable(weather_alert_hint_getter) else ""
            if weather_alert_hint:
                lines.append(weather_alert_hint)
            lines.append("这是来自官方气象渠道的当前预警：优先保留等级、现象和防护建议等事实，用熟悉的口吻及时说清；不要提接口、缓存、轮询、API Host 或内部字段，不把预警写成夸张灾情，也不要替用户判断已经发生了什么。")
        elif reason == "personal_goal_progress":
            personal_goal_hint_getter = getattr(self, "_format_personal_goal_prompt", None)
            personal_goal_hint = personal_goal_hint_getter(user, reason=reason) if callable(personal_goal_hint_getter) else ""
            if personal_goal_hint:
                lines.append(personal_goal_hint)
            lines.append("这是 Bot 自己的非创作型长期目标变化：只说一个真实进展、停滞或完成结果，不向用户索取监督，不把百分比写成系统汇报。")
        elif reason == "memo_note_reminder":
            memo_hint_getter = getattr(self, "_format_memo_note_prompt", None)
            memo_hint = memo_hint_getter(user, reason=reason) if callable(memo_hint_getter) else ""
            if memo_hint:
                lines.append(memo_hint)
            lines.append("这是用户自己设置的到期便签：直接提醒便签里的事项，一次说清，不解释为什么现在发送，也不要追问用户是否完成。")
        elif reason == "morning_greeting":
            lines.append("这是当天第一次普通早安：只自然打招呼或递出一个很轻的早晨片段，说完就停。用户还没有回应，禁止问早餐/早饭、吃了吗、吃什么，也不要追加起床查岗、健康确认或其他需要回答的问题；饮食关心会在用户回应后的独立时机处理。")
        elif reason == "meal_care":
            lines.append("这是饭点关心：自然问用户这一顿吃了没有。问题主体必须是用户，不要回答成自己吃了什么；像熟悉的人顺口惦记一句，不说教、不盘问，也不要同一条里连续列很多问题。")
        elif reason == "meal_care_followup":
            lines.append("这是一次且仅一次的吃饭补问：根据话题判断是确认后来有没有吃上，还是问已经吃过的具体内容。保持很短、低压力，不责怪用户没回，也不要重复上一句原话。")
        elif reason == "birthday_eve_hint":
            lines.append("这是生日前夜的一点留白：可以温柔地提醒对方明天多偏爱自己一点，但不要说出生日、准备、惊喜或任何剧透；一小句就停，不制造期待压力。")
        elif reason == "birthday_makeup":
            lines.append("这是次日午前的低调补送：真诚祝福即可，不要反复道歉、不解释系统或错过原因，也不要把昨天的生日写成今天。")
        elif reason == "birthday_afterglow":
            lines.append("这是用户在生日祝福后已经回应过才会出现的余温收尾：只轻轻接住一个开心瞬间，不重复说生日快乐、不追问安排，也不延长成连续庆祝。")
        elif reason == "birthday_celebration":
            lines.append("今天是用户明确允许记住的生日，是一年一次的轻量仪式。表达必须服从当前人格：可以热闹、安静、含蓄或只留一句，不要强行煽情。先送出真诚、具体、低压力的祝福；不要提系统、记录、年龄、出生年份或精确日期，不承诺永远陪伴，也不要求回复或追问庆祝安排。若带图，正文只自然递出，不描述制作过程。")
        elif reason == "special_day_greeting":
            special_context = user.get("planned_special_day_context") if isinstance(user.get("planned_special_day_context"), dict) else {}
            title = _single_line(special_context.get("observance_title"), 32) or "这个特别的日子"
            timing = _single_line(special_context.get("delivery_timing"), 24)
            if timing == "midnight":
                lines.append(f"这是{title}零点后的第一句问候：先看人格是否喜欢仪式感；若不偏节日表达，就用平常口吻轻轻带过，不要硬写浪漫。只围绕一个具体情绪或祝愿自然说一句；不要写成节日科普、营销文案、固定祝福模板，也不要要求用户回复或追问安排。")
            else:
                lines.append(f"这是错过零点后的{title}白天补上：自然承认今天这个特别日子即可，不解释系统延迟，不使用僵硬的节日贺词，不把普通寒暄扩成盘问。")
        elif reason == "birthday_curiosity":
            lines.append("这是一次低频的资料好奇：只自然地问生日的月日，可顺带问公历还是农历；明确说不想回答也完全没关系。不要索要出生年份、年龄、证件信息，也不要假装已经准备了生日惊喜。")
        elif reason == "web_exploration_share":
            lines.append("自然地向用户分享自己刚看的这条内容。只把标题、探索印象和链接当作事实依据，像当前人格平时聊天一样表达。")
        elif kind in {"continuation", "reminder"}:
            lines.append("这是有来源的续接/提醒：可以顺着来源，但不要写成用户刚刚又发了新消息。")
        elif kind in {"self_share", "external_share", "observation"}:
            lines.append("这是分享/观察型主动：只取一个最小切口，不写成报告、推荐文或观察总结。")
            true_external_info = reason in {"bili_video_share", "news_share", "web_exploration_share"}
            if true_external_info:
                lines.append("外界分享必须贴住这次看到的标题、视频、新闻或资料本身；如果只是低压地放一句，也要围绕来源表达感受，不要改成无关的个人状态或泛泛压力询问。")
                lines.append("最终正文必须让用户一眼知道你在分享什么：至少带标题、BV/链接、来源名或具体内容锚点之一；不要只写“看这个/这条好离谱/给你看个东西”。")
            elif anchor_type == "group_context":
                lines.append("群聊见闻只是一段共同群里的小片段：可以轻轻转述一个具体笑点或画面，不要把内部话题名写成“标题/新闻/资料”。")
        elif kind in {"care", "check_in", "light_touch"}:
            lines.append("这是靠近型主动：不要直接说想念、关心或刷存在感，要侧着落到一个小动作或小片段。")

        hesitation_note = _single_line(user.get("last_proactive_hesitation_note"), 100)
        hesitation_at = _safe_float(user.get("last_proactive_hesitation_at"), 0)
        if hesitation_note and hesitation_at > 0 and _now_ts() - hesitation_at <= 12 * 3600:
            lines.append(f"前面有过一次犹豫：{hesitation_note}。如果要用，只能变成很淡的语气底色，不要明说系统延后。")
        deferred_share_tense_hint = self._deferred_immediate_share_tense_hint(user, action)
        if deferred_share_tense_hint:
            lines.append("这段生活分享已不是当下现场：必须使用已发生时态，不要暗示事件与发送同一时刻，也不要解释延后。")

        if _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            lines.append("对方最近还没回应：不要连续提问，不要控诉，也不要把沉默写成对方故意不理。")
        if "message" == str(action or "message") and not _single_line(action_context, 120):
            lines.append("本轮没有真实媒体或工具结果：正文只围绕聊天内容本身，不描述动作结果。")
        lines.append("以上只用于决定怎么写，最终正文里不要出现“语义/自然度/压力/风险/开口欲/主动表达温度/犹豫”等分析词。")
        return "\n".join(lines) if len(lines) > 2 else ""

    def _unexecuted_relay_claim_reason(self, text: str, *, action_context: str = "") -> str:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return ""
        context = str(action_context or "")
        if any(token in context for token in ("pc_relay_message", "转述工具", "消息已发送", "已挂起", "atrelay")):
            return ""
        target_patterns = (
            r"我(?:这就|现在|等下|一会儿|待会儿)?(?:去|会|来|可以)?(?:帮你|替你)?(?:跟|和|给)([^，。！？!?、\s]{1,12})(?:说一声|说一下|转告|转达|带话|留言)",
            r"我(?:这就|现在|等下|一会儿|待会儿)?(?:帮你|替你)([^，。！？!?、\s]{0,12})(?:转告|转达|带话|留言)",
            r"(?:已经|已)(?:帮你|替你)?(?:转告|转达|带话|留言|说过)",
        )
        for pattern in target_patterns:
            match = re.search(pattern, cleaned)
            if not match:
                continue
            target = _single_line(match.group(1) if match.lastindex else "", 20)
            if target and target.startswith(("你", "妳")):
                continue
            return "没有真实转述工具执行结果"
        return ""

    def _fallback_unexecuted_relay_reply(self, inbound_text: str) -> str:
        inbound = _single_line(inbound_text, 160)
        if any(token in inbound for token in ("替我", "帮我", "你去", "跟他", "和他", "跟她", "和她", "说一声", "转告", "转达")):
            return "我不能假装已经说过。你把对象和要带的话再说清楚一点。"
        return "我不能假装已经替你说过。要我带话的话，你把对象和内容说清楚。"

    def _proactive_time_guard_hint(self, reason: str, current_item: dict[str, Any] | None) -> str:
        activity = _single_line((current_item or {}).get("activity"), 80)
        _, period_guard = self._current_time_period_label()
        prefix = f"先遵守当前真实时段：{period_guard}"
        if reason == "morning_greeting":
            return f"{prefix} 这次只能像早晨刚醒、赖床、洗漱或刚开始一天时那样开口；只做自然问候，不问早餐、吃了吗或吃什么，也不要附带健康和查岗问题。"
        if reason == "noon_greeting":
            return f"{prefix} 这次只能像中午、吃东西、发懒、午间发呆或午休前后那样开口；不要写成刚醒起床或准备睡觉。"
        if reason == "evening_greeting":
            return f"{prefix} 这次只能像傍晚收尾、天色往下落、回到家或一天快慢下来时那样开口；不要写成刚醒起床。"
        if activity and any(token in activity for token in ("便利店", "出门", "吹风", "路上", "窗边", "收拾", "吃", "洗漱", "洗澡", "刷视频", "书桌")):
            return f"{prefix} 优先贴着这一小段生活片段来开口：{activity}。不要忽然跳成不在这个时段里的“刚醒”“赖床”或“要睡了”。"
        return f"{prefix} 贴着当前这小段生活片段开口，不要忽然跳成不在这个时段里的“刚醒”“赖床”或“要睡了”。"

    def _build_framework_voice_prompt(
        self,
        *,
        user: dict[str, Any],
        name: str,
        reason: str,
        target: str,
        strict_tts: bool = False,
    ) -> str:
        state = self.data.get("daily_state", {})
        last_user_message = _single_line(user.get("last_user_message"), 80)
        profile = self._relationship_profile(user)
        tts_prompt = self._get_tts_prompt_text(target)
        req = self._voice_requirement_profile(target)
        state_hint = self._format_state_for_framework_prompt(
            state if isinstance(state, dict) else {},
            reason=reason,
            action="voice",
        )
        state_hint = self._sanitize_owner_environment_context_for_private_user(state_hint, user)
        return f"""
你现在要在同一段私聊会话里，准备一小句真正会被念出来的主动语音内容。
当前会话里已有的人格、关系、上下文会继续生效，这里不要再重复铺陈。
站位必须清楚：这是你主动发语音,不是对方刚刚来找你、叫醒你或问候你。聊天历史只作背景,不要把最后一句历史当成当前新消息。

补充信息：
- 对方称呼：{name}
- 主动原因：{reason}
- 最近一句用户消息：{last_user_message or "（暂无）"}
- 关系画像：{profile['level']}｜偏好：{profile['preference']}
- 当前状态底色：{state_hint or "今天整体比较平稳。"}
- 当前会话 TTS 规则：{tts_prompt or "（当前没有额外 TTS 提示词,就按人格自己的语音习惯来）"}
- 当前语音格式重点：{req['summary']}

要求：
1. 只输出这句真正要被念出来的语音内容，不要解释。
2. 如果当前人格或 TTS 规则要求使用 <tts>...</tts>、日语、情绪标签、双语格式，就严格遵守。
3. 如果没有明确格式要求，就写成适合私聊语音的一小句，不像朗读稿。
4. 可以有一点嘴硬、黏人、藏着的想念，但不要把喜欢说满。
5. 不要提 AI、模型、插件、TTS、语音合成这些词。
{"6. 这次必须优先满足语音格式要求；如果有日语或 <tts> 规则，不要退回普通中文句子。" if strict_tts else ""}
""".strip()

    async def _capture_framework_send_message_calls(
        self,
        *,
        target_session: str,
        runner_factory: Any,
        max_steps: int = 20,
    ) -> tuple[Any, list[_CapturedSendMessageCall]]:
        captured: list[_CapturedSendMessageCall] = []
        try:
            from astrbot.core.tools.message_tools import SendMessageToUserTool
            from astrbot.core.agent.runners.tool_loop_agent_runner import _ToolExecutionInterrupted
        except Exception:
            result = await runner_factory()
            return result, captured

        original_call = SendMessageToUserTool.call

        async def _intercept_call(tool_self, context, **kwargs):
            session_value = kwargs.get("session") or getattr(
                getattr(getattr(context, "context", None), "event", None),
                "unified_msg_origin",
                "",
            )
            messages = kwargs.get("messages")
            session_text = str(session_value or "")
            if session_text == target_session and isinstance(messages, list):
                captured.append(_CapturedSendMessageCall(session_text, messages))
                logger.info(
                    "[PrivateCompanion] 已拦截框架内 send_message_to_user 工具调用: session=%s components=%s",
                    session_text,
                    len(messages),
                )
                raise _ToolExecutionInterrupted("PrivateCompanion captured send_message_to_user payload.")
            return await original_call(tool_self, context, **kwargs)

        SendMessageToUserTool.call = _intercept_call
        try:
            result = await runner_factory()
            runner = getattr(result, "agent_runner", None) if result is not None else None
            if runner is not None and hasattr(runner, "step_until_done"):
                try:
                    async for _ in runner.step_until_done(max_steps):
                        pass
                except (_CapturedFrameworkSendMessage, _ToolExecutionInterrupted):
                    logger.info(
                        "[PrivateCompanion] 主动主链工具发送已捕获,提前结束工具循环: session=%s captured=%s",
                        target_session,
                        len(captured),
                    )
        finally:
            SendMessageToUserTool.call = original_call
        return result, captured

    def _captured_send_plain_text(self, captured_tool_sends: list[Any]) -> str:
        if not captured_tool_sends:
            return ""
        captured_text_parts: list[str] = []
        for call in captured_tool_sends:
            messages = getattr(call, "messages", [])
            if not isinstance(messages, list):
                continue
            for item in messages:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or "").strip().lower() != "plain":
                    continue
                text_value = self._sanitize_captured_plain_text(item.get("text"))
                if text_value:
                    captured_text_parts.append(text_value)
        return "\n".join(captured_text_parts).strip()

    def _filter_incompatible_proactive_framework_tools(
        self,
        req: ProviderRequest,
        names: set[str] | None = None,
    ) -> list[str]:
        tool_set = getattr(req, "func_tool", None)
        remove_tool = getattr(tool_set, "remove_tool", None)
        if not callable(remove_tool):
            return []
        excluded = {str(name).strip() for name in (names or {"AIsearch"}) if str(name).strip()}
        existing = {
            str(getattr(tool, "name", "") or "").strip()
            for tool in list(getattr(tool_set, "tools", []) or [])
        }
        removed = sorted(name for name in excluded if name in existing)
        for name in removed:
            remove_tool(name)
        if removed:
            logger.info(
                "[PrivateCompanion] 主动主链已隔离不兼容全局工具: %s",
                ",".join(removed),
            )
        return removed

    def _install_proactive_semantic_provider_fallback(
        self,
        build_result: Any,
        *,
        label: str,
    ) -> bool:
        """Let AstrBot's native fallback chain handle successful error responses."""
        runner = getattr(build_result, "agent_runner", None)
        if runner is None:
            return False
        installed_marker = "_private_companion_semantic_provider_fallback_installed"
        if bool(getattr(runner, installed_marker, False)):
            return True
        original_iter = getattr(runner, "_iter_llm_responses", None)
        if not callable(original_iter):
            return False

        async def _guarded_iter(*args: Any, **kwargs: Any):
            buffered_chunks: list[LLMResponse] = []
            async for response in original_iter(*args, **kwargs):
                if isinstance(response, LLMResponse) and bool(response.is_chunk):
                    buffered_chunks.append(response)
                    continue

                result_chain = getattr(response, "result_chain", None)
                chain = list(getattr(result_chain, "chain", []) or [])
                has_non_plain_component = any(
                    not isinstance(component, Plain) for component in chain
                )
                completion_text = str(
                    getattr(response, "completion_text", "") or ""
                ).strip()
                response_role = str(
                    getattr(response, "role", "") or ""
                ).strip().lower()
                is_native_provider_error = (
                    isinstance(response, LLMResponse)
                    and response_role == "err"
                    and not bool(getattr(response, "is_chunk", False))
                )
                is_semantic_provider_error = (
                    isinstance(response, LLMResponse)
                    and response_role == "assistant"
                    and not bool(getattr(response, "is_chunk", False))
                    and not list(getattr(response, "tools_call_name", []) or [])
                    and not has_non_plain_component
                    and bool(completion_text)
                    and _looks_like_upstream_llm_error_response(completion_text)
                )
                if is_native_provider_error or is_semantic_provider_error:
                    provider = getattr(runner, "provider", None)
                    provider_config = getattr(provider, "provider_config", {})
                    provider_id = (
                        _single_line(provider_config.get("id"), 80)
                        if isinstance(provider_config, dict)
                        else ""
                    )
                    response_ref = hashlib.sha256(
                        completion_text.encode("utf-8", errors="replace")
                    ).hexdigest()[:12]
                    logger.warning(
                        "[PrivateCompanion] 主动主链识别到 Provider 错误响应,已交给 AstrBot 原生回退链: label=%s provider=%s kind=%s response_ref=%s",
                        _single_line(label, 80),
                        provider_id or type(provider).__name__,
                        "native_error" if is_native_provider_error else "semantic_error",
                        response_ref,
                    )
                    sanitized_response = LLMResponse(
                        role="err",
                        completion_text=(
                            "Provider API error: upstream returned an internal "
                            "failure message."
                        ),
                    )
                    for attr_name in ("id", "usage"):
                        attr_value = getattr(response, attr_name, None)
                        if attr_value is not None:
                            try:
                                setattr(sanitized_response, attr_name, attr_value)
                            except Exception:
                                pass
                    yield sanitized_response
                    return

                for chunk in buffered_chunks:
                    yield chunk
                buffered_chunks.clear()
                yield response
                return

            for chunk in buffered_chunks:
                yield chunk

        try:
            setattr(runner, "_iter_llm_responses", _guarded_iter)
            setattr(runner, installed_marker, True)
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 主动主链无法安装 Provider 语义错误回退适配器: label=%s error_type=%s",
                _single_line(label, 80),
                type(exc).__name__,
            )
            return False
        return True

    def _framework_agent_meta_summary_leak(self, text: str) -> bool:
        cleaned = _single_line(text, 500).lower()
        if not cleaned:
            return False
        normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff_]+", " ", cleaned).strip()
        compact = re.sub(r"[^a-z0-9\u4e00-\u9fff_]+", "", cleaned)
        if self._is_proactive_delivery_receipt_text(text):
            return True
        if self._is_proactive_instruction_leak_text(text):
            return True
        if (
            ("差不多20条" in cleaned or "差不多 20 条" in cleaned or "20条不同" in cleaned)
            and any(token in cleaned for token in ("没收到回复", "发消息", "消息主要是", "工具调用"))
        ):
            return True
        if (
            ("二十次" in cleaned or "20次" in cleaned or "多次" in cleaned)
            and any(token in cleaned for token in ("试着给", "发私信", "发消息"))
            and any(token in cleaned for token in ("有没有成功", "成功发出去", "没收到回复", "不确定这些消息"))
        ):
            return True
        if (
            ("读取图片文件" in cleaned or "图片文件有问题" in cleaned)
            and any(token in cleaned for token in ("占位", "工具调用", "没法继续", "多次发消息"))
        ):
            return True
        if "工具调用限制" in cleaned and any(token in cleaned for token in ("没法继续", "多次发消息", "发消息")):
            return True
        markers = (
            "trying to send messages",
            "trying to send various messages",
            "sent 20",
            "no response yet",
            "shared parts",
            "asked for her thoughts",
            "message captured",
            "executed the same tool",
            "repetition is now very high",
            "agent reached max steps",
            "forcing a final response",
            "tool `send_message_to_user`",
            "send_message_to_user",
            "一直试着给",
            "发了差不多20条",
            "还没收到回复",
            "读取图片文件有问题",
            "工具调用限制",
        )
        compact_markers = (
            "tryingtosendmessages",
            "tryingtosendvariousmessages",
            "sent20",
            "noresponseyet",
            "sharedparts",
            "askedforherthoughts",
            "messagecaptured",
            "executedthesametool",
            "repetitionisnowveryhigh",
            "agentreachedmaxsteps",
            "forcingafinalresponse",
            "sendmessagetouser",
            "一直试着给",
            "发了差不多20条",
            "还没收到回复",
            "读取图片文件有问题",
            "工具调用限制",
        )
        return any(marker in cleaned or marker in normalized for marker in markers) or any(
            marker in compact for marker in compact_markers
        )

    async def _conversation_db_operation(self, label: str, operation: Any) -> Any:
        lock = getattr(self, "_conversation_db_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self._conversation_db_lock = lock
        for attempt in range(5):
            try:
                async with lock:
                    return await operation()
            except Exception as exc:
                text = str(exc or "").lower()
                locked = "database is locked" in text or "sqlite3.operationalerror" in text
                if locked and attempt < 4:
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                logger.debug("[PrivateCompanion] 会话数据库操作失败: %s error=%s", label, exc)
                raise

    def _is_sqlite_locked_error(self, exc: Exception) -> bool:
        text = str(exc or "").lower()
        return "database is locked" in text or "sqlite3.operationalerror" in text or "sqlalche.me/e/20/e3q8" in text

    async def _get_current_conversation_safely(self, umo: str, *, label: str = "conversation") -> Any:
        async def _read():
            conv_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
            if not conv_id:
                return None
            return await self.context.conversation_manager.get_conversation(umo, conv_id)

        return await self._conversation_db_operation(label, _read)

    async def _ensure_conversation_id_for_umo(self, umo: str, *, title: str = "Private Companion 主动消息") -> str:
        conv_mgr = getattr(getattr(self, "context", None), "conversation_manager", None)
        if conv_mgr is None:
            return ""
        conv_id = await conv_mgr.get_curr_conversation_id(umo)
        if conv_id:
            return str(conv_id)
        session = self._parse_message_session(umo)
        platform_id = _single_line(getattr(session, "platform_id", ""), 80) if session is not None else ""
        try:
            if platform_id:
                conv_id = await conv_mgr.new_conversation(umo, platform_id)
            else:
                conv_id = await conv_mgr.new_conversation(umo, title=title)
        except TypeError:
            try:
                conv_id = await conv_mgr.new_conversation(umo, title=title)
            except TypeError:
                conv_id = await conv_mgr.new_conversation(umo)
        if conv_id:
            logger.info(
                "[PrivateCompanion] 已为主动消息存档创建 AstrBot 会话: umo=%s cid=%s",
                _single_line(umo, 140),
                _single_line(conv_id, 80),
            )
        return str(conv_id or "")

    def _proactive_synthetic_event(self, umo: str, *, prompt: str, name: str) -> AstrMessageEvent | None:
        framework_context = self._proactive_framework_context()
        if framework_context is None:
            return None
        session = self._parse_message_session(umo)
        if not session:
            return None
        return SyntheticPrivateWakeEvent(
            context=framework_context,
            session=session,
            message=prompt,
            sender_name=name or "PrivateCompanion",
        )

    def _proactive_framework_context(self) -> Context | None:
        """Resolve only a native AstrBot Context from current or legacy wrappers."""
        candidate = getattr(self, "context", None)
        pending = [candidate]
        visited: set[int] = set()
        wrapper_attrs = (
            "context_obj",
            "plugin_context",
            "wrapped_context",
            "raw_context",
            "_context",
            "_context_obj",
            "_plugin_context",
            "_wrapped_context",
            "_raw_context",
            "__wrapped__",
        )
        while pending:
            current = pending.pop(0)
            if isinstance(current, Context):
                return current
            if current is None or id(current) in visited:
                continue
            visited.add(id(current))
            for attr in wrapper_attrs:
                try:
                    nested = getattr(current, attr, None)
                except Exception:
                    continue
                if nested is not None and id(nested) not in visited:
                    pending.append(nested)
        return None

    def _proactive_conversation_with_configured_persona(self, conversation: Any) -> Any:
        specific_id = str(
            getattr(
                self,
                "_effective_plugin_persona_id",
                lambda: runtime_persona_setting(self, "plugin_specific_persona_id", ""),
            )()
            or ""
        ).strip()
        if conversation is None or not specific_id:
            return conversation
        if str(getattr(conversation, "persona_id", "") or "").strip() == specific_id:
            return conversation
        try:
            scoped = deepcopy(conversation)
            scoped.persona_id = specific_id
            return scoped
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 无法为主动主链应用插件指定人格,继续使用会话人格: persona=%s error=%s",
                _single_line(specific_id, 80),
                _single_line(exc, 120),
            )
            return conversation

    async def _run_framework_agent_text(
        self,
        *,
        umo: str,
        prompt: str,
        name: str,
        label: str,
        user: dict[str, Any] | None = None,
        max_steps: int = 20,
    ) -> str:
        self._cleanup_framework_delivery_caches()
        cache_key = str(umo or "")
        self._framework_captured_send_cache.pop(cache_key, None)
        getattr(self, "_framework_captured_send_cache_at", {}).pop(cache_key, None)
        deferred_photo_cache = getattr(self, "_framework_deferred_photo_cache", None)
        if isinstance(deferred_photo_cache, dict):
            deferred_photo_cache.pop(cache_key, None)
        getattr(self, "_framework_deferred_photo_cache_at", {}).pop(cache_key, None)
        framework_context = self._proactive_framework_context()
        if framework_context is None:
            context_value = getattr(self, "context", None)
            context_type = type(context_value).__name__ if context_value is not None else "None"
            warning_key = f"{type(context_value).__module__}.{context_type}" if context_value is not None else context_type
            if getattr(self, "_proactive_framework_context_warning_key", "") != warning_key:
                self._proactive_framework_context_warning_key = warning_key
                logger.warning(
                    "[PrivateCompanion] 主动主链未取得 AstrBot 原生 Context,已直接转入人格化兜底: input_type=%s；请重载插件或重启 AstrBot",
                    context_type,
                )
            return ""
        camera_state: dict[str, Any] = {}
        if label == "proactive_message" and isinstance(user, dict):
            camera_prompt_getter = getattr(self, "_reality_touch_camera_proactive_prompt", None)
            if callable(camera_prompt_getter):
                camera_prompt = camera_prompt_getter(
                    user,
                    user_id=str(user.get("user_id") or ""),
                )
                if camera_prompt:
                    prompt = f"{prompt.rstrip()}\n\n{camera_prompt}"
            camera_state_getter = getattr(self, "_reality_touch_camera_proactive_state", None)
            if callable(camera_state_getter):
                value = camera_state_getter(
                    user,
                    user_id=str(user.get("user_id") or ""),
                )
                if isinstance(value, dict):
                    camera_state = value
        event = self._proactive_synthetic_event(umo, prompt=prompt, name=name)
        if event is None:
            return ""
        try:
            setattr(event, "private_companion_skip_external_token_stats", True)
            setattr(event, "private_companion_proactive_framework", True)
            setattr(event, "private_companion_skip_passive_input_status", True)
        except Exception:
            pass
        cfg = framework_context.get_config(umo=umo) if umo else framework_context.get_config()
        provider_settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
        build_cfg = MainAgentBuildConfig(
            tool_call_timeout=int(provider_settings.get("tool_call_timeout", 120) or 120),
            llm_safety_mode=False,
            streaming_response=False,
        )
        req = ProviderRequest(
            prompt=prompt,
            conversation=None,
            session_id=getattr(event, "session_id", None) or umo,
        )

        captured_tool_sends: list[Any] = []
        result = None
        async def _run_with_retries() -> None:
            nonlocal result, captured_tool_sends
            for attempt in range(3):
                try:
                    conv = await self._get_current_conversation_safely(umo, label=f"{label}_framework_read")
                    req.conversation = self._proactive_conversation_with_configured_persona(conv)

                    async def _runner_factory():
                        build_result = await build_main_agent(
                            event=event,
                            # AstrBot 4.26.2+ validates this as the concrete Context type.
                            plugin_context=framework_context,
                            config=build_cfg,
                            req=req,
                        )
                        excluded_tools = {"AIsearch"}
                        if not camera_state.get("direct_allowed"):
                            excluded_tools.add("pc_reality_touch_camera_snapshot")
                        self._filter_incompatible_proactive_framework_tools(req, excluded_tools)
                        self._install_proactive_semantic_provider_fallback(
                            build_result,
                            label=label,
                        )
                        return build_result

                    result, captured_tool_sends = await self._capture_framework_send_message_calls(
                        target_session=umo,
                        runner_factory=_runner_factory,
                        max_steps=max_steps,
                    )
                    break
                except Exception as exc:
                    if self._is_sqlite_locked_error(exc) and attempt < 2:
                        wait_seconds = 0.35 * (attempt + 1)
                        logger.info(
                            "[PrivateCompanion] 主动主链遇到会话库锁,稍后重试: label=%s session=%s retry=%s",
                            label,
                            _single_line(umo, 120),
                            attempt + 1,
                        )
                        await asyncio.sleep(wait_seconds)
                        continue
                    raise
        await _run_with_retries()
        if captured_tool_sends:
            self._framework_captured_send_cache[cache_key] = list(captured_tool_sends)
            captured_at = getattr(self, "_framework_captured_send_cache_at", None)
            if isinstance(captured_at, dict):
                captured_at[cache_key] = time.time()
        if bool(getattr(event, "_private_companion_photo_tool_deferred", False)):
            deferred_path = _path_text(
                getattr(event, "_private_companion_photo_tool_deferred_path", ""),
                1000,
            )
            if deferred_path and os.path.exists(deferred_path):
                cache = getattr(self, "_framework_deferred_photo_cache", None)
                if not isinstance(cache, dict):
                    cache = {}
                    self._framework_deferred_photo_cache = cache
                deferred_caption = self._sanitize_captured_plain_text(
                    getattr(event, "_private_companion_photo_tool_deferred_caption", "")
                )
                cache[cache_key] = {
                    "path": deferred_path,
                    "caption": deferred_caption,
                    "intent_kind": _single_line(
                        getattr(event, "_private_companion_photo_tool_deferred_intent_kind", ""),
                        40,
                    ),
                }
                deferred_at = getattr(self, "_framework_deferred_photo_cache_at", None)
                if isinstance(deferred_at, dict):
                    deferred_at[cache_key] = time.time()
                self._framework_captured_send_cache.pop(cache_key, None)
                getattr(self, "_framework_captured_send_cache_at", {}).pop(cache_key, None)
                logger.info(
                    "[PrivateCompanion] 主动主链已接收 pc_generate_photo 成图，等待统一发送: label=%s session=%s",
                    label,
                    _single_line(cache_key, 120),
                )
                return deferred_caption
        runner = getattr(result, "agent_runner", None) if result else None
        llm_resp = runner.get_final_llm_resp() if runner else None
        text = str(getattr(llm_resp, "completion_text", "") or "").strip()
        response_role = str(getattr(llm_resp, "role", "") or "").strip().lower()
        captured_text = self._captured_send_plain_text(captured_tool_sends)
        if captured_text:
            if text and self._framework_agent_meta_summary_leak(text):
                logger.warning(
                    "[PrivateCompanion] 主动主链 final 疑似工具循环摘要或 Provider 失败,改用已捕获发送文本: label=%s final=%s captured=%s",
                    label,
                    _single_line(text, 160),
                    _single_line(captured_text, 160),
                )
            text = captured_text
        elif response_role == "err" or (
            text and self._framework_agent_meta_summary_leak(text)
        ):
            logger.warning(
                "[PrivateCompanion] 主动主链 final 疑似工具循环摘要或 Provider 失败且无可用捕获文本,已丢弃: label=%s text=%s",
                label,
                _single_line(text, 180),
            )
            return ""
        return text

    async def _generate_proactive_message_via_framework(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        action_context: str = "",
        action: str = "message",
        motive: str = "",
    ) -> str:
        umo = str(user.get("umo") or "").strip()
        if not umo:
            return ""
        prompt = await self._build_framework_proactive_prompt(
            user=user,
            name=name,
            reason=reason,
            action=action,
            action_context=action_context,
            motive=motive,
        )
        recorder = getattr(self, "_record_prompt_injection_snapshot", None)
        if callable(recorder):
            trace_id = f"pro-{uuid.uuid4().hex[:16]}"
            message_preview = _single_line(
                " / ".join(
                    part
                    for part in (
                        name,
                        _single_line(user.get("planned_proactive_topic"), 60),
                        motive,
                        reason,
                        action,
                    )
                    if _single_line(part, 60)
                ),
                220,
            )
            await recorder(
                kind="proactive",
                session=umo,
                title="主动消息提示词",
                text=prompt,
                mode=reason,
                trace_id=trace_id,
                message_preview=message_preview,
                sender_label=_single_line(f"{name}/{user.get('user_id')}", 80),
                metadata={
                    "用户": _single_line(user.get("user_id"), 80),
                    "称呼": name,
                    "原因": reason,
                    "动作": action,
                    "动机": motive,
                    "话题": _single_line(user.get("planned_proactive_topic"), 80),
                },
            )
        try:
            raw_text = await self._run_framework_agent_text(
                umo=umo,
                prompt=prompt,
                name=name,
                label="proactive_message",
                user=user,
                max_steps=20,
            )
            raw_text = str(raw_text or "")
            if not raw_text:
                return ""
            cleaned_text, payloads = self._extract_timer_directives(raw_text)
            if payloads:
                logger.info(
                    "[PrivateCompanion] 主动消息中清理到对话临时预约标签,不再由主动链路登记: user=%s",
                    _single_line(user.get("user_id"), 40),
                )
            return cleaned_text
        except Exception as exc:
            if self._is_sqlite_locked_error(exc):
                logger.warning("[PrivateCompanion] 主动消息主链被会话数据库锁住,本轮跳过并等待下次调度: %s", _single_line(umo, 120))
            else:
                logger.warning("[PrivateCompanion] 主动消息主链生成失败: %s", exc)
            return ""

    def _proactive_history_limit(self, stage: str) -> int:
        review_stage = str(stage or "").strip().lower() == "review"
        attr = "proactive_review_history_limit" if review_stage else "proactive_generation_history_limit"
        default = 30 if review_stage else 20
        return _safe_int(getattr(self, attr, default), default, 1, 200)

    @staticmethod
    def _fit_proactive_history_lines(lines: list[str], max_chars: int) -> list[str]:
        budget = max(0, int(max_chars))
        if not lines or budget <= 0:
            return []
        kept_reversed: list[str] = []
        used = 0
        for raw_line in reversed(lines):
            line = str(raw_line or "").strip()
            if not line:
                continue
            separator = 1 if kept_reversed else 0
            available = budget - used - separator
            if available <= 0:
                break
            if len(line) <= available:
                kept_reversed.append(line)
                used += separator + len(line)
                continue
            if not kept_reversed:
                kept_reversed.append(line[:available].rstrip())
            break
        return list(reversed([line for line in kept_reversed if line]))

    def _format_proactive_history_context(self, lines: list[str]) -> str:
        cleaned_lines = [str(line or "").strip() for line in lines if str(line or "").strip()]
        if not cleaned_lines:
            return ""
        mode = str(
            runtime_persona_setting(self, "proactive_history_context_mode", "compact")
            or "compact"
        ).strip().lower()
        if mode not in {"recent_only", "compact", "expanded"}:
            mode = "compact"
        recent_count = _safe_int(
            runtime_persona_setting(self, "proactive_history_recent_raw_count", 8),
            8,
            1,
            50,
        )
        max_chars = _safe_int(
            runtime_persona_setting(self, "proactive_history_max_chars", 6000),
            6000,
            500,
            20000,
        )

        if mode == "recent_only":
            fitted = self._fit_proactive_history_lines(cleaned_lines[-recent_count:], max_chars)
            return "\n".join(fitted)
        if mode == "expanded":
            fitted = self._fit_proactive_history_lines(cleaned_lines, max_chars)
            return "\n".join(fitted)

        recent_lines = cleaned_lines[-recent_count:]
        older_lines = [_single_line(line, 160) for line in cleaned_lines[:-recent_count]]
        recent_header = "【最近对话（保留原文）】"
        recent_budget = max(0, max_chars - len(recent_header) - 1)
        fitted_recent = self._fit_proactive_history_lines(recent_lines, recent_budget)
        recent_block = recent_header
        if fitted_recent:
            recent_block += "\n" + "\n".join(fitted_recent)
        if not older_lines:
            return recent_block[:max_chars]

        older_header = "【较早对话（已压缩）】"
        remaining = max_chars - len(recent_block) - len(older_header) - 2
        fitted_older = self._fit_proactive_history_lines(older_lines, remaining)
        if not fitted_older:
            return recent_block[:max_chars]
        older_text = "\n".join(fitted_older)
        return f"{older_header}\n{older_text}\n{recent_block}"[:max_chars]

    async def _recent_private_conversation_for_proactive_review(
        self,
        user: dict[str, Any],
        *,
        limit: int = 10,
    ) -> str:
        umo = str(user.get("umo") or "").strip()
        lines: list[str] = []
        if umo:
            try:
                conv = await self._get_current_conversation_safely(umo, label="proactive_review_history_read")
                history = self._load_conversation_history_items(conv)
                for item in history[-max(1, limit):]:
                    line = self._format_history_item_for_summary(item)
                    if line:
                        lines.append(line)
            except Exception as exc:
                logger.debug("[PrivateCompanion] 主动润色读取私聊历史失败: %s", _single_line(exc, 120))
        if not lines:
            last_user = _single_line(user.get("last_user_message"), 180)
            last_bot = _single_line(user.get("last_companion_message"), 180)
            if last_bot:
                lines.append(f"{runtime_persona_setting(self, 'bot_name', '小星')}: {last_bot}")
            if last_user:
                lines.append(f"用户: {last_user}")
        return self._format_proactive_history_context(lines[-max(1, limit):])

    def _clean_persona_reference_rewrite_text(self, text: Any, *, limit: int = 160) -> str:
        cleaned = self._sanitize_proactive_text(str(text or ""))
        if not cleaned:
            return ""
        cleaned = _strip_internal_message_blocks(cleaned)
        cleaned = self._strip_parenthetical_stage_directions(cleaned)
        cleaned = re.sub(r"^(?:最终(?:聊天)?正文|正文|输出|回复)[:：]\s*", "", cleaned).strip()
        cleaned = re.sub(r"\s+", " ", cleaned).strip().strip('"').strip("'")
        if not cleaned:
            return ""
        forbidden = (
            "参考意图", "参考文案", "兜底", "模板", "系统", "提示词", "工具调用",
            "执行状态", "已发送给用户", "消息已发送", "发送成功", "无文字",
        )
        if any(token in cleaned for token in forbidden):
            return ""
        if self._framework_agent_meta_summary_leak(cleaned):
            return ""
        return _single_line(cleaned, limit)

    async def _rewrite_reference_reply_with_persona(
        self,
        reference_text: str,
        *,
        scene: str = "",
        user: dict[str, Any] | None = None,
        event: AstrMessageEvent | None = None,
        history: str = "",
        fallback_text: str = "",
        task: str = "persona_reference_rewrite",
        max_chars: int = 120,
        allow_fallback: bool = False,
        preserve_status: bool = False,
    ) -> str:
        reference = _single_line(reference_text, 420)
        if not reference:
            return _single_line(fallback_text, max_chars) if allow_fallback else ""
        umo = ""
        if event is not None:
            umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if not umo and isinstance(user, dict):
            umo = str(user.get("umo") or "").strip()
        persona = await self._resolve_proactive_persona_prompt(user, umo=umo)
        proactive_rewrite = str(task or "").startswith("proactive")
        if proactive_rewrite:
            reply_style = self._format_proactive_voice_prompt() if callable(getattr(self, "_format_proactive_voice_prompt", None)) else ""
            expression_voice = self._format_expression_voice_for_prompt(
                scope="proactive",
                target_id=_single_line(user.get("user_id") or user.get("id"), 80) if isinstance(user, dict) else "",
                context_owner=user if isinstance(user, dict) else None,
                stage_owner=user if isinstance(user, dict) else None,
            )
            if expression_voice:
                reply_style = f"{reply_style}\n\n{expression_voice}".strip()
        else:
            reply_style = self._format_reply_style_prompt()
        if not history and isinstance(user, dict):
            try:
                history_limit = self._proactive_history_limit("generation") if proactive_rewrite else 6
                history = await self._recent_private_conversation_for_proactive_review(user, limit=history_limit)
            except Exception:
                history = ""
        recipient_identity = self._format_proactive_recipient_identity_guard(
            user,
            _single_line(user.get("nickname"), 40) if isinstance(user, dict) else "",
        )
        creative_excerpt_rule = (
            "- 若参考意图包含创作原文且决定引用，只能连续摘取来源原文，并用一组成对的 `「...」` 包住；"
            "聊天式引入和收尾留在 `「」` 外，不得改写或另编作品片段。"
            if "创作" in str(scene or "")
            else ""
        )
        prompt = f"""
你要把一条“参考意图”改写成当前人格会自然说出的聊天正文。参考意图只说明要表达什么，不是要照抄的句子。

【当前人格】
{persona or "保持自然、简洁、有边界。"}

【{"主动开口风格" if proactive_rewrite else "回复风格"}】
{reply_style or "像日常聊天一样短一点，不要报告式。"}

【最近对话】
{history or "（无可用历史）"}

【当前收件人】
{recipient_identity or "当前收件人身份未知；不要猜测名字或套用人格中的专属称呼。"}

【场景】
{_single_line(scene, 180) or "普通聊天回执"}

【参考意图】
{reference}

要求：
- 只输出最终聊天正文，不要解释。
- 1 句，最多 2 句；尽量像这个人格平时聊天，不要像客服、公告或模板。
- 不要照抄参考意图里的固定说法；只保留事实和语义。
- 不要出现“参考/兜底/模板/系统/工具/执行/已发送给用户/消息已发送”等字样。
- 不要新增事实、承诺、动作小剧场或没有发生的状态。
{creative_excerpt_rule}
- 如果参考意图或模型结果包含 Provider/API 报错、内容策略拒绝、敏感词提示、政策链接或内部诊断，视为本轮失败并输出空文本；不要翻译、复述或润色这类内容。
{"- 必须保留成功/失败/等待/完成/稍后再说等状态语义，不要把失败说成成功。" if preserve_status else "- 如果只是轻轻递一句，不要补多余解释。"}
""".strip()
        try:
            raw = await self._llm_call(
                prompt,
                max_tokens=140,
                provider_id=self._task_provider(
                    getattr(self, "response_review_provider_id", ""),
                    getattr(self, "mai_style_provider_id", ""),
                    getattr(self, "llm_provider_id", ""),
                ),
                task=task,
            )
        except Exception as exc:
            logger.debug("[PrivateCompanion] 人格参考意图改写失败: %s", _single_line(exc, 120))
            raw = ""
        if self._looks_like_internal_provider_error_text(raw):
            logger.warning(
                "[PrivateCompanion] 人格参考意图改写收到 Provider 错误正文，已丢弃: task=%s",
                _single_line(task, 80) or "persona_reference_rewrite",
            )
            raw = ""
        cleaned = self._clean_persona_reference_rewrite_text(raw, limit=max_chars)
        if cleaned:
            return cleaned
        return _single_line(fallback_text, max_chars) if allow_fallback else ""

    def _local_proactive_send_decision(
        self,
        user: dict[str, Any],
        text: str,
        *,
        reason: str,
        action: str,
        motive: str = "",
        topic: str = "",
        action_context: str = "",
    ) -> dict[str, Any]:
        strength = self._proactive_review_strength()
        cleaned = _single_line(text, 500)
        if not cleaned:
            return {"decision": "drop", "reason": "主动消息为空", "hard": True}
        external_info_reasons = {"bili_video_share", "news_share", "web_exploration_share"}
        external_share_active = reason in external_info_reasons
        link_platform_mismatch = self._proactive_link_platform_mismatch_reason(cleaned)
        if link_platform_mismatch:
            if external_share_active:
                external_fix = self._external_share_source_consistency_decision(
                    user,
                    cleaned,
                    reason=reason,
                    topic=topic,
                    motive=motive,
                    action_context=action_context,
                )
                if external_fix:
                    return external_fix
            return {
                "decision": "drop",
                "reason": link_platform_mismatch,
                "hard": True,
            }
        if reason == "environment_change" and re.search(
            r"https?://|(?:^|[^A-Za-z0-9])BV[0-9A-Za-z]{8,16}(?:$|[^A-Za-z0-9])|《[^》\n]{1,120}》",
            cleaned,
            flags=re.I,
        ):
            return {
                "decision": "drop",
                "reason": "环境变化主动消息混入了文章、视频或旧链接来源",
                "hard": True,
            }
        wrong_address = self._wrong_proactive_recipient_address(
            cleaned,
            user,
            _single_line(user.get("nickname"), 40),
        )
        if wrong_address:
            repaired_text, repaired_address = self._repair_proactive_recipient_address(
                cleaned,
                user,
                _single_line(user.get("nickname"), 40),
            )
            if repaired_address:
                return {
                    "decision": "rewrite",
                    "reason": f"已把收件人称呼纠正为当前昵称：{repaired_address}",
                    "text": repaired_text,
                    "hard": True,
                }
            return {
                "decision": "drop",
                "reason": f"主动正文无法确认收件人称呼：{wrong_address}",
                "hard": True,
            }
        outbound_guard = self._validate_proactive_outbound_candidate(
            cleaned,
            reason=reason,
            action=action,
            source="review",
        )
        guard_decision = str(outbound_guard.get("decision") or "send")
        if guard_decision == "drop":
            return {
                "decision": "drop",
                "reason": _single_line(outbound_guard.get("reason"), 120) or "主动候选疑似内部泄漏",
                "hard": bool(outbound_guard.get("hard", True)),
            }
        if guard_decision == "rewrite":
            rewritten_guard_text = _single_line(outbound_guard.get("text"), 500)
            if rewritten_guard_text:
                return {
                    "decision": "rewrite",
                    "reason": _single_line(outbound_guard.get("reason"), 120) or "清理主动候选内部残留",
                    "text": rewritten_guard_text,
                }
            return {"decision": "drop", "reason": _single_line(outbound_guard.get("reason"), 120) or "主动候选只剩内部残留", "hard": True}
        fact_decision = self._unverified_proactive_fact_decision(
            cleaned,
            reason=reason,
            action=action,
            action_context=action_context,
        )
        if fact_decision:
            return fact_decision
        semantics: dict[str, Any] = {}
        semantic_getter = getattr(self, "_planned_proactive_semantics", None)
        if callable(semantic_getter):
            try:
                semantics = semantic_getter(user)
            except Exception:
                semantics = {}
        semantic_kind = _single_line(semantics.get("kind"), 40)
        semantic_anchor_type = _single_line(semantics.get("anchor_type"), 40)
        semantic_score = _safe_float(semantics.get("score"), 0.5)
        semantic_pressure = _safe_float(semantics.get("pressure"), 0.4)
        semantic_risk = _safe_float(semantics.get("risk"), 0.0)
        default_hard_risk = 0.70 if strength == "lenient" else 0.45
        hard_risk_threshold = max(
            0.0,
            min(
                1.0,
                _safe_float(
                    runtime_persona_setting(
                        self, "proactive_review_hard_risk_threshold", default_hard_risk
                    ),
                    default_hard_risk,
                ),
            ),
        )
        low_score_threshold = max(
            0.0,
            min(
                1.0,
                _safe_float(
                    runtime_persona_setting(self, "proactive_review_low_score_threshold", 0.34),
                    0.34,
                ),
            ),
        )
        pressure_threshold = max(
            0.0,
            min(
                1.0,
                _safe_float(
                    runtime_persona_setting(self, "proactive_review_pressure_threshold", 0.55),
                    0.55,
                ),
            ),
        )
        if semantic_risk >= hard_risk_threshold:
            return {
                "decision": "drop",
                "reason": f"候选语义风险偏高 risk={semantic_risk:.2f}/{hard_risk_threshold:.2f}",
                "hard": True,
            }
        if strength != "lenient" and semantic_score < low_score_threshold and semantic_pressure >= pressure_threshold:
            return {"decision": "defer", "reason": "候选由头偏虚且打扰压力高", "delay_minutes": 75}
        reply_like_openers = (
            "好呀", "好啊", "可以呀", "行啊", "那就", "你说呢", "要不", "刚看到", "才看到",
            "你来了", "你叫我", "你问", "我帮你查", "我去问", "我去说",
        )
        matched_reply_opener = next((token for token in reply_like_openers if cleaned.startswith(token)), "")
        if matched_reply_opener and not (external_share_active and matched_reply_opener in {"刚看到", "才看到"}):
            if strength != "strict":
                rewritten = re.sub(
                    r"^(?:好呀|好啊|可以呀|行啊|那就|你说呢|要不|刚看到|才看到|你来了|你叫我|你问|我帮你查|我去问|我去说)[，,。！!？?\s]*",
                    "",
                    cleaned,
                    count=1,
                ).strip()
                if rewritten and len(rewritten) >= 4:
                    return {"decision": "rewrite", "reason": "去掉回复式开头", "text": rewritten}
            return {"decision": "drop", "reason": "像是在回复刚发来的消息"}
        motive_leak_repaired = self._strip_proactive_motive_leak_text(cleaned)
        if motive_leak_repaired != cleaned:
            if motive_leak_repaired and len(motive_leak_repaired) >= 2:
                return {"decision": "rewrite", "reason": "去掉主动动机自述", "text": motive_leak_repaired}
            return {"decision": "defer", "reason": "主动消息只剩动机自述", "delay_minutes": 75}
        vague = ("想你了", "来看看你", "你在忙什么", "最近怎么样", "吃了吗", "辛苦了", "在吗", "忙不忙")
        if strength != "lenient" and reason in {"check_in", "quiet_care", "state_share"} and any(token in cleaned for token in vague):
            return {"decision": "defer", "reason": "普通主动过于泛泛", "delay_minutes": 60}
        if strength != "lenient" and semantic_kind in {"self_share", "external_share", "observation"} and any(token in cleaned for token in vague):
            return {"decision": "defer", "reason": "生成结果偏离分享型由头", "delay_minutes": 60}
        if external_share_active:
            external_fix = self._external_share_source_consistency_decision(
                user,
                cleaned,
                reason=reason,
                topic=topic,
                motive=motive,
                action_context=action_context,
            )
            if external_fix:
                return external_fix
        role = self._private_user_role(user) if isinstance(user, dict) else "friend"
        if role == "owner":
            social_checker = getattr(self, "_daily_plan_clause_has_named_message_interaction", None)
            has_cross_private_interaction = False
            if callable(social_checker):
                try:
                    has_cross_private_interaction = bool(social_checker(cleaned))
                except Exception:
                    has_cross_private_interaction = False
            if has_cross_private_interaction or any(token in cleaned for token in ("朋友那边", "朋友用户", "朋友私聊", "次要用户那边", "次要用户私聊")):
                return {"decision": "drop", "reason": "疑似混入其他私聊互动", "hard": True}
        if _safe_int(user.get("ignored_streak"), 0, 0) >= 1 and cleaned.count("？") + cleaned.count("?") >= 2:
            return {"decision": "rewrite", "reason": "未回应状态下问题太多", "text": re.split(r"[？?]", cleaned, maxsplit=1)[0].rstrip("，,。") + "。"}
        return {"decision": "send", "reason": "本地检查通过"}

    def _external_share_source_consistency_decision(
        self,
        user: dict[str, Any],
        text: str,
        *,
        reason: str = "",
        topic: str = "",
        motive: str = "",
        action_context: str = "",
    ) -> dict[str, Any] | None:
        cleaned = _single_line(text, 240)
        if not cleaned:
            return None
        source_text = self._external_share_anchor_text(
            user,
            reason=reason,
            topic=topic,
            motive=motive,
            action_context=action_context,
        )
        if not source_text:
            return {
                "decision": "drop",
                "reason": "外界分享缺少可见来源",
                "hard": True,
            }
        source_link_match = re.search(r"https?://[^\s；，。！？!?]+", source_text, flags=re.I)
        source_link = source_link_match.group(0).rstrip("）)】]》>。.") if source_link_match else ""
        expected_platform = self._external_share_platform_from_url(source_link)
        claimed_platform = self._external_share_claimed_platform(cleaned)
        platform_mismatch = bool(
            source_link
            and claimed_platform
            and (not expected_platform or claimed_platform != expected_platform)
        )
        if platform_mismatch:
            expected_label = expected_platform or "该网页来源"
            reference = self._external_share_fallback_reference(source_text)
            if reference:
                return {
                    "decision": "rewrite",
                    "reason": f"来源平台错配：链接属于{expected_label}，正文却写成{claimed_platform}",
                    "reference_text": reference,
                    "source_text": source_text,
                    "hard": True,
                }
            return {
                "decision": "drop",
                "reason": f"来源平台错配：应为{expected_label}而不是{claimed_platform}",
                "hard": True,
            }
        require_source_link = bool(
            runtime_persona_setting(self, "external_share_require_source_link", True)
        )
        if require_source_link and source_link and source_link not in cleaned:
            reference = self._external_share_fallback_reference(source_text)
            if reference:
                return {
                    "decision": "rewrite",
                    "reason": "外界分享正文遗漏真实来源链接",
                    "reference_text": reference,
                    "source_text": source_text,
                    "hard": True,
                }
        if self._external_share_text_mentions_source(cleaned, source_text):
            return None
        reference = self._external_share_fallback_reference(source_text)
        if reference:
            return {
                "decision": "rewrite",
                "reason": "外界分享正文偏离来源",
                "reference_text": reference,
                "source_text": source_text,
                "hard": True,
            }
        return {
            "decision": "defer",
            "reason": "外界分享缺少可承接来源",
            "delay_minutes": 75,
            "hard": True,
        }

    def _external_share_anchor_text(
        self,
        user: dict[str, Any],
        *,
        reason: str = "",
        topic: str = "",
        motive: str = "",
        action_context: str = "",
    ) -> str:
        parts: list[str] = []

        def add(value: Any, limit: int = 160) -> None:
            text = self._clean_external_share_source_field(value, limit)
            if text and text not in parts:
                parts.append(text)

        add(topic, 180)
        if action_context:
            for raw_line in str(action_context or "").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if self._looks_like_internal_provider_error_text(line):
                    continue
                if re.match(
                    r"^(?:标题|话题|摘要重点|搜索词|参考来源|来源|链接|UP|短评|回味|内部印象|留下的印象)[:：]",
                    line,
                    flags=re.I,
                ):
                    add(line, 220)
        if isinstance(user, dict):
            context_keys = {
                "bili_video_share": ("bilibili_video_context",),
                "news_share": ("news_context",),
                "web_exploration_share": ("web_exploration_context",),
            }.get(str(reason or "").strip(), ())
            for key in context_keys:
                payload = user.get(key)
                if not isinstance(payload, dict):
                    continue
                prefixed_fields = {
                    "topic": "话题",
                    "headline": "标题",
                    "title": "标题",
                    "source": "来源",
                    "selected_source": "来源",
                    "source_title": "参考来源",
                    "selected_link": "链接",
                    "source_url": "链接",
                    "link": "链接",
                    "url": "链接",
                }
                for field, prefix in prefixed_fields.items():
                    value = payload.get(field)
                    if value:
                        add(f"{prefix}：{value}", 180)
                for field in ("summary", "impression", "comment", "review", "bvid"):
                    add(payload.get(field), 180)
        if not parts:
            add(motive, 140)
        return _single_line("；".join(parts), 760)

    def _external_share_is_vague_pointer(self, text: str) -> bool:
        message = _single_line(text, 260)
        if not message:
            return False
        if re.search(r"https?://|(?:^|[^A-Za-z0-9])BV[0-9A-Za-z]{8,16}(?:$|[^A-Za-z0-9])", message):
            return False
        if re.search(r"[《“\"『「][^》”\"』」]{2,80}[》”\"』」]", message):
            return False
        compact = re.sub(r"[\s，,。！？!?、~～…]+", "", message)
        vague_patterns = (
            "你快看这个",
            "快看这个",
            "看这个",
            "你看看这个",
            "看看这个",
            "给你看个东西",
            "刷到个东西",
            "这个也太",
            "这个太",
            "这个好",
            "这条也太",
            "这条太",
            "这也太",
            "居然这么",
        )
        if any(pattern in compact for pattern in vague_patterns):
            return True
        if len(compact) <= 26 and any(token in compact for token in ("这个", "这条", "那条", "东西")) and any(
            token in compact for token in ("离谱", "逆天", "好笑", "绷不住", "惊了", "怪")
        ):
            return True
        return False

    def _external_share_text_mentions_source(self, text: str, source_text: str) -> bool:
        message = _single_line(text, 260).lower()
        source = _single_line(source_text, 760).lower()
        if not message or not source:
            return False
        if self._looks_like_internal_provider_error_text(message):
            return False
        if self._external_share_is_vague_pointer(message):
            return False
        anchor_tokens = self._external_share_anchor_tokens(source)
        for token in anchor_tokens:
            if token and token in message:
                return True
        return False

    def _external_share_anchor_tokens(self, source_text: str) -> list[str]:
        text = _single_line(source_text, 760)
        if not text:
            return []
        tokens: list[str] = []

        def add(value: str) -> None:
            clean = value.strip(" \t\r\n，。！？；：、,.!?;:()（）[]【】《》“”\"'")
            if len(clean) >= 2 and clean not in tokens:
                tokens.append(clean)

        for item in re.findall(r"[A-Za-z]+[-_A-Za-z0-9]*|[0-9]+(?:多年|年|月|日|次|个|%)?", text):
            add(item.lower())
        for chunk in re.split(r"[\s，。！？；：、,.!?;:|｜/\\\\()（）\\[\\]【】《》“”\"']+", text):
            chunk = chunk.strip()
            if not chunk:
                continue
            if re.fullmatch(r"[\u4e00-\u9fff]{2,12}", chunk):
                add(chunk)
                if len(chunk) > 4:
                    for size in (4, 3, 2):
                        for index in range(0, max(0, len(chunk) - size + 1)):
                            add(chunk[index:index + size])
            elif re.search(r"[\u4e00-\u9fff]", chunk):
                for item in re.findall(r"[\u4e00-\u9fff]{2,8}", chunk):
                    add(item)
        generic = {
            "标题", "视频", "新闻", "文章", "资料", "来源", "分享", "短评", "回味", "评分", "链接",
            "这个", "这条", "那条", "那个", "东西", "内容", "感觉", "有点", "刚刚", "刚才",
            "离谱", "逆天", "好笑", "有趣", "震惊", "惊了", "神奇", "奇怪", "贴", "轻轻",
            "刚刷到一个视频", "刚刷到", "刷到一", "到一个", "一个视", "个视频", "一个视频",
            "b站视频分享线索", "站视频分享线索", "视频分享线索", "分享线索",
            "新闻阅读线索", "阅读线索", "刚扫过", "扫过几", "几条新", "条新闻",
            "网页探索线索", "探索线索", "内部探索笔记", "探索笔记",
            "http", "https", "www", "com", "cn", "bilibili", "video",
        }
        generic_phrases = (
            "b站视频分享线索刚刷到一个视频",
            "新闻阅读线索刚扫过几条新闻其中一条让自己有点想私下提一句",
            "网页探索线索bot刚刚按自己的兴趣主动搜索并了解了一点新东西这是一条内部探索笔记",
            "表达要求不要像播报新闻不要夸大或补充未知事实",
        )
        return [
            token
            for token in tokens
            if token not in generic and not any(token in phrase for phrase in generic_phrases)
        ][:24]

    def _external_share_fallback_reference(self, source_text: str) -> str:
        source = _single_line(source_text, 760)
        if not source:
            return ""
        title = ""
        link = ""
        link_match = re.search(r"https?://[^\s；，。！？!?]+", source, flags=re.I)
        if link_match:
            link = _single_line(link_match.group(0).rstrip("）)】]》>。."), 220)
        source_platform = self._external_share_platform_from_url(link)
        bvid_match = re.search(r"\bBV[0-9A-Za-z]{8,16}\b", source)
        if not link and bvid_match:
            link = f"https://www.bilibili.com/video/{bvid_match.group(0)}"
        reference_match = re.search(r"(?:参考来源|source_title)[:：]\s*([^；。\n\r|｜]{2,90})", source, flags=re.I)
        if reference_match:
            title = _single_line(reference_match.group(1), 64)
        book_match = re.search(r"[《“\"『「]([^》”\"』」]{2,90})[》”\"』」]", source)
        if not title and book_match:
            title = _single_line(book_match.group(1), 64)
        for pattern in (
            r"(?:标题|摘要重点|话题|参考来源|source_title|headline|topic)[:：]\s*([^；。\n\r|｜]{2,90})",
            r"^([^；。\n\r]{4,90})",
        ):
            if title:
                break
            match = re.search(pattern, source, flags=re.I)
            if match:
                title = _single_line(match.group(1), 64)
                title = re.split(
                    r"\s+(?:链接|UP|评分|心情|短评|回味|来源|内部印象|表达气质|额外边界|参考来源|搜索词)[:：]",
                    title,
                    maxsplit=1,
                    flags=re.I,
                )[0]
                break
        if not title:
            if link:
                return _single_line(link, 260)
            return ""
        title = title.strip(" ，。！？；：、,.!?;:|｜")
        if not title:
            if link:
                return _single_line(link, 260)
            return ""
        if self._looks_like_internal_provider_error_text(title):
            return ""
        impression_match = re.search(
            r"(?:留下的印象|内部印象|短评|回味)[:：]\s*([^；\n\r]{4,70})",
            source,
            flags=re.I,
        )
        impression = _single_line(impression_match.group(1), 42).rstrip("。！？!?；;，,") if impression_match else ""
        impression = re.sub(r"让人", "让我", impression)
        if source_platform:
            base = f"刚在{source_platform}刷到“{title}”"
        else:
            base = f"刚看到“{title}”这条内容"
        if impression and impression != title and len(base) + len(impression) <= 96:
            base = f"{base}，{impression}"
        else:
            base = f"{base}，有点想给你看看"
        if link:
            base = f"{base}。{link}"
        else:
            base = f"{base}。"
        return _single_line(base, 300)

    def _strip_proactive_motive_leak_text(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        units: list[str] = []
        for line in cleaned.splitlines() or [cleaned]:
            units.extend(self._split_proactive_sentence_units(line))
        if not units:
            units = [cleaned]

        leak_unit_patterns = (
            r"(?:怕|担心)[^。！？\n]{0,16}(?:太早|太晚|打扰|吵到|烦到)",
            r"(?:先|又|就)?(?:收住|忍住|憋住|忍了一下|放了一会)[^。！？\n]{0,20}",
            r"(?:结果|后来)?[^。！？\n]{0,12}(?:绕了一圈|转了一圈|想了半天)[^。！？\n]{0,24}(?:来找你|找你|说出口)",
            r"(?:还是|又|最后|结果)[^。！？\n]{0,12}(?:来找你|找你|跑来找你|过来找你)[啦了啊呀]*",
            r"(?:没什么事|没有别的事|也没什么)[^。！？\n]{0,18}(?:就是|只是)?想(?:来)?(?:找你|跟你说话|和你说话|说一句)",
        )
        leak_clause_patterns = (
            r"[，,、\s]*(?:刚[^，。！？\n]{0,24})?(?:就|还是)?想(?:先)?(?:跟|和)?你(?:说早安|说早|说一句|说点什么|打个招呼|聊两句|说话)[^，。！？\n]*",
            r"[，,、\s]*(?:中午|晚上|早上|这会儿|刚才|刚刚)?[^，。！？\n]{0,18}(?:就|又|还是)?想(?:顺手)?(?:来)?(?:找你|跟你打个照面|和你打个照面|往你这边冒个头)[^，。！？\n]*",
            r"[，,、\s]*(?:刚刚|刚才|这会儿|今天|明明|还是)?[^，。！？\n]{0,24}想(?:和|给|问|提醒|确认|看看)?用户[^，。！？\n]*",
            r"[，,、\s]*(?:怕|担心)[^，。！？\n]{0,16}(?:太早|太晚|打扰|吵到|烦到)[^，。！？\n]*",
            r"[，,、\s]*(?:就)?先(?:收住|忍住|憋住)[^，。！？\n]*",
            r"[，,、\s]*(?:结果|后来)?[^，。！？\n]{0,12}(?:绕了一圈|转了一圈|想了半天)[^，。！？\n]*",
            r"[，,、\s]*莫名觉得[^，。！？\n]*",
            r"[，,、\s]*(?:顺手)(?:丢给你|放这儿|递给你|想起|分享一下|分享一下)[^，。！？\n]*",
            r"[，,、\s]*(?:多看一眼|也会留意这个|也会看一眼)[^，。！？\n]*",
            r"[，,、\s]*(?:只)?轻轻(?:提一句|提醒[^，。！？\n]*|说声|补上一句)[^，。！？\n]*",
            r"[，,、\s]*想(?:短短|轻轻)(?:说一句|提一句|说句话|提一声|说一下|打声招呼)[^，。！？\n]*",
            r"[，,、\s]*感觉和[^，。！？\n]{0,20}有点贴[^，。！？\n]*",
            r"[，,、\s]*想跟你说一句[^，。！？\n]*",
        )
        kept: list[str] = []
        changed = False
        for raw_unit in units:
            unit = str(raw_unit or "").strip()
            if not unit:
                continue
            if any(re.search(pattern, unit) for pattern in leak_unit_patterns):
                changed = True
                continue
            repaired = unit
            for pattern in leak_clause_patterns:
                repaired, count = re.subn(pattern, "", repaired)
                changed = changed or count > 0
            repaired = repaired.strip(" ，,、。！？!?；;")
            if repaired:
                kept.append(self._ensure_chat_sentence_punctuation(repaired))
            elif repaired != unit:
                changed = True
        if not changed:
            return cleaned
        return "\n".join(kept)[:260].strip()

    def _proactive_review_strength(self) -> str:
        strength = str(
            runtime_persona_setting(self, "proactive_review_strength", "lenient") or "lenient"
        ).strip().lower()
        return strength if strength in {"lenient", "balanced", "strict"} else "lenient"

    def _effective_proactive_review_mode(self) -> str:
        mode = str(
            runtime_persona_setting(self, "proactive_review_mode", "full") or "full"
        ).strip().lower()
        return mode if mode in {"local_only", "severe_only", "full"} else "full"

    @staticmethod
    def _proactive_review_hard_block_reason(reason: str) -> bool:
        text = str(reason or "")
        if not text:
            return False
        markers = (
            "隐私", "泄露", "越界", "风险", "危险", "敏感", "违规", "骚扰", "威胁",
            "其他私聊", "朋友私聊", "混入", "承诺工具", "承诺发图", "承诺语音", "承诺查询",
            "系统动作", "发送状态", "状态汇报", "工具执行", "工具结果", "工具回执",
            "执行回执", "发送回执", "系统回执", "不是角色真正",
        )
        return any(marker in text for marker in markers)

    def _balanced_proactive_defer_release_reason(
        self,
        user: dict[str, Any],
        *,
        note: str = "",
        now: float | None = None,
    ) -> str:
        if not isinstance(user, dict):
            return ""
        note_text = _single_line(note, 120)
        generic_defer = any(
            token in note_text
            for token in ("刚结束", "稍后", "稍候", "时机", "自然", "突兀", "间隔", "不合适")
        )
        if not generic_defer:
            return ""
        today = _today_key()
        sent_today = _safe_int(user.get("sent_today"), 0) if str(user.get("sent_day") or "") == today else 0
        if sent_today > 0:
            return ""
        last_sent_at = max(
            _safe_float(user.get("last_proactive_sent_at"), 0),
            _safe_float(user.get("last_sent"), 0),
        )
        if last_sent_at > 0 and datetime.fromtimestamp(last_sent_at).strftime("%Y-%m-%d") == today:
            return ""
        check_now = _now_ts() if now is None else now
        now_dt = datetime.fromtimestamp(check_now)
        if now_dt.hour * 60 + now_dt.minute < 10 * 60 + 30:
            return ""
        idle_getter = getattr(self, "_effective_user_idle_minutes", None)
        try:
            idle_minutes = (
                idle_getter(user)
                if callable(idle_getter)
                else _safe_int(runtime_persona_setting(self, "idle_minutes", 20), 20)
            )
        except Exception:
            idle_minutes = _safe_int(runtime_persona_setting(self, "idle_minutes", 20), 20)
        recent_private_at = max(
            _safe_float(user.get("last_user_message_at"), 0),
            _safe_float(user.get("last_private_seen"), 0),
        )
        if recent_private_at > 0 and check_now - recent_private_at < max(10, min(60, idle_minutes)) * 60:
            return ""
        return "今日尚无主动且候选非硬风险，标准强度低频放行"

    @staticmethod
    def _proactive_review_elapsed_text(seconds: float) -> str:
        if seconds < 0:
            return "未知"
        if seconds < 90:
            return "刚刚"
        if seconds < 3600:
            return f"约{max(1, int(seconds // 60))}分钟"
        if seconds < 86400:
            return f"约{max(1, int(seconds // 3600))}小时"
        return f"约{max(1, int(seconds // 86400))}天"

    @staticmethod
    def _proactive_has_verified_recent_fact_source(
        *,
        reason: str,
        action: str,
        action_context: str = "",
    ) -> bool:
        source_reasons = {
            "bili_video_share",
            "news_share",
            "web_exploration_share",
            "creative_share",
            "jm_cosmos_share",
            "jm_cosmos_recommendation_request",
            "weather_alert",
            "goodnight_screen_check",
        }
        if str(reason or "").strip() in source_reasons:
            return True
        context = str(action_context or "")
        if str(reason or "").strip() == "group_share" and "群聊分享线索" in context:
            return True
        if re.search(r"(?:真实图片文件|图片路径|真实动作结果|工具结果|来源链接|https?://)", context, re.I):
            return True
        return str(action or "message").strip() not in {"", "message", "photo_text"} and bool(_single_line(context, 240))

    def _unverified_proactive_fact_decision(
        self,
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str = "",
    ) -> dict[str, Any] | None:
        if self._proactive_has_verified_recent_fact_source(
            reason=reason,
            action=action,
            action_context=action_context,
        ):
            return None
        recent_self_action = re.compile(
            r"(?:我\s*)?(?:刚刚|刚才|方才|刚|才)\s*"
            r"(?:刷到|刷了|看到|看见|听到|听见|读到|发现|碰到|遇到|收到|"
            r"买了|拍了|做了|画了|写了|吃了|喝了|回到|到家|出门|回来)"
        )
        stale_meal_attribution = re.compile(
            r"你[^。！？!?；;…~～]{0,12}(?:昨天|昨晚)[^。！？!?；;…~～]{0,16}"
            r"(?:吃的|点的|喝的|吃了|点了|喝了)"
        )
        unsafe_units: list[str] = []
        safe_units: list[str] = []
        for unit in self._split_proactive_sentence_units(text):
            recent_claim = bool(recent_self_action.search(unit))
            stale_claim = reason in {"meal_care", "meal_care_followup"} and bool(stale_meal_attribution.search(unit))
            if recent_claim or stale_claim:
                unsafe_units.append(unit)
            else:
                safe_units.append(unit)
        if not unsafe_units:
            return None
        repaired = " ".join(safe_units).strip()
        if repaired and len(re.sub(r"\s+", "", repaired)) >= 4:
            return {
                "decision": "rewrite",
                "reason": "已移除无真实来源的近期动作或旧饮食归因",
                "text": repaired,
                "hard": True,
            }
        return {
            "decision": "drop",
            "reason": "主动正文依赖无真实来源的近期动作或旧饮食归因",
            "hard": True,
        }

    def _format_proactive_review_runtime_context(self, user: dict[str, Any], *, now: float | None = None) -> str:
        check_now = _now_ts() if now is None else now
        now_dt = datetime.fromtimestamp(check_now)
        today = _today_key()
        sent_today = _safe_int(user.get("sent_today"), 0) if str(user.get("sent_day") or "") == today else 0
        last_sent_at = max(
            _safe_float(user.get("last_proactive_sent_at"), 0),
            _safe_float(user.get("last_sent"), 0),
        )
        activity_getter = getattr(self, "_latest_private_user_activity_ts", None)
        try:
            last_private_at = activity_getter(user) if callable(activity_getter) else 0
        except Exception:
            last_private_at = 0
        if last_private_at <= 0:
            last_private_at = max(
                _safe_float(user.get("last_user_message_at"), 0),
                _safe_float(user.get("last_private_seen"), 0),
            )
        idle_getter = getattr(self, "_effective_user_idle_minutes", None)
        interval_getter = getattr(self, "_effective_user_min_interval_minutes", None)
        try:
            idle_minutes = (
                idle_getter(user)
                if callable(idle_getter)
                else _safe_int(runtime_persona_setting(self, "idle_minutes", 20), 20)
            )
        except Exception:
            idle_minutes = _safe_int(runtime_persona_setting(self, "idle_minutes", 20), 20)
        try:
            min_interval = (
                interval_getter(user)
                if callable(interval_getter)
                else _safe_int(runtime_persona_setting(self, "min_interval_minutes", 80), 80)
            )
        except Exception:
            min_interval = _safe_int(
                runtime_persona_setting(self, "min_interval_minutes", 80), 80
            )
        private_elapsed = check_now - last_private_at if last_private_at > 0 else -1
        sent_elapsed = check_now - last_sent_at if last_sent_at > 0 else -1
        return "\n".join(
            part
            for part in (
                f"当前判定时间：{now_dt.strftime('%Y-%m-%d %H:%M')}",
                f"今天已成功主动：{sent_today} 条",
                f"距用户上次私聊活动：{self._proactive_review_elapsed_text(private_elapsed)}；普通主动要求空闲约 {max(0, int(idle_minutes))} 分钟",
                f"距上次主动发送：{self._proactive_review_elapsed_text(sent_elapsed)}；普通主动最小间隔约 {max(0, int(min_interval))} 分钟",
                f"上次主动内容：{_single_line(user.get('last_proactive_message'), 120)}" if user.get("last_proactive_message") else "",
            )
            if part
        )

    def _stale_proactive_review_defer_release_reason(
        self,
        user: dict[str, Any],
        *,
        note: str = "",
        reason: str = "",
        now: float | None = None,
    ) -> str:
        note_text = _single_line(note, 120)
        reason_key = _single_line(reason, 40).lower()
        time_sensitive_reasons = {
            "morning_greeting": 12 * 60,
            "noon_greeting": 15 * 60,
            "evening_greeting": 23 * 60 + 30,
            "environment_change": 120,
            "creative_share": 180,
            "reminder": 180,
            "meal_care": 120,
            "meal_care_followup": 120,
            "weather_alert": 180,
        }
        if not note_text and not reason_key:
            return ""
        check_now = _now_ts() if now is None else now
        if reason_key in time_sensitive_reasons:
            window_start = _safe_float(user.get("planned_proactive_window_start_at"), 0)
            expire_at = _safe_float(user.get("planned_proactive_expire_at"), 0)
            if expire_at > 0 and check_now >= expire_at:
                return "主动候选的有效窗口已结束，放弃过期复核结果并重新编排"
            if window_start > 0 and check_now - window_start >= time_sensitive_reasons[reason_key] * 60:
                return "主动候选已超过当前场景有效期，放弃过期复核结果并重新编排"
        if not re.search(r"(早安|今早|早上|睡前|晚安)", note_text):
            return ""
        now_minutes = datetime.fromtimestamp(check_now).hour * 60 + datetime.fromtimestamp(check_now).minute
        stale_after = 9 * 60 if ("睡前" in note_text or "晚安" in note_text) else 12 * 60
        if now_minutes < stale_after:
            return ""
        recent_private_at = max(
            _safe_float(user.get("last_user_message_at"), 0),
            _safe_float(user.get("last_private_seen"), 0),
        )
        if recent_private_at > 0 and check_now - recent_private_at < 45 * 60:
            return ""
        return "复核理由沿用了过期早间/睡前语境，已改按当前运行态放行"

    @staticmethod
    def _proactive_rewrite_blacklist_reason(text: str) -> str:
        """Reject structured model leakage without blocking ordinary chat words."""
        candidate = str(text or "")
        if not candidate:
            return ""
        patterns = (
            (r"```(?:json|python|javascript|text)?\s*", "改写残留代码块"),
            (r"\{[^{}\n]{0,600}(?:[\"'](?:decision|text|reason|status|tool|delay_minutes)[\"']\s*:)", "改写残留结构化 JSON"),
            (r"(?:作为(?:一个)?(?:AI|人工智能|语言模型)|我是(?:一个)?(?:AI|语言模型))", "改写暴露模型身份"),
            (r"(?:系统提示|系统消息|提示词泄漏|模型输出|模型回复|工具调用|调用工具|主动消息复核|附加组件(?:发送|列表))", "改写残留内部流程"),
            (r"[\"'](?:decision|text|reason|delay_minutes|planned_reason)[\"']\s*:", "改写残留 JSON 字段"),
        )
        for pattern, reason in patterns:
            if re.search(pattern, candidate, re.IGNORECASE | re.DOTALL):
                return reason
        return ""

    def _accept_proactive_rewrite(
        self,
        text: str,
        *,
        original_text: str = "",
        user: dict[str, Any] | None = None,
        reason: str = "",
        action: str = "",
        topic: str = "",
        motive: str = "",
        action_context: str = "",
        image_path: str = "",
    ) -> str | None:
        """Run the single acceptance chain shared by every proactive rewrite."""
        candidate = self._sanitize_action_boundaries(
            self._sanitize_proactive_text(str(text or "")),
            reason=reason,
            action=action,
            action_context=action_context,
            has_real_image=bool(image_path)
            or "真实图片文件：" in str(action_context or "")
            or "图片路径：" in str(action_context or ""),
        )
        candidate = self._normalize_proactive_sentence_flow(candidate)
        if not candidate:
            return None
        if re.fullmatch(
            r"[嗯哦唔呃诶欸啊呀哎噢喔哈]+[。！？!?…~～]*",
            re.sub(r"\s+", "", candidate),
        ):
            return None
        current_user = user if isinstance(user, dict) else {}
        recipient_name = _single_line(current_user.get("nickname"), 40)
        candidate, _ = self._repair_proactive_recipient_address(
            candidate,
            current_user,
            recipient_name,
        )
        if not candidate or self._wrong_proactive_recipient_address(candidate, current_user, recipient_name):
            return None
        meta_leak_checker = getattr(self, "_response_review_meta_leak_reason", None)
        if callable(meta_leak_checker) and meta_leak_checker(candidate):
            return None
        if self._framework_agent_meta_summary_leak(candidate):
            return None
        original_length = len(str(original_text or "").strip())
        if original_length and len(candidate) > max(original_length + 60, 240):
            return None
        if self._proactive_rewrite_blacklist_reason(candidate):
            return None
        if reason in {"bili_video_share", "news_share", "web_exploration_share"}:
            if self._external_share_source_consistency_decision(
                current_user,
                candidate,
                reason=reason,
                topic=topic,
                motive=motive,
                action_context=action_context,
            ):
                return None
        return candidate

    def _normalize_proactive_review_decision_policy(
        self,
        user: dict[str, Any],
        payload: dict[str, Any],
        *,
        strength: str,
        source: str = "model",
        reason: str = "",
        action: str = "",
        topic: str = "",
        motive: str = "",
        action_context: str = "",
        image_path: str = "",
        original_text: str = "",
    ) -> dict[str, Any]:
        """Normalize the final proactive content gate result."""
        if not isinstance(payload, dict):
            return {"decision": "send", "reason": "empty review result; local safety gate allowed the message"}
        decision = str(payload.get("decision") or "send").strip().lower()
        note = _single_line(payload.get("reason"), 120)
        reviewed_text = str(payload.get("text") or "").strip()
        delay_minutes = max(5, min(240, _safe_int(payload.get("delay_minutes"), 60, 5, 240)))
        if decision not in {"send", "rewrite", "defer", "drop"}:
            decision = "send"
        if decision == "rewrite" and not reviewed_text:
            decision = "drop"
            note = _single_line(f"{note or 'rewrite result is empty'}; candidate dropped", 120)
        if decision == "rewrite" and reviewed_text:
            accepted_text = self._accept_proactive_rewrite(
                reviewed_text,
                original_text=original_text,
                user=user,
                reason=reason,
                action=action,
                topic=topic,
                motive=motive,
                action_context=action_context,
                image_path=image_path,
            )
            if accepted_text is None:
                decision = "drop"
                reviewed_text = ""
                note = _single_line(f"{note or '改写未通过统一验收'}; 最终改写已拒绝", 120)
            else:
                reviewed_text = accepted_text
        return {
            "decision": decision,
            "text": reviewed_text if decision == "rewrite" else "",
            "reason": note or "proactive final content gate",
            "hard": bool(payload.get("hard")),
            "delay_minutes": delay_minutes if decision == "defer" else 0,
        }

    async def _review_proactive_message_send_decision(
        self,
        user: dict[str, Any],
        text: str,
        *,
        reason: str,
        action: str,
        motive: str = "",
        topic: str = "",
        action_summary: str = "",
        image_path: str = "",
    ) -> dict[str, Any]:
        strength = self._proactive_review_strength()
        route_getter = getattr(self, "_proactive_route_for", None)
        route = (
            route_getter(
                reason=reason,
                source=user.get("planned_proactive_source"),
                semantic_kind=user.get("planned_proactive_semantic_kind"),
                kind=user.get("planned_proactive_kind"),
            )
            if callable(route_getter)
            else PROACTIVE_ROUTE_REGISTRY.route_for(
                reason=reason,
                source=user.get("planned_proactive_source"),
                semantic_kind=user.get("planned_proactive_semantic_kind"),
                kind=user.get("planned_proactive_kind"),
            )
        )
        review_context = _single_line(action_summary, 240)
        if image_path:
            review_context = _single_line(f"{review_context}\n真实图片文件：{image_path}", 360)

        def normalize_review_result(payload: dict[str, Any], *, source: str) -> dict[str, Any]:
            return self._normalize_proactive_review_decision_policy(
                user,
                payload,
                strength=strength,
                source=source,
                reason=reason,
                action=action,
                topic=topic,
                motive=motive,
                action_context=review_context,
                image_path=image_path,
                original_text=text,
            )

        def local_model_fallback(fallback_reason: str) -> dict[str, Any]:
            result = normalize_review_result(local, source="local")
            result["review_fallback"] = True
            result["review_fallback_reason"] = _single_line(fallback_reason, 180)
            return result

        local = self._local_proactive_send_decision(
            user,
            text,
            reason=reason,
            action=action,
            motive=motive,
            topic=topic,
            action_context=review_context,
        )
        local_decision = str(local.get("decision") or "send").strip().lower()
        local_hard_block = bool(local.get("hard")) or self._proactive_review_hard_block_reason(_single_line(local.get("reason"), 120))
        if route.key == "transactional" and local_decision in {"drop", "defer"} and not local_hard_block:
            local = {
                "decision": "send",
                "text": "",
                "reason": "事务路线保留原始提醒事实，忽略通用低价值软拦截",
            }
            local_decision = "send"
        review_enabled = bool(
            runtime_persona_setting(self, "enable_proactive_message_review", True)
        )
        review_mode = self._effective_proactive_review_mode()
        if not review_enabled:
            local_mode_label = "主动发送前审核未启用"
            if local_decision in {"drop", "defer"}:
                if local_hard_block:
                    return normalize_review_result(local, source="local")
                return {
                    "decision": "send",
                    "text": "",
                    "reason": f"{local_mode_label}，已跳过非安全性的本地软拦截",
                }
            if local_decision == "rewrite":
                local_rewrite_text = str(local.get("text") or "").strip()
                if local_rewrite_text:
                    local_result = normalize_review_result(local, source="local")
                    local_result["reason"] = _single_line(
                        f"{local_mode_label}，已采用本地确定性改写："
                        + (_single_line(local.get("reason"), 80) or "轻量清理"),
                        120,
                    )
                    return local_result
                if not local_hard_block:
                    return {
                        "decision": "send",
                        "text": "",
                        "reason": f"{local_mode_label}，本地软建议未形成确定改写，保留原文",
                    }
                return {
                    "decision": "drop",
                    "text": "",
                    "reason": f"{local_mode_label}，本地检查仅能提供参考意图，无法形成确定正文，已取消本轮发送",
                    "hard": True,
                }
            return {
                "decision": "send",
                "text": "",
                "reason": f"{local_mode_label}，本地检查允许原文发送",
            }
        if review_mode == "local_only":
            local_mode_label = "仅本地检查模式"
            if local_decision in {"drop", "defer"}:
                return normalize_review_result(local, source="local")
            if local_decision == "rewrite":
                local_rewrite_text = str(local.get("text") or "").strip()
                if local_rewrite_text:
                    local_result = normalize_review_result(local, source="local")
                    local_result["reason"] = _single_line(
                        f"{local_mode_label}，已采用本地确定性改写："
                        + (_single_line(local.get("reason"), 80) or "轻量清理"),
                        120,
                    )
                    return local_result
                return {
                    "decision": "drop",
                    "text": "",
                    "reason": f"{local_mode_label}，本地检查仅能提供参考意图，无法形成确定正文，已取消本轮发送",
                    "hard": True,
                }
            return {
                "decision": "send",
                "text": "",
                "reason": f"{local_mode_label}，本地检查允许原文发送",
            }
        if local_decision in {"drop", "defer"} and local_hard_block:
            return normalize_review_result(local, source="local")
        if local.get("decision") == "rewrite" and str(local.get("reference_text") or "").strip():
            rewrite_scene = _single_line(
                "自然地向用户分享自己刚看的这条内容；保留真实标题、来源和链接"
                if reason in {"bili_video_share", "news_share", "web_exploration_share"}
                else f"主动消息改写；reason={reason or 'check_in'}；action={action or 'message'}",
                180,
            )
            rewritten_reference = await self._rewrite_reference_reply_with_persona(
                str(local.get("reference_text") or ""),
                scene=rewrite_scene,
                user=user,
                fallback_text="",
                task="proactive_reference_rewrite",
                max_chars=140,
                allow_fallback=False,
            )
            if rewritten_reference:
                accepted_reference = self._accept_proactive_rewrite(
                    rewritten_reference,
                    original_text=str(local.get("reference_text") or ""),
                    user=user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                    action_context=review_context,
                    image_path=image_path,
                )
                if accepted_reference is None:
                    safe_reference = _single_line(local.get("reference_text"), 300)
                    accepted_reference = self._accept_proactive_rewrite(
                        safe_reference,
                        original_text=safe_reference,
                        user=user,
                        reason=reason,
                        action=action,
                        topic=topic,
                        motive=motive,
                        action_context=review_context,
                        image_path=image_path,
                    )
                    if accepted_reference:
                        logger.info(
                            "[PrivateCompanion] 主动外界分享人格润色未通过统一验收，已使用确定性来源文本: reason=%s",
                            reason,
                        )
                rewritten_reference = accepted_reference or ""
            if rewritten_reference:
                local = dict(local)
                local["text"] = rewritten_reference
                local.pop("reference_text", None)
            else:
                return {
                    "decision": "drop",
                    "reason": _single_line(local.get("reason"), 80) or "兜底参考意图未能按人格改写",
                    "hard": True,
                }
        if local.get("decision") == "rewrite" and bool(local.get("hard")):
            return normalize_review_result(local, source="local")
        if local_decision == "drop":
            return normalize_review_result(local, source="local")
        if review_mode == "severe_only" and local_decision == "send" and not local_hard_block:
            return {
                "decision": "send",
                "text": "",
                "reason": "主动终审严重问题模式：本地检查通过",
            }
        persona = await self._resolve_proactive_persona_prompt(user)
        history = await self._recent_private_conversation_for_proactive_review(
            user,
            limit=self._proactive_history_limit("review"),
        )
        intent_hint = self._format_proactive_generation_intent_hint(
            user,
            reason=reason,
            action=action,
            motive=motive,
            action_context=review_context,
        )
        proactive_voice = self._format_proactive_voice_prompt() if callable(getattr(self, "_format_proactive_voice_prompt", None)) else ""
        expression_formatter = getattr(self, "_format_expression_voice_for_prompt", None)
        expression_voice = (
            expression_formatter(
                scope="proactive",
                target_id=_single_line(user.get("user_id") or user.get("id"), 80),
                context_owner=user,
                stage_owner=user,
            )
            if callable(expression_formatter)
            else ""
        )
        recipient_identity = self._format_proactive_recipient_identity_guard(
            user,
            _single_line(user.get("nickname"), 40),
        )
        runtime_context = self._format_proactive_review_runtime_context(user)
        troubleshooting_hint = self._proactive_troubleshooting_request_hint(user)
        has_verified_fact_source = self._proactive_has_verified_recent_fact_source(
            reason=reason,
            action=action,
            action_context=review_context,
        )
        fact_source_context = (
            f"本轮存在可核验动作/来源：{review_context}"
            if has_verified_fact_source
            else "本轮没有可核验的近期动作或外部来源；不得声称自己刚刚看见、刷到、听到、收到或完成了某件事。"
        )
        local_context = "；".join(
            part
            for part in (
                f"本地结论={local_decision or 'send'}",
                f"说明={_single_line(local.get('reason'), 100)}" if local.get("reason") else "",
                "硬风险=yes" if local_hard_block else "硬风险=no",
                f"本地建议文本={_single_line(local.get('text'), 120)}" if local.get("text") else "",
            )
            if part
        )
        creative_excerpt_rule = (
            self._creative_share_excerpt_prompt_hint()
            if reason == "creative_share"
            else ""
        )
        route_review_directive = route.review_directive()
        persona_context = (
            "(Creative-share compact review: use the proactive voice and excerpt rule below; do not restate the full persona.)"
            if reason == "creative_share"
            else self._truncate_proactive_context(persona, 2600)
        ) if persona else "(No explicit persona was resolved. Preserve the candidate instead of inventing a new voice.)"
        prompt = f"""
You are the final content gate immediately before one proactive private message is sent.
Return JSON only. You must decide exactly one of send, rewrite, or drop.

Decision contract:
- send: the candidate is natural, persona-consistent, useful now, and ready to send unchanged. Leave text empty.
- rewrite: the message still has a concrete reason to exist, but needs a small rewrite to sound natural in this exact conversation. text must be the complete sendable final message.
- drop: do not send this candidate. Use it for weak, generic, intrusive, fabricated, context-conflicting, reply-to-nothing, internal-status, tool-result, or unsafe content.

Rules:
- This is a content gate, not a scheduler. Never output defer, waiting, or a delay.
- Read the recent conversation and runtime context first. The candidate must read like a natural message from the current persona, not a system-triggered interruption.
- Do not invent facts or promise tools, searches, media, relays, or actions that were not actually performed.
- Planned schedules, persona continuity, and message seeds are narrative inspiration, not evidence that an action happened.
- Relative dates such as yesterday must be supported by the recent conversation or an explicitly dated reliable source.
- Preserve real media context. Do not claim an image exists when none is attached.
- A rewrite must be shorter or similarly sized and must not add new factual claims.
- A rewrite must preserve the candidate's concrete communicative purpose. Never collapse a meaningful reminder, question, warning, or check-in into a standalone filler such as “嗯。”, “哦。”, “唔。”, or “诶。”. If no complete rewrite is better, choose send and keep the candidate unchanged.
- If a user has just been discussing something and the candidate cannot naturally fit, drop it; do not defer it.
- If the candidate or any model output contains a Provider/API error, policy refusal, sensitive-word notice, policy URL, or internal diagnostic, choose drop with an empty text; never translate, quote, or polish it.
- When the current request context says the user explicitly requested this troubleshooting message, treat that request as a concrete reason to speak. Do not drop solely because it is late, the normal proactive interval is short, or there is no spontaneous life story. If the wording is too strong or generic, prefer a shorter, softer rewrite. Fact, safety, privacy, identity, and conversation-conflict checks still apply.
- For a creative share, preserve any `「...」` excerpt exactly as one continuous source quote. Keep conversational introduction and closing outside it; never paraphrase or fabricate text inside the excerpt.

{creative_excerpt_rule}

[Recent conversation]
{history or "(none)"}

[Runtime state]
{runtime_context}

[Current request context]
{troubleshooting_hint or "(ordinary proactive message; no explicit user-requested test)"}

[Verified fact boundary]
{fact_source_context}

[Local safety result]
{local_context or "local gate passed"}

[Proactive source]
route={route.key}({route.label}); review_profile={route.review_profile}; reason={reason or "check_in"}; action={action or "message"}; topic={_single_line(topic, 80) or "none"}; motive={_single_line(motive, 120) or "none"}; summary={_single_line(action_summary, 80) or "none"}

[Route-specific final gate]
{route_review_directive}

[Full persona]
{persona_context}

[Persona and intent constraints]
{intent_hint or "(none)"}

[Proactive voice]
{proactive_voice or "(natural, low-pressure private chat)"}

[Learned expression voice]
{expression_voice or "(none)"}

[Recipient identity boundary]
{recipient_identity or "Use only the current recipient identity. Do not guess or copy an exclusive name from persona examples."}

[Candidate]
{text}

Output:
{{"decision":"send|rewrite|drop","text":"","reason":"brief reason"}}
""".strip()
        started = time.perf_counter()
        review_provider_id = self._task_provider(
            _persona_provider_id(self, "RESPONSE_REVIEW_PROVIDER_ID", "response_review_provider_id", "fast"),
            _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
        )
        timeout_seconds = 8.0
        timeout_getter = getattr(self, "_model_timeout_seconds_for_call", None)
        timeout_override = (
            timeout_getter(
                task="proactive_send_review",
                provider_id=review_provider_id,
                timeout_key="RESPONSE_REVIEW_PROVIDER_ID",
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
                    max_tokens=220,
                    provider_id=review_provider_id,
                    task="proactive_send_review",
                ),
                timeout=timeout_seconds,
            )
        except Exception as exc:
            now = time.time()
            last_log_at = float(getattr(self, "_proactive_review_fallback_log_at", 0.0) or 0.0)
            if now - last_log_at >= 600:
                self._proactive_review_fallback_log_at = now
                logger.info(
                    "[PrivateCompanion] 主动最终内容复核模型暂不可用，已安全回退本地复核（同类日志 10 分钟内不重复）: %s",
                    self._format_send_exception(exc),
                )
            return local_model_fallback(self._format_send_exception(exc))
        payload = self._parse_json_object(raw)
        if not isinstance(payload, dict):
            return local_model_fallback("复核模型未返回有效 JSON")
        decision = str(payload.get("decision") or "").strip().lower()
        if decision not in {"send", "rewrite", "drop"}:
            return local_model_fallback("复核模型返回了无效 decision")
        reviewed_text = str(payload.get("text") or "").strip()
        note = _single_line(payload.get("reason"), 120)
        original_decision = decision
        if decision == "rewrite":
            if not reviewed_text:
                return local_model_fallback("复核模型要求改写但未返回正文")
            accepted_text = self._accept_proactive_rewrite(
                reviewed_text,
                original_text=text,
                user=user,
                reason=reason,
                action=action,
                topic=topic,
                motive=motive,
                action_context=review_context,
                image_path=image_path,
            )
            if accepted_text is None and reason in {"bili_video_share", "news_share", "web_exploration_share"}:
                original_safe = self._accept_proactive_rewrite(
                    text,
                    original_text=text,
                    user=user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                    action_context=review_context,
                    image_path=image_path,
                )
                safe_reference = original_safe or self._external_share_fallback_reference(
                    _single_line(topic or action_summary or text, 300),
                )
                accepted_text = self._accept_proactive_rewrite(
                    safe_reference,
                    original_text=safe_reference,
                    user=user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                    action_context=review_context,
                    image_path=image_path,
                ) if safe_reference else None
                if accepted_text:
                    note = "终审改写未通过统一验收，已恢复复核前或确定性来源文本"
            if accepted_text is None:
                original_safe = self._accept_proactive_rewrite(
                    text,
                    original_text=text,
                    user=user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                    action_context=review_context,
                    image_path=image_path,
                )
                if original_safe:
                    decision = "send"
                    reviewed_text = ""
                    note = "终审改写未通过统一验收，已保留完整原候选"
                else:
                    local_result = normalize_review_result(local, source="local")
                    local_result["review_model_ok"] = True
                    return local_result
            else:
                reviewed_text = accepted_text
        normalized_payload = self._normalize_proactive_review_decision_policy(
            user,
            {
                "decision": decision,
                "text": reviewed_text,
                "reason": note,
                "delay_minutes": payload.get("delay_minutes", 0),
            },
            strength=strength,
            source="model",
            reason=reason,
            action=action,
            topic=topic,
            motive=motive,
            action_context=review_context,
            image_path=image_path,
            original_text=text,
        )
        decision = str(normalized_payload.get("decision") or decision).strip().lower()
        reviewed_text = str(normalized_payload.get("text") or reviewed_text or "").strip()
        note = _single_line(normalized_payload.get("reason") or note, 120)
        if decision == "send" and str(local.get("decision") or "") == "rewrite" and str(local.get("text") or "").strip():
            local_text = self._accept_proactive_rewrite(
                str(local.get("text") or "").strip(),
                original_text=text,
                user=user,
                reason=reason,
                action=action,
                topic=topic,
                motive=motive,
                action_context=review_context,
                image_path=image_path,
            )
            if local_text:
                reviewed_text = local_text
                decision = "rewrite"
                note = _single_line(note or local.get("reason") or "本地轻改写后放行", 120)
            else:
                reviewed_text = ""
                decision = "send"
                note = _single_line(note or "本地改写未通过统一验收，保留原候选", 120)
        if str(local.get("decision") or "").strip().lower() == "defer" and not local_hard_block:
            delay_minutes = max(5, min(240, _safe_int(local.get("delay_minutes"), 60, 5, 240)))
            if decision in {"send", "rewrite"}:
                return {
                    "decision": "defer",
                    "text": "",
                    "delay_minutes": delay_minutes,
                    "reason": _single_line(
                        f"本地时机判断保留延后 {delay_minutes} 分钟：{local.get('reason') or '当前不宜立即发送'}",
                        180,
                    ),
                    "review_model_ok": True,
                }
        final_text = reviewed_text if decision == "rewrite" and reviewed_text else text
        link_platform_mismatch = self._proactive_link_platform_mismatch_reason(final_text)
        if decision in {"send", "rewrite"} and link_platform_mismatch:
            decision = "drop"
            reviewed_text = ""
            note = link_platform_mismatch
        if decision in {"send", "rewrite"} and self._framework_agent_meta_summary_leak(final_text):
            decision = "drop"
            reviewed_text = ""
            note = "主动候选疑似工具循环/内部发送摘要泄漏"
        logger.info(
            "[PrivateCompanion] Proactive final content gate: decision=%s raw=%s strength=%s elapsed=%dms reason=%s",
            decision,
            original_decision,
            strength,
            int((time.perf_counter() - started) * 1000),
            note or "-",
        )
        return {
            "decision": decision,
            "text": reviewed_text,
            "reason": note or "主动发送前价值复核",
            "delay_minutes": max(0, _safe_int(normalized_payload.get("delay_minutes"), 0, 0, 240)),
            "review_model_ok": True,
        }

    def _pop_framework_captured_send_payload(
        self,
        umo: str,
    ) -> tuple[str, str, list[Any]]:
        cache_key = str(umo or "")
        self._cleanup_framework_delivery_caches()
        captured = self._framework_captured_send_cache.pop(cache_key, [])
        getattr(self, "_framework_captured_send_cache_at", {}).pop(cache_key, None)
        if not captured:
            return "", "", []
        text_parts: list[str] = []
        image_path = ""
        extra_components: list[Any] = []
        for call in captured:
            messages = getattr(call, "messages", [])
            if not isinstance(messages, list):
                continue
            for item in messages:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "").strip().lower()
                if item_type == "plain":
                    text_value = self._sanitize_captured_plain_text(item.get("text"))
                    if text_value:
                        text_parts.append(text_value)
                    continue
                if item_type == "image":
                    path_value = str(item.get("path") or "").strip()
                    if path_value and os.path.exists(path_value) and not image_path:
                        image_path = path_value
                        continue
                component = self._captured_framework_message_component(item)
                if component is not None:
                    extra_components.append(component)
        return "\n".join(part for part in text_parts if part).strip(), image_path, extra_components

    def _pop_framework_deferred_photo_payload(self, umo: str) -> dict[str, Any]:
        self._cleanup_framework_delivery_caches()
        cache_key = str(umo or "")
        cache = getattr(self, "_framework_deferred_photo_cache", None)
        if not isinstance(cache, dict):
            return {}
        payload = cache.pop(cache_key, None)
        getattr(self, "_framework_deferred_photo_cache_at", {}).pop(cache_key, None)
        return dict(payload) if isinstance(payload, dict) else {}

    def _cleanup_framework_delivery_caches(self, *, force: bool = False) -> None:
        """Drop abandoned framework delivery payloads and their component references."""
        now = time.time()
        try:
            ttl = max(30.0, float(getattr(self, "framework_delivery_cache_ttl_seconds", 300) or 300))
        except (TypeError, ValueError):
            ttl = 300.0

        def stamp(key: str, stamps: dict[str, Any]) -> float:
            try:
                return float(stamps.get(key, now) or now)
            except (TypeError, ValueError):
                return now

        for cache_name, stamp_name in (
            ("_framework_captured_send_cache", "_framework_captured_send_cache_at"),
            ("_framework_deferred_photo_cache", "_framework_deferred_photo_cache_at"),
        ):
            cache = getattr(self, cache_name, None)
            if not isinstance(cache, dict):
                continue
            stamps = getattr(self, stamp_name, None)
            if not isinstance(stamps, dict):
                stamps = {}
                setattr(self, stamp_name, stamps)
            if force:
                cache.clear()
                stamps.clear()
                continue
            for key in list(stamps):
                if key not in cache:
                    stamps.pop(key, None)
            stale = [key for key in cache if now - stamp(key, stamps) > ttl]
            for key in stale:
                cache.pop(key, None)
                stamps.pop(key, None)
            # A small defensive cap protects against callers that bypass the normal writer.
            max_items = 128
            if len(cache) > max_items:
                ordered = sorted(cache, key=lambda key: stamp(key, stamps))
                for key in ordered[: len(cache) - max_items]:
                    cache.pop(key, None)
                    stamps.pop(key, None)

    def _captured_framework_message_component(self, item: dict[str, Any]) -> Any | None:
        item_type = str(item.get("type") or "").strip().lower()
        path_value = str(item.get("path") or "").strip()
        url_value = str(item.get("url") or "").strip()
        if item_type == "mention_user":
            mention_user_id = item.get("mention_user_id")
            return At(qq=mention_user_id) if mention_user_id else None
        if item_type == "image":
            if url_value:
                try:
                    return Image.fromURL(url_value)
                except Exception:
                    return None
            return None
        if CoreMessageComponents is None:
            return None
        if item_type in {"record", "video"}:
            component_cls = getattr(CoreMessageComponents, item_type.capitalize(), None)
            if component_cls is None:
                return None
            try:
                if path_value and os.path.exists(path_value):
                    return component_cls.fromFileSystem(path_value)
                if url_value:
                    return component_cls.fromURL(url_value)
            except Exception:
                return None
            return None
        if item_type == "file":
            component_cls = getattr(CoreMessageComponents, "File", None)
            if component_cls is None:
                return None
            name = _single_line(item.get("text"), 120) or os.path.basename(path_value or url_value) or "file"
            if path_value and os.path.exists(path_value):
                return component_cls(name=name, file=path_value)
            if url_value:
                return component_cls(name=name, url=url_value)
        return None

    def _sanitize_captured_plain_text(self, raw_text: Any) -> str:
        text = str(raw_text or "").strip()
        if not text:
            return ""
        kept: list[str] = []
        for raw_line in text.replace("\r", "\n").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if self._is_proactive_delivery_receipt_text(line):
                continue
            if self._looks_like_internal_provider_error_text(line):
                continue
            kept.append(line)
        cleaned = "\n".join(kept).strip().strip('"').strip("'")
        cleaned = cleaned.replace("（图片已送达）", "").replace("(图片已送达)", "")
        tts_cleaner = getattr(self, "_clean_tool_plain_text_tts_markup", None)
        if callable(tts_cleaner):
            cleaned = tts_cleaner(cleaned)
        else:
            cleaned = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if self._looks_like_internal_provider_error_text(cleaned):
            return ""
        return cleaned[:260]

    async def _generate_voice_note_via_framework(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        *,
        target: str,
        strict_tts: bool = False,
    ) -> str:
        umo = str(user.get("umo") or target or "").strip()
        if not umo:
            return ""
        prompt = self._build_framework_voice_prompt(
            user=user,
            name=name,
            reason=reason,
            target=target,
            strict_tts=strict_tts,
        )
        try:
            raw_text = await self._run_framework_agent_text(
                umo=umo,
                prompt=prompt,
                name=name,
                label="proactive_voice",
                user=user,
                max_steps=20,
            )
            return str(raw_text or "").strip()
        except Exception as exc:
            if self._is_sqlite_locked_error(exc):
                logger.warning("[PrivateCompanion] 主动语音主链被会话数据库锁住,本轮跳过并等待下次调度: %s", _single_line(umo, 120))
            else:
                logger.warning("[PrivateCompanion] 主动语音主链内容生成失败: %s", exc)
            return ""

    async def _generate_proactive_message_with_llm(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        action_context: str = "",
        action: str = "message",
        motive: str = "",
    ) -> str:
        user.pop("_proactive_render_failure_stage", None)
        umo = _single_line(user.get("umo"), 240)
        self._clear_proactive_reaction_intent(umo)
        if not runtime_persona_setting(self, "enable_llm_proactive_message", True):
            user["_proactive_render_failure_stage"] = "主动消息模型生成已关闭"
            return ""
        raw_text = await self._generate_proactive_message_via_framework(
            user,
            name,
            reason,
            action_context=action_context,
            action=action,
            motive=motive,
        )
        deferred_photo_cache = getattr(self, "_framework_deferred_photo_cache", None)
        if isinstance(deferred_photo_cache, dict) and umo in deferred_photo_cache:
            logger.info(
                "[PrivateCompanion] 主动正文已由 pc_generate_photo caption/纯图承载，跳过文本兜底: user=%s",
                _single_line(user.get("user_id"), 40),
            )
            return str(raw_text or "")
        async def finalize_candidate(candidate: str) -> tuple[str, str]:
            extractor = getattr(self, "_extract_reaction_expression_hidden_intent", None)
            visible_candidate, reaction_intent = (
                extractor(candidate)
                if callable(extractor)
                else (str(candidate or ""), {})
            )
            if not reaction_intent:
                fallback_builder = getattr(
                    self,
                    "_proactive_reaction_expression_fallback_intent",
                    None,
                )
                if callable(fallback_builder):
                    try:
                        reaction_intent = fallback_builder(
                            visible_candidate,
                            action=action,
                        )
                    except Exception as exc:
                        logger.debug(
                            "[PrivateCompanion] 高频主动表情兜底构建失败: error_type=%s",
                            type(exc).__name__,
                        )
            finalized, failure_stage = await self._finalize_proactive_generated_text(
                user,
                visible_candidate,
                name=name,
                reason=reason,
                action=action,
                action_context=action_context,
                motive=motive,
            )
            if finalized:
                self._store_proactive_reaction_intent(
                    user,
                    reaction_intent if isinstance(reaction_intent, dict) else {},
                    action=action,
                )
            return finalized, failure_stage

        failure_stages: list[str] = []
        if raw_text:
            finalized, failure_stage = await finalize_candidate(raw_text)
            if finalized:
                return finalized
            failure_stages.append(f"框架主链{failure_stage or '处理后为空'}")
        else:
            failure_stages.append("框架主链返回空文本")

        fallback_text = await self._generate_proactive_message_direct_fallback(
            user,
            name=name,
            reason=reason,
            action=action,
            action_context=action_context,
            motive=motive,
        )
        if fallback_text:
            finalized, failure_stage = await finalize_candidate(fallback_text)
            if finalized:
                logger.info(
                    "[PrivateCompanion] 主动框架主链为空后已由直接人格化兜底恢复: user=%s reason=%s",
                    _single_line(user.get("user_id"), 40),
                    reason,
                )
                return finalized
            failure_stages.append(f"直接人格化兜底{failure_stage or '处理后为空'}")
        else:
            failure_stages.append("直接人格化兜底返回空文本")

        failure_detail = "；".join(failure_stages)[:240]
        user["_proactive_render_failure_stage"] = failure_detail
        logger.warning(
            "[PrivateCompanion] 主动正文两级生成均未产出: user=%s reason=%s stage=%s",
            _single_line(user.get("user_id"), 40),
            reason,
            failure_detail,
        )
        return ""

    async def _generate_proactive_message_direct_fallback(
        self,
        user: dict[str, Any],
        *,
        name: str,
        reason: str,
        action: str,
        action_context: str = "",
        motive: str = "",
    ) -> str:
        relationship_sanitizer = getattr(self, "_sanitize_generation_relationship_context", None)

        def sanitize_relationship_source(value: Any, source: str) -> str:
            if callable(relationship_sanitizer):
                try:
                    return relationship_sanitizer(value, source=source)
                except Exception:
                    pass
            return str(value or "").strip()

        topic = _single_line(
            sanitize_relationship_source(user.get("planned_proactive_topic"), "proactive_fallback.topic"),
            120,
        )
        planned_motive = _single_line(
            sanitize_relationship_source(
                motive or user.get("planned_proactive_motive"),
                "proactive_fallback.motive",
            ),
            220,
        )
        context = sanitize_relationship_source(
            self._format_action_prompt_context(action, action_context),
            "proactive_fallback.action_context",
        )
        if (
            (context.startswith("message：") and "图片动作本轮未产出" not in context)
            or context in {"普通文字", "普通私聊文本"}
        ):
            context = ""
        reference = "\n".join(
            part
            for part in (
                f"主动话题：{topic}" if topic else "",
                f"想表达：{planned_motive}" if planned_motive else "",
                f"真实动作上下文：{context}" if context else "",
            )
            if part
        )
        body_health_hint_getter = getattr(self, "_format_body_monitor_health_prompt", None)
        if reason == "health_alert" and callable(body_health_hint_getter):
            body_health_hint = body_health_hint_getter(user, reason=reason)
            if body_health_hint:
                reference = f"{reference}\n{body_health_hint}" if reference else body_health_hint
        balance_hint_getter = getattr(self, "_format_balance_awareness_prompt", None)
        if reason == "low_balance" and callable(balance_hint_getter):
            balance_hint = balance_hint_getter(user, reason=reason)
            if balance_hint:
                reference = f"{reference}\n{balance_hint}" if reference else balance_hint
        environment_hint_getter = getattr(self, "_format_environment_change_prompt", None)
        if reason == "environment_change" and callable(environment_hint_getter):
            environment_hint = environment_hint_getter(user, reason=reason)
            if environment_hint:
                reference = f"{reference}\n{environment_hint}" if reference else environment_hint
        weather_alert_hint_getter = getattr(self, "_format_weather_alert_prompt", None)
        if reason == "weather_alert" and callable(weather_alert_hint_getter):
            weather_alert_hint = weather_alert_hint_getter(user, reason=reason)
            if weather_alert_hint:
                reference = f"{reference}\n{weather_alert_hint}" if reference else weather_alert_hint
        personal_goal_hint_getter = getattr(self, "_format_personal_goal_prompt", None)
        if reason == "personal_goal_progress" and callable(personal_goal_hint_getter):
            personal_goal_hint = personal_goal_hint_getter(user, reason=reason)
            if personal_goal_hint:
                reference = f"{reference}\n{personal_goal_hint}" if reference else personal_goal_hint
        memo_hint_getter = getattr(self, "_format_memo_note_prompt", None)
        if reason == "memo_note_reminder" and callable(memo_hint_getter):
            memo_hint = memo_hint_getter(user, reason=reason)
            if memo_hint:
                reference = f"{reference}\n{memo_hint}" if reference else memo_hint
        if reason == "goodnight_screen_check":
            reference = (
                f"互道晚安后，如果{name or '对方'}还没睡，就轻声提醒忙完早点休息；"
                "不提看见了什么，不追问，不要求回复，也不表现成在监控。"
            )
        elif reason == "anonymous_area_dwell":
            reference = (
                f"{reference}\n" if reference else ""
            ) + (
                "这是用户离开一个未命名区域后的延迟关心。不要提位置、地图、城市、城区、定位或停留时长；"
                "只写成后来想起用户刚才在外面待了挺久，轻轻关心是否顺利，不追问具体去了哪里。"
            )
        elif reason == "anonymous_area_familiarity":
            reference = (
                f"{reference}\n" if reference else ""
            ) + (
                "这是多次匿名区域到访留下的模糊熟悉感。不要提位置来源、地图、次数或具体地点；"
                "可以说‘最近好像有个常去的地方’，但必须给用户留出否认或不解释的空间。"
            )
        relationship_initiative_hint = self._format_proactive_relationship_initiative_hint(
            user,
            reason=reason,
            action=action,
        )
        if relationship_initiative_hint:
            reference = f"{reference}\n{relationship_initiative_hint}" if reference else relationship_initiative_hint
        if not reference:
            reference = f"自然地向{name or '对方'}主动说一句与当前状态有关、低压力且无需立即回复的话。"
        reference = sanitize_relationship_source(reference, "proactive_fallback.reference")
        if not reference:
            reference = f"自然地向{name or '对方'}主动说一句低压力且无需立即回复的话。"
        fallback_scene = f"主动开口；原因={reason or 'check_in'}；动作={action or 'message'}"
        if reason == "creative_share":
            fallback_scene = "主动分享自己的创作；作品原文与聊天引入必须保持清晰边界"
        return await self._rewrite_reference_reply_with_persona(
            reference,
            scene=fallback_scene,
            user=user,
            fallback_text="",
            task="proactive_message_fallback",
            max_chars=180,
            allow_fallback=False,
        )

    async def _finalize_proactive_generated_text(
        self,
        user: dict[str, Any],
        raw_text: str,
        *,
        name: str,
        reason: str,
        action: str,
        action_context: str = "",
        motive: str = "",
    ) -> tuple[str, str]:
        if self._looks_like_internal_provider_error_text(raw_text):
            logger.warning(
                "[PrivateCompanion] 主动正文生成收到 Provider 错误正文，跳过清洗并进入回退: user=%s reason=%s",
                _single_line(user.get("user_id"), 40),
                _single_line(reason, 60) or "check_in",
            )
            return "", "Provider/API 错误正文"
        cleaned = self._sanitize_action_boundaries(
            self._sanitize_proactive_text(raw_text),
            reason=reason,
            action=action,
            action_context=action_context,
            has_real_image="真实图片文件：" in action_context or "图片路径：" in action_context,
        )
        if not cleaned:
            return "", "在动作边界清洗后为空"
        cleaned, repaired_address = self._repair_proactive_recipient_address(cleaned, user, name)
        if repaired_address:
            logger.warning(
                "[PrivateCompanion] 主动消息已纠正串用户句首称呼: user=%s wrong=%s replacement=%s",
                _single_line(user.get("user_id"), 40),
                repaired_address,
                _single_line(name or user.get("nickname"), 40) or "你",
            )
        remaining_wrong_address = self._wrong_proactive_recipient_address(cleaned, user, name)
        if remaining_wrong_address:
            return "", f"含其他用户专属称呼：{remaining_wrong_address}"
        if self._is_overabstract_proactive_text(cleaned, action=action):
            cleaned = self._ground_proactive_text(
                cleaned,
                reason=reason,
                action=action,
                action_context=action_context,
            )
        cleaned = self._apply_proactive_style_variation(cleaned, user)
        cleaned = self._collapse_multi_candidate_proactive_text(cleaned, user=user, name=name)
        cleaned = self._repair_proactive_subject_drift(cleaned, reason=reason, action=action, action_context=action_context)
        if reason == "morning_greeting":
            cleaned = self._strip_morning_meal_questions(cleaned)
        cleaned = self._visible_text_without_tts_reading(cleaned, limit=1000)
        if not cleaned:
            return "", "在主客体/可见文本清洗后为空"
        relay_claim_note = self._unexecuted_relay_claim_reason(cleaned, action_context=action_context)
        if relay_claim_note:
            logger.info(
                "[PrivateCompanion] 主动消息含未执行转述承诺,已丢弃: reason=%s text=%s",
                relay_claim_note,
                _single_line(cleaned, 120),
            )
            return "", f"含未执行转述承诺：{_single_line(relay_claim_note, 80)}"
        if self._should_drop_vague_generic_proactive(
            user,
            reason=reason,
            action=action,
            action_context=action_context,
            text=cleaned,
        ):
            # 连续未回应时的泛泛措辞是表达质量问题，不是安全问题。
            # 交给主动生成提示词收短、降压，避免在终审关闭时被本地规则直接吞掉。
            logger.debug(
                "[PrivateCompanion] 泛化主动由提示词收敛，不再直接拦截: user=%s text=%s",
                _single_line(user.get("user_id") or user.get("umo"), 80),
                _single_line(cleaned, 140),
            )
        if self._should_drop_misstaged_proactive_text(cleaned, reason=reason, action=action):
            return "", "错接旧对话或时段"
        reviewed = await self._review_proactive_message_stance(
            user,
            cleaned,
            reason=reason,
            action=action,
            action_context=action_context,
            motive=motive,
        )
        if not reviewed:
            return "", "回复空气复核后为空"
        reviewed, repaired_review_address = self._repair_proactive_recipient_address(reviewed, user, name)
        if repaired_review_address:
            logger.warning(
                "[PrivateCompanion] 主动复核结果已纠正串用户称呼: user=%s wrong=%s",
                _single_line(user.get("user_id"), 40),
                repaired_review_address,
            )
        remaining_review_address = self._wrong_proactive_recipient_address(reviewed, user, name)
        if remaining_review_address:
            return "", f"回复空气复核引入其他用户专属称呼：{remaining_review_address}"
        reviewed = self._trim_proactive_status_inventory(reviewed)
        reviewed = self._trim_performative_self_state_tail(reviewed)
        if reason == "morning_greeting":
            reviewed = self._strip_morning_meal_questions(reviewed)
        finalized = self._normalize_proactive_sentence_flow(reviewed)
        return (finalized, "") if finalized else ("", "最终句式整理后为空")

    @staticmethod
    def _strip_morning_meal_questions(text: str) -> str:
        """Keep a morning greeting while removing an accidentally appended meal question."""
        source = str(text or "").strip()
        if not source:
            return ""
        query_pattern = re.compile(
            r"(?:早餐|早饭).{0,12}(?:吗|没|没有|什么|啥|呢|[？?])"
            r"|(?:吃|喝).{0,6}(?:了吗|了没|没有|什么|啥)(?:呢|[？?])?"
        )
        kept: list[str] = []
        for unit in re.split(r"(?<=[。！？!?])\s*|\n+", source):
            candidate = unit.strip()
            if not candidate:
                continue
            match = query_pattern.search(candidate)
            if not match:
                kept.append(candidate)
                continue
            prefix = candidate[: match.start()].rstrip(" ，,；;、")
            if prefix:
                kept.append(prefix)
        return "\n".join(kept).strip()

    def _proactive_reply_air_flags(
        self,
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str = "",
    ) -> list[str]:
        cleaned = _single_line(text, 260)
        if not cleaned or action not in {"message", "photo_text"}:
            return []
        flags: list[str] = []
        reply_opener_pattern = (
            r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就|你说呢|要不|不然|"
            r"确实|对呀|对啊|是吧|也是|哈哈[,，\s]*我也|我也觉得|你说得对)"
        )
        if re.search(reply_opener_pattern, cleaned):
            flags.append("reply_air_opener")
        if re.search(r"(?:刚看到|才看到|刚才看到|看到你(?:刚刚|刚才)?发|看到你说)", cleaned):
            flags.append("pretends_recent_inbound")
        if re.search(r"你(?:刚刚|刚才|现在)?(?:叫|喊|问|说|发|来找|找|催)我", cleaned):
            flags.append("inverts_initiator")
        if re.search(r"(?:你问|你说|你刚才说|你刚刚说)[^。！？\n]{0,24}(?:我觉得|我也|确实|可以|好呀|好啊)", cleaned):
            flags.append("answers_old_context")
        if self._is_proactive_delivery_receipt_text(cleaned):
            flags.append("delivery_receipt")
        if reason in {"morning_greeting", "noon_greeting", "evening_greeting", "check_in"} and re.search(
            r"(?:一直等着|等你问|你到时候|到时候叫|到时候喊|那就这么说定|按你说的)",
            cleaned,
        ):
            flags.append("stale_agreement")
        if "真实图片文件：" not in str(action_context or "") and "图片路径：" not in str(action_context or ""):
            if re.search(r"(?:发你看|给你看图|看图|图里|照片里|图片里)", cleaned):
                flags.append("claims_missing_media")
        return list(dict.fromkeys(flags))

    def _repair_proactive_reply_air_locally(self, text: str, flags: list[str]) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        units = self._split_proactive_sentence_units(cleaned) or [cleaned]
        repaired: list[str] = []
        opener_pattern = (
            r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就|你说呢|要不|不然|"
            r"确实|对呀|对啊|是吧|也是|哈哈[,，\s]*我也|我也觉得|你说得对)"
            r"[，,、。！？!?；;:\s]*"
        )
        stale_patterns = (
            r"(?:刚看到|才看到|刚才看到|看到你(?:刚刚|刚才)?发|看到你说)",
            r"你(?:刚刚|刚才|现在)?(?:叫|喊|问|说|发|来找|找|催)我",
            r"(?:你问|你说|你刚才说|你刚刚说)[^。！？\n]{0,24}(?:我觉得|我也|确实|可以|好呀|好啊)",
        )
        for unit in units:
            candidate = str(unit or "").strip()
            if not candidate:
                continue
            if "reply_air_opener" in flags:
                candidate = re.sub(opener_pattern, "", candidate, count=1).strip()
            if any(re.search(pattern, candidate) for pattern in stale_patterns):
                continue
            if candidate:
                repaired.append(self._ensure_chat_sentence_punctuation(candidate))
        return "\n".join(repaired).strip()

    async def _review_proactive_message_stance(
        self,
        user: dict[str, Any],
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str = "",
        motive: str = "",
    ) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        relationship_sanitizer = getattr(self, "_sanitize_generation_relationship_context", None)

        def sanitize_relationship_source(value: Any, source: str) -> str:
            if callable(relationship_sanitizer):
                try:
                    return relationship_sanitizer(value, source=source)
                except Exception:
                    pass
            return str(value or "").strip()

        flags = self._proactive_reply_air_flags(
            cleaned,
            reason=reason,
            action=action,
            action_context=action_context,
        )
        if not flags:
            return cleaned
        mode = self._effective_proactive_review_mode()
        review_disabled = not bool(
            runtime_persona_setting(self, "enable_proactive_message_review", True)
        )
        if review_disabled or mode == "local_only":
            hard_flags = {"delivery_receipt", "claims_missing_media"}
            if hard_flags.intersection(flags):
                # 只移除命中硬风险的句子；同一候选里若还有安全正文，继续交给
                # 轻量主动修正，避免一条附带回执的多句消息被整条吞掉。
                safe_units: list[str] = []
                for unit in self._split_proactive_sentence_units(cleaned) or [cleaned]:
                    unit_flags = self._proactive_reply_air_flags(
                        unit,
                        reason=reason,
                        action=action,
                        action_context=action_context,
                    )
                    if hard_flags.intersection(unit_flags):
                        continue
                    safe_units.append(unit)
                cleaned = "\n".join(safe_units).strip()
                if not cleaned:
                    logger.info(
                        "[PrivateCompanion] 主动消息仅剩不可用动作/内部回执,本地安全检查已丢弃: flags=%s",
                        ",".join(flags),
                    )
                    return ""
                flags = self._proactive_reply_air_flags(
                    cleaned,
                    reason=reason,
                    action=action,
                    action_context=action_context,
                )
            repaired = self._repair_proactive_reply_air_locally(cleaned, flags)
            remaining_flags = self._proactive_reply_air_flags(
                repaired,
                reason=reason,
                action=action,
                action_context=action_context,
            ) if repaired else flags
            if repaired and not remaining_flags:
                logger.info(
                    "[PrivateCompanion] 主动消息疑似回复空气,已用本地轻量规则修正: flags=%s before=%s after=%s",
                    ",".join(flags),
                    _single_line(cleaned, 100),
                    _single_line(repaired, 100),
                )
                return repaired
            logger.warning(
                "[PrivateCompanion] 主动消息疑似回复空气但终审未启用,本地无法可靠改写，保留原文并交由生成提示词约束: flags=%s text=%s",
                ",".join(flags),
                _single_line(cleaned, 120),
            )
            return cleaned
        intent_hint = self._format_proactive_generation_intent_hint(
            user,
            reason=reason,
            action=action,
            motive=motive,
            action_context=action_context,
        )
        intent_hint = sanitize_relationship_source(intent_hint, "proactive_review.intent")
        review_motive = _single_line(
            sanitize_relationship_source(
                motive or user.get("planned_proactive_motive"),
                "proactive_review.motive",
            ),
            160,
        )
        review_topic = _single_line(
            sanitize_relationship_source(
                user.get("planned_proactive_topic"),
                "proactive_review.topic",
            ),
            120,
        )
        review_action_context = _single_line(
            sanitize_relationship_source(action_context, "proactive_review.action_context"),
            260,
        )
        persona = await self._resolve_proactive_persona_prompt(user)
        proactive_voice = self._format_proactive_voice_prompt() if callable(getattr(self, "_format_proactive_voice_prompt", None)) else ""
        expression_formatter = getattr(self, "_format_expression_voice_for_prompt", None)
        expression_voice = (
            expression_formatter(
                scope="proactive",
                target_id=_single_line(user.get("user_id") or user.get("id"), 80),
                context_owner=user,
                stage_owner=user,
            )
            if callable(expression_formatter)
            else ""
        )
        recipient_identity = self._format_proactive_recipient_identity_guard(
            user,
            _single_line(user.get("nickname"), 40),
        )
        creative_excerpt_rule = (
            self._creative_share_excerpt_prompt_hint()
            if reason == "creative_share"
            else ""
        )
        prompt = f"""
把下面这条主动私聊消息改成真正的主动开口。
它不是在回复用户刚发来的消息；聊天历史只能当背景。

【原主动消息】
{cleaned}

【问题】
{", ".join(flags)}

【主动原因】
{reason or "check_in"}

【动机/话题】
{review_motive}
{review_topic}

【动作上下文】
{review_action_context or "（无）"}

【内在约束】
{intent_hint or "（无额外约束）"}

【完整人格】
{self._truncate_proactive_context(persona, 2600) if persona else "（没有解析到显式人格；尽量保留原文语气，不要另造一种通用陪伴人格）"}

【主动开口风格】
{proactive_voice or "（无额外主动风格；保持原文已有的人格语气）"}

【已形成的表达底色】
{expression_voice or "（无额外表达底色）"}

【当前收件人】
{recipient_identity or "不要猜名字或套用其他对象的专属称呼。"}

要求：
- 只输出要发送的正文
- 不要把“用户”“对方”“收信人”这类内部称呼写进正文；需要称呼时用自然的“你”或对方昵称
- 不要写成“好呀/确实/我也觉得/刚看到/你刚刚问我/你来找我了”
- 不要把历史消息当成当前正在发生的对话
- 没有真实图片或工具结果时，只写聊天内容本身，不描述动作结果
- 如果原文只是过程状态或工具结果，请不要改写成另一种状态汇报；改不成自然聊天就输出空文本
- 如果原文或模型结果包含 Provider/API 报错、内容策略拒绝、敏感词提示、政策链接或内部诊断，输出空文本；不要翻译、复述或润色
- 改写后仍要贴合内在约束里的候选语义；不能把分享型改成泛泛问候，也不能把低压关心改成追问
- 只修正“回复空气”的问题；不得把原文改成另一种人格，也不得降低或升级当前关系亲密度
- 尽量 1 到 2 句，像自然想起对方后随手说一句
{creative_excerpt_rule}
""".strip()
        started = time.perf_counter()
        rewritten = await self._llm_call(
            prompt,
            max_tokens=180,
            provider_id=self._task_provider(
                _persona_provider_id(self, "RESPONSE_REVIEW_PROVIDER_ID", "response_review_provider_id", "fast"),
                _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
            ),
            task="response_review",
        )
        candidate = self._sanitize_proactive_text(str(rewritten or "").strip())
        candidate = self._sanitize_action_boundaries(
            candidate,
            reason=reason,
            action=action,
            action_context=action_context,
            has_real_image="真实图片文件：" in action_context or "图片路径：" in action_context,
        )
        if self._looks_like_internal_provider_error_text(candidate):
            logger.warning(
                "[PrivateCompanion] 回复/主动复核返回 Provider 错误正文，已丢弃: task=response_review"
            )
            return ""
        meta_leak_checker = getattr(self, "_response_review_meta_leak_reason", None)
        if callable(meta_leak_checker) and meta_leak_checker(candidate):
            logger.error(
                "[PrivateCompanion] 回复/主动复核返回内部判断，已丢弃: output=%s",
                _single_line(candidate, 180),
            )
            return ""
        logger.info(
            "[PrivateCompanion] 回复/主动复核完成: mode=%s flags=%s elapsed=%dms before=%s after=%s",
            mode,
            ",".join(flags),
            int((time.perf_counter() - started) * 1000),
            _single_line(cleaned, 100),
            _single_line(candidate, 100),
        )
        if not candidate:
            return ""
        if len(candidate) > max(len(cleaned) + 80, 260):
            return ""
        if re.search(r"(提示词|系统|JSON|改写后|以下是|主动消息|聊天历史)", candidate, re.IGNORECASE):
            return ""
        remaining_flags = self._proactive_reply_air_flags(
            candidate,
            reason=reason,
            action=action,
            action_context=action_context,
        )
        if remaining_flags:
            logger.info(
                "[PrivateCompanion] 回复/主动复核后仍疑似回复空气,已丢弃: flags=%s text=%s",
                ",".join(remaining_flags),
                _single_line(candidate, 120),
            )
            return ""
        return candidate

    def _repair_proactive_subject_drift(
        self,
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str = "",
    ) -> str:
        cleaned = str(text or "").strip()
        if not cleaned or action != "message":
            return cleaned
        state_context = "\n".join(
            _single_line(part, 260)
            for part in (
                action_context,
                self._format_schedule_context_for_prompt(),
                self._format_plan_item_for_prompt(self._get_current_plan_item(self.data.get("daily_plan", {}))),
            )
            if _single_line(part, 260)
        )
        bot_task_markers = (
            "作业", "写题", "题", "上课", "放学", "课本", "书桌", "试卷", "复习", "预习",
            "任务", "代码", "创作", "草稿", "报告", "练习",
        )
        if not any(token in state_context for token in bot_task_markers):
            return cleaned
        user_progress_patterns = (
            r"你[^。！？\n]{0,12}(?:作业|题|试卷|课|任务|代码|报告|草稿|练习)[^。！？\n]{0,18}(?:还差多少|写完了吗|做完了吗|弄完了吗|忙完了吗|上完了吗|差多少|完成了吗|怎么样了)[呀啊嘛呢了]*[？?。!！]?",
            r"(?:作业|题|试卷|课|任务|代码|报告|草稿|练习)[^。！？\n]{0,12}(?:还差多少|写完了吗|做完了吗|弄完了吗|忙完了吗|上完了吗|差多少|完成了吗)[呀啊嘛呢了]*[？?。!！]?",
        )
        repaired = cleaned
        changed = False
        for pattern in user_progress_patterns:
            repaired, count = re.subn(pattern, "", repaired)
            changed = changed or count > 0
        if not changed:
            return cleaned
        repaired = re.sub(r"\s+", " ", repaired).strip(" ，,。！？!?、")
        if repaired:
            logger.info(
                "[PrivateCompanion] 主动消息修正主客体错位问句: reason=%s before=%s after=%s",
                reason,
                _single_line(cleaned, 120),
                _single_line(repaired, 120),
            )
            return repaired
        logger.info(
            "[PrivateCompanion] 主动消息主客体错位且无剩余自然内容,已丢弃本轮生成: reason=%s text=%s",
            reason,
            _single_line(cleaned, 120),
        )
        return ""

    def _should_drop_misstaged_proactive_text(self, text: str, *, reason: str, action: str) -> bool:
        cleaned = _single_line(text, 220)
        if not cleaned:
            return True
        if action != "message" or reason not in {"morning_greeting", "noon_greeting", "evening_greeting", "check_in"}:
            return False
        reply_openers = ("好呀", "好啊", "可以呀", "可以啊", "行呀", "行啊", "嗯好", "那就", "你说呢", "要不", "不然")
        old_invite_markers = (
            "下午陪你", "陪你出去", "出去走走", "五点", "放学之后", "下班之后",
            "到时候叫我", "到时候喊我", "到时候", "垫上", "我哪来的钱",
            "一直等着", "等着呢", "想去哪", "去哪儿", "去哪逛", "哪儿逛", "哪里逛", "去逛",
        )
        if reason in {"morning_greeting", "noon_greeting", "evening_greeting"} and cleaned.startswith(reply_openers) and any(token in cleaned for token in old_invite_markers):
            logger.info(
                "[PrivateCompanion] 主动消息疑似把旧邀约当成当前回复,已丢弃: reason=%s text=%s",
                reason,
                cleaned,
            )
            return True
        if reason in {"morning_greeting", "noon_greeting", "evening_greeting"}:
            stale_reply_patterns = (
                r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就).{0,30}(?:你到时候|到时候你|到时候叫|到时候喊)",
                r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就).{0,30}(?:我得|我得等|我只能|我可以).{0,18}(?:之后|以后|才行)",
                r"^(?:你说呢|要不|不然).{0,30}(?:我哪来|哪来的钱|先帮我|帮我垫|垫上)",
                r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就|你说呢|要不|不然).{0,36}(?:下午|五点|放学|下班|垫上|哪来的钱)",
                r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就).{0,30}(?:一直等|等着呢|等你).{0,30}(?:去哪|哪儿|哪里|逛|走走)",
                r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就).{0,36}(?:想去哪|去哪儿|去哪逛|哪儿逛|哪里逛|去逛)",
            )
            if any(re.search(pattern, cleaned) for pattern in stale_reply_patterns):
                logger.info(
                    "[PrivateCompanion] 主动消息疑似接续旧对话而非主动开口,已丢弃: reason=%s text=%s",
                    reason,
                    cleaned,
                )
                return True
        return False

    def _proactive_time_mismatch_reason(self, text: str, *, reason: str, action: str) -> str:
        if str(action or "message").strip() != "message":
            return ""
        cleaned = _single_line(text, 240)
        if not cleaned:
            return ""
        now = self._environment_now()
        minutes = now.hour * 60 + now.minute
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        current_text = _single_line(self._format_plan_item_for_prompt(current_item), 180)
        current_is_school_or_afternoon = bool(re.search(r"(上课|课间|放学|校门|教室|作业|书包|回家路上)", current_text))
        if reason == "morning_greeting" and re.search(r"(晚上|晚安|睡觉|好梦|睡前|夜里|放学|下班)", cleaned):
            return f"早间主动含有非早间场景: {cleaned}"
        if reason == "noon_greeting" and re.search(r"(早安|刚醒|赖床|晚安|好梦|睡觉|夜里)", cleaned):
            return f"午间主动含有错时问候: {cleaned}"
        if reason == "evening_greeting" and re.search(r"(早安|刚醒|赖床|上午|中午吃了吗)", cleaned):
            return f"晚间主动含有错时问候: {cleaned}"
        if minutes < 12 * 60 and re.search(r"(放学|放学就|放学后|放学回来|下课回来|下午回来|傍晚回来|晚上回来)", cleaned):
            return f"上午主动提前叙述放学/傍晚场景: {cleaned}"
        if minutes < 15 * 60 and re.search(r"(五点|5点|17点|下午五点|傍晚|晚上见|晚点回来找你)", cleaned):
            return f"当前时段过早,主动含有傍晚/五点场景: {cleaned}"
        if minutes >= 22 * 60 and re.search(r"(放学|下课|下午|傍晚|出去走走|等我回来找你)", cleaned):
            return f"夜间主动含有已过时段场景: {cleaned}"
        if re.search(r"(放学|下课|校门|教室|书包|回家路上)", cleaned) and not current_is_school_or_afternoon and not (14 * 60 <= minutes <= 19 * 60):
            return f"主动文本与当前日程不匹配: 当前={current_text or '无'} 文本={cleaned}"
        return ""


    def _collapse_multi_candidate_proactive_text(self, text: str, *, user: dict[str, Any], name: str = "") -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if len(lines) >= 2:
            collapsed_lines = self._collapse_near_duplicate_proactive_lines(lines)
            if len(collapsed_lines) < len(lines):
                result = "\n".join(collapsed_lines).strip()
                logger.info(
                    "[PrivateCompanion] 主动消息已合并同轮近似候选: before=%s after=%s",
                    _single_line(cleaned, 180),
                    _single_line(result, 160),
                )
                return result or cleaned
        units: list[str] = []
        for line in lines or [cleaned]:
            units.extend(self._split_proactive_sentence_units(line))
        units = [unit.strip() for unit in units if unit and unit.strip()]
        if len(units) <= 2:
            return cleaned

        opener_tokens = [
            _single_line(name, 16),
            _single_line(user.get("nickname") if isinstance(user, dict) else "", 16),
            _single_line(runtime_persona_setting(self, "default_nickname", ""), 16),
        ]
        first_opener = ""
        match = re.match(r"^([\w\u4e00-\u9fffぁ-んァ-ヶー]{1,8})[，,、\s]", units[0])
        if match:
            first_opener = match.group(1)
            opener_tokens.append(first_opener)
        opener_tokens = [token for token in dict.fromkeys(opener_tokens) if token]

        repeated_opener_index = 0
        for index, unit in enumerate(units[1:], start=1):
            if any(unit.startswith(token) and index >= 2 for token in opener_tokens):
                repeated_opener_index = index
                break
        if repeated_opener_index:
            units = units[:repeated_opener_index]

        if self._private_user_role(user) == "friend" and len(units) > 2:
            units = units[:2]
        return "\n".join(units).strip() or cleaned

    def _proactive_candidate_core_text(self, text: str) -> str:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return ""
        cleaned = re.sub(r"^[\w\u4e00-\u9fffぁ-んァ-ヶー]{1,8}[，,、\s]+", "", cleaned)
        cleaned = re.sub(r"^(?:早上好|早安|上午好|中午好|午安|下午好|晚上好)[。！？!?…~～,，\s]*", "", cleaned)
        cleaned = re.sub(r"^(?:唔|嗯|诶|欸|啊|嗨|嘿)[。！？!?…~～,，\s]*", "", cleaned)
        cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", cleaned)
        filler_tokens = ("刚刚", "刚才", "现在", "今天", "这会儿", "好像", "感觉", "一点", "有点")
        for token in filler_tokens:
            cleaned = cleaned.replace(token, "")
        return cleaned

    def _proactive_candidate_bigrams(self, text: str) -> set[str]:
        cleaned = self._proactive_candidate_core_text(text)
        if len(cleaned) < 2:
            return set()
        return {cleaned[index : index + 2] for index in range(len(cleaned) - 1)}

    def _collapse_near_duplicate_proactive_lines(self, lines: list[str]) -> list[str]:
        kept: list[str] = []
        for line in lines:
            current = line.strip()
            if not current:
                continue
            current_core = self._proactive_candidate_core_text(current)
            duplicate_index = -1
            for index, old in enumerate(kept):
                old_core = self._proactive_candidate_core_text(old)
                if not current_core or not old_core:
                    continue
                shorter = min(len(current_core), len(old_core))
                if shorter < 8:
                    continue
                same_core = current_core == old_core
                contained = current_core in old_core or old_core in current_core
                current_bigrams = self._proactive_candidate_bigrams(current)
                old_bigrams = self._proactive_candidate_bigrams(old)
                bigram_overlap = 0.0
                if current_bigrams and old_bigrams:
                    bigram_overlap = len(current_bigrams & old_bigrams) / max(1, min(len(current_bigrams), len(old_bigrams)))
                if same_core or contained or bigram_overlap >= 0.86:
                    duplicate_index = index
                    break
            if duplicate_index < 0:
                kept.append(current)
                continue
            old = kept[duplicate_index]
            old_core = self._proactive_candidate_core_text(old)
            prefer_current = (
                len(current_core) < len(old_core)
                or (len(current) + 6 < len(old) and not re.search(r"^(?:早上好|早安|上午好|中午好|午安|下午好|晚上好)", current))
            )
            if prefer_current:
                kept[duplicate_index] = current
        return kept

    def _should_drop_vague_generic_proactive(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        action_context: str = "",
        text: str = "",
    ) -> bool:
        if reason != "check_in" or action != "message":
            return False
        if _safe_int(user.get("ignored_streak"), 0, 0) < 2:
            return False
        context = _single_line(action_context, 180)
        if context and not context.startswith("message") and "普通私聊文本" not in context:
            return False
        cleaned = _single_line(text, 160)
        if not cleaned:
            return True
        vague_tokens = ("想找你", "来看看你", "刷存在感", "最近忙不忙", "辛苦了", "在吗", "有点想你", "没什么事", "就是想")
        concrete_markers = ("刚", "路上", "窗", "雨", "书", "饭", "水", "图", "群", "视频", "作业", "游戏", "梦")
        return any(token in cleaned for token in vague_tokens) and not any(token in cleaned for token in concrete_markers)

    def _apply_proactive_style_variation(self, text: str, user: dict[str, Any]) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        items = user.get("action_consequences")
        if not isinstance(items, list):
            return cleaned
        recent_texts = [
            _single_line(item.get("text"), 80)
            for item in items[-5:]
            if isinstance(item, dict) and _single_line(item.get("text"), 80)
        ]
        if not recent_texts:
            return cleaned
        current_opening = re.split(r"[，,。！？!?…\s]", _single_line(cleaned, 80), maxsplit=1)[0][:6]
        repeated_opening = current_opening and any(
            re.split(r"[，,。！？!?…\s]", text, maxsplit=1)[0][:6] == current_opening
            for text in recent_texts
        )
        proactive_voice = str(
            runtime_persona_setting(self, "persona_proactive_voice_prompt", "") or ""
        )
        # Repetition control must not erase an opening explicitly defined by the persona.
        if repeated_opening and current_opening not in proactive_voice:
            cleaned = re.sub(r"^(唔|嗯|诶|啊|欸)[…\.。!！?？~～\s，,]*", "", cleaned).strip()
            cleaned = re.sub(r"^(刚好|突然|我就是|我来|来找你)[^，,。！？!?…\n]{0,16}[，,。！？!?…\s]*", "", cleaned).strip()
        if sum(cleaned.count(token) for token in ("唔", "嗯", "诶", "呀", "啦", "嘛", "哦", "呢")) >= 5:
            cleaned = re.sub(r"(呀|啦|嘛|哦|呢)(?=.*\1)", "", cleaned)
        return cleaned or str(text or "").strip()

    def _format_action_prompt_context(self, action: str, action_context: str) -> str:
        context = str(action_context or "").strip()
        if not context:
            return "普通文字"
        return _single_line(self._sanitize_action_context_text(action, context), 420)

    def _sanitize_action_boundaries(
        self,
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str = "",
        has_real_image: bool = False,
    ) -> str:
        cleaned = self._soften_social_proactive_text(text, action=action)
        if not cleaned:
            return ""
        if not has_real_image and "photo_text" not in action:
            cleaned = self._remove_unbacked_media_claims(cleaned)
        if "screen_peek" in action:
            photo_patterns = (
                "拍了张照片",
                "拍了照片",
                "拍了自拍",
                "自拍",
                "风景照",
                "窗外阳光",
                "要看看吗",
                "给你看照片",
                "发你照片",
                "看图",
            )
            if any(pattern in cleaned for pattern in photo_patterns):
                return ""
        if "poke" in action and "photo_text" not in action and "voice" not in action:
            cleaned = cleaned.replace("戳一戳", "戳你一下")
            cleaned = cleaned.replace("我刚刚戳了你", "我刚戳你了")
            cleaned = cleaned.replace("我刚刚戳了你一下", "我刚戳你了")
        if action == "voice":
            cleaned = cleaned.replace("我给你发了一条语音", "刚给你发了条语音")
            cleaned = cleaned.replace("我发了一条语音", "刚给你发了条语音")
            cleaned = cleaned.replace("我生成了一条语音", "刚给你发了条语音")
            cleaned = cleaned.replace("我合成了一条语音", "刚给你发了条语音")
            cleaned = cleaned.replace("要不要听", "你有空再听嘛")
            cleaned = cleaned.replace("要听吗", "你有空再听嘛")
        if action == "photo_text":
            if has_real_image:
                if reason not in {"bili_video_share", "news_share", "web_exploration_share"}:
                    cleaned = self._repair_non_external_title_share_text(
                        cleaned,
                        reason=reason,
                        action_context=action_context,
                    )
                replacements = {
                    "我画了一张图": "这个画面",
                    "我刚画了张图": "这个画面",
                    "我生成了一张图": "这个画面",
                    "我做了张图": "这个画面",
                    "我生了一张图": "这个画面",
                    "我渲染了一张图": "这个画面",
                    "画面是": "画面里是",
                }
                for old, new in replacements.items():
                    cleaned = cleaned.replace(old, new)
                queue_replacements = {
                    "图好了": "",
                    "图片好了": "",
                    "照片好了": "",
                    "图生成好了": "",
                    "图片生成好了": "",
                    "还在队列里": "",
                    "还在排队": "",
                    "等图出来": "",
                    "等图片出来": "",
                    "已经发过去啦": "",
                    "已经发过去了": "",
                }
                for old, new in queue_replacements.items():
                    cleaned = cleaned.replace(old, new)
                for old in ("要看看吗", "要看吗", "想看吗"):
                    cleaned = cleaned.replace(old, "")
                cleaned = self._deemphasize_state_report_preamble(cleaned, reason=reason)
                return self._soften_social_proactive_text(cleaned, action=action)
            replacements = {
                "拍了张照片": "想到一个画面",
                "拍了照片": "想到一个画面",
                "拍了美美的照片": "想到一个挺想拍下来的画面",
                "发你照片": "想跟你说说刚才那个画面",
                "给你看照片": "想跟你说说刚才那个画面",
                "要看看吗": "先跟你说一下",
                "要看吗": "先跟你说一下",
            }
            for old, new in replacements.items():
                cleaned = cleaned.replace(old, new)
        cleaned = self._deemphasize_state_report_preamble(cleaned, reason=reason)
        return self._soften_social_proactive_text(cleaned, action=action)

    def _repair_non_external_title_share_text(
        self,
        text: str,
        *,
        reason: str = "",
        action_context: str = "",
    ) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        if reason in {"bili_video_share", "news_share", "web_exploration_share"}:
            return cleaned
        title_leak_pattern = r"刚看到[，,、\s]*[“\"『「].{2,60}[”\"』」](?:这个)?标题"
        if not re.search(title_leak_pattern, cleaned):
            return cleaned
        context = _single_line(action_context, 520)
        if reason == "group_share" or "群" in context:
            repaired = re.sub(rf"{title_leak_pattern}[，,。！？!?\s]*", "", cleaned, count=1).strip()
            return repaired if len(repaired) >= 2 else ""
        if "图片路径：" in context or "真实图片文件：" in context or "photo_text" in context:
            repaired = re.sub(rf"{title_leak_pattern}[，,。！？!?\s]*", "", cleaned, count=1).strip()
            return repaired if len(repaired) >= 2 else ""
        repaired = re.sub(
            r"刚看到[，,、\s]*[“\"『「]([^”\"』」]{2,60})[”\"』」](?:这个)?标题[，,。！？!?\s]*",
            "",
            cleaned,
            count=1,
        )
        return repaired.strip()

    def _remove_unbacked_media_claims(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        replacements = {
            "我拍了张照片": "我看到一个画面",
            "我拍了照片": "我看到一个画面",
            "拍了张照片": "看到一个画面",
            "拍了照片": "看到一个画面",
            "拍了张照": "看到一个画面",
            "拍了照": "看到一个画面",
            "给你拍了张照片": "看到一个画面就想到你",
            "给你拍了照片": "看到一个画面就想到你",
            "给你拍了张照": "看到一个画面就想到你",
            "给你拍了照": "看到一个画面就想到你",
            "发你看看": "跟你说一下",
            "发给你看看": "跟你说一下",
            "发你看": "跟你说一下",
            "发给你看": "跟你说一下",
            "给你看照片": "跟你说说这个画面",
            "给你看图": "跟你说说这个画面",
            "看图": "听我说",
            "你看看喜不喜欢": "你应该会喜欢",
            "你看看喜欢吗": "你应该会喜欢",
            "你看看": "跟你说一下",
        }
        for old, new in replacements.items():
            cleaned = cleaned.replace(old, new)
        cleaned = re.sub(r"[，,、\s]*(?:照片|图片|图)(?:里|上)?[，,、\s]*(?=被|看着|颜色|特别|挺)", "画面", cleaned)
        cleaned = re.sub(r"(?:这张|那张|这幅|那幅)(?:照片|图片|图)", "这个画面", cleaned)
        cleaned = cleaned.replace("[图片]", "").replace("【图片】", "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,。")
        return cleaned

    def _is_overabstract_proactive_text(self, text: str, *, action: str) -> bool:
        cleaned = _single_line(text, 220)
        if not cleaned:
            return False
        weak_patterns = (
            "最近忙不忙",
            "发现你好像在忙",
            "数据有意思吗",
            "刚好想到你",
            "来找你一下",
            "碰你一下",
            "我就是来一下",
            "顺手来一下",
        )
        if any(token in cleaned for token in weak_patterns):
            return True
        if "screen_peek" in action and any(token in cleaned for token in ("还在忙啊", "看你在忙", "你好像在忙")):
            return True
        return False

    def _ground_proactive_text(
        self,
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str,
    ) -> str:
        context = str(action_context or "")
        if reason == "goodnight_screen_check":
            return "还没睡的话，忙完就早点休息，不用回我。"
        if "screen_peek" in action:
            if "逻辑分支" in context:
                return "你还在跟那个逻辑分支较劲啊。先别急,慢慢捋嘛。"
            if any(token in context for token in ("测试", "进度", "插件")):
                return "你还在盯那个进度啊。眼睛先歇一下啦。"
            return "你半天都没抬头了诶。先缓一口气。"
        if "poke" in action:
            return "我刚戳你了。怎么又不出声啦。"
        if "photo_text" in action:
            return text
        if "voice" in action:
            return "刚给你发了条语音。你有空再听嘛。"
        if reason == "quiet_care":
            return "感觉你这阵子都没怎么松下来。歇一小会儿嘛,又不会怎样。"
        if reason == "evening_greeting":
            return "都这个点了,你还没收工吗。别一直绷着啦。"
        if reason == "noon_greeting":
            return "中午了诶。你吃东西没有,别又随便糊弄过去。"
        if reason in {"meal_care", "meal_care_followup"}:
            return "到饭点了。你吃东西没有呀？"
        if reason in {"activity_share", "diary_share", "background_schedule"}:
            return "有件小事想跟你说一下。"
        return "刚好到能休息一小会儿的时候,想问你一句。"

    async def _execute_proactive_action(
        self,
        action: str,
        user: dict[str, Any],
        name: str,
        reason: str,
    ) -> dict[str, Any]:
        normalized = str(action or "message").strip() or "message"
        parts = [part.strip() for part in normalized.split("+") if part.strip()]
        if not parts:
            parts = ["message"]
        contexts: list[str] = []
        extra_components: list[Any] = []
        summary_parts: list[str] = []
        effective_parts: list[str] = []
        for part in parts:
            payload = await self._execute_single_action(part, user, name, reason)
            contexts.append(str(payload.get("context") or "").strip())
            extra_components.extend(list(payload.get("extra_components") or []))
            summary = _single_line(payload.get("summary") or part, 60)
            if summary:
                summary_parts.append(summary)
            effective_action = _single_line(payload.get("effective_action") or part, 40)
            if effective_action:
                effective_parts.append(effective_action)
            if not bool(payload.get("success", True)):
                return {
                    "success": False,
                    "context": "\n".join(item for item in contexts if item),
                    "extra_components": [],
                    "summary": " + ".join(summary_parts) or normalized,
                    "effective_action": "+".join(effective_parts) or normalized,
                }
        return {
            "success": True,
            "context": "\n".join(item for item in contexts if item) or "message：只发送私聊文本",
            "extra_components": extra_components,
            "summary": " + ".join(summary_parts) or normalized,
            "effective_action": "+".join(effective_parts) or normalized,
        }

    async def _execute_single_action(
        self,
        action: str,
        user: dict[str, Any],
        name: str,
        reason: str,
    ) -> dict[str, Any]:
        fallback_action = self._fallback_action_for_unavailable(action, user)
        if fallback_action != action:
            logger.info(
                "[PrivateCompanion] 主动行为依赖不可用,已回退: requested=%s fallback=%s user=%s",
                action,
                fallback_action,
                str(user.get("user_id") or ""),
            )
            if fallback_action == "message":
                return {
                    "success": True,
                    "context": "message：只发送普通私聊文本",
                    "extra_components": [],
                    "summary": "文字",
                    "effective_action": "message",
                }
            return await self._execute_single_action(fallback_action, user, name, reason)
        if action == "screen_peek":
            context = await self._run_screen_peek_action(
                user,
                name,
                reason,
                quota_exempt=bool(user.get("planned_proactive_quota_exempt")),
            )
            return {
                "success": not self._is_unusable_screen_peek_context(context),
                "context": context,
                "extra_components": [],
                "summary": "窥屏",
                "effective_action": "screen_peek",
            }
        if action == "photo_text":
            context = await self._run_photo_text_action(user, name, reason)
            image_ready = "真实图片" in context and "图片路径：" in context
            if not image_ready and reason == "birthday_celebration":
                return {
                    "success": True,
                    "context": "message：生日卡未生成，改为只发送生日祝福正文",
                    "extra_components": [],
                    "summary": "生日祝福文字",
                    "effective_action": "message",
                }
            return {
                "success": image_ready,
                "context": context,
                "extra_components": [],
                "summary": "发图",
                "effective_action": "photo_text",
            }
        if action == "poke":
            context = await self._run_poke_action(user, name, reason)
            return {
                "success": context.startswith("poke：已"),
                "context": context,
                "extra_components": [],
                "summary": "戳了你一下",
                "effective_action": "poke",
            }
        if "voice" in action and "photo_text" not in action:
            payload = await self._run_voice_action(user, name, reason)
            payload.setdefault("summary", "留了句语音")
            payload.setdefault("effective_action", "voice")
            return payload
        if action == "jm_cosmos_read":
            result = await self._run_jm_cosmos_read_action(user)
            if isinstance(result, dict):
                user["jm_cosmos_reading_context"] = result
                return {
                    "success": True,
                    "context": self._format_jm_cosmos_action_context(user),
                    "extra_components": [],
                    "summary": "私下翻了会儿漫画",
                    "effective_action": "jm_cosmos_read",
                }
            return {
                "success": False,
                "context": "私密阅读线索：这次没有找到适合继续看的内容",
                "extra_components": [],
                "summary": "没有读到合适内容",
                "effective_action": "jm_cosmos_read",
            }
        if action.startswith("external:"):
            return await self._execute_external_proactive_ability(action.split(":", 1)[1], user, name, reason)
        return {"success": True, "context": "message：只发送私聊文本", "extra_components": [], "summary": "文字", "effective_action": "message"}

    async def _execute_external_proactive_ability(
        self,
        ability_name: str,
        user: dict[str, Any],
        display_name: str,
        reason: str,
    ) -> dict[str, Any]:
        user = user if isinstance(user, dict) else {}
        name = self._normalize_external_ability_name(ability_name)
        runtime = self._external_proactive_abilities.get(name)
        if not isinstance(runtime, dict) or not callable(runtime.get("executor")):
            return {"success": False, "context": "external：外部主动能力未注册或不可用", "extra_components": [], "summary": "外部能力不可用", "effective_action": "message"}
        user_key = _single_line(
            user.get("user_id") or user.get("id") or user.get("umo"),
            180,
        ) or "global"
        lock_key = f"{name}:{user_key}"
        locks = getattr(self, "_external_ability_execution_locks", None)
        if not isinstance(locks, dict):
            locks = {}
            self._external_ability_execution_locks = locks
        lock = locks.get(lock_key)
        if not isinstance(lock, asyncio.Lock):
            if len(locks) >= 512:
                for old_key, old_lock in list(locks.items()):
                    if isinstance(old_lock, asyncio.Lock) and not old_lock.locked():
                        locks.pop(old_key, None)
                    if len(locks) < 384:
                        break
            lock = asyncio.Lock()
            locks[lock_key] = lock
        async with lock:
            runtime = self._external_proactive_abilities.get(name)
            if not isinstance(runtime, dict) or not callable(runtime.get("executor")):
                return {
                    "success": False,
                    "context": "external：外部主动能力未注册或不可用",
                    "extra_components": [],
                    "summary": "外部能力不可用",
                    "effective_action": "message",
                }
            return await self._execute_external_proactive_ability_locked(
                name,
                runtime,
                user,
                display_name,
                reason,
            )

    async def _execute_external_proactive_ability_locked(
        self,
        name: str,
        runtime: dict[str, Any],
        user: dict[str, Any],
        display_name: str,
        reason: str,
    ) -> dict[str, Any]:
        available = {
            self._normalize_external_ability_name(item.get("name"))
            for item in self._available_external_proactive_abilities(user)
            if isinstance(item, dict)
        }
        if name not in available:
            return {
                "success": False,
                "context": f"external:{name}：当前不可用或仍在冷却",
                "extra_components": [],
                "summary": "外部能力暂不可用",
                "effective_action": "message",
            }
        config = self._external_ability_config(name)
        call_context = {
            "user": dict(user or {}),
            "display_name": display_name,
            "reason": reason,
            "bot_name": runtime_persona_setting(self, "bot_name", "小星"),
            "state": deepcopy(self.data.get("daily_state", {})),
            "current_plan_item": deepcopy(self._get_current_plan_item(self.data.get("daily_plan", {})) or {}),
            "config": config,
            "plugin": self,
        }
        try:
            result = runtime["executor"](call_context)
            if hasattr(result, "__await__"):
                result = await result
        except Exception as exc:
            logger.warning("[PrivateCompanion] 外部主动能力执行失败: %s: %s", name, exc, exc_info=True)
            self._note_external_ability_execution(
                name,
                user=user,
                success=False,
                status=f"执行失败: {exc}",
            )
            return {"success": False, "context": f"external:{name}：执行失败", "extra_components": [], "summary": "外部能力失败", "effective_action": f"external:{name}"}
        payload = result if isinstance(result, dict) else {"text": str(result or "")}
        success = bool(payload.get("ok", payload.get("success", True)))
        text = _single_line(payload.get("text"), 500)
        context = str(payload.get("context") or payload.get("summary") or text or "").strip()
        image_path = str(payload.get("image_path") or "").strip()
        extra_components = list(payload.get("extra_components") or []) if isinstance(payload.get("extra_components"), list) else []
        if image_path and os.path.exists(image_path):
            extra_components.extend(self._build_outbound_chain("", image_path))
        snapshot = payload.get("photo_snapshot") if isinstance(payload.get("photo_snapshot"), dict) else {}
        if success and image_path and os.path.exists(image_path) and snapshot:
            remember = getattr(self, "_remember_recent_photo_share_snapshot", None)
            if callable(remember):
                remember(
                    user,
                    caption=_single_line(snapshot.get("caption"), 260),
                    topic=_single_line(snapshot.get("topic"), 100),
                    motive=_single_line(snapshot.get("motive"), 180),
                    reason=_single_line(snapshot.get("reason"), 40) or name,
                    subject_owner=_single_line(snapshot.get("subject_owner"), 20),
                )
        memory = _single_line(payload.get("memory"), 500)
        if memory:
            user.setdefault("external_proactive_memory", [])
            memories = user.get("external_proactive_memory")
            if not isinstance(memories, list):
                memories = []
                user["external_proactive_memory"] = memories
            memories.append({"name": name, "ts": _now_ts(), "memory": memory})
            del memories[:-12]
        self._note_external_ability_execution(
            name,
            user=user,
            success=success,
            status=_single_line(payload.get("status") or context, 120),
            summary=_single_line(payload.get("summary") or text, 120),
        )
        return {
            "success": success,
            "context": f"external:{name}：{context or '外部能力已执行'}",
            "extra_components": extra_components,
            "summary": _single_line(payload.get("summary") or runtime.get("label") or name, 60),
            "effective_action": f"external:{name}",
        }

    def _note_external_ability_execution(
        self,
        name: str,
        *,
        user: dict[str, Any] | None = None,
        success: bool,
        status: str = "",
        summary: str = "",
    ) -> None:
        try:
            store = self._external_ability_store()
            item = store.get(name) if isinstance(store.get(name), dict) else {"name": name}
            executed_at = _now_ts()
            item["last_executed_ts"] = executed_at
            item["last_status"] = status
            item["last_summary"] = summary
            item["success_count"] = _safe_int(item.get("success_count"), 0, 0) + (1 if success else 0)
            item["failure_count"] = _safe_int(item.get("failure_count"), 0, 0) + (0 if success else 1)
            store[name] = item
            if isinstance(user, dict):
                user_last = user.setdefault("external_proactive_ability_last", {})
                if not isinstance(user_last, dict):
                    user_last = {}
                    user["external_proactive_ability_last"] = user_last
                user_last[name] = executed_at
            save_sections = {"external_proactive_abilities"}
            if isinstance(user, dict):
                save_sections.add("users")
            self._save_data_sync(sections=save_sections)
        except Exception:
            pass

    def _is_unusable_screen_peek_context(self, context: str) -> bool:
        text = str(context or "").strip()
        if not text:
            return True
        fail_tokens = (
            "screen_peek：失败",
            "屏幕插件不可用",
            "未授权",
            "不可用",
            "Invalid base64 image_url",
            "图片预处理结果为空",
            "所有视觉链路都失败",
            "视觉 provider 调用失败",
            "当前 provider 不支持原生视频上传",
            "没看清",
            "稍后再让我看看",
            "没有得到屏幕观察结果",
            "识屏分析失败",
        )
        return any(token in text for token in fail_tokens)

    def _is_screen_peek_provider_failure(self, context: str) -> bool:
        text = str(context or "")
        fail_tokens = (
            "Invalid base64 image_url",
            "图片预处理结果为空",
            "所有视觉链路都失败",
            "视觉 provider 调用失败",
            "Asset upload returned",
            "BadRequest",
            "InvalidParameter",
        )
        return any(token in text for token in fail_tokens)

    @staticmethod
    def _goodnight_screen_check_reply_matches(text: Any) -> bool:
        cleaned = _single_line(text, 240)
        if not cleaned:
            return False
        return bool(re.search(r"晚安|好梦|早点睡|睡吧|休息吧|明天见", cleaned))

    def _maybe_schedule_goodnight_screen_check(
        self,
        user: dict[str, Any],
        bot_reply: Any,
        *,
        now: float | None = None,
    ) -> bool:
        """Schedule one private screen check after a mutual goodnight."""
        if not bool(runtime_persona_setting(self, "enable_screen_glance_action", False)) or not bool(
            runtime_persona_setting(self, "enable_goodnight_screen_check", False)
        ):
            return False
        if not isinstance(user, dict) or not self._goodnight_screen_check_reply_matches(bot_reply):
            return False
        user_id = _single_line(user.get("user_id") or user.get("id"), 128)
        if not user_id or self._private_user_role(user, user_id) != "owner":
            return False
        umo = _single_line(user.get("umo"), 240)
        if not umo or ":FriendMessage:" not in umo or not user.get("enabled", True):
            return False

        rest_kind = _single_line(user.get("user_rest_kind"), 24).lower()
        rest_set_at = _safe_float(user.get("user_rest_set_at"), 0)
        rest_reason = _single_line(user.get("user_rest_reason"), 240)
        if rest_kind != "sleep" or rest_set_at <= 0:
            return False
        quiet_checker = getattr(self, "_user_rest_signal_should_block_current_reply", None)
        if callable(quiet_checker) and quiet_checker(rest_reason):
            return False

        check_now = _now_ts() if now is None else float(now)
        if check_now + 0.001 < rest_set_at or check_now - rest_set_at > 30 * 60:
            return False
        episode_key = f"{user_id}:{rest_set_at:.3f}"
        if _single_line(user.get("goodnight_screen_check_episode_key"), 180) == episode_key:
            return False
        if _single_line(user.get("goodnight_screen_check_checked_episode_key"), 180) == episode_key:
            return False

        delay_minutes = max(
            1,
            min(
                180,
                _safe_int(
                    runtime_persona_setting(
                        self, "goodnight_screen_check_delay_minutes", 45
                    ),
                    45,
                    1,
                    180,
                ),
            ),
        )
        user["goodnight_screen_check_due_at"] = check_now + delay_minutes * 60
        user["goodnight_screen_check_episode_at"] = rest_set_at
        user["goodnight_screen_check_episode_key"] = episode_key
        user["goodnight_screen_check_scheduled_at"] = check_now
        user["goodnight_screen_check_checked_at"] = 0
        user["goodnight_screen_check_state"] = "scheduled"
        return True

    def _goodnight_screen_check_block_reason(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        episode_at: float,
        now: float,
        require_screen: bool,
    ) -> str:
        if not bool(runtime_persona_setting(self, "enable_screen_glance_action", False)):
            return "screen_glance_disabled"
        if not bool(runtime_persona_setting(self, "enable_goodnight_screen_check", False)):
            return "goodnight_screen_check_disabled"
        if not isinstance(user, dict) or self._private_user_role(user, user_id) != "owner":
            return "not_primary_user"
        enabled_checker = getattr(self, "_user_enabled_for_proactive", None)
        if callable(enabled_checker) and not enabled_checker(user_id, user):
            return "private_proactive_disabled"
        umo = _single_line(user.get("umo"), 240)
        if not umo or ":FriendMessage:" not in umo:
            return "private_route_unavailable"
        generation_disabled = getattr(self, "_proactive_generation_disabled", None)
        if callable(generation_disabled) and generation_disabled(user):
            return "proactive_generation_disabled"

        rest_set_at = _safe_float(user.get("user_rest_set_at"), 0)
        if _single_line(user.get("user_rest_kind"), 24).lower() != "sleep" or abs(rest_set_at - episode_at) > 0.01:
            return "goodnight_episode_ended"
        rest_reason = _single_line(user.get("user_rest_reason"), 240)
        quiet_checker = getattr(self, "_user_rest_signal_should_block_current_reply", None)
        if callable(quiet_checker) and quiet_checker(rest_reason):
            return "explicit_do_not_disturb"
        latest_activity = max(
            _safe_float(user.get("last_activity_at"), 0),
            _safe_float(user.get("last_user_message_at"), 0),
        )
        if latest_activity > episode_at + 0.001:
            return "user_active_after_goodnight"
        rest_until_getter = getattr(self, "_user_rest_silence_until", None)
        if callable(rest_until_getter) and rest_until_getter(user, now=now) <= now:
            return "rest_window_ended"

        reset_daily = getattr(self, "_reset_daily_counter_if_needed", None)
        if callable(reset_daily):
            reset_daily(user)
        daily_limit_getter = getattr(self, "_effective_user_daily_limit", None)
        daily_limit = daily_limit_getter(user) if callable(daily_limit_getter) else 0
        unlimited_checker = getattr(self, "_proactive_daily_limit_is_unlimited", None)
        unlimited = bool(unlimited_checker(daily_limit)) if callable(unlimited_checker) else False
        if daily_limit <= 0 or (not unlimited and _safe_int(user.get("sent_today"), 0) >= daily_limit):
            return "daily_proactive_limit"

        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        if not callable(expression_builder):
            return "expression_decision_unavailable"
        try:
            decision = expression_builder(
                user,
                proactive_candidate={"eligible": True, "dynamic_allowance": daily_limit, "current_ts": now},
                message_intent={"requested_content_tier": "normal"},
                now=now,
            )
            expression = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
        except Exception:
            return "expression_decision_unavailable"
        if _single_line(expression.get("blocker"), 40):
            return f"expression_{_single_line(expression.get('blocker'), 40)}"
        if _safe_float(expression.get("proactive_cooldown_until"), 0) > now:
            return "expression_proactive_cooldown"
        if _safe_int(expression.get("proactive_budget"), 0, 0) <= 0:
            return "expression_proactive_budget_zero"
        if bool(user.get("proactive_sending")):
            return "another_proactive_message_is_sending"
        if require_screen and not self._screen_glance_available(user):
            return "screen_glance_unavailable"
        return ""

    async def _classify_goodnight_screen_activity(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        name: str,
    ) -> str:
        plugin = self._get_screen_companion_plugin()
        if plugin is None or not callable(getattr(plugin, "_invoke_screen_skill", None)):
            return "uncertain"
        async with self._data_lock:
            current = self._get_user(user_id)
            self._note_screen_peek_attempt(user_id, reason="goodnight_screen_check", count_daily=True)
            self._save_data_sync(sections={"users"})

        event = None
        target = _single_line(user.get("umo"), 240)
        if target and hasattr(plugin, "_create_virtual_event"):
            try:
                event = plugin._create_virtual_event(target)
            except Exception as exc:
                logger.debug("[PrivateCompanion] 创建晚安识屏虚拟事件失败: %s", _single_line(exc, 160))
        prompt = (
            "这是一次用户已授权的晚安后单次状态确认，只用于决定是否需要轻声提醒休息。"
            "请只判断当前画面是否能明确证明用户仍在主动使用电脑，不要转述或摘录任何屏幕内容。"
            "active 仅用于存在明确持续操作或正在进行活动的证据；画面静止、锁屏、黑屏、无人操作、"
            "证据不足或无法判断都输出 inactive 或 uncertain。"
            "只输出 JSON：{\"state\":\"active|inactive|uncertain\",\"reason\":\"不含隐私的极短判断依据\"}。"
            "reason 禁止包含应用名、窗口名、账号、联系人、文件名、聊天内容、网页内容或屏幕文字。"
        )
        try:
            result = await plugin._invoke_screen_skill(
                event,
                request_prompt=prompt,
                history_user_text=f"晚安后单次确认 {name or '用户'} 是否仍在主动使用电脑。",
                task_id="private_companion_goodnight_screen_check",
            )
        except Exception as exc:
            context = f"goodnight_screen_check：失败,{_single_line(exc, 240)}"
            logger.warning("[PrivateCompanion] 晚安识屏判断失败: %s", _single_line(exc, 180))
            if self._is_screen_peek_provider_failure(context):
                self._note_screen_peek_failure(user, context)
            return "uncertain"
        if self._is_screen_peek_provider_failure(str(result or "")):
            self._note_screen_peek_failure(user, _single_line(result, 180))
            return "uncertain"
        parser = getattr(self, "_parse_json_object", None)
        parsed = parser(result) if callable(parser) else None
        if not isinstance(parsed, dict) and isinstance(result, dict):
            parsed = result
        state = _single_line(parsed.get("state"), 24).lower() if isinstance(parsed, dict) else ""
        return state if state in {"active", "inactive", "uncertain"} else "uncertain"

    async def _maybe_process_goodnight_screen_checks(self) -> None:
        now = _now_ts()
        claimed: list[tuple[str, float, str]] = []
        changed = False
        async with self._data_lock:
            users = self.data.get("users")
            if not isinstance(users, dict):
                return
            for raw_user_id, user in users.items():
                if not isinstance(user, dict):
                    continue
                due_at = _safe_float(user.get("goodnight_screen_check_due_at"), 0)
                if due_at <= 0 or due_at > now:
                    continue
                user_id = _single_line(user.get("user_id") or raw_user_id, 128)
                episode_at = _safe_float(user.get("goodnight_screen_check_episode_at"), 0)
                episode_key = _single_line(user.get("goodnight_screen_check_episode_key"), 180)
                user["goodnight_screen_check_due_at"] = 0
                user["goodnight_screen_check_checked_at"] = now
                user["goodnight_screen_check_checked_episode_key"] = episode_key
                user["goodnight_screen_check_state"] = "claimed"
                changed = True
                if user_id and episode_at > 0:
                    claimed.append((user_id, episode_at, episode_key))
            if changed:
                self._save_data_sync(sections={"users"})

        for user_id, episode_at, episode_key in claimed:
            async with self._data_lock:
                user = self._get_user(user_id)
                block_reason = self._goodnight_screen_check_block_reason(
                    user_id,
                    user,
                    episode_at=episode_at,
                    now=_now_ts(),
                    require_screen=True,
                )
                if block_reason:
                    user["goodnight_screen_check_state"] = block_reason
                    self._save_data_sync(sections={"users"})
                    continue
                name = _single_line(user.get("nickname"), 40) or user_id

            state = await self._classify_goodnight_screen_activity(user_id, user, name=name)
            async with self._data_lock:
                current = self._get_user(user_id)
                current["goodnight_screen_check_state"] = state
                current["goodnight_screen_check_result_at"] = _now_ts()
                self._save_data_sync(sections={"users"})
            if state != "active":
                continue

            async with self._data_lock:
                current = self._get_user(user_id)
                block_reason = self._goodnight_screen_check_block_reason(
                    user_id,
                    current,
                    episode_at=episode_at,
                    now=_now_ts(),
                    require_screen=False,
                )
                if block_reason:
                    current["goodnight_screen_check_state"] = block_reason
                    self._save_data_sync(sections={"users"})
                    continue
                current["proactive_sending"] = True
                current["proactive_sending_started_at"] = _now_ts()
                user = current
                name = _single_line(current.get("nickname"), 40) or user_id
                umo = _single_line(current.get("umo"), 240)
                self._save_data_sync(sections={"users"})

            motive = "互道晚安后仍有明确活动迹象，轻声提醒一次早点休息，不要求回复"
            safe_context = "内部状态判断：晚安后仍有明确活动迹象；没有提供任何屏幕内容或应用信息"
            try:
                text = await self._generate_proactive_message_with_llm(
                    user,
                    name,
                    "goodnight_screen_check",
                    action_context=safe_context,
                    action="message",
                    motive=motive,
                )
                if not text:
                    continue
                review = await self._review_proactive_message_send_decision(
                    user,
                    text,
                    reason="goodnight_screen_check",
                    action="message",
                    motive=motive,
                    topic="早点休息",
                    action_summary=safe_context,
                )
                decision = _single_line(review.get("decision"), 20).lower()
                if decision in {"drop", "defer"}:
                    continue
                if decision == "rewrite" and _single_line(review.get("text"), 500):
                    text = _single_line(review.get("text"), 500)
                outcome = await self._send_proactive_message_chain(umo, text)
                if not bool(getattr(outcome, "delivered", False)):
                    continue
                delivered_text = str(
                    getattr(outcome, "delivered_text", "") or text
                ).strip()
                delivery_umo = str(
                    getattr(outcome, "delivery_umo", "") or umo
                ).strip()
                assistant_archive_text = self._delivered_assistant_text_from_chain(
                    list(getattr(outcome, "delivered_chain", ()) or ()),
                    fallback_text=delivered_text,
                )
                if getattr(self, "context", None) is not None:
                    await self._archive_proactive_message_to_conversation(
                        user=user,
                        umo=delivery_umo,
                        user_prompt=self._build_proactive_archive_user_prompt(
                            reason="goodnight_screen_check",
                            action="message",
                            motive=motive,
                            action_summary=safe_context,
                        ),
                        assistant_response=assistant_archive_text,
                    )
                await self._record_final_assistant_in_livingmemory(
                    umo=delivery_umo,
                    assistant_response=assistant_archive_text,
                    delivery_id=f"goodnight:{user_id}:{_now_ts():.6f}",
                )
                memory_companion_recorder = getattr(
                    self,
                    "_memory_companion_record_proactive_message",
                    None,
                )
                if callable(memory_companion_recorder):
                    await memory_companion_recorder(
                        user=user,
                        user_id=user_id,
                        text=delivered_text,
                        umo=delivery_umo,
                        reason="goodnight_screen_check",
                        action="message",
                        motive=motive,
                        action_summary=safe_context,
                    )
                sent_at = _now_ts()
                visible = self._visible_text_without_tts_reading(delivered_text, limit=500)
                async with self._data_lock:
                    current = self._get_user(user_id)
                    self._reset_daily_counter_if_needed(current)
                    current["last_sent"] = sent_at
                    current["last_proactive_sent_at"] = sent_at
                    current["last_proactive_message"] = _single_line(visible, 500)
                    current["last_companion_message"] = _single_line(visible, 500)
                    current["last_companion_message_at"] = sent_at
                    current["last_proactive_reason"] = "goodnight_screen_check"
                    current["last_proactive_action"] = "message"
                    current["last_proactive_motive"] = motive
                    current["last_proactive_delivery_umo"] = delivery_umo
                    current["last_proactive_delivery_inbound_count"] = _safe_int(current.get("inbound_count"), 0)
                    current["goodnight_screen_check_reminded_at"] = sent_at
                    current["goodnight_screen_check_state"] = "reminded"
                    current["goodnight_screen_check_reminded_episode_key"] = episode_key
                    current["sent_today"] = _safe_int(current.get("sent_today"), 0) + 1
                    current["proactive_sent_count"] = _safe_int(current.get("proactive_sent_count"), 0) + 1
                    self._save_data_sync(sections={"users"})
            finally:
                async with self._data_lock:
                    current = self._get_user(user_id)
                    current["proactive_sending"] = False
                    current["proactive_sending_started_at"] = 0
                    self._save_data_sync(sections={"users"})

    async def _run_screen_peek_action(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        *,
        quota_exempt: bool = False,
    ) -> str:
        if not runtime_persona_setting(self, "enable_screen_glance_action", False):
            return "screen_peek：未授权,跳过"
        plugin = self._get_screen_companion_plugin()
        if plugin is None:
            return "screen_peek：屏幕插件不可用"
        target = str(user.get("umo") or "").strip()
        if not self._screen_glance_available(user, ignore_daily_limit=quota_exempt):
            return "screen_peek：今日额度或冷却未满足,跳过"
        async with self._data_lock:
            self._note_screen_peek_attempt(
                str(user.get("user_id") or user.get("umo") or name),
                reason=reason,
                count_daily=not quota_exempt,
            )
            self._save_data_sync(sections={"users"})
        event = None
        if target and hasattr(plugin, "_create_virtual_event"):
            try:
                event = plugin._create_virtual_event(target)
            except Exception as e:
                logger.debug(f"[PrivateCompanion] 创建屏幕虚拟事件失败: {e}")
        prompt = (
            f"这是一次用户已授权的主动陪伴行为。请只做视觉观察,"
            f"用很短的话描述用户电脑当前大概在看什么、做什么、是不是像在忙。"
            f"不要直接对用户说话,不要安慰、提醒、关心、陪伴,不要输出隐私细节、账号、完整文本、聊天内容。"
            f"只留一个内部观察印象。主动原因：{reason}"
        )
        try:
            result = await plugin._invoke_screen_skill(
                event,
                request_prompt=prompt,
                history_user_text=f"主动陪伴想轻轻看一眼 {name} 现在在忙什么。",
                task_id="private_companion_screen_peek",
            )
            context = "screen_peek：\n" + (_single_line(result, 300) if result else "没有得到屏幕观察结果")
            if self._is_screen_peek_provider_failure(context):
                self._note_screen_peek_failure(user, context)
            return context
        except Exception as e:
            error_text = _single_line(e, 240)
            logger.warning(f"[PrivateCompanion] screen_peek 主动行为失败: {error_text}")
            context = f"screen_peek：失败,{error_text}"
            if self._is_screen_peek_provider_failure(context):
                self._note_screen_peek_failure(user, context)
            return context

    def _get_screen_companion_plugin(self) -> Any:
        for module_name in ("astrbot_plugin_screen_companion.main", "data.plugins.astrbot_plugin_screen_companion.main"):
            try:
                module = importlib.import_module(module_name)
                plugin = getattr(module, "_screen_companion_tool_plugin", None)
                if plugin is not None and callable(getattr(plugin, "_invoke_screen_skill", None)):
                    return plugin
            except Exception:
                continue
        for module in list(sys.modules.values()):
            try:
                plugin = getattr(module, "_screen_companion_tool_plugin", None)
                if plugin is not None and callable(getattr(plugin, "_invoke_screen_skill", None)):
                    return plugin
            except Exception:
                continue
        return None

    def _poke_action_cooldown_remaining(self, user: dict[str, Any] | None, *, now: float | None = None) -> float:
        if not isinstance(user, dict):
            return 0.0
        current_ts = float(now if now is not None else _now_ts())
        inflight_until = _safe_float(user.get("poke_action_inflight_until"), 0.0)
        if inflight_until > current_ts:
            return inflight_until - current_ts
        cooldown_seconds = max(
            0,
            _safe_int(
                runtime_persona_setting(self, "poke_action_cooldown_minutes", 30),
                30,
                0,
                1440,
            ),
        ) * 60
        last_at = _safe_float(user.get("last_poke_action_at"), 0.0)
        return max(0.0, last_at + cooldown_seconds - current_ts) if cooldown_seconds > 0 and last_at > 0 else 0.0

    async def _send_single_poke(self, client: Any, *, user_id: str, group_id: str) -> None:
        if group_id and callable(getattr(client, "group_poke", None)):
            await client.group_poke(group_id=int(group_id), user_id=int(user_id))
            return
        if not group_id and callable(getattr(client, "friend_poke", None)):
            await client.friend_poke(user_id=int(user_id))
            return
        try:
            from data.plugins.astrbot_plugin_pokepro.core.send_poke import PokeSender
        except Exception:
            from astrbot_plugin_pokepro.core.send_poke import PokeSender
        await PokeSender.poke_func(client=client, user_id=user_id, group_id=group_id or None)

    async def _run_poke_action(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        *,
        explicit_count: int | None = None,
    ) -> str:
        if not runtime_persona_setting(self, "enable_poke_action", False):
            return "poke：未启用"
        user_umo = str(user.get("umo") or "")
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("poke", umo=user_umo):
            return "poke：当前平台不支持戳一戳，已改用普通文字"
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return "poke：未找到可用的 QQ 客户端"
        user_id = str(user.get("user_id") or "").strip()
        if not user_id.isdigit():
            return "poke：目标 QQ 号无效"
        group_id = self._extract_group_id_from_umo(str(user.get("umo") or ""))
        max_count = min(3, max(0, self._effective_user_poke_daily_limit(user)))
        if max_count <= 0:
            return "poke：当前用户未允许主动戳一戳"
        requested_count = int(explicit_count) if explicit_count is not None else self._choose_poke_repeat_count(user, reason)
        poke_count = max(1, min(max_count, requested_count))
        reserved = False
        try:
            async with self._data_lock:
                current = self._get_user(user_id)
                now = _now_ts()
                remaining = self._poke_action_cooldown_remaining(current, now=now)
                if remaining > 0:
                    return f"poke：冷却中，约 {max(1, math.ceil(remaining / 60))} 分钟后可再次执行"
                current["poke_action_inflight_until"] = now + max(30.0, poke_count * 3.0)
                current["poke_echo_suppress_until"] = now + max(30.0, poke_count * 3.0)
                self._save_data_sync(sections={"users"})
                reserved = True
            for index in range(poke_count):
                await self._send_single_poke(client, user_id=user_id, group_id=group_id)
                if index + 1 < poke_count:
                    await asyncio.sleep(random.uniform(0.35, 0.9))
            async with self._data_lock:
                current = self._get_user(user_id)
                current["last_poke_action_at"] = _now_ts()
                current["poke_action_inflight_until"] = 0
                self._save_data_sync(sections={"users"})
            if poke_count <= 1:
                return f"poke：已轻轻戳了 {name} 一下\n主动原因：{reason}"
            return f"poke：已轻轻连着戳了 {name} {poke_count} 下\n主动原因：{reason}"
        except Exception as e:
            if reserved:
                try:
                    async with self._data_lock:
                        current = self._get_user(user_id)
                        current["poke_action_inflight_until"] = 0
                        self._save_data_sync(sections={"users"})
                except Exception:
                    pass
            logger.warning(f"[PrivateCompanion] poke 主动行为失败: {e}")
            return f"poke：失败,{e}"

    def _choose_poke_repeat_count(self, user: dict[str, Any], reason: str) -> int:
        max_times = self._effective_user_poke_daily_limit(user)
        if max_times <= 0:
            return 0
        if max_times <= 1:
            return 1
        motive = _single_line(
            user.get("planned_proactive_motive") or user.get("last_proactive_motive"),
            120,
        )
        profile = self._persona_action_profile()
        weights: list[tuple[int, float]] = [(1, 1.0)]
        second_weight = 0.45
        third_weight = 0.12
        if profile.get("playful"):
            second_weight += 0.22
            third_weight += 0.1
        if profile.get("clingy"):
            second_weight += 0.12
            third_weight += 0.06
        if reason in {"quiet_care", "check_in"}:
            second_weight += 0.08
        if any(token in motive for token in ("轻轻叫你", "刷存在感", "碰你一下", "没忍住", "冒个头")):
            second_weight += 0.15
        if any(token in motive for token in ("偷偷看", "放心不下", "想起你", "不想吵你")):
            third_weight += 0.04
        weights.append((2, second_weight))
        if max_times >= 3:
            weights.append((3, third_weight))
        return int(self._weighted_choice([(str(count), weight) for count, weight in weights]))

    def _choose_pre_message_poke_count(
        self,
        user: dict[str, Any],
        reason: str,
        *,
        action: str = "message",
        motive: str = "",
    ) -> int:
        if "poke" in {part.strip() for part in str(action or "").split("+") if part.strip()}:
            return 0
        if (
            not self._poke_available()
            or self._effective_user_poke_daily_limit(user) <= 0
            or self._poke_action_cooldown_remaining(user) > 0
        ):
            return 0
        profile = self._persona_action_profile()
        probability = 0.12
        if reason in {"check_in", "quiet_care", "important_date_share"}:
            probability += 0.22
        if profile.get("playful"):
            probability += 0.14
        if profile.get("clingy"):
            probability += 0.08
        if action in {"voice", "photo_text"}:
            probability -= 0.02
        motive_text = str(motive or user.get("planned_proactive_motive") or "")
        if any(token in motive_text for token in ("轻轻叫你", "戳", "碰碰你", "确认一下", "放心不下", "叫你一声")):
            probability += 0.08
        probability = max(0.0, min(0.72, probability))
        if random.random() >= probability:
            return 0
        return self._choose_poke_repeat_count(user, reason)

    async def _maybe_run_pre_message_poke(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        *,
        action: str = "message",
        motive: str = "",
    ) -> tuple[int, str]:
        poke_count = self._choose_pre_message_poke_count(
            user,
            reason,
            action=action,
            motive=motive,
        )
        if poke_count <= 0:
            return 0, ""
        context = await self._run_poke_action(user, name, reason, explicit_count=poke_count)
        if not context.startswith("poke：已"):
            return 0, context
        return poke_count, context

    async def _run_voice_action(self, user: dict[str, Any], name: str, reason: str) -> dict[str, Any]:
        if not runtime_persona_setting(self, "enable_voice_action", False):
            return {"success": False, "context": "voice：未启用", "extra_components": [], "summary": "语音"}
        target = str(user.get("umo") or "").strip()
        if not target:
            return {"success": False, "context": "voice：缺少目标会话,无法发送语音", "extra_components": [], "summary": "语音"}
        voice_text = await self._build_voice_note_text(user, name, reason, target=target)
        touch_allowed = getattr(self, "_reality_touch_proactive_voice_allowed", lambda _: False)(user)
        components, audio_note = await self._create_voice_record_component(
            target,
            voice_text,
            defer_local_playback=touch_allowed,
        )
        if not components:
            return {
                "success": False,
                "context": (
                    "voice：语音生成失败\n"
                    f"想说的话：{voice_text}\n"
                    f"失败原因：{_single_line(audio_note, 160)}"
                ),
                "extra_components": [],
                "summary": "语音",
            }
        touch_player = getattr(self, "_mirror_reality_touch_proactive_voice", None)
        touched = bool(await touch_player(user, audio_note)) if touch_allowed and callable(touch_player) else False
        return {
            "success": True,
            "context": (
                "voice：已生成真实语音\n"
                f"语音内容：{self._strip_tts_markup(voice_text)}\n"
                f"真实语音文件：{audio_note}\n"
                f"现实触及：{'已同步到所选电脑音频设备' if touched else '未同步到电脑音频设备'}"
            ),
            "extra_components": components,
            "summary": "留了句语音",
        }

    def _resolve_aiocqhttp_client(self) -> Any:
        platform_manager = getattr(self.context, "platform_manager", None)
        platforms: list[Any] = []
        if platform_manager is not None:
            try:
                platforms = list(platform_manager.get_insts())
            except Exception:
                platforms = list(getattr(platform_manager, "platform_insts", []) or [])
        for platform in platforms:
            platform_names = set()
            try:
                meta = platform.meta()
                platform_names.add(str(getattr(meta, "id", "") or "").strip())
                platform_names.add(str(getattr(meta, "name", "") or "").strip())
            except Exception:
                pass
            platform_desc = f"{platform.__class__.__module__}.{platform.__class__.__name__}".lower()
            for attr in ("bot", "client", "_bot", "_client", "cqhttp"):
                client = getattr(platform, attr, None)
                client_desc = f"{client.__class__.__module__}.{client.__class__.__name__}".lower() if client is not None else ""
                if client is not None and (
                    "aiocqhttp" in platform_names
                    or "default(aiocqhttp)" in platform_names
                    or "aiocqhttp" in platform_desc
                    or "aiocqhttp" in client_desc
                    or (hasattr(client, "send_private_msg") and hasattr(client, "send_group_msg"))
                    or hasattr(client, "friend_poke")
                    or hasattr(client, "group_poke")
                ):
                    return client
        return None

    def _onebot_action_result_ok(self, result: Any) -> bool:
        if result is None:
            return True
        if isinstance(result, dict):
            status = str(result.get("status") or result.get("result") or "").strip().lower()
            if status in {"failed", "fail", "error", "nok"}:
                return False
            retcode = result.get("retcode", result.get("code", None))
            if retcode is not None:
                try:
                    return int(retcode) == 0
                except Exception:
                    return False
        return True

    @staticmethod
    def _onebot_action_reported_success(action: str, result_or_error: Any) -> bool:
        if str(action or "").strip().lower() != "set_online_status":
            return False
        text = _single_line(result_or_error, 500).lower()
        return "set status success" in text or "set online status success" in text

    def _delivery_outcome_is_uncertain(self, error: Any) -> bool:
        if isinstance(error, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
            return True
        text = _single_line(error, 500).lower()
        return any(
            token in text
            for token in (
                "timed out",
                "timeout",
                "deadline exceeded",
                "read timeout",
                "write timeout",
                "connection reset",
                "connection closed",
                "server disconnected",
                "remote disconnected",
                "broken pipe",
                "回执超时",
                "响应超时",
                "连接被重置",
                "连接已关闭",
            )
        )

    def _log_uncertain_onebot_submission(self, action: str, error: Any) -> None:
        logger.warning(
            "[PrivateCompanion] OneBot 动作回执不确定，为避免同一内容被别名立即重复提交，本次按已提交处理: action=%s error=%s",
            action,
            self._format_send_exception(error),
        )

    async def _call_onebot_action(self, client: Any, action: str, **params: Any) -> bool:
        ok, _ = await self._call_onebot_action_with_error(client, action, **params)
        return ok

    async def _call_onebot_action_with_error(
        self,
        client: Any,
        action: str,
        *,
        at_most_once: bool = False,
        **params: Any,
    ) -> tuple[bool, str]:
        candidates = (
            "call_action",
            "call_api",
            "api",
        )
        last_error = ""
        for attr in candidates:
            func = getattr(client, attr, None)
            if not callable(func):
                continue
            try:
                result = func(action, **params)
            except TypeError:
                try:
                    result = func(action, params)
                except Exception as exc:
                    if self._onebot_action_reported_success(action, exc):
                        return True, "协议端已设置状态"
                    if self._is_onebot_event_checker_send_rejection(exc):
                        return False, self._onebot_event_checker_rejection_summary()
                    if at_most_once and self._delivery_outcome_is_uncertain(exc):
                        self._log_uncertain_onebot_submission(action, exc)
                        return True, "回执不确定，已停止立即重试"
                    last_error = self._format_send_exception(exc)
                    if at_most_once:
                        return False, last_error
                    continue
            except Exception as exc:
                if self._onebot_action_reported_success(action, exc):
                    return True, "协议端已设置状态"
                if self._is_onebot_event_checker_send_rejection(exc):
                    return False, self._onebot_event_checker_rejection_summary()
                if at_most_once and self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True, "回执不确定，已停止立即重试"
                last_error = self._format_send_exception(exc)
                if at_most_once:
                    return False, last_error
                continue
            try:
                if hasattr(result, "__await__"):
                    result = await result
            except Exception as exc:
                if self._onebot_action_reported_success(action, exc):
                    return True, "协议端已设置状态"
                if self._is_onebot_event_checker_send_rejection(exc):
                    return False, self._onebot_event_checker_rejection_summary()
                if at_most_once and self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True, "回执不确定，已停止立即重试"
                last_error = self._format_send_exception(exc)
                if at_most_once:
                    return False, last_error
                continue
            if self._onebot_action_result_ok(result) or self._onebot_action_reported_success(action, result):
                return True, ""
            last_error = f"{attr} 返回失败: {_single_line(result, 180)}"
            if self._is_onebot_event_checker_send_rejection(result):
                return False, self._onebot_event_checker_rejection_summary()
            if at_most_once:
                return False, last_error
        func = getattr(client, action, None)
        if callable(func):
            try:
                result = func(**params)
            except Exception as exc:
                if self._onebot_action_reported_success(action, exc):
                    return True, "协议端已设置状态"
                if at_most_once and self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True, "回执不确定，已停止立即重试"
                return False, self._format_send_exception(exc)
            try:
                if hasattr(result, "__await__"):
                    result = await result
            except Exception as exc:
                if self._onebot_action_reported_success(action, exc):
                    return True, "协议端已设置状态"
                if at_most_once and self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True, "回执不确定，已停止立即重试"
                return False, self._format_send_exception(exc)
            if self._onebot_action_result_ok(result) or self._onebot_action_reported_success(action, result):
                return True, ""
            return False, f"{action} 返回失败: {_single_line(result, 180)}"
        return False, last_error or f"OneBot 客户端不支持动作 {action}"

    def _input_status_user_id_from_umo(self, umo: str) -> str:
        if not umo or ":FriendMessage:" not in str(umo):
            return ""
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("input_status", umo=umo):
            return ""
        session = self._parse_message_session(umo)
        if not session:
            return ""
        user_id = str(getattr(session, "session_id", "") or "").strip()
        return user_id if user_id.isdigit() else ""

    async def _send_input_status_once(self, user_id: str, *, client: Any | None = None) -> bool:
        user_id = str(user_id or "").strip()
        if not user_id.isdigit():
            return False
        if client is None:
            client = self._resolve_aiocqhttp_client()
        if client is None:
            return False
        variants = (
            {"user_id": int(user_id), "event_type": 1},
            {"user_id": int(user_id), "status": 1},
            {"user_id": int(user_id), "typing": True},
        )
        for params in variants:
            if await self._call_onebot_action(client, "set_input_status", **params):
                self._last_input_status_at[user_id] = _now_ts()
                return True
        return False

    async def _maybe_send_input_status(self, umo: str, text: str = "") -> None:
        user_id = self._input_status_user_id_from_umo(umo)
        if not user_id:
            return
        now = _now_ts()
        last_at = _safe_float(self._last_input_status_at.get(user_id), 0)
        if now - last_at < 45:
            return
        duration = max(1.2, min(4.5, len(str(text or "")) / 18))
        if not await self._send_input_status_once(user_id):
            return
        self._last_input_status_at[user_id] = now
        await asyncio.sleep(random.uniform(duration * 0.55, duration))

    async def _passive_input_status_loop(self, user_id: str, *, max_seconds: float = 90.0) -> None:
        user_id = str(user_id or "").strip()
        if not user_id.isdigit():
            return
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return
        started_at = _now_ts()
        while not bool(getattr(self, "_stop_event", asyncio.Event()).is_set()):
            if _now_ts() - started_at > max_seconds:
                return
            try:
                await self._send_input_status_once(user_id, client=client)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("[PrivateCompanion] 私聊输入状态刷新失败: %s", _single_line(exc, 120))
                return
            await asyncio.sleep(random.uniform(3.2, 4.8))

    def _start_passive_input_status_loop(self, event: AstrMessageEvent, user_id: str = "") -> None:
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        parsed_user_id = self._input_status_user_id_from_umo(umo)
        # ``user_id`` is normally the platform-scoped profile storage key.  It
        # may contain a namespace/digest for a QQ-official event, while the
        # OneBot transport still requires the numeric sender from the UMO.
        storage_user_id = str(user_id or parsed_user_id or "").strip()
        if not parsed_user_id or not parsed_user_id.isdigit():
            return
        task_key = storage_user_id or parsed_user_id
        tasks = getattr(self, "_passive_input_status_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self._passive_input_status_tasks = tasks
        old_task = tasks.get(task_key)
        if isinstance(old_task, asyncio.Task) and not old_task.done():
            old_task.cancel()
        task = asyncio.create_task(self._passive_input_status_loop(parsed_user_id))
        tasks[task_key] = task
        try:
            # Keep the scoped key on the event so stop/cleanup remains
            # isolated, but expose the numeric transport ID for diagnostics.
            setattr(event, "private_companion_input_status_user_id", task_key)
            setattr(event, "private_companion_input_status_transport_id", parsed_user_id)
        except Exception:
            pass

        def _cleanup(done_task: asyncio.Task) -> None:
            current = tasks.get(task_key)
            if current is done_task:
                tasks.pop(task_key, None)

        task.add_done_callback(_cleanup)

    def _stop_passive_input_status_loop(self, event_or_user: Any) -> None:
        user_id = ""
        if isinstance(event_or_user, str):
            user_id = event_or_user.strip()
        else:
            user_id = str(getattr(event_or_user, "private_companion_input_status_user_id", "") or "").strip()
            if not user_id:
                try:
                    user_id = str(event_or_user.get_sender_id()).strip()
                except Exception:
                    user_id = ""
        if not user_id:
            return
        tasks = getattr(self, "_passive_input_status_tasks", None)
        if not isinstance(tasks, dict):
            return
        task = tasks.pop(user_id, None)
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()

    def _qq_presence_codes(self, mode: str) -> tuple[int, int, str]:
        normalized = str(mode or "").strip().lower()
        table = {
            "online": (10, 0, "在线"),
            "away": (30, 0, "离开"),
            "busy": (50, 0, "忙碌"),
            "invisible": (40, 0, "隐身"),
        }
        return table.get(normalized, table["online"])

    async def _set_qq_online_presence(self, mode: str) -> tuple[bool, str]:
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return False, "未找到可用 QQ 客户端"
        status, ext_status, label = self._qq_presence_codes(mode)
        ok, error = await self._call_onebot_action_with_error(
            client,
            "set_online_status",
            at_most_once=True,
            status=status,
            ext_status=ext_status,
            battery_status=0,
        )
        if ok:
            return True, label
        return False, f"平台不支持 set_online_status：{label}（{_single_line(error, 100)}）"

    async def _set_qq_custom_presence(self, text: str) -> tuple[bool, str]:
        if not getattr(self, "enable_qq_custom_presence_sync", False):
            return False, "QQ 自定义短状态未开启"
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return False, "未找到可用 QQ 客户端"
        custom_text = _single_line(text, 8)
        if not custom_text:
            return False, "自定义状态文本为空,跳过同步"
        # Avoid set_custom_online_status: some OneBot adapters disconnect on this unsupported extension API.
        ok, error = await self._call_onebot_action_with_error(
            client,
            "set_diy_online_status",
            at_most_once=True,
            face_id=21,
            face_type=1,
            wording=custom_text,
        )
        if ok:
            return True, f"自定义状态：{custom_text}"
        return False, f"平台不支持自定义状态：{custom_text}（{_single_line(error, 100)}）"

    async def _reset_stale_qq_presence_if_needed(self) -> None:
        if not self.enable_qq_presence_sync:
            return
        await asyncio.sleep(2)
        try:
            await self._ensure_current_detail_presence_status()
        except Exception as exc:
            logger.debug("[PrivateCompanion] 启动同步当前 QQ 状态失败: %s", exc)
        async with self._data_lock:
            state = self.data.get("qq_presence_state", {})
            if not isinstance(state, dict) or str(state.get("date") or "") == _today_key():
                return
            previous_mode = str(state.get("mode") or "")
        ok, note = await self._set_qq_online_presence("online")
        async with self._data_lock:
            state = self.data.setdefault("qq_presence_state", {})
            if not isinstance(state, dict):
                state = {}
                self.data["qq_presence_state"] = state
            state.update(
                {
                    "date": _today_key(),
                    "plan_date": "",
                    "detail_key": "",
                    "mode": "online",
                    "custom_text": "",
                    "reason": "清理跨日 QQ 状态",
                    "updated_at": _now_ts(),
                    "ok": bool(ok),
                    "note": _single_line(f"跨日重置：{previous_mode or 'unknown'} -> {note}", 120),
                }
            )
            self._save_data_sync(sections={"qq_presence_state"})

    def _extract_group_id_from_umo(self, target: str) -> int | None:
        text = str(target or "").strip()
        match = re.search(r":GroupMessage:(\d+)$", text)
        if not match:
            return None
        try:
            return int(match.group(1))
        except Exception:
            return None

    async def _build_voice_note_text(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        *,
        target: str = "",
    ) -> str:
        requirement = self._voice_requirement_profile(target)
        framework_text = await self._generate_voice_note_via_framework(
            user,
            name,
            reason,
            target=target,
        )
        if framework_text:
            spoken = str(framework_text).strip()
            if requirement["strict"] and not self._voice_text_matches_requirement(spoken, requirement):
                logger.info(
                    "[PrivateCompanion] 主动语音未命中格式要求,进行框架严格重试: target=%s summary=%s",
                    target,
                    requirement["summary"],
                )
                retry_text = await self._generate_voice_note_via_framework(
                    user,
                    name,
                    reason,
                    target=target,
                    strict_tts=True,
                )
                if retry_text:
                    spoken = str(retry_text).strip()
            if "<tts>" not in spoken:
                spoken = _single_line(spoken, runtime_persona_setting(self, "voice_action_max_chars", 30))
                spoken = re.sub(r"[“”\"'`]", "", spoken).strip()
            if requirement["strict"] and not self._voice_text_matches_requirement(spoken, requirement):
                repair_prompt = self._build_voice_repair_prompt(
                    spoken=spoken,
                    requirement=requirement,
                    target=target,
                )
                repaired = await self._llm_call(
                    repair_prompt,
                    max_tokens=140,
                    provider_id=self._task_provider(
                        _persona_provider_id(self, "VOICE_PROMPT_PROVIDER_ID", "voice_prompt_provider_id", "fast"),
                        _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
                    ),
                    task="voice_repair",
                )
                if repaired:
                    spoken = str(repaired).strip()
                    if "<tts>" not in spoken:
                        spoken = _single_line(spoken, runtime_persona_setting(self, "voice_action_max_chars", 30))
                        spoken = re.sub(r"[“”\"'`]", "", spoken).strip()
            if requirement["strict"] and not self._voice_text_matches_requirement(spoken, requirement):
                logger.warning(
                    "[PrivateCompanion] 主动语音仍未完全命中格式要求,保留当前结果: target=%s summary=%s text=%s",
                    target,
                    requirement["summary"],
                    self._strip_tts_markup(spoken),
                )
            else:
                logger.info(
                    "[PrivateCompanion] 主动语音最终文本已命中格式要求: target=%s text=%s",
                    target,
                    self._strip_tts_markup(spoken),
                )
            return spoken
        persona = self._get_default_persona_prompt()
        state = self.data.get("daily_state", {})
        last_user_message = _single_line(user.get("last_user_message"), 80)
        profile = self._relationship_profile(user)
        tts_prompt = self._get_tts_prompt_text(target)
        prompt = f"""
你正在替角色写一条马上要发出去的语音。这条语音是真的会被 TTS 念出来,不是文字陪聊。

【人格】
{persona}

【对象】
称呼：{name}
关系：{profile['level']}｜偏好：{profile['preference']}
最近一句：{last_user_message or '（暂无）'}

【主动原因】
{reason}

【当前状态】
{self._format_state_for_prompt(state if isinstance(state, dict) else {})}

【当前会话 TTS 规则】
{tts_prompt or "（当前没有额外 TTS 提示词,就按人格自己的语音习惯来）"}

要求：
1. 优先遵守人格里自己写的特殊 TTS 规则；如果人格或当前会话 TTS 规则要求使用 <tts>...</tts>、日语、情绪标签或双语格式,就按那个格式输出。
2. 如果没有明确格式要求,就只输出适合真正念出来的一小句语音内容,不要解释。
3. 整体要短,适合私聊语音,不像朗读稿,也不要太正式；纯中文可控制在 {runtime_persona_setting(self, "voice_action_max_chars", 30)} 个字以内。
4. 可以有一点嘴硬、黏人、藏着的想念,但不要把喜欢说满。
5. 不要提 AI、模型、插件、TTS、语音合成这些词。
""".strip()
        text = await self._llm_call(
            prompt,
            max_tokens=120,
            provider_id=self._task_provider(
                _persona_provider_id(self, "VOICE_PROMPT_PROVIDER_ID", "voice_prompt_provider_id", "fast"),
                _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
            ),
            task="voice",
        )
        spoken = str(text or "").strip()
        if not spoken:
            spoken = random.choice(VOICE_FALLBACK_TEMPLATES)
        if "<tts>" not in spoken:
            spoken = _single_line(spoken, runtime_persona_setting(self, "voice_action_max_chars", 30))
            spoken = re.sub(r"[“”\"'`]", "", spoken).strip()
        if requirement["strict"] and not self._voice_text_matches_requirement(spoken, requirement):
            repair_prompt = self._build_voice_repair_prompt(
                spoken=spoken,
                requirement=requirement,
                target=target,
            )
            repaired = await self._llm_call(
                repair_prompt,
                max_tokens=140,
                provider_id=self._task_provider(
                    _persona_provider_id(self, "VOICE_PROMPT_PROVIDER_ID", "voice_prompt_provider_id", "fast"),
                    _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
                ),
                task="voice_repair",
            )
            if repaired:
                spoken = str(repaired).strip()
                if "<tts>" not in spoken:
                    spoken = _single_line(spoken, runtime_persona_setting(self, "voice_action_max_chars", 30))
                    spoken = re.sub(r"[“”\"'`]", "", spoken).strip()
        return spoken

    async def _create_voice_record_component(
        self,
        target: str,
        spoken_text: str,
        *,
        defer_local_playback: bool = False,
    ) -> tuple[list[Any], str]:
        if not spoken_text:
            return [], "语音内容为空"
        try:
            config = self.context.get_config(target)
        except Exception:
            try:
                config = self.context.get_config()
            except Exception as e:
                return [], f"读取配置失败：{e}"
        provider_settings = dict(config.get("provider_tts_settings", {}) or {})
        astrbot_provider = None
        try:
            astrbot_provider = self.context.get_using_tts_provider(target)
        except Exception as e:
            logger.debug("[PrivateCompanion] 主动语音读取 AstrBot TTS provider 失败: %s", _single_line(e, 120))
        resolver = getattr(self, "_resolve_tts_synthesis_provider", None)
        if callable(resolver):
            try:
                tts_provider = resolver(SimpleNamespace(unified_msg_origin=target), astrbot_provider)
            except Exception:
                tts_provider = astrbot_provider
        else:
            tts_provider = astrbot_provider
        if not tts_provider:
            return [], "当前没有可用的 AstrBot TTS Provider 或 MiMo Voice Clone 联动"
        if tts_provider is astrbot_provider and not provider_settings.get("enable", False):
            return [], "当前会话未启用 AstrBot TTS"
        if "<tts>" in spoken_text and "</tts>" in spoken_text:
            components, note = await self._build_tts_modify_components(
                spoken_text,
                tts_provider,
                provider_settings,
                config,
            )
            records = [component for component in components if isinstance(component, Record)]
            if records:
                # The proactive message generator supplies the visible companion text.
                # Keep only the prebuilt audio here so it cannot be sent twice.
                return records, note
        record_builder = getattr(self, "_tts_record_component", None)
        if callable(record_builder):
            record_kwargs = {
                "source_text": spoken_text,
                "source": "private_companion",
            }
            if defer_local_playback:
                record_kwargs["defer_delivery_effects"] = True
            try:
                record = await record_builder(
                    spoken_text,
                    tts_provider,
                    provider_settings,
                    config,
                    **record_kwargs,
                )
            except TypeError:
                record_kwargs.pop("defer_delivery_effects", None)
                record = await record_builder(
                    spoken_text,
                    tts_provider,
                    provider_settings,
                    config,
                    **record_kwargs,
                )
            if record is not None:
                return [record], self._extract_record_note([record]) or "已通过 TTS强化生成语音"
            return [], "TTS 没有返回音频文件"
        try:
            audio_path = await tts_provider.get_audio(spoken_text)
        except Exception as e:
            logger.warning(f"[PrivateCompanion] voice 主动行为生成失败: {e}")
            return [], str(e)
        if not audio_path:
            return [], "TTS 没有返回音频文件"
        try:
            audio_file = Path(audio_path).resolve()
            expected_dir = Path(get_astrbot_data_path()).resolve()
            if not audio_file.is_relative_to(expected_dir):
                return [], f"语音文件路径不安全：{audio_path}"
        except Exception as e:
            return [], str(e)
        final_ref = str(audio_path)
        if provider_settings.get("use_file_service", False):
            callback_api_base = str(config.get("callback_api_base", "") or "").strip()
            if callback_api_base:
                try:
                    token = await file_token_service.register_file(str(audio_path))
                    final_ref = f"{callback_api_base}/api/file/{token}"
                except Exception as e:
                    logger.warning(f"[PrivateCompanion] 注册语音文件失败,将回退到本地路径: {e}")
        try:
            component = Record(file=final_ref, url=final_ref)
        except TypeError:
            try:
                component = Record(file=final_ref)
            except TypeError:
                component = Record.fromFileSystem(str(audio_path))
        self._annotate_tts_record_component(component, spoken_text, source_text=spoken_text)
        return [component], str(audio_path)

    def _get_tts_prompt_text(self, target: str) -> str:
        if runtime_persona_setting(self, "enable_tts_enhancement", False):
            builder = getattr(self, "_build_tts_rule_prompt", None)
            if callable(builder):
                return str(builder("generic") or "").strip()
        return ""

    def _voice_requirement_profile(self, target: str) -> dict[str, Any]:
        persona = self._get_default_persona_prompt()
        tts_prompt = self._get_tts_prompt_text(target)
        combined = f"{persona}\n{tts_prompt}".lower()
        require_tts_tags = "<tts>" in combined or "</tts>" in combined
        japanese_markers = ("日语", "日文", "日本語", "假名", "片假名", "平假名", "日语语音", "日文语音")
        bilingual_markers = ("双语", "中日双语", "中文文本", "中文显示", "日语语音")
        prefer_japanese = any(marker in combined for marker in japanese_markers)
        prefer_bilingual = any(marker in combined for marker in bilingual_markers)
        strict = require_tts_tags or prefer_japanese or prefer_bilingual
        parts: list[str] = []
        if require_tts_tags:
            parts.append("需要 <tts> 标签")
        if prefer_japanese:
            parts.append("语音正文优先日语")
        if prefer_bilingual:
            parts.append("可能需要双语/日中并存格式")
        if not parts:
            parts.append("没有明显额外语音格式要求")
        return {
            "strict": strict,
            "require_tts_tags": require_tts_tags,
            "prefer_japanese": prefer_japanese,
            "prefer_bilingual": prefer_bilingual,
            "summary": "；".join(parts),
        }

    def _voice_text_matches_requirement(self, spoken: str, requirement: dict[str, Any]) -> bool:
        text = str(spoken or "").strip()
        if not text:
            return False
        if requirement.get("require_tts_tags") and ("<tts>" not in text.lower() or "</tts>" not in text.lower()):
            return False
        core = self._strip_tts_markup(text)
        if requirement.get("prefer_japanese"):
            if not re.search(r"[\u3040-\u30ff\u31f0-\u31ff]", core):
                return False
        return True

    def _build_voice_repair_prompt(
        self,
        *,
        spoken: str,
        requirement: dict[str, Any],
        target: str,
    ) -> str:
        persona = self._get_default_persona_prompt()
        tts_prompt = self._get_tts_prompt_text(target)
        return f"""
你要把下面这句主动语音修正成符合当前语音规则的最终版本。

【人格】
{persona}

【当前会话 TTS 规则】
{tts_prompt or "（当前没有额外 TTS 提示词）"}

【必须满足的格式重点】
{requirement.get("summary") or "按人格自己的语音习惯处理"}

【当前版本】
{spoken}

要求：
1. 只输出修正后的最终语音内容，不要解释。
2. 如果需要 <tts>...</tts>，必须补齐。
3. 如果要求日语语音，就让真正会被念出来的那一部分变成自然的日语，而不是普通中文。
4. 如果没有强制格式，也保持私聊语音的自然感。
""".strip()

    async def _build_tts_modify_components(
        self,
        spoken_text: str,
        tts_provider: Any,
        provider_settings: dict[str, Any],
        config: dict[str, Any],
    ) -> tuple[list[Any], str]:
        try:
            processor = getattr(self, "_process_tts_tags", None)
            if not callable(processor):
                return [], "TTS强化未接入"
            components = await processor(
                spoken_text,
                tts_provider,
                provider_settings,
                config,
            )
        except Exception as e:
            logger.warning(f"[PrivateCompanion] TTS强化处理主动语音失败: {e}")
            return [], str(e)
        audio_note = self._extract_record_note(components)
        return components or [], audio_note or "已通过 TTS强化生成语音"

    def _get_tts_modify_plugin(self, config: dict[str, Any]) -> Any:
        for module_name in ("astrbot_plugin_tts_modify.main", "data.plugins.astrbot_plugin_tts_modify.main"):
            try:
                module = importlib.import_module(module_name)
                plugin_cls = getattr(module, "TTSModifyPlugin", None)
                if plugin_cls is not None:
                    return plugin_cls(self.context, config)
            except Exception:
                continue
        return None

    def _extract_record_note(self, components: list[Any]) -> str:
        for component in components or []:
            file_value = str(getattr(component, "file", "") or "").strip()
            url_value = str(getattr(component, "url", "") or "").strip()
            if file_value:
                return file_value
            if url_value:
                return url_value
        return ""

    def _strip_tts_markup(self, text: str) -> str:
        stripped = self._visible_text_without_tts_reading(text)
        stripped = stripped.replace("\r", "\n")
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        return _single_line(" ".join(lines), 120)

    def _visible_text_without_tts_reading(self, text: str, *, limit: int = 1000) -> str:
        source = str(text or "").strip()
        if not source:
            return ""
        if self._is_proactive_delivery_receipt_text(source):
            return ""
        placeholder_cleaner = getattr(self, "_sanitize_orphan_tts_placeholders", None)
        if callable(placeholder_cleaner):
            source = placeholder_cleaner(source)
        emotion_cleaner = getattr(self, "_strip_visible_tts_emotion_cues", None)
        if callable(emotion_cleaner):
            source = emotion_cleaner(source)
        normalizer = getattr(self, "_normalize_tts_tags", None)
        if callable(normalizer) and re.search(r"</?t{2,}s\b", source, flags=re.IGNORECASE):
            try:
                source = str(normalizer(source) or source).strip()
            except Exception:
                pass
        if re.search(r"<tts\b[^>]*>.*?</tts>", source, flags=re.IGNORECASE | re.DOTALL):
            outside = re.sub(r"<tts\b[^>]*>.*?</tts>", "", source, flags=re.IGNORECASE | re.DOTALL)
            outside = re.sub(r"</?t{2,}s\b[^>]*>", "", outside, flags=re.IGNORECASE).strip()
            if re.search(r"[\u4e00-\u9fff]", outside):
                return _single_line(_strip_internal_message_blocks(outside), limit)
            source = re.sub(r"</?t{2,}s\b[^>]*>", "", source, flags=re.IGNORECASE).strip()
        has_kana = bool(re.search(r"[\u3040-\u30ff]", source))
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", source))
        if (
            has_kana
            and has_cjk
            and runtime_persona_setting(self, "tts_voice_language", "zh") != "zh"
        ):
            units = re.findall(r".*?[。！？!?…~～]+|.+$", source, flags=re.DOTALL)
            kept: list[str] = []
            dropped = False
            for unit in units:
                cleaned = str(unit or "").strip()
                if not cleaned:
                    continue
                if re.search(r"[\u3040-\u30ff]", cleaned):
                    dropped = True
                    continue
                kept.append(cleaned)
            if dropped and kept and any(re.search(r"[\u4e00-\u9fff]", item) for item in kept):
                return _single_line(_strip_internal_message_blocks("".join(kept)), limit)
        return _single_line(_strip_internal_message_blocks(source), limit)

    async def _run_photo_text_action(self, user: dict[str, Any], name: str, reason: str) -> str:
        if not runtime_persona_setting(self, "enable_photo_text_action", True):
            return "photo_text：未启用"
        user_id = str(user.get("user_id") or "")
        scope_checker = getattr(self, "_photo_generation_scope_allowed", None)
        if callable(scope_checker) and not scope_checker(proactive=True, user=user, user_id=user_id):
            return "photo_text：主动生图不在当前配置的使用范围内,不能假装已经拍照"
        load_defer_note = self._photo_text_load_defer_note("photo_text", force_refresh=True)
        if load_defer_note:
            return f"photo_text：{load_defer_note},不能假装已经拍照"
        if not self._photo_text_available(user):
            return "photo_text：今日发图额度已用完或生图后端不可用,不能假装已经拍照"
        if not self._photo_text_available():
            return "photo_text：当前没有可用的生图后端,不能假装已经拍照"

        scene = await self._build_photo_scene_prompt(user, name, reason)
        workflow_kind = scene.get("kind", "text2img")
        normalized_workflow_kind = _single_line(workflow_kind, 40).strip().lower()
        raw_subject_owner = _normalize_photo_subject_owner(scene.get("subject_owner"))
        subject_owner = raw_subject_owner
        if not subject_owner:
            subject_owner = (
                "bot"
                if bool(scene.get("use_persona_reference"))
                or normalized_workflow_kind in {"selfie", "portrait", "自拍", "人像"}
                else "scene"
            )
        if normalized_workflow_kind in {"selfie", "portrait", "自拍", "人像"} and subject_owner == "scene":
            subject_owner = "bot"
        non_bot_identity_owner = raw_subject_owner in {"third_party", "unknown"} or subject_owner in {
            "third_party",
            "unknown",
        }
        bot_identity_required = (
            not non_bot_identity_owner
            and (
                bool(scene.get("use_persona_reference"))
                or normalized_workflow_kind in {"selfie", "portrait", "自拍", "人像"}
                or subject_owner == "bot"
            )
        )
        reference_image_path = ""
        reference_selection_source = ""

        def valid_reference_path(value: Any) -> str:
            """Return a local image path only when it is an existing file."""
            if isinstance(value, SelectionResult):
                value = value.selected
            if isinstance(value, dict):
                value = value.get("path") or value.get("source") or value.get("file_path")
            raw = _path_text(value, 1000)
            if not raw or re.match(r"^(?:https?|data):", raw, flags=re.I):
                return ""
            try:
                path = Path(raw).expanduser()
                if not path.is_absolute():
                    data_dir = _path_text(getattr(self, "data_dir", ""), 1000)
                    if data_dir:
                        path = Path(data_dir) / path
                path = path.resolve()
                if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    return ""
            except (OSError, ValueError, TypeError, RuntimeError):
                return ""
            return str(path)

        # A third-party or ambiguous owner must never inherit Bot's persona image.
        # Such scenes may proceed only with an explicitly supplied, valid reference.
        if non_bot_identity_owner:
            reference_image_path = valid_reference_path(scene.get("reference_image_path"))
            reference_selection_source = "explicit_reference" if reference_image_path else ""
            if not reference_image_path:
                return (
                    "photo_text：缺少有效身份参考图，已停止提交人物画面\n"
                    f"画面草稿：{_single_line(scene.get('caption'), 180)}\n"
                    "失败原因：第三方或归属不明的人物只能使用对应的可验证参考图，不能套用 Bot 身份图。"
                )

        if bot_identity_required:
            # Character-bearing photo_text scenes need identity continuity even when
            # their rendering workflow is text2img rather than selfie.
            reference_image_path = valid_reference_path(scene.get("reference_image_path"))
            if reference_image_path:
                reference_selection_source = "explicit_reference"
            async_reference_getter = getattr(
                self,
                "_photo_persona_reference_image_for_kind_async",
                None,
            )
            if not reference_image_path and callable(async_reference_getter):
                try:
                    selected_path = await async_reference_getter(
                        "selfie",
                        allow_daily_outfit=True,
                        requester_user_id=str(user.get("user_id") or ""),
                        request_text=_single_line(scene.get("prompt"), 900),
                        ambient_context=_single_line(scene.get("scene_context"), 900),
                    )
                    reference_image_path = valid_reference_path(selected_path)
                    if reference_image_path:
                        reference_selection_source = "selected_reference"
                except Exception as exc:
                    logger.debug(
                        "[PrivateCompanion] proactive photo reference selection failed: %s",
                        _single_line(exc, 160),
                    )
            if not reference_image_path:
                fallback_getter = getattr(
                    self,
                    "_photo_persona_reference_image_for_kind",
                    None,
                )
                if callable(fallback_getter):
                    try:
                        reference_image_path = valid_reference_path(
                            fallback_getter("selfie", allow_daily_outfit=False)
                        )
                    except Exception as exc:
                        logger.debug(
                            "[PrivateCompanion] proactive photo identity fallback failed: %s",
                            _single_line(exc, 160),
                        )
                if not reference_image_path:
                    fallback_path_getter = getattr(
                        self,
                        "_photo_persona_reference_image_path_async",
                        None,
                    )
                    if callable(fallback_path_getter):
                        try:
                            reference_image_path = valid_reference_path(
                                await fallback_path_getter()
                            )
                        except Exception as exc:
                            logger.debug(
                                "[PrivateCompanion] proactive photo identity path fallback failed: %s",
                                _single_line(exc, 160),
                            )
                if reference_image_path:
                    reference_selection_source = "identity_fallback"
            if not reference_image_path:
                return (
                    "photo_text：缺少有效身份参考图，已停止提交人物画面\n"
                    f"画面草稿：{_single_line(scene.get('caption'), 180)}\n"
                    "失败原因：需要 Bot 或其他人物的可验证参考图，不能生成无来源的人脸。"
                )
        elif subject_owner == "scene":
            scene["prompt"] = self._append_photo_negative_terms(
                scene.get("prompt", ""),
                ["people", "human figures", "faces", "silhouettes"],
                limit=900,
            )
        session_key = str(user.get("umo") or user.get("user_id") or name)
        continuity_key = self._compose_photo_continuity_key(session_key, user.get("user_id"))
        backend_name, image_path, workflow_note = await self._generate_photo_image(
            workflow_kind=workflow_kind,
            prompt_text=scene["prompt"],
            request_text=scene["prompt"],
            session_key=session_key,
            continuity_key=continuity_key,
            requester_user_id=str(user.get("user_id") or ""),
            reference_image_path=reference_image_path,
            prompt_format=_single_line(scene.get("prompt_format"), 40),
        )
        if not image_path:
            counted_attempt = self._photo_generation_failure_counts_as_attempt(workflow_note)
            if counted_attempt:
                async with self._data_lock:
                    self._note_photo_generation_attempt(user_id, image_path="")
                    scope_notifier = getattr(self, "_note_photo_generation_scope_attempt", None)
                    if callable(scope_notifier):
                        scope_notifier(
                            proactive=True,
                            user=user,
                            user_id=user_id,
                            scope="proactive",
                        )
                    self._save_photo_generation_attempts_compat()
            return (
                "photo_text：生图失败,不能假装已经拍照\n"
                f"画面草稿：{scene['caption']}\n"
                f"失败原因：{_single_line(workflow_note, 160)}"
                + ("\n本次已计入今日生图尝试额度,避免接口失败时反复请求。" if counted_attempt else "")
            )
        async with self._data_lock:
            self._note_photo_generation_attempt(user_id, image_path=image_path)
            scope_notifier = getattr(self, "_note_photo_generation_scope_attempt", None)
            if callable(scope_notifier):
                scope_notifier(
                    proactive=True,
                    user=user,
                    user_id=user_id,
                    scope="proactive",
                )
            self._save_photo_generation_attempts_compat()
        scene_context_line = _single_line(scene.get("scene_context"), 500)
        return (
            f"photo_text：已通过 {backend_name} 生成真实图片\n"
            f"图片类型：{workflow_kind}\n"
            f"后端：{backend_name}\n"
            f"图片路径：{image_path}\n"
            f"画面：{scene['caption']}\n"
            f"图片主体归属：{subject_owner}\n"
            f"人物参考图：{('已使用（' + (reference_selection_source or 'selected_reference') + '）') if reference_image_path else '未使用'}\n"
            + (f"统一情境：{scene_context_line}\n" if scene_context_line else "")
            + f"生图提示：{_single_line(scene['prompt'], 240)}"
        )

    def _save_photo_generation_attempts_compat(self) -> None:
        """Persist photo quota changes while tolerating legacy test/host overrides."""
        saver = getattr(self, "_save_data_sync", None)
        if not callable(saver):
            return
        try:
            parameters = inspect.signature(saver).parameters.values()
            accepts_sections = any(
                parameter.name == "sections"
                or parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        except (TypeError, ValueError):
            accepts_sections = True
        if accepts_sections:
            saver(sections={"users", "photo_generation_scope_attempts"})
        else:
            saver()

    def _photo_generation_failure_counts_as_attempt(self, note: str) -> bool:
        text = _single_line(note, 500)
        if not text:
            return False
        count_tokens = (
            "HTTP",
            "超时",
            "请求",
            "接口",
            "上游",
            "upstream",
            "返回格式",
            "未返回",
            "返回空",
            "下载",
            "保存",
            "响应不是图片",
            "输出不是图片",
            "工作流完成但图片",
            "Error code",
            "Exception",
        )
        if any(token in text for token in count_tokens):
            return True
        skip_tokens = (
            "未启用",
            "未配置",
            "不可用或未配置",
            "后端不可用",
            "插件不可用",
            "工作流名",
            "未找到匹配工作流",
            "电脑高负荷",
            "负载偏高",
            "今日发图额度",
            "文本/聊天模型",
            "请改成图片模型",
        )
        return not any(token in text for token in skip_tokens)

    async def _ensure_daily_outfit_photo(
        self,
        diary: dict[str, Any] | None = None,
        *,
        force: bool = False,
    ) -> dict[str, Any] | None:
        if not force and not runtime_persona_setting(self, "enable_daily_outfit_photo", False):
            return None
        today = _today_key()
        async with self._data_lock:
            existing = self.data.get("daily_outfit_photo") if isinstance(self.data.get("daily_outfit_photo"), dict) else {}
            if not force and existing.get("date") == today:
                return dict(existing)
        lock = getattr(self, "_daily_outfit_photo_generation_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._daily_outfit_photo_generation_lock = lock
        async with lock:
            async with self._data_lock:
                existing = self.data.get("daily_outfit_photo") if isinstance(self.data.get("daily_outfit_photo"), dict) else {}
                if not force and existing.get("date") == today:
                    return dict(existing)
            return await self._ensure_daily_outfit_photo_unlocked(diary, force=force, today=today)

    async def _ensure_daily_outfit_photo_unlocked(
        self,
        diary: dict[str, Any] | None = None,
        *,
        force: bool = False,
        today: str = "",
    ) -> dict[str, Any] | None:
        today = today or _today_key()
        if not runtime_persona_setting(self, "enable_photo_text_action", True):
            return await self._record_daily_outfit_photo_result(today, "", "主动拍照/生图未开启")
        scope_checker = getattr(self, "_photo_generation_scope_allowed", None)
        if callable(scope_checker) and not scope_checker(proactive=True):
            return await self._record_daily_outfit_photo_result(today, "", "主动生图不在当前配置的使用范围内")
        if not self._photo_text_available():
            return await self._record_daily_outfit_photo_result(today, "", "当前没有可用的生图后端")
        memory_context = ""
        composer = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(composer):
            try:
                memory_context = await composer(
                    kind="daily_outfit_photo",
                    query=(
                        "今日穿搭生成：历史穿搭、今天日程、天气、地点、用户常问衣服颜色、"
                        "最近自拍、服装连续性、需要避免的造型重复"
                    ),
                    top_k=5,
                    max_chars=900,
                )
            except Exception as exc:
                logger.debug("[PrivateCompanion] 每日穿搭 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))
        schedule_hint = self._daily_outfit_schedule_text()
        weather = self._format_weather_for_prompt() if callable(getattr(self, "_format_weather_for_prompt", None)) else ""
        outfit_profile = self._select_daily_outfit_profile(
            schedule_hint=schedule_hint,
            weather=weather,
            date_key=today,
        )
        prompt_text = self._build_daily_outfit_photo_prompt(
            diary if isinstance(diary, dict) else {},
            memory_context=memory_context,
            outfit_profile=outfit_profile,
        )
        prompt_sections = self._build_daily_outfit_photo_prompt(
            diary if isinstance(diary, dict) else {},
            memory_context=memory_context,
            outfit_profile=outfit_profile,
            structured=True,
        )
        backend_name, image_path, note = await self._generate_photo_image(
            workflow_kind="selfie",
            prompt_text=prompt_text,
            request_text=prompt_text,
            session_key="daily_outfit",
            image_size="1024x1024",
            allow_daily_outfit_reference=False,
            prompt_sections=prompt_sections,
        )
        if image_path:
            return await self._record_daily_outfit_photo_result(
                today,
                image_path,
                "",
                backend=backend_name,
                prompt=prompt_text,
                note=note,
                outfit_profile=outfit_profile,
            )
        return await self._record_daily_outfit_photo_result(
            today,
            "",
            _single_line(note, 220) or "生图失败",
            backend=backend_name,
            prompt=prompt_text,
            note=note,
            outfit_profile=outfit_profile,
        )

    async def _record_daily_outfit_photo_result(
        self,
        date_key: str,
        image_path: str,
        error: str = "",
        *,
        backend: str = "",
        prompt: str = "",
        note: str = "",
        outfit_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item = {
            "date": _single_line(date_key, 20),
            "path": _path_text(image_path, 1000),
            "error": _single_line(error, 240),
            "backend": _single_line(backend, 80),
            "prompt": _single_line(prompt, 500),
            "note": _single_line(note, 220),
            "generated_at": _now_ts(),
            "outfit_profile": self._normalize_daily_outfit_profile(outfit_profile),
        }
        async with self._data_lock:
            history = self._daily_outfit_history_items(include_current=True)
            if image_path:
                history.insert(0, dict(item))
            self.data["daily_outfit_history"] = history[:30]
            self.data["daily_outfit_photo"] = item
            self._save_data_sync(
                sections={"daily_outfit_history", "daily_outfit_photo"}
            )
        if image_path:
            await self._memory_companion_record_daily_outfit(item)
            logger.info(
                "[PrivateCompanion] 每日穿搭照片已生成: backend=%s path=%s",
                _single_line(backend, 80) or "-",
                _single_line(image_path, 160),
            )
        else:
            logger.info("[PrivateCompanion] 每日穿搭照片未生成: %s", _single_line(error or note, 180))
        return item

    def _daily_outfit_schedule_text(self) -> str:
        plan = self.data.get("daily_plan", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        if not isinstance(plan, dict):
            return ""
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            return ""
        lines: list[str] = []
        for item in items[:12]:
            if not isinstance(item, dict):
                continue
            time_text = _single_line(item.get("time"), 12)
            activity = _single_line(item.get("activity"), 120)
            mood = _single_line(item.get("mood"), 24)
            if not activity:
                continue
            line = f"{time_text} {activity}".strip()
            if mood:
                line = f"{line}（{mood}）"
            lines.append(line)
        return _single_line("；".join(lines), 620)

    @staticmethod
    def _normalize_daily_outfit_profile(profile: Any) -> dict[str, str]:
        if not isinstance(profile, dict):
            return {}
        limits = {
            "look_id": 80,
            "scene": 32,
            "weather": 32,
            "palette": 120,
            "silhouette": 120,
            "top": 160,
            "outer": 160,
            "bottom": 140,
            "accessory": 140,
        }
        return {
            key: value
            for key, maximum in limits.items()
            if (value := _single_line(profile.get(key), maximum))
        }

    def _daily_outfit_history_items(self, *, include_current: bool = True) -> list[dict[str, Any]]:
        data = self.data if isinstance(getattr(self, "data", {}), dict) else {}
        candidates: list[Any] = []
        if include_current:
            candidates.append(data.get("daily_outfit_photo"))
        raw_history = data.get("daily_outfit_history")
        if isinstance(raw_history, list):
            candidates.extend(raw_history)

        history: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_item in candidates:
            if not isinstance(raw_item, dict):
                continue
            path = _path_text(raw_item.get("path"), 1000)
            if not path:
                continue
            profile = self._normalize_daily_outfit_profile(raw_item.get("outfit_profile"))
            item = {
                "date": _single_line(raw_item.get("date"), 20),
                "path": path,
                "generated_at": _single_line(raw_item.get("generated_at"), 40),
                "outfit_profile": profile,
            }
            identity = "|".join(
                (
                    item["path"],
                    item["generated_at"],
                    item["date"],
                    profile.get("look_id", ""),
                )
            )
            if identity in seen:
                continue
            seen.add(identity)
            history.append(item)
        return history[:30]

    def _daily_outfit_rotation_history(self) -> list[dict[str, Any]]:
        rotation_days = _safe_int(
            runtime_persona_setting(self, "daily_outfit_rotation_days", 10),
            10,
            1,
            30,
        )
        cutoff = date.today() - timedelta(days=rotation_days - 1)
        history: list[dict[str, Any]] = []
        for item in self._daily_outfit_history_items():
            profile = self._normalize_daily_outfit_profile(item.get("outfit_profile"))
            if not profile:
                continue
            date_key = _single_line(item.get("date"), 20)
            try:
                item_date = datetime.strptime(date_key, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                item_date = None
            if item_date and item_date < cutoff:
                continue
            history.append({**item, "outfit_profile": profile})
        return history[:30]

    @staticmethod
    def _daily_outfit_scene_kind(schedule_hint: str, weather: str) -> str:
        text = f"{schedule_hint} {weather}".lower()
        if any(token in text for token in ("运动", "跑步", "健身", "体育", "workout", "gym", "running")):
            return "sport"
        if any(token in text for token in ("校服", "上课", "教室", "学校", "自习", "放学", "school", "class")):
            return "school"
        if any(token in text for token in ("上班", "工作", "会议", "通勤", "办公室", "office", "commute", "meeting")):
            return "commute"
        if any(token in text for token in ("家", "房间", "卧室", "午休", "起床", "睡前", "入睡", "home", "bedroom")):
            return "home"
        return "daily"

    @staticmethod
    def _daily_outfit_weather_kind(weather: str) -> str:
        text = _single_line(weather, 240).lower()
        if any(token in text for token in ("冷", "降温", "低温", "寒", "雪", "snow", "cold")):
            return "cold"
        if any(token in text for token in ("热", "高温", "闷", "暑", "hot", "heat")):
            return "hot"
        if any(token in text for token in ("雨", "阵雨", "雷", "storm", "rain", "wet")):
            return "rainy"
        return "mild"

    @staticmethod
    def _daily_outfit_outer_options(scene: str, weather_kind: str) -> list[str]:
        if scene == "sport":
            options = {
                "cold": [
                    "lightweight insulated track jacket",
                    "technical hooded running jacket",
                    "short quilted sports jacket",
                    "fleece zip-up athletic layer",
                ],
                "hot": [
                    "no heavy outer layer, breathable short-sleeve overshirt",
                    "no outer layer, airy sun-protection layer tied at the waist",
                    "no outer layer, light mesh sports layer",
                    "no outer layer, sleeveless technical vest",
                ],
                "rainy": [
                    "water-resistant hooded running jacket",
                    "compact rain shell with reflective trim",
                    "light technical windbreaker",
                    "hooded quick-dry sports jacket",
                ],
                "mild": [
                    "clean zip-up track jacket",
                    "lightweight athletic windbreaker",
                    "soft cropped sports jacket",
                    "open technical overshirt",
                ],
            }
            return options[weather_kind]
        if scene == "home":
            options = {
                "cold": [
                    "soft oversized knit cardigan",
                    "warm zip-up hoodie",
                    "light quilted home jacket",
                    "plush lounge cardigan",
                ],
                "hot": [
                    "no outer layer, loose breathable overshirt",
                    "no outer layer, thin open cotton shirt",
                    "no outer layer, airy short-sleeve layer",
                    "no outer layer, light linen cardigan",
                ],
                "rainy": [
                    "soft hooded cardigan for a rainy day indoors",
                    "light zip-up hoodie",
                    "cozy knit cardigan",
                    "thin water-resistant overshirt near the doorway",
                ],
                "mild": [
                    "soft open cardigan",
                    "light zip-up hoodie",
                    "relaxed cotton overshirt",
                    "thin knit vest",
                ],
            }
            return options[weather_kind]
        options = {
            "cold": [
                "camel wool coat with a soft scarf",
                "short dark quilted jacket",
                "navy duffle coat",
                "light gray padded jacket",
            ],
            "hot": [
                "no heavy outer layer, breathable overshirt left open",
                "no outer layer, thin sun-protection cardigan",
                "no outer layer, rolled-sleeve linen overshirt",
                "no outer layer, light short-sleeve shirt layer",
            ],
            "rainy": [
                "water-resistant hooded jacket",
                "light trench coat suitable for rain",
                "compact windbreaker with a hood",
                "short rain shell with clean lines",
            ],
            "mild": [
                "soft knit cardigan",
                "light denim jacket",
                "short bomber jacket",
                "unstructured lightweight blazer",
            ],
        }
        return options[weather_kind]

    def _daily_outfit_candidate_profiles(self, scene: str, weather_kind: str) -> list[dict[str, str]]:
        base_options: dict[str, list[dict[str, str]]] = {
            "school": [
                {"palette": "navy, ivory, and muted burgundy", "silhouette": "neat layered campus silhouette", "top": "crisp white shirt with a navy knit vest", "bottom": "straight-cut charcoal trousers", "accessory": "small burgundy ribbon and a simple watch"},
                {"palette": "pale blue, gray, and silver", "silhouette": "clean relaxed academic silhouette", "top": "pale blue oxford shirt under a fine gray cardigan", "bottom": "neat navy trousers", "accessory": "slim silver hair clip or lapel pin"},
                {"palette": "cream, forest green, and warm brown", "silhouette": "soft collegiate silhouette", "top": "cream sweatshirt with a collared shirt edge showing", "bottom": "tailored brown trousers", "accessory": "small canvas shoulder bag"},
                {"palette": "black, white, and dusty rose", "silhouette": "compact modern campus silhouette", "top": "fine striped tee beneath a clean black overshirt", "bottom": "straight dark jeans", "accessory": "subtle rose-toned hair tie or keychain"},
                {"palette": "sage green, ivory, and charcoal", "silhouette": "quiet knitwear silhouette", "top": "sage knit polo layered over a light tee", "bottom": "relaxed charcoal slacks", "accessory": "small geometric earrings or a simple ring"},
                {"palette": "lavender, cream, and deep gray", "silhouette": "light preppy silhouette", "top": "lavender crewneck knit over a white collar", "bottom": "clean deep-gray trousers", "accessory": "thin patterned scarf or a neat ribbon"},
            ],
            "commute": [
                {"palette": "charcoal, ivory, and cobalt blue", "silhouette": "clean tailored commute silhouette", "top": "ivory ribbed knit top with a cobalt accent", "bottom": "straight charcoal trousers", "accessory": "minimal metal watch and structured tote"},
                {"palette": "sand, white, and muted olive", "silhouette": "relaxed smart-casual silhouette", "top": "white shirt under a muted olive knit vest", "bottom": "sand-colored tapered trousers", "accessory": "small leather crossbody bag"},
                {"palette": "black, soft gray, and wine red", "silhouette": "sleek layered city silhouette", "top": "soft gray mock-neck top with a wine-red scarf accent", "bottom": "black straight-leg pants", "accessory": "simple silver earrings or cufflinks"},
                {"palette": "dusty blue, cream, and camel", "silhouette": "light professional silhouette", "top": "dusty-blue blouse or shirt with a cream knit layer", "bottom": "camel tailored trousers", "accessory": "thin belt and understated wristwatch"},
                {"palette": "deep green, black, and ivory", "silhouette": "structured modern silhouette", "top": "deep-green fine knit with a crisp ivory collar", "bottom": "black pleated trousers", "accessory": "small enamel pin and clean shoulder bag"},
                {"palette": "warm taupe, navy, and white", "silhouette": "comfortable polished silhouette", "top": "warm taupe long-sleeve tee beneath a navy overshirt", "bottom": "white or light-stone straight trousers", "accessory": "subtle patterned scarf"},
            ],
            "sport": [
                {"palette": "cobalt blue, white, and graphite", "silhouette": "clean athletic silhouette", "top": "cobalt quick-dry training tee", "bottom": "graphite track pants", "accessory": "simple sports watch and compact water bottle"},
                {"palette": "black, lime green, and gray", "silhouette": "light running silhouette", "top": "black technical long-sleeve top with lime trim", "bottom": "gray joggers", "accessory": "small sweatband and running watch"},
                {"palette": "coral, navy, and white", "silhouette": "bright casual sports silhouette", "top": "coral breathable tee under a navy sleeveless layer", "bottom": "navy athletic pants", "accessory": "minimal cap or hair band"},
                {"palette": "sage green, cream, and black", "silhouette": "relaxed outdoor exercise silhouette", "top": "sage performance polo with a cream inner layer", "bottom": "black tapered joggers", "accessory": "small crossbody sports pouch"},
                {"palette": "lavender, charcoal, and silver", "silhouette": "soft technical silhouette", "top": "lavender moisture-wicking zip collar top", "bottom": "charcoal training pants", "accessory": "reflective wrist band"},
                {"palette": "rust orange, white, and deep blue", "silhouette": "energetic training silhouette", "top": "rust-orange athletic tee with a white panel", "bottom": "deep-blue track pants", "accessory": "compact earbud case or sports watch"},
            ],
            "home": [
                {"palette": "cream, pale blue, and soft gray", "silhouette": "soft relaxed home silhouette", "top": "cream cotton lounge top", "bottom": "pale-blue relaxed pants", "accessory": "simple fabric hair band or soft slippers"},
                {"palette": "sage green, ivory, and warm brown", "silhouette": "cozy knitwear silhouette", "top": "sage knit tee over an ivory inner layer", "bottom": "warm-brown lounge trousers", "accessory": "small mug held naturally"},
                {"palette": "lavender, charcoal, and white", "silhouette": "quiet oversized silhouette", "top": "lavender oversized sweatshirt", "bottom": "charcoal soft joggers", "accessory": "thin reading glasses or a simple hair clip"},
                {"palette": "dusty rose, cream, and gray", "silhouette": "light comfortable silhouette", "top": "dusty-rose long-sleeve tee", "bottom": "cream cotton pants", "accessory": "small pendant necklace"},
                {"palette": "navy, light gray, and muted yellow", "silhouette": "casual layered home silhouette", "top": "navy striped lounge shirt", "bottom": "light-gray relaxed pants", "accessory": "muted-yellow blanket edge or soft socks"},
                {"palette": "white, olive, and soft black", "silhouette": "minimal restful silhouette", "top": "white breathable henley shirt", "bottom": "olive lounge pants", "accessory": "small wireless earbud case"},
            ],
            "daily": [
                {"palette": "denim blue, white, and red", "silhouette": "casual layered street silhouette", "top": "white tee under a denim-blue overshirt", "bottom": "dark straight-leg jeans", "accessory": "small red hair tie or keychain"},
                {"palette": "cream, black, and forest green", "silhouette": "clean relaxed silhouette", "top": "cream ribbed knit top with a forest-green collar layer", "bottom": "black tapered trousers", "accessory": "minimal canvas crossbody bag"},
                {"palette": "dusty rose, charcoal, and ivory", "silhouette": "soft modern silhouette", "top": "dusty-rose sweatshirt over an ivory tee", "bottom": "charcoal straight trousers", "accessory": "small silver pendant"},
                {"palette": "sage green, navy, and light gray", "silhouette": "easy outdoor silhouette", "top": "sage polo layered with a light-gray tee", "bottom": "navy relaxed trousers", "accessory": "simple cap or structured backpack"},
                {"palette": "lavender, white, and deep blue", "silhouette": "light casual silhouette", "top": "lavender knit tee with a white collar detail", "bottom": "deep-blue jeans", "accessory": "thin patterned scarf"},
                {"palette": "warm brown, ivory, and muted orange", "silhouette": "textured everyday silhouette", "top": "ivory henley shirt beneath a warm-brown knit vest", "bottom": "muted-orange straight trousers", "accessory": "small leather bracelet or watch"},
            ],
        }
        outer_options = self._daily_outfit_outer_options(scene, weather_kind)
        candidates: list[dict[str, str]] = []
        for base_index, base in enumerate(base_options.get(scene, base_options["daily"])):
            for outer_index, outer in enumerate(outer_options):
                candidates.append(
                    {
                        **base,
                        "scene": scene,
                        "weather": weather_kind,
                        "outer": outer,
                        "look_id": f"{scene}-{base_index + 1}-{outer_index + 1}",
                    }
                )
        return candidates

    def _select_daily_outfit_profile(
        self,
        *,
        schedule_hint: str,
        weather: str,
        date_key: str = "",
    ) -> dict[str, str]:
        scene = self._daily_outfit_scene_kind(schedule_hint, weather)
        weather_kind = self._daily_outfit_weather_kind(weather)
        candidates = self._daily_outfit_candidate_profiles(scene, weather_kind)
        if not candidates:
            return {}
        history = self._daily_outfit_rotation_history()
        fields = ("palette", "silhouette", "top", "outer", "bottom", "accessory")
        weights = {
            "palette": 16,
            "silhouette": 12,
            "top": 20,
            "outer": 18,
            "bottom": 10,
            "accessory": 8,
        }

        def cooldown_score(candidate: dict[str, str]) -> int:
            score = 0
            for index, item in enumerate(history):
                previous = self._normalize_daily_outfit_profile(item.get("outfit_profile"))
                if not previous:
                    continue
                recency_weight = max(1, 8 - index)
                matching_fields = sum(
                    1
                    for field in fields
                    if candidate.get(field) and candidate.get(field) == previous.get(field)
                )
                score += sum(
                    weights[field] * recency_weight
                    for field in fields
                    if candidate.get(field) and candidate.get(field) == previous.get(field)
                )
                if candidate.get("look_id") == previous.get("look_id"):
                    score += 600 * recency_weight
                if index == 0:
                    changed_fields = len(fields) - matching_fields
                    if changed_fields < 2:
                        score += 10000
                    elif changed_fields < 3:
                        score += 800
            return score

        rotation_seed = f"{date_key or _today_key()}|{len(history)}"

        def tie_breaker(candidate: dict[str, str]) -> int:
            digest = hashlib.sha1(
                f"{rotation_seed}|{candidate.get('look_id', '')}".encode("utf-8")
            ).hexdigest()
            return int(digest[:12], 16)

        return min(candidates, key=lambda candidate: (cooldown_score(candidate), tie_breaker(candidate)))

    def _daily_outfit_rotation_reference(self) -> str:
        history = self._daily_outfit_rotation_history()
        if not history:
            return ""
        labels = {
            "palette": "color palettes",
            "outer": "outer layers",
            "silhouette": "silhouettes",
        }
        fragments: list[str] = []
        for field, label in labels.items():
            values: list[str] = []
            for item in history:
                profile = self._normalize_daily_outfit_profile(item.get("outfit_profile"))
                value = _single_line(profile.get(field), 56)
                if value and value not in values:
                    values.append(value)
                if len(values) >= 2:
                    break
            if values:
                fragments.append(f"{label}: {' / '.join(values)}")
        return _single_line("; ".join(fragments), 280)

    def _format_weather_for_prompt(self) -> str:
        data = getattr(self, "data", None)
        weather = data.get("daily_weather") if isinstance(data, dict) else None
        if not isinstance(weather, dict):
            return ""
        formatter = getattr(self, "_weather_summary_text", None)
        if callable(formatter):
            try:
                text = _single_line(formatter(weather), 120)
                if text and text != "暂无天气信息":
                    return text
            except Exception:
                pass
        text = _single_line(weather.get("prompt"), 120)
        return "" if text == "暂无天气信息" else text

    def _build_daily_outfit_photo_prompt(
        self,
        diary: dict[str, Any],
        *,
        memory_context: str = "",
        outfit_profile: dict[str, Any] | None = None,
        structured: bool = False,
    ) -> str | tuple[PhotoPromptSection, ...]:
        persona = self._daily_outfit_role_appearance_text()
        style_name, style_instruction = self._get_photo_style_instruction()
        style_prompt = self._photo_style_prompt_en(style_name, style_instruction)
        state = self.data.get("daily_state", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        weather = self._format_weather_for_prompt() if callable(getattr(self, "_format_weather_for_prompt", None)) else ""
        schedule_hint = self._daily_outfit_schedule_text()
        state_visual = self._daily_outfit_visual_state_text(state if isinstance(state, dict) else {})
        outfit_profile = self._normalize_daily_outfit_profile(outfit_profile)
        if not outfit_profile:
            outfit_profile = self._select_daily_outfit_profile(schedule_hint=schedule_hint, weather=weather)
        outfit_hint = self._daily_outfit_outfit_hint(
            schedule_hint=schedule_hint,
            weather=weather,
            outfit_profile=outfit_profile,
        )
        rotation_reference = self._daily_outfit_rotation_reference()
        scene_hint = self._daily_outfit_scene_hint(state if isinstance(state, dict) else {}, schedule_hint=schedule_hint, weather=weather)
        visual_memory = ""
        visual_memory_getter = getattr(self, "_visual_photo_memory_context", None)
        if callable(visual_memory_getter):
            try:
                visual_memory = visual_memory_getter(memory_context, limit=260)
            except Exception:
                visual_memory = ""
        diary_hint = _single_line(
            (diary or {}).get("summary")
            or (diary or {}).get("share_seed")
            or (diary or {}).get("body"),
            80,
        )
        custom = _single_line(
            runtime_persona_setting(self, "daily_outfit_photo_prompt", ""), 220
        )
        anime_style = style_name == "二次元"
        composition_style = (
            [
                "daily outfit character illustration",
                "selfie-inspired outfit portrait composition",
                "non-mirror casual illustrated portrait",
                "soft illustrated lighting",
                "clean illustrated background",
                "anime slice-of-life atmosphere",
            ]
            if anime_style
            else [
                "daily outfit selfie",
                "selfie outfit photo",
                "non-mirror handheld selfie or natural environmental outfit portrait",
                "natural phone snapshot",
                "soft natural light",
                "clean background",
                "lifelike daily atmosphere",
            ]
        )
        positive = [
            "single character",
            *composition_style[:3],
            "solo",
            "visible face",
            "complete head and hair",
            "clear eyes",
            "natural expression",
            "upper body to three-quarter body portrait, not a full-length mirror shot",
            "centered composition",
            "1:1 square cover composition",
            "safe margins around head and body",
            *composition_style[3:],
            persona or "keep the face, hairstyle, hair color, eye color, and key traits consistent with the reference image",
            outfit_hint,
            scene_hint,
            state_visual or "relaxed natural mood",
            style_prompt,
        ]
        if rotation_reference:
            positive.append(
                "wardrobe rotation: show exactly one character wearing one coherent new outfit in this single image; "
                "make that one outfit differ from recent daily outfit photos in at least two design dimensions, "
                f"but never display the old outfit or multiple alternatives; avoid repeating {rotation_reference}"
            )
        if diary_hint:
            positive.append(f"daily mood cue: {diary_hint}")
        negative = [
            "cropped head",
            "headless",
            "faceless",
            "face hidden",
            "extreme close-up",
            "arm in foreground",
            "body only",
            "outfit only",
            "back view",
            "mirror selfie",
            "full-length mirror selfie",
            "full body mirror shot",
            "standing in front of a mirror",
            "dressing room mirror",
            "phone covering face",
            "cut off face",
            "bad hands",
            "extra fingers",
            "text",
            "caption",
            "label",
            "watermark",
            "logo",
            "other people",
            "duplicate character",
            "twins",
            "multiple people",
            "multiple outfits",
            "outfit comparison",
            "before and after",
            "split screen",
            "side-by-side panels",
            "diptych",
            "collage",
            "character sheet",
            "user in frame",
            "private screen",
            "nsfw",
            "revealing outfit",
        ]
        if anime_style:
            negative.extend(
                [
                    "photorealistic",
                    "real person",
                    "live-action",
                    "realistic photography",
                    "photo-real skin texture",
                ]
            )
        if rotation_reference:
            negative.extend(
                [
                    "same outfit as a recent daily outfit photo",
                    f"repeat any recently used outfit element: {rotation_reference}",
                ]
            )
        sections = [
            PhotoPromptSection(
                name="user_request",
                source="user_request",
                positive=", ".join(
                    _single_line(part, 400) for part in positive if _single_line(part, 400)
                ),
                protected=True,
            ),
            PhotoPromptSection(
                name="daily_outfit_contract",
                source="composition",
                negative=", ".join(negative),
            ),
        ]
        if visual_memory:
            sections.append(
                PhotoPromptSection(
                    name="visual_memory",
                    source="visual_memory",
                    positive=f"visual continuity reference: {visual_memory}",
                )
            )
        if custom:
            sections.append(
                PhotoPromptSection(
                    name="daily_outfit_preference",
                    source="fixed_prompt",
                    positive=f"additional outfit preference: {custom}",
                )
            )
        if structured:
            return tuple(sections)
        prompt = (
            "Positive prompt: "
            + ", ".join(section.positive for section in sections if section.positive)
            + ". Negative prompt: "
            + ", ".join(section.negative for section in sections if section.negative)
            + "."
        )
        return _single_line(prompt, 1400)

    def _photo_style_prompt_en(self, style_name: str, style_instruction: str = "") -> str:
        name = _single_line(style_name, 40)
        instruction = _single_line(style_instruction, 220)
        if name == "二次元":
            return "2D anime illustration style, clean detailed character art, cel-shaded rendering, soft colors, slice-of-life feeling"
        if name == "真实":
            return "realistic photography style, believable phone photo, natural lighting, realistic fabric details"
        if instruction:
            return instruction
        return "consistent visual style, natural daily-life feeling"

    def _daily_outfit_visual_state_text(self, state: dict[str, Any]) -> str:
        fragments: list[str] = []
        energy = _safe_int((state or {}).get("energy"), 70, 0, 100)
        if energy < 40:
            fragments.append("slightly sleepy, soft expression")
        elif energy > 82:
            fragments.append("fresh and energetic, bright eyes")
        mood = _single_line((state or {}).get("mood_bias"), 20).replace("黏人", "粘人")
        mood_map = {
            "开心": "gentle happy mood",
            "轻快": "light cheerful mood",
            "柔和": "soft gentle mood",
            "安静": "quiet calm mood",
            "疲惫": "tired but gentle mood",
            "困": "sleepy mood",
            "困倦": "sleepy mood",
            "低落": "subdued mood",
            "敏感": "delicate sensitive mood",
            "粘人": "soft attached mood",
        }
        if mood and mood not in {"平稳", "中性"}:
            fragments.append(mood_map.get(mood, f"{mood} mood"))
        conditions = (state or {}).get("conditions")
        visual_tokens = ("雨", "风", "冷", "热", "困", "疲", "生理期", "感冒", "发烧", "头痛", "胃", "睡", "醒")
        if isinstance(conditions, list):
            for cond in conditions[:6]:
                if not isinstance(cond, dict):
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 30)
                if label and any(token in label for token in visual_tokens):
                    fragments.append(self._daily_outfit_condition_hint_en(label))
                if len(fragments) >= 3:
                    break
        return _single_line(", ".join(dict.fromkeys(item for item in fragments if item)), 140)

    def _daily_outfit_condition_hint_en(self, text: str) -> str:
        value = _single_line(text, 60).lower()
        if any(token in value for token in ("雨", "rain", "淋")):
            return "rainy-day softness"
        if any(token in value for token in ("风", "wind")):
            return "slight wind-blown hair"
        if any(token in value for token in ("冷", "寒", "snow")):
            return "cold-weather outfit"
        if any(token in value for token in ("热", "暑", "hot")):
            return "light breathable outfit"
        if any(token in value for token in ("困", "疲", "睡", "醒")):
            return "sleepy gentle expression"
        if any(token in value for token in ("生理期", "胃", "感冒", "发烧", "头痛")):
            return "soft low-energy expression"
        return _single_line(text, 60)

    def _daily_outfit_scene_hint(self, state: dict[str, Any], *, schedule_hint: str = "", weather: str = "") -> str:
        location = ""
        try:
            location = _single_line(self._current_location_state_text(state), 60)
            coarse = _single_line(self._coarse_roleplay_location_text(location), 40)
            location = coarse or location
        except Exception:
            location = ""
        text = f"{schedule_hint} {weather}".lower()
        if not location:
            if any(token in text for token in ("上课", "教室", "学校", "校门", "放学", "自习")):
                location = "school or commute-to-school setting"
            elif any(token in text for token in ("出门", "路上", "街", "公交", "地铁", "下班", "回家")):
                location = "outdoor street or commute setting"
            elif any(token in text for token in ("家", "房间", "卧室", "起床", "午休", "睡")):
                location = "home or bedroom setting"
            else:
                location = "daily-life setting"
        else:
            location = self._daily_outfit_location_hint_en(location)
        weather_hint = self._daily_outfit_weather_visual_hint(weather)
        return _single_line(", ".join(part for part in [location, weather_hint, "simple background, lived-in daily atmosphere"] if part), 180)

    def _daily_outfit_location_hint_en(self, location: str) -> str:
        text = _single_line(location, 80).lower()
        if any(token in text for token in ("学校", "教室", "上课", "school", "classroom")):
            return "school or classroom setting"
        if any(token in text for token in ("家", "房间", "卧室", "home", "room", "bedroom")):
            return "home or bedroom setting"
        if any(token in text for token in ("工作", "office", "公司")):
            return "workplace or office setting"
        if any(token in text for token in ("外面", "路", "街", "通勤", "outside", "street")):
            return "outdoor street or commute setting"
        return _single_line(location, 80)

    def _daily_outfit_weather_visual_hint(self, weather: str) -> str:
        text = _single_line(weather, 200).lower()
        if not text:
            return ""
        hints: list[str] = []
        if any(token in text for token in ("雨", "阵雨", "雷", "storm", "rain")):
            hints.append("rainy-day atmosphere, umbrella or damp ground, light jacket")
        if any(token in text for token in ("风", "大风", "强对流", "wind")):
            hints.append("windy feeling, slightly wind-blown hair and hem")
        if any(token in text for token in ("冷", "降温", "低温", "寒", "snow")):
            hints.append("cold weather, warm outerwear")
        if any(token in text for token in ("热", "高温", "闷", "暑", "hot")):
            hints.append("hot weather, light breathable clothes")
        return _single_line(", ".join(dict.fromkeys(hints)), 140)

    def _daily_outfit_outfit_hint(
        self,
        *,
        schedule_hint: str = "",
        weather: str = "",
        outfit_profile: dict[str, Any] | None = None,
    ) -> str:
        profile = self._normalize_daily_outfit_profile(outfit_profile)
        if profile:
            fields = (
                ("palette", "color palette"),
                ("silhouette", "silhouette"),
                ("top", "top"),
                ("outer", "outer layer"),
                ("bottom", "bottoms"),
                ("accessory", "accessories"),
            )
            hints = ["intentionally distinct coordinated daily outfit"]
            hints.extend(
                f"{label}: {profile[key]}"
                for key, label in fields
                if profile.get(key)
            )
            return _single_line(", ".join(hints), 620)
        text = f"{schedule_hint} {weather}".lower()
        hints: list[str] = []
        if any(token in text for token in ("校服", "上课", "教室", "学校", "高一", "自习", "放学")):
            hints.append("neat school outfit or school-uniform inspired outfit")
        if any(token in text for token in ("上班", "工作", "会议", "通勤")):
            hints.append("clean daily commute outfit")
        if any(token in text for token in ("运动", "跑步", "健身", "体育")):
            hints.append("light sporty outfit")
        if any(token in text for token in ("家", "房间", "午休", "整理", "起床")) and not hints:
            hints.append("soft casual home outfit")
        if any(token in text for token in ("睡衣", "睡前", "入睡", "刚醒")) and not any(token in text for token in ("上课", "上班", "出门", "通勤")):
            hints.append("comfortable pajamas or loungewear")
        weather_hint = self._daily_outfit_weather_visual_hint(weather)
        if weather_hint:
            hints.append(weather_hint)
        if not hints:
            hints.append("natural daily outfit, coordinated colors, clear clothing layers")
        return _single_line(", ".join(dict.fromkeys(hints)), 180)

    def _daily_outfit_role_appearance_text(self) -> str:
        persona = str(runtime_persona_setting(self, "schedule_persona_prompt", "") or "")
        recognition = str(
            runtime_persona_setting(self, "private_image_self_recognition_hint", "") or ""
        )
        labels = {
            "性别": "gender",
            "识别点": "key visual traits",
            "外貌": "appearance",
            "主要识别点": "key visual traits",
            "发型发色": "hairstyle and hair color",
            "发色": "hair color",
            "发型": "hairstyle",
            "瞳色": "eye color",
            "眼睛": "eyes",
            "服饰风格": "clothing style",
            "服装": "clothing",
            "衣着": "outfit",
        }
        parts: list[str] = []
        for line in persona.replace("\r", "\n").split("\n"):
            text = line.strip()
            if not text or ("：" not in text and ":" not in text):
                continue
            label, value = text.split("：", 1) if "：" in text else text.split(":", 1)
            label = label.strip()
            value = _single_line(value, 160)
            english_label = labels.get(label)
            if english_label and value:
                parts.append(f"{english_label}: {value}")
        if recognition:
            parts.append(f"additional visual recognition notes: {_single_line(recognition, 180)}")
        seen: set[str] = set()
        unique = []
        for item in parts:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return _single_line(", ".join(unique), 620)

    def _choose_photo_workflow_name(self, kind: str) -> str:
        normalized = str(kind or "").strip().lower()
        if normalized in {"selfie", "portrait", "自拍", "人像", "edit", "改图", "修图", "重绘", "p图"}:
            return self.comfyui_selfie_workflow_name or self.comfyui_text2img_workflow_name
        return self.comfyui_text2img_workflow_name or self.comfyui_selfie_workflow_name

    def _photo_generation_trace_id(self, session_key: str, workflow_kind: str) -> str:
        seed = f"{session_key}|{workflow_kind}|{_now_ts()}|{uuid.uuid4().hex[:8]}"
        return hashlib.sha1(seed.encode("utf-8", "ignore")).hexdigest()[:10]

    def _photo_generation_file_detail(self, image_path: str) -> str:
        path_text = _path_text(image_path, 1000)
        if not path_text:
            return "path=- exists=false size=0"
        if re.match(r"^(?:https?://|data:|base64://)", path_text, flags=re.I):
            return f"path={_single_line(path_text, 120)} exists=remote size=-"
        local_text = path_text[len("file://"):] if path_text.startswith("file://") else path_text
        try:
            path = Path(local_text)
            exists = path.exists() and path.is_file()
            size = path.stat().st_size if exists else 0
            return f"path={_single_line(str(path), 160)} exists={str(exists).lower()} size={size}"
        except Exception:
            return f"path={_single_line(path_text, 120)} exists=unknown size=-"

    def _photo_generation_backend_config_summary(self) -> str:
        nai_api_getter = getattr(self, "_nai_image_api", None)
        nai_installed = nai_api_getter() is not None if callable(nai_api_getter) else False
        configured_endpoints = getattr(self, "external_image_api_endpoints", [])
        if isinstance(configured_endpoints, list) and configured_endpoints:
            queue_getter = getattr(self, "_external_image_api_endpoint_queue", None)
            endpoints: list[dict[str, Any]] = []
            if callable(queue_getter):
                try:
                    endpoints = [
                        endpoint
                        for endpoint in queue_getter(include_incomplete=True, include_disabled=True)
                        if isinstance(endpoint, dict)
                    ]
                except Exception:
                    endpoints = []
            endpoint_bits = []
            for index, endpoint in enumerate(endpoints[:6]):
                ready = not bool(self._external_image_api_endpoint_unavailable_note(endpoint))
                endpoint_bits.append(
                    f"{index + 1}:{_single_line(endpoint.get('name') or endpoint.get('model'), 40) or '-'}"
                    f"/{_single_line(endpoint.get('platform'), 20) or 'auto'}"
                    f"/{'ready' if ready else 'unready'}"
                )
            return (
                f"preferred={_single_line(runtime_persona_setting(self, 'photo_generation_backend', ''), 30) or 'auto'} "
                f"comfyui={self._comfyui_photo_available()} "
                f"sdgen={self._sdgen_photo_available()} "
                f"external={self._external_photo_available()} "
                f"external_queue={len(endpoints)} "
                f"external_queue_items={';'.join(endpoint_bits) or '-'} "
                f"backup_note={_single_line(self._backup_external_photo_unavailable_note(), 80) or '-'} "
                f"nai={nai_installed} "
                f"tool_call={self._custom_tool_photo_available()} "
                f"tool_name={_single_line(getattr(self, 'custom_photo_tool_name', ''), 80) or '-'}"
            )
        external_base = _single_line(self._normalized_external_image_api_base_url(), 120)
        if external_base:
            external_base = re.sub(r"([?&](?:key|token|access_token|api_key)=)[^&]+", r"\1***", external_base, flags=re.I)
        backup_base = _single_line(getattr(self, "backup_external_image_api_base_url", ""), 120)
        if backup_base:
            backup_base = re.sub(r"([?&](?:key|token|access_token|api_key)=)[^&]+", r"\1***", backup_base, flags=re.I)
        return (
            f"preferred={_single_line(runtime_persona_setting(self, 'photo_generation_backend', ''), 30) or 'auto'} "
            f"comfyui={self._comfyui_photo_available()} "
            f"sdgen={self._sdgen_photo_available()} "
            f"external={self._external_photo_available()} "
            f"external_platform={self._resolved_external_image_api_platform()} "
            f"external_model={_single_line(getattr(self, 'external_image_api_model', ''), 80) or '-'} "
            f"external_size={_single_line(runtime_persona_setting(self, 'external_image_api_size', ''), 40) or '-'} "
            f"external_base={external_base or '-'} "
            f"backup_external={self._backup_external_photo_available()} "
            f"backup_platform={_single_line(runtime_persona_setting(self, 'backup_external_image_api_platform', ''), 30) or '-'} "
            f"backup_model={_single_line(getattr(self, 'backup_external_image_api_model', ''), 80) or '-'} "
            f"backup_base={backup_base or '-'} "
            f"backup_note={_single_line(self._backup_external_photo_unavailable_note(), 80) or '-'} "
            f"nai={nai_installed} "
            f"tool_call={self._custom_tool_photo_available()} "
            f"tool_name={_single_line(getattr(self, 'custom_photo_tool_name', ''), 80) or '-'}"
        )

    def _photo_generation_trace_max_bytes(self) -> int:
        max_kb = _safe_int(
            runtime_persona_setting(self, "photo_generation_trace_max_size_kb", 0),
            0,
        )
        return max(0, min(102400, max_kb)) * 1024

    def _photo_generation_trace_backup_count(self) -> int:
        return max(
            0,
            min(
                20,
                _safe_int(
                    runtime_persona_setting(self, "photo_generation_trace_backup_count", 5), 5
                ),
            ),
        )

    def _photo_generation_trace_file_path(self) -> Path:
        return Path(self.data_dir) / "photo_generation_trace.txt"

    def _rotate_photo_generation_trace_files(self, path: Path) -> None:
        backup_count = self._photo_generation_trace_backup_count()
        if backup_count <= 0:
            path.unlink(missing_ok=True)
            return
        for index in range(backup_count, 0, -1):
            source = path if index == 1 else path.with_name(
                f"{path.stem}.{index - 1}{path.suffix}"
            )
            target = path.with_name(f"{path.stem}.{index}{path.suffix}")
            if source.exists():
                os.replace(source, target)

    def _sanitize_photo_generation_trace_value(
        self,
        value: Any,
        *,
        key: str = "",
        depth: int = 0,
    ) -> Any:
        if depth > 5:
            return "[truncated]"
        normalized_key = str(key or "").strip().lower()
        if any(
            token in normalized_key
            for token in ("api_key", "apikey", "authorization", "access_token", "secret", "password")
        ):
            return "***"
        if isinstance(value, dict):
            return {
                _single_line(item_key, 80): self._sanitize_photo_generation_trace_value(
                    item_value,
                    key=str(item_key),
                    depth=depth + 1,
                )
                for item_key, item_value in list(value.items())[:48]
                if _single_line(item_key, 80)
            }
        if isinstance(value, (list, tuple, set)):
            return [
                self._sanitize_photo_generation_trace_value(item, depth=depth + 1)
                for item in list(value)[:48]
            ]
        if isinstance(value, str):
            redacted = _redact_outbound_secrets(value, self)
            if normalized_key.endswith("path") or normalized_key.endswith("_path"):
                return _path_text(redacted, 1000)
            if normalized_key in {"prompt", "submitted_prompt"}:
                return redacted
            return _single_line(redacted, 1200)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return _single_line(value, 500)

    def _append_photo_generation_trace_event(
        self,
        trace_id: str,
        stage: str,
        *,
        status: str = "ok",
        data: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        try:
            max_bytes = self._photo_generation_trace_max_bytes()
            if max_bytes <= 0:
                return
            normalized_trace = _single_line(trace_id, 80)
            normalized_stage = _single_line(stage, 80)
            if not normalized_trace or not normalized_stage:
                return
            now = _now_ts()
            states = getattr(self, "_photo_generation_trace_states", None)
            if not isinstance(states, dict):
                states = {}
                self._photo_generation_trace_states = states
            if normalized_trace not in states and len(states) >= 128:
                states.pop(next(iter(states)), None)
            state = states.setdefault(
                normalized_trace,
                {"started_at": now, "seq": 0, "context": {}},
            )
            state["seq"] = _safe_int(state.get("seq"), 0, 0) + 1
            if context:
                state["context"].update(self._sanitize_photo_generation_trace_value(context))
            payload = {
                "schema_version": 1,
                "ts": now,
                "time": datetime.fromtimestamp(now).astimezone().isoformat(timespec="milliseconds"),
                "trace": normalized_trace,
                "seq": state["seq"],
                "stage": normalized_stage,
                "status": _single_line(status, 30) or "ok",
                "elapsed_ms": max(0, int((now - _safe_float(state.get("started_at"), now, 0.0)) * 1000)),
                "context": dict(state["context"]),
                "data": self._sanitize_photo_generation_trace_value(data or {}),
            }
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            encoded_size = len(line.encode("utf-8"))
            if encoded_size > max_bytes:
                payload["context"] = {
                    "truncated": True,
                    "reason": "event_exceeds_max_size",
                }
                payload["data"] = {
                    "truncated": True,
                    "reason": "event_exceeds_max_size",
                    "original_bytes": encoded_size,
                }
                line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
                encoded_size = len(line.encode("utf-8"))
            path = self._photo_generation_trace_file_path()
            with _PHOTO_GENERATION_TRACE_FILE_LOCK:
                path.parent.mkdir(parents=True, exist_ok=True)
                current_size = path.stat().st_size if path.exists() else 0
                if current_size and current_size + encoded_size > max_bytes:
                    self._rotate_photo_generation_trace_files(path)
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line)
            if normalized_stage in {"delivery_completed", "delivery_failed", "failed"}:
                states.pop(normalized_trace, None)
        except Exception as exc:
            logger.debug(
                "[PrivateCompanion] 记录生图可观测 trace 失败: %s",
                _single_line(exc, 120),
            )

    def _record_recent_photo_generation(
        self,
        *,
        trace_id: str,
        session_key: str,
        continuity_key: str = "",
        workflow_kind: str,
        backend: str,
        ok: bool,
        prompt_text: str,
        image_path: str = "",
        note: str = "",
        reference_image_path: str = "",
        image_size: str = "",
        elapsed_ms: int = 0,
        presets: list[str] | None = None,
        reference_used: bool = False,
        reference_candidate: dict[str, Any] | None = None,
        reference_intent: ReferenceIntent | None = None,
        reference_plan: PhotoReferencePlan | None = None,
        reference_fallback: ReferenceFallback | None = None,
        submitted_reference_ids: tuple[str, ...] = (),
        wardrobe: PhotoWardrobeDecision | None = None,
        prompt_hash: str = "",
        submitted_prompt_hash: str = "",
        prompt_path: str = "",
        complete_prompt_length: int = 0,
        submitted_prompt_length: int = 0,
        prompt_sections: dict[str, str] | None = None,
        conflicts: list[str] | None = None,
        removed_conflicts: list[str] | None = None,
        residual_conflicts: list[str] | None = None,
        reference_removed: dict[str, Any] | None = None,
        sanitizer_version: int = 0,
        detected_conflict_details: list[dict[str, Any]] | None = None,
        removed_conflict_details: list[dict[str, Any]] | None = None,
        residual_conflict_details: list[dict[str, Any]] | None = None,
        suggested_scene_preset: str = "",
        prompt_format: str = "",
        workflow_fixed_prompt_audit: dict[str, Any] | None = None,
        generation_completed: bool = False,
        failure_stage: str = "",
    ) -> None:
        try:
            reference_candidate = reference_candidate or {}
            wardrobe_payload = wardrobe.as_dict() if wardrobe is not None else {}
            intent_payload = reference_intent or ReferenceIntent((), (), "ambiguous", 0.0, "none")
            plan_payload = reference_plan or PhotoReferencePlan((), "", "", "")
            fallback_payload = reference_fallback or ReferenceFallback((), (), (), "")
            final_presets = [
                _single_line(name, 40)
                for name in (presets or [])
                if _single_line(name, 40)
            ][:1]

            def compact_audit(values: list[dict[str, Any]] | None) -> list[dict[str, str]]:
                result: list[dict[str, str]] = []
                for value in values or []:
                    if not isinstance(value, dict):
                        continue
                    item = {
                        key: _single_line(value.get(key), 120 if key == "preview" else 80)
                        for key in ("source", "section", "rule", "category", "action", "preview", "sha256")
                        if _single_line(value.get(key), 120 if key == "preview" else 80)
                    }
                    if item:
                        result.append(item)
                return result[:24]

            fixed_prompt_audit = dict(workflow_fixed_prompt_audit or {})
            item = {
                "schema_version": 3,
                "ts": _now_ts(),
                "trace": _single_line(trace_id, 40),
                "session": _single_line(session_key, 340),
                "continuity_key": self._normalize_photo_continuity_key(continuity_key),
                "kind": _single_line(workflow_kind, 30),
                "backend": _single_line(backend, 80),
                "ok": bool(ok),
                "generation_completed": bool(generation_completed),
                "failure_stage": _single_line(failure_stage, 60),
                "prompt_format": (
                    self._normalize_photo_generation_prompt_format(prompt_format)
                    if prompt_format
                    else self._photo_generation_prompt_format_mode()
                ),
                "prompt": _single_line(prompt_text, 900),
                "path": _path_text(image_path, 1000),
                "note": _single_line(note, 240),
                "reference": bool(reference_image_path),
                "reference_used": bool(reference_used),
                "reference_path": _path_text(reference_image_path, 1000),
                "reference_id": _single_line(reference_candidate.get("id"), 60),
                "reference_kind": _single_line(reference_candidate.get("kind"), 40),
                "reference_roles": list(reference_candidate.get("reference_roles") or [])[:8],
                "reference_outfit_category": _single_line(reference_candidate.get("outfit_category"), 40),
                "reference_intent": {
                    "requested_roles": list(intent_payload.requested_roles),
                    "excluded_roles": list(intent_payload.excluded_roles),
                    "continuity_mode": _single_line(intent_payload.continuity_mode, 30),
                    "confidence": round(float(intent_payload.confidence), 3),
                    "source": _single_line(intent_payload.source, 40),
                },
                "reference_plan": {
                    "bindings": [
                        {
                            "reference_id": _single_line(binding.reference_id, 80),
                            "path": _path_text(binding.path, 1000),
                            "roles": list(binding.roles),
                            "priority": int(binding.priority),
                            "preserve": list(binding.preserve),
                            "ignore": list(binding.ignore),
                            "submitted": binding.reference_id in submitted_reference_ids,
                        }
                        for binding in plan_payload.bindings
                    ],
                    "primary_reference_id": _single_line(plan_payload.primary_reference_id, 80),
                    "selection_reason": _single_line(plan_payload.selection_reason, 80),
                    "fallback_reason": _single_line(plan_payload.fallback_reason, 80),
                    "submitted_reference_ids": [
                        _single_line(reference_id, 80)
                        for reference_id in submitted_reference_ids
                        if _single_line(reference_id, 80)
                    ],
                },
                "reference_fallback": {
                    "requested_roles": list(fallback_payload.requested_roles),
                    "fulfilled_roles": list(fallback_payload.fulfilled_roles),
                    "missing_roles": list(fallback_payload.missing_roles),
                    "message": _single_line(fallback_payload.message, 260),
                },
                "image_size": _single_line(image_size, 40),
                "elapsed_ms": int(max(0, elapsed_ms or 0)),
                "presets": final_presets,
                "preset_hint": _single_line(suggested_scene_preset, 80),
                "requested_scene_preset": _single_line(suggested_scene_preset, 80),
                "scene_preset": final_presets[0] if final_presets else "",
                "wardrobe_decision_version": _safe_int(wardrobe_payload.get("decision_version"), 0),
                "wardrobe_rule_id": _single_line(wardrobe_payload.get("rule_id"), 80),
                "wardrobe_mode": _single_line(wardrobe_payload.get("mode"), 40),
                "wardrobe_source": _single_line(wardrobe_payload.get("source"), 40),
                "wardrobe_category": _single_line(wardrobe_payload.get("category"), 40),
                "outfit_locked": bool(wardrobe_payload.get("lock_outfit")),
                "daily_outfit_removed": bool(wardrobe_payload.get("remove_daily_outfit_context")),
                "wardrobe_reason": _single_line(wardrobe_payload.get("reason"), 240),
                "preset_source": _single_line(wardrobe_payload.get("preset_source"), 40),
                "suggestion_status": _single_line(wardrobe_payload.get("suggestion_status"), 60),
                "wardrobe_selected_presets": [
                    _single_line(value, 80)
                    for value in (wardrobe_payload.get("selected_presets") or [])
                    if _single_line(value, 80)
                ][:6],
                "wardrobe_adjustments": [
                    _single_line(value, 120)
                    for value in (wardrobe_payload.get("adjustments") or [])
                    if _single_line(value, 120)
                ][:12],
                "prompt_hash": _single_line(prompt_hash, 80),
                "submitted_prompt_hash": _single_line(submitted_prompt_hash, 80),
                "prompt_path": _path_text(prompt_path, 1000),
                "complete_prompt_length": _safe_int(complete_prompt_length, 0, 0),
                "submitted_prompt_length": _safe_int(submitted_prompt_length, 0, 0),
                "prompt_sections": {
                    _single_line(key, 50): _single_line(value, 240)
                    for key, value in (prompt_sections or {}).items()
                    if _single_line(key, 50) and _single_line(value, 240)
                },
                "conflicts": [_single_line(value, 120) for value in (conflicts or []) if _single_line(value, 120)][:12],
                "removed_conflicts": [
                    _single_line(value, 120)
                    for value in (removed_conflicts or [])
                    if _single_line(value, 120)
                ][:12],
                "residual_conflicts": [
                    _single_line(value, 120)
                    for value in (residual_conflicts or [])
                    if _single_line(value, 120)
                ][:12],
                "reference_removed": bool(reference_removed),
                "reference_removal": dict(reference_removed or {}),
                "sanitizer_version": _safe_int(sanitizer_version, 0, 0),
                "workflow_fixed_prompt": {
                    "scope": _single_line(fixed_prompt_audit.get("scope"), 30),
                    "config_key": _single_line(fixed_prompt_audit.get("config_key"), 80),
                    "configured": bool(fixed_prompt_audit.get("configured")),
                    "normalized": bool(fixed_prompt_audit.get("normalized")),
                    "normalization_changed": bool(
                        fixed_prompt_audit.get("normalization_changed")
                    ),
                    "conflict_cleaned": bool(fixed_prompt_audit.get("conflict_cleaned")),
                    "cleaned": bool(fixed_prompt_audit.get("cleaned")),
                    "applied": bool(fixed_prompt_audit.get("applied")),
                    "raw_length": _safe_int(fixed_prompt_audit.get("raw_length"), 0, 0),
                    "normalized_length": _safe_int(
                        fixed_prompt_audit.get("normalized_length"), 0, 0
                    ),
                    "applied_length": _safe_int(
                        fixed_prompt_audit.get("applied_length"), 0, 0
                    ),
                    "raw_sha256": _single_line(
                        fixed_prompt_audit.get("raw_sha256"), 80
                    ),
                    "normalized_sha256": _single_line(
                        fixed_prompt_audit.get("normalized_sha256"), 80
                    ),
                    "applied_sha256": _single_line(
                        fixed_prompt_audit.get("applied_sha256"), 80
                    ),
                    "removed_rules": [
                        _single_line(value, 80)
                        for value in (fixed_prompt_audit.get("removed_rules") or [])
                        if _single_line(value, 80)
                    ][:12],
                },
                "detected_conflicts": compact_audit(detected_conflict_details),
                "removed_conflict_details": compact_audit(removed_conflict_details),
                "residual_conflict_details": compact_audit(residual_conflict_details),
            }
            raw = self.data.setdefault("recent_photo_generations", [])
            if not isinstance(raw, list):
                raw = []
                self.data["recent_photo_generations"] = raw
            raw.insert(0, item)
            del raw[48:]
            self._save_data_sync(sections={"recent_photo_generations"})
        except Exception as exc:
            logger.debug("[PrivateCompanion] 记录最近生图提示词失败: %s", _single_line(exc, 120))

    def _record_photo_reference_feedback(
        self,
        feedback_text: Any,
        *,
        continuity_key: str = "",
        session_key: str = "",
    ) -> dict[str, Any]:
        feedback = analyze_photo_reference_feedback(feedback_text)
        if not feedback.issues and not feedback.regenerate_requested:
            return {}
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        generations = data.get("recent_photo_generations")
        if not isinstance(generations, list):
            return {}
        normalized_continuity = self._normalize_photo_continuity_key(continuity_key)
        normalized_session = _single_line(session_key, 340)
        if not normalized_continuity and not normalized_session:
            return {}
        linked: dict[str, Any] | None = None
        now = _now_ts()
        for candidate in generations:
            if not isinstance(candidate, dict):
                continue
            if normalized_continuity and self._normalize_photo_continuity_key(
                candidate.get("continuity_key")
            ) != normalized_continuity:
                continue
            if normalized_session and _single_line(candidate.get("session"), 340) != normalized_session:
                continue
            generated_at = _safe_float(candidate.get("ts"), 0.0, 0.0)
            if generated_at and now - generated_at > 6 * 3600:
                continue
            linked = candidate
            break
        if linked is None:
            return {}

        issues = list(dict.fromkeys((*linked.get("reference_feedback_issues", []), *feedback.issues)))
        linked["regeneration_requested"] = bool(
            linked.get("regeneration_requested") or feedback.regenerate_requested
        )
        linked["reference_feedback_issues"] = issues
        linked["reference_feedback_count"] = _safe_int(
            linked.get("reference_feedback_count"), 0, 0
        ) + 1
        record = {
            "schema_version": 1,
            "ts": now,
            "feedback": _single_line(feedback_text, 500),
            "regenerate_requested": feedback.regenerate_requested,
            "issues": list(feedback.issues),
            "confidence": feedback.confidence,
            "source": feedback.source,
            "generation_trace": _single_line(linked.get("trace"), 40),
            "generation_ts": linked.get("ts"),
            "continuity_key": self._normalize_photo_continuity_key(linked.get("continuity_key")),
            "session": _single_line(linked.get("session"), 340),
            "backend": _single_line(linked.get("backend"), 80),
            "final_prompt": _single_line(linked.get("prompt"), 900),
            "prompt_hash": _single_line(linked.get("prompt_hash"), 80),
            "prompt_path": _path_text(linked.get("prompt_path"), 1000),
            "reference_intent": deepcopy(linked.get("reference_intent") or {}),
            "reference_plan": deepcopy(linked.get("reference_plan") or {}),
            "reference_fallback": deepcopy(linked.get("reference_fallback") or {}),
        }
        records = data.setdefault("photo_reference_feedback", [])
        if not isinstance(records, list):
            records = []
            data["photo_reference_feedback"] = records
        records.insert(0, record)
        del records[96:]
        self._save_data_sync(
            sections={"recent_photo_generations", "photo_reference_feedback"}
        )
        return record

    def _record_photo_reference_feedback_from_event(self, event: Any) -> dict[str, Any]:
        if event is None or bool(getattr(event, "_private_companion_photo_feedback_recorded", False)):
            return {}
        try:
            setattr(event, "_private_companion_photo_feedback_recorded", True)
        except Exception:
            pass
        text = str(getattr(event, "message_str", "") or "")
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            user_id = ""
        session = _single_line(getattr(event, "unified_msg_origin", ""), 340)
        continuity_key = self._compose_photo_continuity_key(session, user_id)
        if not continuity_key:
            return {}
        return self._record_photo_reference_feedback(
            text,
            continuity_key=continuity_key,
        )

    def _write_photo_prompt_debug_file(
        self,
        *,
        trace_id: str,
        session_key: str,
        workflow_kind: str,
        base_prompt: str,
        scene_context_before: str,
        scene_context_after: str,
        reference: dict[str, Any] | None,
        wardrobe: PhotoWardrobeDecision,
        presets: list[str],
        prompt_sections_before: dict[str, Any],
        prompt_sections: dict[str, str],
        prompt_sections_after: dict[str, Any],
        final_prompt: str,
        submitted_prompt: str = "",
        conflicts: list[str],
        removed_conflicts: list[str],
        residual_conflicts: list[str],
        detected_conflict_details: list[dict[str, Any]],
        removed_conflict_details: list[dict[str, Any]],
        residual_conflict_details: list[dict[str, Any]],
        reference_removed: dict[str, Any] | None,
        sanitizer_version: int,
        reference_intent: ReferenceIntent | None = None,
        reference_plan: PhotoReferencePlan | None = None,
        reference_fallback: ReferenceFallback | None = None,
        suggested_scene_preset: str = "",
        prompt_format: str = "",
        workflow_fixed_prompt_audit: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        prompt_hash = hashlib.sha256(str(final_prompt or "").encode("utf-8", "ignore")).hexdigest()
        submitted_prompt_hash = hashlib.sha256(
            str(submitted_prompt or final_prompt or "").encode("utf-8", "ignore")
        ).hexdigest()
        if self._photo_generation_trace_max_bytes() <= 0:
            return "", prompt_hash
        try:
            root = Path(self.data_dir) / "photo_prompt_debug"
            root.mkdir(parents=True, exist_ok=True)
            now = datetime.now()
            filename = f"{now.strftime('%Y%m%d_%H%M%S_%f')}_{_single_line(trace_id, 40) or 'photo'}.json"
            path = root / filename

            def redact(value: Any) -> Any:
                if isinstance(value, str):
                    return _redact_outbound_secrets(value, self)
                if isinstance(value, dict):
                    return {str(key): redact(item) for key, item in value.items()}
                if isinstance(value, (list, tuple)):
                    return [redact(item) for item in value]
                return value

            reference_payload = {
                "id": _single_line((reference or {}).get("id"), 60),
                "kind": _single_line((reference or {}).get("kind"), 40),
                "path": _path_text((reference or {}).get("path"), 1000),
                "roles": list((reference or {}).get("reference_roles") or []),
                "outfit_category": _single_line((reference or {}).get("outfit_category"), 40),
                "outfit_lock_default": bool((reference or {}).get("outfit_lock_default")),
                "preferred_preset": _single_line((reference or {}).get("preferred_preset"), 60),
                "metadata_source": _single_line((reference or {}).get("metadata_source"), 30),
            }
            intent_payload = reference_intent or ReferenceIntent((), (), "ambiguous", 0.0, "none")
            plan_payload = reference_plan or PhotoReferencePlan((), "", "", "")
            fallback_payload = reference_fallback or ReferenceFallback((), (), (), "")
            payload = redact(
                {
                    "schema_version": 4,
                    "created_at": now.isoformat(timespec="seconds"),
                    "trace": _single_line(trace_id, 40),
                    "session": _single_line(session_key, 340),
                    "workflow_kind": _single_line(workflow_kind, 40),
                    "preset_hint": _single_line(suggested_scene_preset, 80),
                    "requested_scene_preset": _single_line(suggested_scene_preset, 80),
                    "prompt_format": (
                        self._normalize_photo_generation_prompt_format(prompt_format)
                        if prompt_format
                        else self._photo_generation_prompt_format_mode()
                    ),
                    "base_prompt": base_prompt,
                    "scene_context_before": scene_context_before,
                    "scene_context_after": scene_context_after,
                    "reference": reference_payload,
                    "reference_intent": {
                        "requested_roles": list(intent_payload.requested_roles),
                        "excluded_roles": list(intent_payload.excluded_roles),
                        "continuity_mode": intent_payload.continuity_mode,
                        "confidence": intent_payload.confidence,
                        "source": intent_payload.source,
                    },
                    "reference_plan": {
                        "bindings": [
                            {
                                "reference_id": binding.reference_id,
                                "path": binding.path,
                                "roles": list(binding.roles),
                                "priority": binding.priority,
                                "preserve": list(binding.preserve),
                                "ignore": list(binding.ignore),
                            }
                            for binding in plan_payload.bindings
                        ],
                        "primary_reference_id": plan_payload.primary_reference_id,
                        "selection_reason": plan_payload.selection_reason,
                        "fallback_reason": plan_payload.fallback_reason,
                    },
                    "reference_fallback": {
                        "requested_roles": list(fallback_payload.requested_roles),
                        "fulfilled_roles": list(fallback_payload.fulfilled_roles),
                        "missing_roles": list(fallback_payload.missing_roles),
                        "message": fallback_payload.message,
                    },
                    "wardrobe_decision": wardrobe.as_dict(),
                    "presets": list(presets)[:1],
                    "prompt_sections_before": prompt_sections_before,
                    "prompt_sections": prompt_sections,
                    "prompt_sections_after": prompt_sections_after,
                    "conflicts": list(conflicts),
                    "removed_conflicts": list(removed_conflicts),
                    "residual_conflicts": list(residual_conflicts),
                    "detected_conflicts": list(detected_conflict_details),
                    "removed_conflict_details": list(removed_conflict_details),
                    "residual_conflict_details": list(residual_conflict_details),
                    "reference_removed": dict(reference_removed or {}),
                    "sanitizer_version": _safe_int(sanitizer_version, 0, 0),
                    "workflow_fixed_prompt": dict(workflow_fixed_prompt_audit or {}),
                    "final_prompt": final_prompt,
                    "final_prompt_length": len(str(final_prompt or "")),
                    "final_prompt_sha256": prompt_hash,
                    "submitted_prompt_length": len(str(submitted_prompt or final_prompt or "")),
                    "submitted_prompt_sha256": submitted_prompt_hash,
                }
            )
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            debug_files = sorted(
                root.glob("*.json"),
                key=lambda item: item.name,
                reverse=True,
            )
            for stale in debug_files[40:]:
                try:
                    stale.unlink()
                except OSError:
                    pass
            return str(path), prompt_hash
        except Exception as exc:
            logger.debug(
                "[PrivateCompanion] 写入完整生图提示词调试文件失败: trace=%s error=%s",
                _single_line(trace_id, 40),
                _single_line(exc, 160),
            )
            return "", prompt_hash

    def _photo_generation_result_metadata(
        self,
        *,
        image_path: str = "",
        session_key: str = "",
    ) -> dict[str, Any]:
        raw = self.data.get("recent_photo_generations") if isinstance(getattr(self, "data", None), dict) else []
        if not isinstance(raw, list):
            return {}
        target_path = _path_text(image_path, 1000)
        target_session = _single_line(session_key, 340)
        for item in raw:
            if not isinstance(item, dict):
                continue
            if target_path and _path_text(item.get("path"), 1000) != target_path:
                continue
            if not target_path and target_session and _single_line(item.get("session"), 340) != target_session:
                continue
            return dict(item)
        return {}

    @staticmethod
    def _normalize_photo_continuity_key(value: Any) -> str:
        key = _single_line(value, 340).strip()
        if key.startswith("tool_photo_"):
            key = key[len("tool_photo_") :]
        return key

    @classmethod
    def _compose_photo_continuity_key(cls, session_key: Any, user_id: Any) -> str:
        session = cls._normalize_photo_continuity_key(session_key)
        sender = _single_line(user_id, 80).strip()
        if not session or not sender:
            return ""
        return _single_line(f"{session}|sender={sender}", 340)

    @classmethod
    def _photo_continuity_store_key(cls, continuity_key: Any) -> str:
        normalized = cls._normalize_photo_continuity_key(continuity_key)
        if not normalized:
            return ""
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]

    def _remember_sent_photo_continuity_reference(self, item: dict[str, Any]) -> None:
        if not isinstance(item, dict) or not bool(item.get("ok")) or not bool(item.get("sent")):
            return
        continuity_key = self._normalize_photo_continuity_key(item.get("continuity_key"))
        store_key = self._photo_continuity_store_key(continuity_key)
        image_path = _path_text(item.get("path"), 1000)
        if not store_key or not image_path:
            return
        try:
            path = Path(image_path).expanduser().resolve()
            if (
                not path.exists()
                or not path.is_file()
                or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}
            ):
                return
        except (OSError, ValueError):
            return

        final_presets = [
            _single_line(value, 80)
            for value in (item.get("presets") if isinstance(item.get("presets"), list) else [])
            if _single_line(value, 80)
        ]
        final_scene_preset = (
            final_presets[0]
            if final_presets
            else (
                _single_line(item.get("scene_preset"), 80)
                if _safe_int(item.get("schema_version"), 1) >= 2
                else ""
            )
        )
        now = _now_ts()
        raw_store = self.data.setdefault("recent_photo_continuity", {})
        if not isinstance(raw_store, dict):
            raw_store = {}
            self.data["recent_photo_continuity"] = raw_store
        raw_store[store_key] = {
            "schema_version": 2,
            "continuity_key": continuity_key,
            "sent_at": now,
            "generated_at": _safe_float(item.get("ts"), now),
            "path": str(path),
            "kind": _single_line(item.get("kind"), 30),
            "intent_kind": _single_line(item.get("intent_kind"), 30),
            "prompt": _single_line(item.get("prompt"), 900),
            "caption": _single_line(item.get("caption"), 160),
            "scene_preset": final_scene_preset,
            "preset_source": _single_line(item.get("preset_source"), 40),
            "reference_path": _path_text(item.get("reference_path"), 1000),
            "wardrobe_mode": _single_line(item.get("wardrobe_mode"), 40),
            "wardrobe_category": _single_line(item.get("wardrobe_category"), 40),
            "reference_roles": list(item.get("reference_roles") or []),
        }

        keep_after = now - 24 * 3600
        for key, record in list(raw_store.items()):
            if not isinstance(record, dict) or _safe_float(record.get("sent_at"), 0) < keep_after:
                raw_store.pop(key, None)
        if len(raw_store) > 96:
            ordered = sorted(
                raw_store.items(),
                key=lambda pair: _safe_float(pair[1].get("sent_at"), 0) if isinstance(pair[1], dict) else 0,
                reverse=True,
            )
            self.data["recent_photo_continuity"] = dict(ordered[:96])

    def _recent_sent_photo_continuity_candidate(
        self,
        continuity_key: Any,
        *,
        now: float | None = None,
        max_age_seconds: float = 45 * 60,
    ) -> dict[str, str]:
        normalized = self._normalize_photo_continuity_key(continuity_key)
        store_key = self._photo_continuity_store_key(normalized)
        data = getattr(self, "data", {})
        raw_store = data.get("recent_photo_continuity") if isinstance(data, dict) else {}
        record = raw_store.get(store_key) if store_key and isinstance(raw_store, dict) else None
        if not isinstance(record, dict):
            return {}
        if self._normalize_photo_continuity_key(record.get("continuity_key")) != normalized:
            return {}
        check_now = _now_ts() if now is None else float(now)
        sent_at = _safe_float(record.get("sent_at"), 0)
        age = check_now - sent_at
        if sent_at <= 0 or age < -300 or age > max(60.0, float(max_age_seconds)):
            return {}
        image_path = _path_text(record.get("path"), 1000)
        try:
            path = Path(image_path).expanduser().resolve()
            if (
                not path.exists()
                or not path.is_file()
                or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}
            ):
                return {}
        except (OSError, ValueError):
            return {}
        previous_prompt = _single_line(record.get("prompt"), 360)
        previous_caption = _single_line(record.get("caption"), 120)
        record_schema_version = _safe_int(record.get("schema_version"), 1)
        previous_scene = (
            _single_line(record.get("scene_preset"), 80)
            if record_schema_version >= 2
            else ""
        )
        details = "；".join(
            part
            for part in (
                f"上一张画面要求：{previous_prompt}" if previous_prompt else "",
                f"上一张附言：{previous_caption}" if previous_caption else "",
                f"上一张场景预设：{previous_scene}" if previous_scene else "",
            )
            if part
        )
        note = (
            "同一会话刚刚已经实际发送的上一张成图；只有当前要求是在原画面上自然续拍，主要改变动作、表情、视线、机位或近似构图时使用；"
            "若明确更换人物、服装、地点、时间、整体场景或另起主题则不要使用"
        )
        if details:
            note = f"{note}；{details}"
        return {
            "id": "recent_sent_photo",
            "path": str(path),
            "source": str(path),
            "kind": "recent_sent_photo",
            "note": _single_line(note, 760),
            "reference_roles": ["identity", "outfit", "scene", "continuity"],
            "outfit_category": _single_line(record.get("wardrobe_category"), 40),
            "outfit_lock_default": True,
            "preferred_preset": previous_scene,
            "metadata_source": "runtime",
        }

    def _annotate_recent_photo_generation(
        self,
        *,
        image_path: str = "",
        session_key: str = "",
        trigger: str = "",
        intent_kind: str = "",
        sent: bool | None = None,
        caption: str = "",
        preset_hint: str = "",
        tool_name: str = "",
    ) -> None:
        try:
            raw = self.data.get("recent_photo_generations")
            if not isinstance(raw, list):
                return
            target_path = _path_text(image_path, 1000)
            target_session = _single_line(session_key, 340)
            for item in raw:
                if not isinstance(item, dict):
                    continue
                same_path = bool(target_path and _path_text(item.get("path"), 1000) == target_path)
                same_session = bool(target_session and _single_line(item.get("session"), 340) == target_session)
                if not (same_path if target_path else same_session):
                    continue
                if trigger:
                    item["trigger"] = _single_line(trigger, 40)
                if intent_kind:
                    item["intent_kind"] = _single_line(intent_kind, 30)
                if sent is not None:
                    item["sent"] = bool(sent)
                if caption:
                    item["caption"] = _single_line(caption, 120)
                if preset_hint:
                    item["preset_hint"] = _single_line(preset_hint, 80)
                if tool_name:
                    item["tool_name"] = _single_line(tool_name, 60)
                item["annotated_at"] = _now_ts()
                if sent is True:
                    self._remember_sent_photo_continuity_reference(item)
                save_sections = {"recent_photo_generations"}
                if sent is True:
                    save_sections.add("recent_photo_continuity")
                self._save_data_sync(sections=save_sections)
                return
        except Exception as exc:
            logger.debug("[PrivateCompanion] 标注最近生图记录失败: %s", _single_line(exc, 120))

    def _apply_photo_generation_fixed_prompt(self, prompt_text: str) -> str:
        prompt = str(prompt_text or "").strip()
        fixed = _single_line(
            runtime_persona_setting(self, "photo_generation_fixed_prompt", ""), 500
        )
        if not fixed:
            return prompt
        if fixed in prompt:
            return _single_line(prompt, 1800)
        return _single_line(f"{prompt}\n\nAdditional fixed prompt: {fixed}".strip(), 1800)

    @staticmethod
    def _sanitize_photo_generation_fixed_prompt_config(value: Any, *, limit: int = 5000) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
        text = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]", "", text)
        text = re.sub(
            r"</?(?:instruction|system|assistant|user|tool|memorycompanion-context)\b[^>]*>",
            " ",
            text,
            flags=re.I,
        )
        text = re.sub(
            r"(?im)^\s*\[(?:user image request|reference and wardrobe ruling|"
            r"scene, style and final preset|composition and continuity)\]\s*$",
            " ",
            text,
        )
        return _single_line(text, max(0, int(limit or 0)))

    def _photo_generation_workflow_fixed_prompt_section(
        self,
        workflow_kind: str,
    ) -> tuple[PhotoPromptSection, dict[str, Any]]:
        normalized = str(workflow_kind or "").strip().lower()
        if normalized in {"edit", "改图", "修图", "重绘", "p图"}:
            scope = "edit"
            config_key = "photo_generation_edit_fixed_prompt"
            label = "Additional image-edit fixed prompt"
        elif normalized in {"selfie", "portrait", "自拍", "人像"}:
            scope = "selfie"
            config_key = "photo_generation_selfie_fixed_prompt"
            label = "Additional selfie fixed prompt"
        else:
            scope = "text2img"
            config_key = "photo_generation_text2img_fixed_prompt"
            label = "Additional text-to-image fixed prompt"

        raw = str(getattr(self, config_key, "") or "")
        normalized_prompt = self._sanitize_photo_generation_fixed_prompt_config(raw)
        positive, negative = self._photo_generation_semantic_prompt_parts(normalized_prompt)
        section = PhotoPromptSection(
            name="workflow_fixed_prompt",
            source="fixed_prompt",
            positive=f"{label}: {positive}" if positive else "",
            negative=negative,
            protected=True,
            sanitize_conflicts=True,
        )
        raw_trimmed = raw.strip()
        audit = {
            "scope": scope,
            "config_key": config_key,
            "configured": bool(raw_trimmed),
            "normalized": bool(normalized_prompt),
            "normalization_changed": raw_trimmed != normalized_prompt,
            "raw_length": len(raw),
            "normalized_length": len(normalized_prompt),
            "raw_sha256": hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()
            if raw
            else "",
            "normalized_sha256": hashlib.sha256(
                normalized_prompt.encode("utf-8", "ignore")
            ).hexdigest()
            if normalized_prompt
            else "",
        }
        return section, audit

    @staticmethod
    def _normalize_photo_generation_prompt_format(value: Any) -> str:
        text = str(value or "traditional").strip().lower().replace("-", "_")
        if text in {"nai", "novelai", "nai4", "nai_4", "nai45", "nai_diffusion", "naidiffusion", "nai联动", "nai插件联动"}:
            return "nai"
        if text in {"natural", "natural_language", "description", "prose", "自然语言", "自然语言描述"}:
            return "natural_language"
        return "traditional"

    @staticmethod
    def _normalize_bot_relationship_cards(value: Any) -> list[str]:
        return normalize_bot_relationship_cards(value)

    def _photo_generation_prompt_format_mode(self) -> str:
        return self._normalize_photo_generation_prompt_format(
            runtime_persona_setting(self, "photo_generation_prompt_format", "traditional")
        )

    @staticmethod
    def _normalize_photo_generation_negative_prompt_mode(value: Any) -> str:
        normalized = str(value or "safe_default").strip().lower().replace("-", "_")
        if normalized in {"merge", "append", "custom_merge", "合并", "合并自定义"}:
            return "merge"
        if normalized in {"replace", "override", "custom_replace", "替换", "完全替换"}:
            return "replace"
        return "safe_default"

    def _photo_generation_negative_prompt_mode(self) -> str:
        return self._normalize_photo_generation_negative_prompt_mode(
            runtime_persona_setting(
                self, "photo_generation_negative_prompt_mode", "safe_default"
            )
        )

    @classmethod
    def _sanitize_photo_generation_negative_prompt_config(
        cls,
        value: Any,
        *,
        limit: int = 3000,
    ) -> str:
        raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = cls._sanitize_photo_generation_fixed_prompt_config(
            raw.replace("\n", ", "),
            limit=limit,
        )
        if not text:
            return ""
        negative_match = re.search(r"negative\s+prompt\s*:\s*(.*)$", text, flags=re.I | re.S)
        if negative_match:
            text = negative_match.group(1)
        else:
            text = re.sub(r"^(?:avoid|negative|负面提示词)\s*[：:]?\s*", "", text, flags=re.I)
        values: list[str] = []
        seen: set[str] = set()
        for raw_part in re.split(r"(?:\r?\n+|[,，;；]+)", text):
            part = re.sub(r"\s+", " ", raw_part).strip(" .。")
            if not part:
                continue
            _is_negative, content = cls._photo_generation_negative_clause_content(part)
            content = content or part
            key = content.casefold()
            if key in seen:
                continue
            seen.add(key)
            values.append(content)
        return _single_line(", ".join(values), limit)

    def _photo_generation_custom_negative_prompt(self, workflow_kind: str) -> str:
        raw_kind = str(workflow_kind or "").strip().lower()
        if raw_kind in {"edit", "改图", "修图", "重绘", "p图"}:
            normalized = "edit"
        elif raw_kind in {"selfie", "portrait", "自拍", "人像", "sticker", "emoji", "meme", "表情包", "贴纸"}:
            normalized = "selfie"
        else:
            normalized = "text2img"
        scoped_key = {
            "text2img": "photo_generation_text2img_negative_prompt",
            "selfie": "photo_generation_selfie_negative_prompt",
            "edit": "photo_generation_edit_negative_prompt",
        }[normalized]
        values = (
            runtime_persona_setting(self, "photo_generation_negative_prompt", ""),
            getattr(self, scoped_key, ""),
        )
        combined: list[str] = []
        seen: set[str] = set()
        for value in values:
            sanitized = self._sanitize_photo_generation_negative_prompt_config(value)
            for part in (item.strip() for item in sanitized.split(",")):
                key = part.casefold()
                if not part or key in seen:
                    continue
                seen.add(key)
                combined.append(part)
        return _single_line(", ".join(combined), 5000)

    def _apply_photo_generation_negative_prompt_policy(
        self,
        sections: tuple[PhotoPromptSection, ...],
        workflow_kind: str,
    ) -> tuple[PhotoPromptSection, ...]:
        mode = self._photo_generation_negative_prompt_mode()
        adjusted = list(sections)
        if mode == "replace":
            replaceable_names = {
                "natural_language_contract",
                "daily_outfit_contract",
                "edit_contract",
                "composition",
                "subject_count",
            }
            adjusted = [
                replace(section, negative="")
                if section.name in replaceable_names and section.negative
                else section
                for section in adjusted
            ]
        if mode in {"merge", "replace"}:
            custom_negative = self._photo_generation_custom_negative_prompt(workflow_kind)
            if custom_negative:
                adjusted.append(
                    PhotoPromptSection(
                        name="custom_negative_prompt",
                        source="fixed_prompt",
                        negative=custom_negative,
                        protected=True,
                        sanitize_conflicts=True,
                    )
                )
        return tuple(adjusted)

    def _photo_generation_prompt_format_instruction(self) -> str:
        mode = self._photo_generation_prompt_format_mode()
        if mode == "nai":
            return (
                "NAI4.5：danbooru标签精简勿堆砌；加权用{}、降权用[]，"
                "1.5::tags::加权可用高权重(2/5/10)，-1::负向移除；"
                "已知角色用 人物名(作品名)；情绪词有效；"
                "多角色{人物[tags]人物}(勿删)；互动source#/target#/mutual#"
            )
        if mode == "natural_language":
            return (
                "自然语言描述：连贯具体的英文句子，覆盖主体、外观、动作、场景、光线、镜头、构图与风格；"
                "不要标签堆或权重语法；要避免的内容在末句用 Avoid ... 表达。"
            )
        return (
            "传统文生图写法：英文短词组按主体、外观、服装、场景、光线、镜头、构图、风格排列，逗号分隔；"
            "用 Positive prompt: ... Negative prompt: ... 结构，不写解释段落。"
        )

    @staticmethod
    def _photo_generation_negative_clause_content(clause: str) -> tuple[bool, str]:
        text = re.sub(r"\s+", " ", str(clause or "")).strip(" ,.;；。，")
        if not text:
            return False, ""
        text = re.sub(
            r"^(?:user\s+request|requested\s+final\s+image|用户要求|画面要求)\s*[：:]\s*",
            "",
            text,
            flags=re.I,
        ).strip()
        prefix = re.compile(
            r"^(?:请)?(?:不要|别(?:再)?(?:穿|用|选)?|不想穿|不穿|不用|不是|无需|无须|避免|禁止|不许|不得|排除|拒绝|去掉|脱下|取消)\s*"
            r"|^(?:do\s+not|don't|not|avoid|without|no|exclude|skip|remove)\s+",
            flags=re.I,
        )
        match = prefix.match(text)
        if match:
            return True, text[match.end():].strip(" ,.;；。，")
        postfix = re.compile(
            r"\s*(?:不要(?:了)?|别穿|不穿|不用|算了|就算了|除外|排除|取消|not|no)\s*$",
            flags=re.I,
        )
        match = postfix.search(text)
        if match:
            return True, text[:match.start()].strip(" ,.;；。，")
        return False, text

    @classmethod
    def _photo_generation_semantic_prompt_parts(cls, prompt_text: str) -> tuple[str, str]:
        """Separate positive request clauses from explicit exclusions without losing mixed requests."""
        prompt = str(prompt_text or "").strip()
        positive_match = re.search(
            r"positive\s+prompt\s*:\s*(.*?)(?=negative\s+prompt\s*:|$)",
            prompt,
            flags=re.I | re.S,
        )
        if positive_match:
            positive_raw = positive_match.group(1).strip()
            negative_match = re.search(r"negative\s+prompt\s*:\s*(.*)$", prompt, flags=re.I | re.S)
            negative_raw = negative_match.group(1).strip() if negative_match else ""
        else:
            positive_raw = prompt
            negative_raw = ""

        positive_parts: list[str] = []
        negative_parts: list[str] = []

        def add_clause(raw_clause: str) -> None:
            clause = re.sub(r"\s+", " ", str(raw_clause or "")).strip(" ,.;；。，")
            if not clause:
                return
            is_negative, content = cls._photo_generation_negative_clause_content(clause)
            if is_negative:
                transition = re.search(
                    r"(?:但|而|不过|可是)?(?:改穿|换成|换上|换为|改为|要穿|穿上|而要)"
                    r"|\b(?:but|instead|and)\s+(?:wear|change\s+into|switch\s+to|put\s+on)\b",
                    content,
                    flags=re.I,
                )
                if transition and transition.start() > 0:
                    excluded = content[:transition.start()].strip(" ,.;；。，")
                    requested = content[transition.start():].strip(" ,.;；。，")
                    if excluded:
                        negative_parts.append(excluded)
                    if requested:
                        positive_parts.append(requested)
                    return
                if content:
                    negative_parts.append(content)
                return
            if content:
                positive_parts.append(content)

        for clause in re.split(r"(?:\r?\n+|[。；;，,]+|(?<=[.!?])\s+)", positive_raw):
            add_clause(clause)
        for clause in re.split(r"(?:\r?\n+|[。；;，,]+|(?<=[.!?])\s+)", negative_raw):
            cleaned = re.sub(r"\s+", " ", str(clause or "")).strip(" ,.;；。，")
            if not cleaned:
                continue
            _, content = cls._photo_generation_negative_clause_content(cleaned)
            if content:
                negative_parts.append(content)

        return ", ".join(dict.fromkeys(positive_parts)), ", ".join(dict.fromkeys(negative_parts))

    def _apply_photo_generation_prompt_format(
        self,
        prompt_text: str,
        *,
        prompt_format: str = "",
    ) -> str:
        prompt = str(prompt_text or "").strip()
        if not prompt:
            return ""
        mode = (
            self._normalize_photo_generation_prompt_format(prompt_format)
            if prompt_format
            else self._photo_generation_prompt_format_mode()
        )
        if mode == "nai":
            # Preserve NovelAI inline syntax ({}/[], weight::tags::, multi-character blocks) as authored.
            positive_match = re.search(
                r"positive\s+prompt\s*:\s*(.*?)(?=negative\s+prompt\s*:|$)",
                prompt,
                flags=re.I | re.S,
            )
            negative_match = re.search(r"negative\s+prompt\s*:\s*(.*)$", prompt, flags=re.I | re.S)
            if positive_match or negative_match:
                if positive_match:
                    positive_raw = positive_match.group(1).strip()
                elif negative_match:
                    positive_raw = prompt[:negative_match.start()].strip(" \t\r\n,;。；")
                else:
                    positive_raw = prompt
                negative_raw = negative_match.group(1).strip(" \t\r\n.,;!?。；！") if negative_match else ""
                separator = ", " if positive_raw and negative_raw else ""
                prompt = positive_raw + (f"{separator}-1.5::{negative_raw}::" if negative_raw else "")
            return self._photo_prompt_clip(prompt, 2400, preserve_tail=True)
        positive, negative = self._photo_generation_semantic_prompt_parts(prompt)
        positive = positive or "the requested image"
        if mode == "natural_language":
            natural = f"Create a single coherent image showing {positive}."
            if negative:
                natural += f" Avoid {negative}."
            return self._photo_prompt_clip(natural, 6000, preserve_tail=True)
        formatted = f"Positive prompt: {positive}."
        if negative:
            formatted += f" Negative prompt: {negative}."
        return self._photo_prompt_clip(formatted, 6000, preserve_tail=True)

    def _photo_generation_selfie_schedule_scene_hint(
        self,
        user_id: str = "",
        *,
        include_dialogue_outfit: bool = True,
    ) -> str:
        snapshot_builder = getattr(self, "_build_companion_scene_snapshot", None)
        snapshot_formatter = getattr(self, "_format_companion_scene_snapshot", None)
        if callable(snapshot_builder) and callable(snapshot_formatter):
            try:
                scene_user: dict[str, Any] | None = None
                normalized_user_id = _single_line(user_id, 80)
                if normalized_user_id:
                    user_getter = getattr(self, "_get_user", None)
                    if callable(user_getter):
                        try:
                            candidate = user_getter(normalized_user_id)
                            if isinstance(candidate, dict):
                                scene_user = dict(candidate)
                                scene_user.setdefault("user_id", normalized_user_id)
                        except Exception:
                            scene_user = {"user_id": normalized_user_id, "relationship_role": "owner"}
                try:
                    snapshot = snapshot_builder(
                        scene_user,
                        include_dialogue_outfit=include_dialogue_outfit,
                    )
                except TypeError:
                    snapshot = snapshot_builder(scene_user)
                snapshot_text = _single_line(
                    snapshot_formatter(snapshot, purpose="selfie_scene"),
                    700,
                )
                if snapshot_text:
                    return snapshot_text
            except Exception as exc:
                logger.debug(
                    "[PrivateCompanion] 自拍场景读取统一情境快照失败，已回退旧路径: %s",
                    _single_line(exc, 160),
                )
        plan = self.data.get("daily_plan", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        plan = plan if isinstance(plan, dict) else {}
        state = self.data.get("daily_state", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        state = state if isinstance(state, dict) else {}

        current_schedule = ""
        try:
            current_item = self._get_current_plan_item(plan)
            if isinstance(current_item, dict):
                current_schedule = _single_line(self._format_plan_item_for_prompt(current_item), 260)
        except Exception:
            current_schedule = ""
        if not current_schedule and callable(getattr(self, "_format_schedule_context_for_prompt", None)):
            try:
                current_schedule = _single_line(self._format_schedule_context_for_prompt(plan), 260)
            except Exception:
                current_schedule = ""

        location = ""
        if callable(getattr(self, "_current_location_state_text", None)):
            try:
                location = _single_line(self._current_location_state_text(state), 60)
            except Exception:
                location = ""
        coarse_location = ""
        if location and callable(getattr(self, "_coarse_roleplay_location_text", None)):
            try:
                coarse_location = _single_line(self._coarse_roleplay_location_text(location), 40)
            except Exception:
                coarse_location = ""
        location_text = coarse_location or location
        if location and coarse_location and location != coarse_location:
            location_text = f"{coarse_location}（{location}）"

        parts: list[str] = []
        if current_schedule:
            parts.append(f"当前日程：{current_schedule}")
        if location_text:
            parts.append(f"当前位置：{location_text}")
        _, scene_category_label = infer_companion_scene_category(current_schedule, location_text)
        if scene_category_label:
            parts.append(f"当前场景：{scene_category_label}")
        return _single_line("；".join(parts), 460)

    def _photo_reference_schedule_history_context(self) -> str:
        """Format today's started schedule items for reference selection only."""

        snapshot_builder = getattr(self, "_build_companion_scene_snapshot", None)
        if not callable(snapshot_builder):
            return ""
        try:
            snapshot = snapshot_builder()
        except Exception:
            return ""
        schedule = snapshot.get("schedule") if isinstance(snapshot, dict) else {}
        history = schedule.get("history") if isinstance(schedule, dict) else []
        if not isinstance(history, list):
            return ""
        labels = {
            "active": "进行中",
            "completed": "已完成",
            "changed": "已变更",
        }
        lines: list[str] = []
        for item in history[:24]:
            if not isinstance(item, dict):
                continue
            status = _single_line(item.get("status"), 20).lower()
            if status not in labels:
                continue
            start = _single_line(item.get("time"), 12)
            end = _single_line(item.get("end"), 12)
            activity = _single_line(item.get("activity"), 160)
            mood = _single_line(item.get("mood"), 32)
            if not activity:
                continue
            window = "-".join(part for part in (start, end) if part)
            lines.append(
                "｜".join(
                    part
                    for part in (
                        window,
                        labels[status],
                        activity,
                        f"情绪：{mood}" if mood else "",
                    )
                    if part
                )
            )
        return self._photo_prompt_clip("\n".join(lines), 2400)

    @staticmethod
    def _photo_reference_paths_equal(left: str, right: str) -> bool:
        left_text = str(left or "").strip()
        right_text = str(right or "").strip()
        if not left_text or not right_text:
            return False
        try:
            left_text = str(Path(left_text).expanduser().resolve())
            right_text = str(Path(right_text).expanduser().resolve())
        except (OSError, ValueError):
            pass
        return os.path.normcase(left_text) == os.path.normcase(right_text)

    @staticmethod
    def _photo_persona_fallback_allowed(
        workflow_kind: str,
        reference_intent: ReferenceIntent,
    ) -> bool:
        requested_roles = set(reference_intent.requested_roles or ())
        excluded_roles = set(reference_intent.excluded_roles or ())
        return (
            str(workflow_kind or "").strip().lower()
            in {"selfie", "portrait", "自拍", "人像"}
            and reference_intent.continuity_mode != "new_topic"
            and "identity" in requested_roles
            and "identity" not in excluded_roles
        )

    def _photo_generation_recent_continuity_constraint(
        self,
        workflow_kind: str,
        *,
        reference_image_path: str,
        continuity_key: str,
        wardrobe: PhotoWardrobeDecision | None = None,
    ) -> tuple[str, bool]:
        normalized_kind = str(workflow_kind or "").strip().lower()
        if normalized_kind not in {"selfie", "portrait", "自拍", "人像"}:
            return "", False
        recent = self._recent_sent_photo_continuity_candidate(continuity_key)
        if not recent or not self._photo_reference_paths_equal(
            reference_image_path,
            recent.get("path", ""),
        ):
            return "", False
        effective_roles = set(getattr(wardrobe, "effective_reference_roles", ()) or ())
        preserved = ["identity", "face", "hairstyle"]
        if "outfit" in effective_roles:
            preserved.append("exact outfit and accessories")
        if effective_roles & {"scene", "continuity"}:
            preserved.extend(("room or location", "lighting", "time of day"))
        continuity_instruction = (
            "Recent-photo continuity: this reference is the last image actually sent in the same conversation. "
            f"Unless the current request explicitly changes them, preserve {', '.join(preserved)}. "
            "Change only the requested action, pose, expression, gaze, camera angle, or framing. "
            "Any explicit new clothing, person, place, time, or scene request still has priority."
        )
        return continuity_instruction, True

    @staticmethod
    def _photo_prompt_clip(value: Any, limit: int, *, preserve_tail: bool = False) -> str:
        return _clip_photo_prompt_text(value, limit, preserve_tail=preserve_tail)

    @staticmethod
    def _photo_prompt_split_formatted(prompt_text: str) -> tuple[str, str]:
        prompt = str(prompt_text or "").strip()
        positive_match = re.search(
            r"positive\s+prompt\s*:\s*(.*?)(?=negative\s+prompt\s*:|$)",
            prompt,
            flags=re.I | re.S,
        )
        if not positive_match:
            avoid_match = re.search(
                r"(?:^|(?<=[.!?。！？]))\s*avoid\s+(.+?)\s*[.!?。！？]?\s*$",
                prompt,
                flags=re.I | re.S,
            )
            if avoid_match:
                positive = prompt[:avoid_match.start()].rstrip(" \t\r\n.!?。！？")
                negative = avoid_match.group(1).strip(" \t\r\n.!?。！？")
                return positive, negative
            return prompt, ""
        negative_match = re.search(r"negative\s+prompt\s*:\s*(.*)$", prompt, flags=re.I | re.S)
        return positive_match.group(1).strip(), (negative_match.group(1).strip() if negative_match else "")

    @staticmethod
    def _photo_generation_reference_wardrobe_section(
        reference: dict[str, Any] | None,
        wardrobe: PhotoWardrobeDecision,
    ) -> tuple[str, str]:
        reference = reference or {}
        effective_roles = tuple(wardrobe.effective_reference_roles)
        roles = ", ".join(effective_roles)
        parts: list[str] = []
        if reference:
            active_outfit_category = (
                _single_line(reference.get("outfit_category"), 40) or "unspecified"
                if "outfit" in effective_roles
                else "not active"
            )
            parts.append(
                "Reference responsibility: "
                f"effective roles={roles or 'none'}; "
                f"outfit category={active_outfit_category}."
            )
            if reference.get("kind") == "relation_role":
                role_name = _single_line(reference.get("role_name"), 80) or "the named relationship role"
                relationship = _single_line(reference.get("relationship"), 100)
                role_context = f" ({relationship})" if relationship else ""
                parts.append(
                    "Named relationship-role reference: "
                    f"the image identifies {role_name}{role_context}, not Bot. "
                    "Use it to depict that role only when the current request explicitly asks that role to appear or share the frame; "
                    "otherwise keep the role off-camera and use natural contextual cues. Do not transfer this identity, face, or body to Bot."
                )
        if wardrobe.positive_instruction:
            parts.append(f"Wardrobe decision: {wardrobe.positive_instruction}")
        return " ".join(parts), wardrobe.negative_instruction

    @classmethod
    def _photo_generation_compact_scene_hint(cls, scene_hint: str, *, limit: int = 420) -> str:
        text = _single_line(scene_hint, 1600)
        if not text or len(text) <= limit:
            return text
        parts = [part.strip() for part in re.split(r"[；;]+", text) if part.strip()]
        if len(parts) <= 1:
            return cls._photo_prompt_clip(text, min(limit, 260), preserve_tail=True)
        priorities = (
            (r"^(?:当前位置|地点|位置)[：:]", 90),
            (r"^(?:当前场景|场景)[：:]", 60),
            (r"^(?:时间|当前时间)[：:]", 60),
            (r"^(?:当前日程|日程)[：:]", 130),
            (r"^(?:今日穿搭|当天基础穿搭|当天穿搭|日常穿搭|today'?s outfit|daily outfit)[：:]", 120),
            (r"^(?:天气背景|天气|当前天气)[：:]", 90),
            (r"^(?:状态|状态余波|情绪)[：:]", 80),
            (r"^(?:视觉话题|背景)[：:]", 80),
        )
        ordered: list[tuple[str, int]] = []
        used: set[int] = set()
        for pattern, field_limit in priorities:
            for index, part in enumerate(parts):
                if index not in used and re.search(pattern, part, flags=re.I):
                    ordered.append((part, field_limit))
                    used.add(index)
        ordered.extend((part, 80) for index, part in enumerate(parts) if index not in used)
        kept: list[str] = []
        for part, field_limit in ordered:
            compact = cls._photo_prompt_clip(part, field_limit, preserve_tail=True)
            candidate = "；".join((*kept, compact))
            if len(candidate) <= limit:
                kept.append(compact)
                continue
            remaining = limit - len("；".join(kept)) - (1 if kept else 0)
            if remaining >= 36:
                kept.append(cls._photo_prompt_clip(compact, remaining, preserve_tail=True))
            break
        return "；".join(kept)

    @staticmethod
    def _photo_generation_selfie_scene_constraint(
        workflow_kind: str,
        scene_hint: str,
        *,
        has_reference: bool,
    ) -> str:
        normalized = str(workflow_kind or "").strip().lower()
        if normalized not in {"selfie", "portrait", "自拍", "人像"} or not scene_hint:
            return ""
        reference_boundary = (
            "The reference controls only the roles declared by the wardrobe ruling. "
            if has_reference
            else "Do not assume an unavailable reference was supplied. "
        )
        return (
            "Resolved selfie scene facts: "
            f"{scene_hint}. An explicit scene or location in the current request overrides conflicting facts; otherwise use these facts for time, location, activity, mood, weather, and light. "
            f"{reference_boundary}"
            "Do not restore a conflicting schedule location or wardrobe, and avoid unrelated rooms."
        )

    def _photo_generation_composition_sections(
        self,
        workflow_kind: str,
        prompt_text: str,
        *,
        allow_group_photo: bool = False,
    ) -> tuple[str, str]:
        normalized = str(workflow_kind or "").strip().lower()
        if normalized not in {"selfie", "portrait", "自拍", "人像"}:
            return "", ""
        explicit_mirror = self._photo_generation_explicit_mirror_request(prompt_text)
        explicit_back_view = self._photo_generation_explicit_back_view_request(prompt_text)
        if allow_group_photo and _photo_group_request_matches(prompt_text):
            positive = (
                "Referenced multi-person composition: preserve every person represented by the submitted visual references, "
                "their count, identity, and relative placement in one continuous scene; do not invent anyone else."
            )
            negative = "unreferenced extra people, invented faces, duplicated people, comparison panels, split screen, collage"
        elif explicit_back_view:
            positive = (
                "Back-view character composition: exactly one recognizable character wearing one coherent outfit in one continuous scene; "
                "the requested back view or facing-away pose is intentional, preserve the reference hairstyle silhouette and stable appearance, "
                "and compose a natural environmental portrait without requiring the face to be visible."
            )
            negative = "duplicated subject, twins, multiple people, outfit alternatives, comparison panels, split screen, side-by-side panels, collage, character sheet"
        elif explicit_mirror:
            positive = (
                "Selfie composition: exactly one character wearing one coherent outfit in one continuous scene; "
                "one mirror reflection of that same outfit is allowed; keep the complete face visible and do not let the phone cover it."
            )
            negative = "duplicated subject, outfit alternatives, comparison panels, split screen, side-by-side panels, collage, character sheet, phone covering face"
        else:
            positive = (
                "Selfie composition: exactly one character wearing one coherent outfit in one continuous scene; keep the face visible, "
                "prefer a handheld selfie or natural environmental portrait with upper-body to three-quarter framing, and place the character naturally in the resolved scene."
            )
            negative = (
                "duplicate character, twins, multiple people, multiple outfits, outfit comparison, before and after, split screen, "
                "side-by-side panels, diptych, collage, character sheet, mirror selfie, full-length mirror selfie, dressing-room mirror, phone covering face"
            )
        return positive, negative

    @staticmethod
    def _photo_generation_subject_count_contract(
        workflow_kind: str,
        request_text: str,
        *,
        explicit_reference_supplied: bool,
    ) -> tuple[str, str]:
        normalized = str(workflow_kind or "").strip().lower()
        if normalized in {"edit", "改图", "修图", "重绘", "p图"}:
            return "", ""
        group_photo_requested = _photo_group_request_matches(request_text)
        if explicit_reference_supplied and group_photo_requested:
            return (
                "Multi-person composition is permitted only because the current request supplied an explicit source reference; "
                "preserve the referenced people's identities and do not invent additional people.",
                "unreferenced extra people, invented faces, duplicated people",
            )
        if normalized not in {"selfie", "portrait", "自拍", "人像"} and not group_photo_requested:
            return "", ""
        return (
            "Subject-count boundary: show at most one recognizable human character in one continuous scene. "
            "Other people may be implied only by non-human traces such as a second cup, gift, note, or off-camera context; "
            "do not show another face, body, silhouette, reflection, or portrait.",
            "group photo, group portrait, couple photo, two people, multiple people, extra person, second person, "
            "companion in frame, crowd, invented face",
        )

    @staticmethod
    def _photo_generation_explicit_back_view_request(text: str) -> bool:
        raw = _single_line(text, 1200)
        if not raw:
            return False
        positive = re.split(r"negative prompt\s*:", raw, maxsplit=1, flags=re.I)[0]
        positive = re.sub(
            r"(?:不要|避免|别|不许|禁止).{0,18}(?:背影|背对镜头|背对相机)|"
            r"\b(?:no|not|avoid|without)\s+(?:a\s+)?(?:back[-\s]?view|facing\s+away)[^,.;；。]*",
            " ",
            positive,
            flags=re.I,
        )
        return bool(
            any(marker in positive for marker in ("背影", "背对镜头", "背对相机", "从背后", "身后视角"))
            or re.search(r"\b(?:back[-\s]?view|from\s+behind|facing\s+away)\b", positive, flags=re.I)
        )

    @staticmethod
    def _photo_generation_edit_contract(workflow_kind: str) -> tuple[str, str]:
        normalized = str(workflow_kind or "").strip().lower()
        if normalized not in {"edit", "改图", "修图", "重绘", "p图"}:
            return "", ""
        return (
            "Image edit contract: use the user-provided image as the sole source canvas and visual identity reference. "
            "Treat the request strictly as a constrained edit of that supplied canvas. Preserve every subject, face, body, outfit, pose, composition, camera angle, and background detail unless the user explicitly asks to change it. "
            "Apply only the requested edit and keep unrelated pixels and details as close to the source as possible.",
            "a selfie or a new character portrait, replacing the source person with the assistant persona, restoring today's outfit, unrelated redesigns",
        )

    @staticmethod
    def _photo_generation_explicit_mirror_request(text: str) -> bool:
        raw = _single_line(text, 1200)
        if not raw:
            return False
        lowered = raw.lower()
        detection_text = re.split(r"negative prompt\s*:", lowered, maxsplit=1, flags=re.I)[0]
        positive_scan = re.sub(
            r"(?:不要|避免|别|不许|禁止).{0,18}(?:镜前|对镜|镜中|镜子|全身镜|穿衣镜|试衣镜)",
            " ",
            detection_text,
            flags=re.I,
        )
        positive_scan = re.sub(
            r"(?:no|not|avoid|without)\s+(?:a\s+)?(?:mirror|mirror\s+selfie|full[-\s]?length\s+mirror|"
            r"full[-\s]?body\s+mirror|mirror\s+shot|mirror\s+photo|mirror\s+portrait)[^,.;；。]*",
            " ",
            positive_scan,
            flags=re.I,
        )
        positive_scan = re.sub(r"\bnon[-\s]?mirror\b", " ", positive_scan, flags=re.I)
        positive_scan = re.sub(r"unless[^,.;；。]*mirror[^,.;；。]*", " ", positive_scan, flags=re.I)
        if re.search(
            r"镜前|对镜|镜中|镜子|全身镜|穿衣镜|试衣镜|\bmirror\b|looking\s+in\s+the\s+mirror|in\s+front\s+of\s+(?:a\s+)?mirror",
            positive_scan,
            flags=re.I,
        ):
            return True
        return False

    @staticmethod
    def _append_photo_negative_terms(prompt_text: str, terms: list[str], *, limit: int = 1800) -> str:
        prompt = str(prompt_text or "").strip()
        if not prompt:
            return ""
        existing = prompt.lower()
        missing = [term for term in terms if term and term.lower() not in existing]
        if not missing:
            return _single_line(prompt, limit)
        suffix = ", ".join(missing)
        if re.search(r"negative prompt\s*:", prompt, flags=re.I):
            prompt = prompt.rstrip().rstrip(".")
            return _single_line(f"{prompt}, {suffix}.", limit)
        return _single_line(f"{prompt}. Negative prompt: {suffix}.", limit)

    def _sanitize_unrequested_mirror_selfie_prompt(
        self,
        prompt_text: str,
        *,
        context_text: str = "",
        limit: int = 1800,
    ) -> str:
        prompt = str(prompt_text or "").strip()
        if not prompt:
            return ""
        if self._photo_generation_explicit_mirror_request(context_text):
            return _single_line(prompt, limit)
        replacements = (
            (r"\bfull[-\s]?length\s+mirror\s+(?:selfie|shot|photo|portrait)\b", "natural upper-body to three-quarter portrait"),
            (r"\bfull[-\s]?body\s+mirror\s+(?:selfie|shot|photo|portrait)\b", "natural upper-body to three-quarter portrait"),
            (r"\bmirror\s+(?:selfie|shot|photo|portrait)\b", "handheld selfie or natural environmental portrait"),
            (r"\bstanding\s+in\s+front\s+of\s+(?:a\s+)?mirror\b", "standing naturally in the current location"),
            (r"\bdressing[-\s]?room\s+mirror\b", "current-location background"),
            (r"\bphone\s+covering\s+(?:the\s+)?face\b", "visible face"),
            (r"全身镜自拍|全身对镜|对镜自拍|镜前自拍|镜中自拍|穿衣镜|试衣镜", "自然半身或四分之三身随手拍"),
        )
        cleaned = prompt
        for pattern, replacement in replacements:
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.I)
        cleaned = re.sub(r"\s*,\s*,+", ", ", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;；")
        negative_terms = [
            "mirror selfie",
            "full-length mirror selfie",
            "full body mirror shot",
            "dressing room mirror",
            "phone covering face",
        ]
        return self._append_photo_negative_terms(cleaned or prompt, negative_terms, limit=limit)

    def _apply_photo_generation_selfie_composition_guard(self, prompt_text: str, workflow_kind: str) -> str:
        prompt = str(prompt_text or "").strip()
        normalized = str(workflow_kind or "").strip().lower()
        if normalized not in {"selfie", "portrait", "自拍", "人像"}:
            return _single_line(prompt, 1800)
        explicit_mirror = self._photo_generation_explicit_mirror_request(prompt)
        explicit_back_view = self._photo_generation_explicit_back_view_request(prompt)
        if explicit_back_view:
            guard = (
                "Back-view character composition guard: exactly one recognizable character wearing exactly one coherent outfit in one continuous scene; "
                "the explicitly requested back-view pose is allowed, preserve the reference hairstyle silhouette and stable appearance, "
                "and do not require the face to be visible."
            )
        elif explicit_mirror:
            guard = (
                "Selfie composition guard: exactly one character wearing exactly one coherent outfit in one continuous scene; "
                "a single mirror reflection of that same outfit is allowed, but do not create outfit alternatives, comparison panels, duplicated subjects, or a collage; "
                "keep the face visible and avoid the phone covering the face."
            )
        else:
            guard = (
                "Default selfie composition guard: exactly one character wearing exactly one coherent outfit in one continuous scene; "
                "no duplicated subject, outfit alternatives, comparison layout, split screen, side-by-side panels, diptych, collage, or character sheet; "
                "no mirror selfie or full-length mirror shot unless explicitly requested; keep the face visible, avoid phone covering face, "
                "use upper-body to three-quarter framing, and place the character naturally in the current scene."
            )
        merged = f"{prompt}\n\n{guard}".strip()
        negative_terms = [
            "duplicate character",
            "twins",
            "multiple people",
            "multiple outfits",
            "outfit comparison",
            "before and after",
            "split screen",
            "side-by-side panels",
            "diptych",
            "collage",
            "character sheet",
        ]
        if not explicit_mirror and not explicit_back_view:
            negative_terms.extend(
                ["mirror selfie", "full-length mirror selfie", "full body mirror shot", "dressing room mirror"]
            )
        if not explicit_back_view:
            negative_terms.append("phone covering face")
        return self._append_photo_negative_terms(
            merged,
            negative_terms,
            limit=1800,
        )

    def _apply_photo_generation_edit_guard(self, prompt_text: str, workflow_kind: str) -> str:
        prompt = str(prompt_text or "").strip()
        normalized = str(workflow_kind or "").strip().lower()
        if normalized not in {"edit", "改图", "修图", "重绘", "p图"}:
            return _single_line(prompt, 1800)
        guard = (
            "Image edit contract: use the user-provided image as the sole source canvas and visual identity reference. "
            "This is not a selfie or a new character portrait. Preserve every subject, face, body, outfit, pose, "
            "composition, camera angle, and background detail unless the user explicitly asks to change it. "
            "Never replace a person with the assistant persona, a configured persona reference, or today's outfit. "
            "Apply only the requested edit and keep all unrelated pixels and details as close to the source as possible."
        )
        return _single_line(f"{guard}\n\nEdit request and existing prompt: {prompt}".strip(), 1800)

    def _builtin_photo_generation_scene_presets(self) -> dict[str, str]:
        return {
            "角色自拍": (
                "natural casual character photo, single character, face visible by default, clear face, hair, expression, neck and shoulders, "
                "phone snapshot feeling, lifelike composition, no cropped head, no hidden face or back view unless explicitly requested, no body-only framing"
            ),
            "COS自拍": (
                "cosplay themed selfie, keep the character's own face, hair color, eye color, and key visual traits, "
                "clear costume theme, tasteful outfit, convention snapshot or room fitting photo feeling"
            ),
            "日常穿搭": (
                "daily outfit portrait without mirror, exactly one character wearing one coherent outfit in one continuous frame, "
                "no outfit comparison, no split screen, no side-by-side panels, handheld selfie or natural environmental portrait, "
                "upper-body to three-quarter framing, visible face, clear clothing layers and color palette, "
                "location-appropriate background, no phone covering face, not body-only"
            ),
            "居家睡衣": (
                "sleepwear or bedtime loungewear portrait matching the explicit clothing request and selected reference, "
                "exactly one coherent sleepwear outfit, preserve the character identity, natural home or bedtime context, "
                "do not restore a daytime outfit, coat, school uniform, or commuter layers unless explicitly requested"
            ),
            "居家服": (
                "comfortable homewear portrait, one coherent relaxed indoor outfit, natural home activity and lived-in setting, "
                "preserve the character identity and selected homewear reference, no commuter coat or formal layers unless requested"
            ),
            "校服人像": (
                "school-uniform portrait matching the explicit request, one coherent uniform with consistent layers and colors, "
                "natural school or campus context, preserve the character identity, not cosplay unless explicitly requested"
            ),
            "礼服人像": (
                "formalwear portrait matching the explicit request, one coherent formal outfit with consistent silhouette and materials, "
                "location-appropriate formal context, preserve the character identity, no casual or sportswear substitution"
            ),
            "泳装人像": (
                "swimwear portrait matching the explicit request, one coherent swim outfit, appropriate pool or beach context, "
                "preserve the character identity, tasteful natural composition, no unrelated daytime clothing layers"
            ),
            "运动服人像": (
                "sportswear portrait matching the explicit request, one coherent practical athletic outfit, natural activity setting, "
                "preserve the character identity, no formalwear or commuter outfit substitution"
            ),
            "镜前穿搭": (
                "explicitly requested mirror outfit photo, half-body to three-quarter mirror composition, "
                "clear clothes, jacket, accessories and color palette, complete visible face, no phone covering face, "
                "subject inside square safe area, not full-length body-only, not outfit-only, not clothing close-up"
            ),
            "头像特写": (
                "avatar-ready face close-up, clear hair, eyes and expression, clean background, centered face, enough margin, "
                "no text, no watermark, no cluttered props"
            ),
            "房间日常": (
                "indoor slice-of-life photo, natural desk objects, books, cup, window side or bedside details, "
                "one clear subject, calm lived-in atmosphere, avoid overcrowded composition"
            ),
            "可拍画面": (
                "casual photo shared with a close friend, concrete visual subject, natural lighting, not a vague landscape, "
                "not a weather report, when the request is scenery or an object frame it from the photographer's point of view, "
                "do not insert an unrequested person, character, visible photographer, or back-view figure, "
                "no private screen, no real personal information, no unrelated text, no watermark"
            ),
            "表情包场景": (
                "single sticker-like image for chat, clear emotion, simple composition, cute exaggerated expression, "
                "character remains recognizable, only include short text if the user explicitly requested it"
            ),
        }

    def _parse_photo_generation_scene_presets(self, raw: Any) -> dict[str, str]:
        presets: dict[str, str] = {}
        if isinstance(raw, dict):
            iterable = raw.items()
            for key, value in iterable:
                name = _single_line(key, 40)
                prompt = _single_line(value, 900)
                if name and prompt:
                    presets[name] = prompt
            return presets
        items: list[Any] = []
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, str):
            text = raw.replace("\r\n", "\n").replace("\r", "\n")
            items = [line for line in text.split("\n") if str(line or "").strip()]
        for item in items:
            if isinstance(item, dict):
                name = _single_line(item.get("name") or item.get("key") or item.get("title"), 40)
                prompt = _single_line(item.get("prompt") or item.get("value") or item.get("content"), 900)
            else:
                text = str(item or "").strip()
                if ":" in text:
                    name, prompt = text.split(":", 1)
                elif "：" in text:
                    name, prompt = text.split("：", 1)
                else:
                    continue
                name = _single_line(name, 40)
                prompt = _single_line(prompt, 900)
            if name and prompt:
                presets[name] = prompt
        return presets

    def _photo_generation_scene_presets(self) -> dict[str, str]:
        presets = self._builtin_photo_generation_scene_presets()
        presets.update(
            self._parse_photo_generation_scene_presets(
                runtime_persona_setting(self, "photo_generation_scene_presets", "")
            )
        )
        return presets

    def _apply_photo_generation_scene_presets(
        self,
        prompt_text: str,
        workflow_kind: str,
        *,
        preset_names: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        prompt = str(prompt_text or "").strip()
        presets = self._photo_generation_scene_presets()
        requested_names = preset_names or []
        names = [name for name in requested_names if name in presets][:1]
        if not names:
            return _single_line(prompt, 1800), []
        blocks = []
        for name in names:
            content = _single_line(presets.get(name), 900)
            if content and content not in prompt:
                blocks.append(f"{self._photo_generation_scene_preset_label_en(name)}: {content}")
        if not blocks:
            return _single_line(prompt, 1800), names
        merged = f"{prompt}\n\nScene preset: " + "; ".join(blocks)
        return _single_line(merged, 1800), names

    def _photo_generation_scene_preset_label_en(self, name: str) -> str:
        return {
            "角色自拍": "casual character selfie",
            "COS自拍": "cosplay selfie",
            "日常穿搭": "daily outfit portrait",
            "居家睡衣": "home sleepwear portrait",
            "居家服": "comfortable homewear portrait",
            "校服人像": "school uniform portrait",
            "礼服人像": "formalwear portrait",
            "泳装人像": "swimwear portrait",
            "运动服人像": "sportswear portrait",
            "镜前穿搭": "mirror outfit photo",
            "头像特写": "avatar close-up",
            "房间日常": "indoor slice-of-life",
            "可拍画面": "casual shareable photo",
            "表情包场景": "sticker scene",
        }.get(_single_line(name, 40), _single_line(name, 40) or "scene preset")

    async def _generate_photo_image(
        self,
        **kwargs: Any,
    ) -> tuple[str, str, str]:
        """Run image generation through the selected backend service."""
        nai_selected = getattr(self, "_nai_image_selected", None)
        if callable(nai_selected) and nai_selected(kwargs.get("workflow_kind", "")):
            nai_bridge = getattr(self, "_nai_image_generate", None)
            if callable(nai_bridge):
                return await nai_bridge(**kwargs)
            return (
                "NAI 生图",
                "",
                "生图后端已选择 NAI 直连，但未检测到 NAI 生图插件，请安装并启用 astrbot_plugin_nai_image。",
            )
        bridge = getattr(self, "_image_companion_generate", None)
        if callable(bridge):
            return await bridge(**kwargs)
        # Standalone mixin users (tests and third-party integrations) may call
        # this method without constructing ``PrivateCompanionPlugin``. The
        # production plugin always mixes in ImageCompanionBridgeMixin, so this
        # branch is compatibility-only and is never the host runtime path.
        legacy = getattr(self, "_generate_photo_image_legacy", None)
        if callable(legacy):
            return await legacy(**kwargs)
        return (
            "独立生图服务",
            "",
            "生图能力已拆分，请安装并启用“我会画给你看”插件 astrbot_plugin_image_companion。",
        )

    async def _generate_photo_image_legacy(self, **kwargs: Any) -> tuple[str, str, str]:
        """Compatibility alias backed by Image Companion's external runtime."""
        bridge = getattr(self, "_image_companion_generate", None)
        if callable(bridge):
            return await bridge(**kwargs)
        for module_name in (
            "data.plugins.astrbot_plugin_image_companion.image_runtime",
            "astrbot_plugin_image_companion.image_runtime",
        ):
            try:
                module = importlib.import_module(module_name)
                runtime_type = getattr(module, "ProactiveMessageMixin", None)
                executor = getattr(runtime_type, "_generate_photo_image_legacy", None)
                if callable(executor):
                    return await executor(self, **kwargs)
            except (ImportError, AttributeError):
                continue
        return (
            "独立生图服务",
            "",
            "生图能力已拆分，请安装并启用“我会画给你看”插件 astrbot_plugin_image_companion。",
        )

    async def _materialize_external_image_value(self, *args: Any, **kwargs: Any) -> tuple[str, str]:
        """Compatibility proxy for integrations that used the old private helper."""
        for module_name in (
            "data.plugins.astrbot_plugin_image_companion.image_runtime",
            "astrbot_plugin_image_companion.image_runtime",
        ):
            try:
                module = importlib.import_module(module_name)
                if "_EXTERNAL_IMAGE_MAX_BYTES" in globals():
                    setattr(module, "_EXTERNAL_IMAGE_MAX_BYTES", globals()["_EXTERNAL_IMAGE_MAX_BYTES"])
                executor = getattr(getattr(module, "ProactiveMessageMixin", None), "_materialize_external_image_value", None)
                if callable(executor):
                    return await executor(self, *args, **kwargs)
            except (ImportError, AttributeError):
                continue
        return "", "独立生图运行时不可用"
    async def _generate_photo_image_result(self, **kwargs: Any) -> PhotoGenerationResult:
        backend, image_path, note = await self._generate_photo_image(**kwargs)
        metadata: dict[str, Any] = {}
        for getter_name in ("_image_companion_last_metadata", "_nai_image_last_metadata"):
            getter = getattr(self, getter_name, None)
            if callable(getter):
                metadata = getter() or {}
                if metadata:
                    break
        if not metadata:
            metadata = self._photo_generation_result_metadata(
                image_path=image_path,
                session_key=_single_line(kwargs.get("session_key"), 340),
            )
        reference_path = _path_text(
            metadata.get("reference_path") or kwargs.get("reference_image_path"),
            1000,
        )
        intent_metadata = metadata.get("reference_intent") if isinstance(metadata.get("reference_intent"), dict) else {}
        plan_metadata = metadata.get("reference_plan") if isinstance(metadata.get("reference_plan"), dict) else {}
        fallback_metadata = metadata.get("reference_fallback") if isinstance(metadata.get("reference_fallback"), dict) else {}
        return PhotoGenerationResult(
            backend=_single_line(backend, 80),
            image_path=_path_text(image_path, 1000),
            note=_single_line(note, 500),
            trace_id=_single_line(metadata.get("trace"), 40),
            reference_selected_path=reference_path,
            reference_used=bool(metadata.get("reference_used")),
            reference_id=_single_line(metadata.get("reference_id"), 60),
            reference_kind=_single_line(metadata.get("reference_kind"), 40),
            reference_roles=tuple(
                _single_line(role, 40)
                for role in (metadata.get("reference_roles") or [])
                if _single_line(role, 40)
            ),
            wardrobe_mode=_single_line(metadata.get("wardrobe_mode"), 40),
            wardrobe_category=_single_line(metadata.get("wardrobe_category"), 40),
            outfit_locked=bool(metadata.get("outfit_locked")),
            daily_outfit_removed=bool(metadata.get("daily_outfit_removed")),
            preset_names=tuple(
                _single_line(name, 60)
                for name in (metadata.get("presets") or [])
                if _single_line(name, 60)
            )[:1],
            preset_hint=_single_line(metadata.get("preset_hint"), 80),
            preset_source=_single_line(metadata.get("preset_source"), 40),
            suggestion_status=_single_line(metadata.get("suggestion_status"), 60),
            prompt_hash=_single_line(metadata.get("prompt_hash"), 80),
            prompt_path=_path_text(metadata.get("prompt_path"), 1000),
            reference_requested_roles=tuple(
                _single_line(role, 40)
                for role in (intent_metadata.get("requested_roles") or [])
                if _single_line(role, 40)
            ),
            reference_excluded_roles=tuple(
                _single_line(role, 40)
                for role in (intent_metadata.get("excluded_roles") or [])
                if _single_line(role, 40)
            ),
            continuity_mode=_single_line(intent_metadata.get("continuity_mode"), 30) or "ambiguous",
            reference_confidence=_safe_float(intent_metadata.get("confidence"), 0.0, 0.0, 1.0),
            reference_plan=tuple(
                dict(binding)
                for binding in (plan_metadata.get("bindings") or [])
                if isinstance(binding, dict)
            ),
            reference_fulfilled_roles=tuple(
                _single_line(role, 40)
                for role in (fallback_metadata.get("fulfilled_roles") or [])
                if _single_line(role, 40)
            ),
            reference_missing_roles=tuple(
                _single_line(role, 40)
                for role in (fallback_metadata.get("missing_roles") or [])
                if _single_line(role, 40)
            ),
            reference_fallback_message=_single_line(fallback_metadata.get("message"), 260),
            generation_completed=bool(metadata.get("generation_completed")),
            failure_stage=_single_line(metadata.get("failure_stage"), 60),
        )

    async def _build_photo_scene_prompt(
        self, user: dict[str, Any], name: str, reason: str
    ) -> dict[str, Any]:
        prompt_format = self._photo_generation_prompt_format_mode()
        prompt_format_instruction = self._photo_generation_prompt_format_instruction()
        persona = self._get_default_persona_prompt()
        state = self.data.get("daily_state", {})
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        style_name, style_instruction = self._get_photo_style_instruction()
        style_prompt_en = self._photo_style_prompt_en(style_name, style_instruction)
        topic_hint = _single_line(user.get("planned_proactive_topic"), 60)
        motive_hint = _single_line(user.get("planned_proactive_motive"), 120)
        schedule_context = self._format_plan_item_for_prompt(current_item)
        pure_scene_context = "；".join(
            part for part in (topic_hint, motive_hint, schedule_context) if part
        )
        explicit_person_scene = bool(
            re.search(
                r"自拍|合影|合照|人像|人物|角色|穿搭|女孩|男孩|女生|男生|女人|男人|"
                r"少女|少年|路人|猫女|拟人|"
                r"\b(?:selfie|portrait|character|person|people|woman|man|girl|boy|outfit)\b",
                pure_scene_context,
                flags=re.I,
            )
        )
        explicit_pure_scene = reason == "birthday_celebration" or (
            bool(
                re.search(
                    r"风景|景色|风光|日落|晚霞|天空|海边风景|海边景色|"
                    r"食物|美食|早餐|午餐|晚餐|甜点|蛋糕|咖啡|饮料|"
                    r"动物|猫|狗|小猫|小狗|桌面(?:物品)?|物品|礼物|花束|花瓶|"
                    r"卡片|生日卡|手机屏幕|屏幕|"
                    r"\b(?:scenery|landscape|sunset|sky|seascape|food|breakfast|lunch|"
                    r"dinner|dessert|cake|coffee|drink|animal|cat|dog|tabletop|object|"
                    r"gift|bouquet|vase|card|birthday card|screen)\b",
                    pure_scene_context,
                    flags=re.I,
                )
            )
            and not explicit_person_scene
        )

        def scene_subject_flags(text: Any) -> tuple[bool, bool]:
            """Classify explicit person and pure-scene cues in scene text."""
            value = _single_line(text, 900)
            has_person = bool(
                re.search(
                    r"自拍|合影|合照|人像|人物|角色|穿搭|女孩|男孩|女生|男生|女人|男人|"
                    r"少女|少年|路人|猫女|拟人|"
                    r"\b(?:selfie|portrait|character|person|people|woman|man|girl|boy|outfit|human)\b",
                    value,
                    flags=re.I,
                )
            )
            has_scene = bool(
                re.search(
                    r"风景|景色|风光|日落|晚霞|天空|海边风景|海边景色|"
                    r"食物|美食|早餐|午餐|晚餐|甜点|蛋糕|咖啡|饮料|"
                    r"动物|猫|狗|小猫|小狗|桌面(?:物品)?|物品|礼物|花束|花瓶|"
                    r"卡片|生日卡|手机屏幕|屏幕|"
                    r"\b(?:scenery|landscape|sunset|sky|seascape|food|breakfast|lunch|"
                    r"dinner|dessert|cake|coffee|drink|soup|ramen|noodles|meal|bread|"
                    r"toast|sandwich|rice|fruit|animal|cat|dog|tabletop|object|"
                    r"gift|bouquet|vase|card|birthday card|screen)\b",
                    value,
                    flags=re.I,
                )
            )
            return has_person, has_scene
        delayed_scene = bool(self._deferred_immediate_share_tense_hint(user, "photo_text"))
        if delayed_scene:
            schedule_context = "本次画面对应较早的生活片段；日程只用于保持人物与场景连续，不可作为发送当下的事实依据。"
        scene_snapshot: dict[str, Any] = {}
        scene_context = ""
        snapshot_builder = getattr(self, "_build_companion_scene_snapshot", None)
        snapshot_formatter = getattr(self, "_format_companion_scene_snapshot", None)
        if callable(snapshot_builder) and callable(snapshot_formatter):
            try:
                scene_snapshot = snapshot_builder(user)
                scene_context = _single_line(
                    snapshot_formatter(
                        scene_snapshot,
                        purpose="proactive_photo",
                    ),
                    1200,
                )
                snapshot_schedule = scene_snapshot.get("schedule")
                if not delayed_scene and isinstance(snapshot_schedule, dict):
                    schedule_context = (
                        _single_line(snapshot_schedule.get("text"), 320)
                        or schedule_context
                    )
            except Exception as exc:
                scene_snapshot = {}
                scene_context = ""
                logger.debug(
                    "[PrivateCompanion] 主动照片读取统一情境快照失败，已回退旧路径: %s",
                    _single_line(exc, 160),
                )
        if not scene_context:
            scene_context = _single_line(
                "；".join(
                    part
                    for part in (
                        self._format_state_for_prompt(state if isinstance(state, dict) else {}),
                        schedule_context,
                    )
                    if part
                ),
                1200,
            )
        relationship_block = ""
        if runtime_persona_setting(self, "enable_bot_relationship_network", False):
            card_lines: list[str] = []
            for raw_card in self._normalize_bot_relationship_cards(
                runtime_persona_setting(self, "bot_relationship_cards", [])
            ):
                parts = [_single_line(part, 200) for part in raw_card.split(" || ", 2)]
                relation = parts[1] if len(parts) > 1 else ""
                appearance = parts[2] if len(parts) > 2 else ""
                card_lines.append(f"- 角色：{parts[0]}；与Bot的关系：{relation or '（未填写）'}；外貌描述：{appearance or '（未填写）'}")
            if card_lines:
                relationship_block = (
                    "【Bot 关系网】\n"
                    + "\n".join(card_lines)
                    + "\n使用方式：这些角色卡首先用于理解关系情境；角色卡文字不能替代人物参考图。只有当前请求明确点名角色/关系，或明确要求合影、合照、一起入镜时，"
                    "并且候选中确实选中了对应的角色参考图，才可让该角色按参考图自然入镜；没有匹配参考图时不要凭文字补画脸、身体、背影、剪影或倒影。"
                    "未明确要求角色出现时，仍不得让关系卡人物本人入镜，保持 Bot 单人或纯场景；在没有其他可验证人物参考时，禁止合影、合照、双人/多人同框。"
                    "可用第二只杯子、礼物、便签、空座位等非人物线索间接表达；不合适时忽略本节。\n\n"
                )
        prompt = f"""
请根据 AstrBot 默认人格和主动原因,生成一张要通过生图后端制作的“社交媒体随手拍/自拍/生活碎片图”提示词。

【人格】
{persona}

【收信人】
{name}

【当前统一情境快照】
{scene_context}
使用方式：这是当前事实和连续性参考。优先保持时间、地点、日程和情绪互相一致；今日穿搭只在本次没有新的服装请求时用于连续性。若话题、动机或画面需求明确要求睡衣、居家服、礼服、COS 等服装变化，以本次明确请求为准，不要被今日穿搭覆盖。它只帮助选择自然画面，不要求把所有字段都画出来或写进配文。

【这次想分享的画面钩子】
话题：{topic_hint or '（未指定）'}
那一刻的小动机：{motive_hint or '（未指定）'}

{relationship_block}【生日卡特殊规则】
{"如果主动原因是 birthday_celebration：制作一张没有文字、没有姓名、没有日期的温柔生日小卡。只选一个与人格和用户偏好相称的具体意象，不画蛋糕上文字、不出现年龄、不要节庆海报或营销风。" if reason == "birthday_celebration" else "（非生日卡）"}

【内容选择菜单】
{self._format_content_choice_options_for_prompt("photo_text")}

【生图风格】
{style_name}
风格要求：{style_instruction}

【提示词表达方式】
{prompt_format_instruction}

主动原因：{reason}

输出 JSON：
{{
  "kind": "selfie 或 text2img；自拍/人像用 selfie,其他随手拍用 text2img",
  "use_persona_reference": true,
  "prompt": "按上方提示词表达方式输出的英文生图提示词",
  "caption": "图片完成后可转述给最终私聊模型的一句话画面描述"
}}

要求：
1. 画面必须符合当前时间、日程和人格,不要把身份设定里没有的场景、职业、服装或外观细节写进去。日程是背景参考，不可单独当作动作已经发生的证明。
2. 图片不要总是天气或窗外。先从“内容选择菜单”里单选一个视觉锚点；当前日程、话题和人格只用于筛选主体和调整画面气质,不要把多个主体拼在一张图里。若本次来自延后候选，画面应与原话题连续，不应伪装成发送当下的新现场。
3. 可以是路上风景、桌面小物、随手自拍、偶遇小动物等,但不要每次都是自拍；没有明确自拍动机时优先 text2img。
4. `prompt` 必须使用英文，并严格遵守“提示词表达方式”；可以把必要中文专名作为 visual note 保留，但不要写任务说明或聊天口吻。
5. `prompt` 里要明确体现上面的风格要求。
6. 不要包含 NSFW、隐私信息、用户真实电脑画面。
7. 如果“话题”已经很具体,就优先把那个具体视觉主体画出来；如果话题很抽象,从菜单里另选一个适合拍照的具体画面。不要退回成泛泛的天气图、手部动作或普通记录照。
8. 不要默认生成全身镜/对镜自拍/手机挡脸自拍；只有话题、动机或当前日程明确出现“镜前/对镜/镜子/全身镜/mirror”时才允许。普通穿搭图用当前地点里的手持自拍、半身或四分之三身环境人像。
9. `use_persona_reference` 仅表示画面中是否出现 Bot 本人：自拍、人物生活照、人物穿搭图填 true；纯风景、食物、桌面物品、动物、手机屏幕或生日卡填 false。
10. 服装语义优先级为：本次明确服装需求优先；具体场景服装参考用于落实该需求；今日穿搭仅在没有新服装意图时作为连续性补充。不要同时写入彼此冲突的两套服装。
11. 只有当前请求明确要求关系角色出现/合影，且选中了对应的角色参考图时，才可让该角色按参考图自然入镜；否则禁止凭文字补画另一人的脸、身体、背影、剪影、倒影或肖像。未明确要求时，关系卡只影响情境，并用非人物生活线索间接表达关系。
""".strip()
        text = ""
        try:
            text = await self._llm_call(
                prompt,
                max_tokens=260,
                provider_id=self._task_provider(
                    _persona_provider_id(self, "PHOTO_PROMPT_PROVIDER_ID", "photo_prompt_provider_id", "creative"),
                    _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
                ),
                task="photo_prompt",
            )
        except Exception as exc:
            logger.debug(
                "[PrivateCompanion] proactive photo prompt model failed; using deterministic fallback: %s",
                _single_line(exc, 160),
            )
        payload = self._extract_json_payload(text or "")
        model_scene_valid = isinstance(payload, dict) and bool(
            _single_line(payload.get("prompt"), 600)
        )
        if model_scene_valid:
            kind = _single_line(payload.get("kind"), 20).lower()
            image_prompt = _single_line(payload.get("prompt"), 600)
            caption = _single_line(payload.get("caption"), 180)
            raw_use_reference = payload.get("use_persona_reference")
            if isinstance(raw_use_reference, bool):
                use_persona_reference = raw_use_reference
            elif str(raw_use_reference or "").strip().lower() in {"true", "1", "yes", "是", "使用"}:
                use_persona_reference = True
            elif str(raw_use_reference or "").strip().lower() in {"false", "0", "no", "否", "不使用"}:
                use_persona_reference = False
            else:
                use_persona_reference = not explicit_pure_scene
        else:
            kind = "text2img"
            image_prompt = ""
            caption = ""
            # When the scene model is unavailable, prefer a stable character photo
            # for ordinary proactive sharing instead of allowing an arbitrary face.
            use_persona_reference = not explicit_pure_scene
        model_person_scene, model_pure_scene = scene_subject_flags(
            f"{image_prompt} {caption}"
        )
        if model_pure_scene and not model_person_scene:
            explicit_pure_scene = True
            raw_model_reference = payload.get("use_persona_reference") if isinstance(payload, dict) else None
            explicit_model_reference = (
                isinstance(raw_model_reference, bool)
                or str(raw_model_reference or "").strip().lower()
                in {"true", "1", "yes", "是", "使用", "false", "0", "no", "否", "不使用"}
            )
            if not explicit_model_reference:
                use_persona_reference = False
        if kind not in {"selfie", "portrait", "自拍", "人像", "text2img", "scene", "photo", "风景"}:
            kind = "text2img"
        if kind in {"portrait", "自拍", "人像"}:
            kind = "selfie"
        if kind in {"scene", "photo", "风景"}:
            kind = "text2img"
        if kind == "selfie":
            use_persona_reference = True
        elif isinstance(payload, dict) and payload.get("use_persona_reference") is None:
            use_persona_reference = not explicit_pure_scene
        if not image_prompt:
            if topic_hint:
                image_prompt = (
                    f"Visual note: {topic_hint}; concrete visual subject kept faithful to this topic, "
                    f"natural everyday snapshot shared with a close friend, "
                    f"the moment is motivated by {motive_hint or 'a small moment worth sharing'}, "
                    f"{style_prompt_en}, clear composition, soft natural light"
                )
            else:
                image_prompt = (
                    f"Casual everyday snapshot with a concrete subject from the current context: "
                    f"{schedule_context or 'an ordinary daily moment'}, "
                    f"{motive_hint or 'a small moment worth sharing'}, "
                    f"{style_prompt_en}, clear composition, soft natural light"
                )
        if kind == "selfie":
            mirror_context = "；".join(
                part
                for part in (
                    f"reason={reason}",
                    f"topic={topic_hint}",
                    f"motive={motive_hint}",
                    f"schedule={schedule_context}",
                )
                if _single_line(part, 260)
            )
            image_prompt = self._sanitize_unrequested_mirror_selfie_prompt(
                image_prompt,
                context_text=mirror_context,
                limit=900,
            )
        if not caption:
            if topic_hint:
                caption = f"我把{topic_hint}这个小画面拍下来分享给你。"
            elif motive_hint:
                caption = f"刚好想把这个片刻拍下来给你看看：{motive_hint}。"
            else:
                caption = "今天看到一个很适合拍下来分享的小画面。"
        if use_persona_reference:
            subject_owner = "bot"
        else:
            character_text = f"{image_prompt} {caption}"
            subject_owner = (
                "third_party"
                if re.search(r"\b(?:person|people|man|woman|boy|girl|character|human)\b", character_text, flags=re.I)
                or any(token in character_text for token in ("人物", "男人", "女人", "男生", "女生", "男孩", "女孩", "路人"))
                else "scene"
            )
        if not use_persona_reference and subject_owner == "scene":
            image_prompt = _single_line(
                f"{image_prompt}; do not insert an unrequested person, character, visible photographer, "
                "back-view figure, face, body, silhouette, or reflection",
                900,
            )
        return {
            "kind": kind,
            "prompt": image_prompt,
            "caption": caption,
            "use_persona_reference": use_persona_reference,
            "subject_owner": subject_owner,
            "scene_context": scene_context,
            "prompt_format": prompt_format,
        }

    def _get_photo_style_instruction(self) -> tuple[str, str]:
        style = str(runtime_persona_setting(self, "photo_generation_style", "真实") or "真实").strip()
        if style == "二次元":
            return "二次元", "日系二次元插画风,人物与场景干净细腻,保留生活感,不要写实摄影质感"
        if style == "其他":
            custom = _single_line(
                runtime_persona_setting(self, "photo_generation_style_custom_prompt", ""),
                200,
            )
            if custom:
                return "其他", custom
            return "其他", "保持统一审美风格,自然生活感,避免默认写实照片风格"
        return "真实", "真实摄影风格,像手机随手拍到的生活照片,光线自然,细节可信"

    def _q5_structured_reference_assets_enabled(self) -> bool:
        return bool(runtime_persona_setting(self, "enable_p5_structured_reference_assets", False))

    def _q5_structured_reference_generation_mode(
        self,
        workflow_kind: str,
        prompt_text: str,
        reference_candidate: dict[str, Any],
    ) -> str:
        kind = _single_line(workflow_kind, 40).lower()
        if kind in {"edit", "改图", "修图", "重绘", "p图"}:
            return "edit"
        if _single_line(reference_candidate.get("kind"), 40).lower() == "recent_sent_photo" or re.search(
            r"续拍|继续拍|接着拍|再来一张|换个姿势|换个表情|same scene|continue the photo",
            str(prompt_text or ""),
            flags=re.I,
        ):
            return "continuation"
        return "new_topic"

    def _q5_prepare_structured_reference_plan(
        self,
        *,
        generation_id: str,
        workflow_kind: str,
        prompt_text: str,
        reference_candidate: dict[str, Any],
        explicit_reference_supplied: bool,
    ) -> tuple[ReferenceAssetGate | None, ReferenceAssetPlan | None, str]:
        if not self._q5_structured_reference_assets_enabled():
            return None, None, "disabled"
        # User-provided and quoted paths remain legacy single-image flows. They
        # can never be promoted into the managed multi-image sink.
        if explicit_reference_supplied:
            return None, None, "legacy_explicit_reference"
        gate = ReferenceAssetGate(getattr(self, "data_dir", ""))
        mode = self._q5_structured_reference_generation_mode(
            workflow_kind,
            prompt_text,
            reference_candidate,
        )
        plan, status = gate.plan(
            runtime_persona_setting(self, "photo_structured_reference_assets", []),
            generation_id=generation_id,
            mode=mode,
        )
        if not plan:
            logger.info(
                "[PrivateCompanion] Q5 受管参考素材未进入图片输入汇: trace=%s status=%s",
                _single_line(generation_id, 80),
                status,
            )
            return gate, None, status
        return gate, plan, "ok"

    @staticmethod
    def _q5_managed_reference_candidate(plan: ReferenceAssetPlan) -> dict[str, Any]:
        primary = plan.primary_asset
        if primary is None:
            return {}
        return {
            "id": primary.asset_id,
            "kind": "managed_asset",
            "source": "q5_reference_asset_gate",
            "note": "管理员登记并校验的受管身份参考素材",
            "reference_roles": [item.role for item in plan.assets],
            "outfit_lock_default": any(
                item.role == "outfit" and item.outfit_lock_default
                for item in plan.assets
            ),
            "metadata_source": "q5_reference_asset_gate",
        }

    def _photo_persona_reference_image_path(self) -> str:
        catalog = runtime_persona_setting(self, "photo_reference_catalog", None)
        if catalog is None:
            raw = _path_text(
                runtime_persona_setting(self, "photo_persona_reference_image_path", ""),
                1000,
            )
        else:
            persona = next(
                (
                    item
                    for item in (catalog or ())
                    if isinstance(item, PhotoReference) and item.kind == "persona"
                ),
                None,
            )
            raw = _path_text(persona.source if persona is not None else "", 1000)
        if not raw:
            return ""
        raw = raw.strip().strip('"').strip("'")
        if re.match(r"^https?://", raw, flags=re.I):
            return ""
        candidates = [Path(raw).expanduser()]
        if not candidates[0].is_absolute():
            candidates.append(Path(self.data_dir) / raw)
        for candidate in candidates:
            try:
                path = candidate.resolve()
            except Exception:
                path = candidate
            if not path.exists() or not path.is_file():
                continue
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            return str(path)
        return ""

    @staticmethod
    def _photo_reference_normalize_roles(value: Any) -> list[str]:
        if isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = re.split(r"[,，、/|\s]+", str(value or ""))
        aliases = {
            "identity": "identity",
            "persona": "identity",
            "face": "identity",
            "人设": "identity",
            "身份": "identity",
            "人物": "identity",
            "脸": "identity",
            "outfit": "outfit",
            "wardrobe": "outfit",
            "clothing": "outfit",
            "服装": "outfit",
            "穿搭": "outfit",
            "pose": "pose",
            "姿势": "pose",
            "scene": "scene",
            "background": "scene",
            "场景": "scene",
            "背景": "scene",
            "style": "style",
            "画风": "style",
            "风格": "style",
            "continuity": "continuity",
            "连续性": "continuity",
            "source": "source",
            "原图": "source",
        }
        roles: list[str] = []
        for item in raw_items:
            key = str(item or "").strip().lower()
            normalized = aliases.get(key, "")
            if normalized and normalized not in roles:
                roles.append(normalized)
        return roles

    @staticmethod
    def _photo_outfit_category_matches(value: Any) -> list[tuple[str, int, int, str]]:
        text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
        if not text:
            return []
        patterns = (
            ("cosplay", r"(?<![a-z0-9])cos(?:play)?(?![a-z0-9])|角色扮演|扮成|女仆装|巫女服|魔法少女|表演服"),
            ("school_uniform", r"校服|学院制服|学生制服|school[\s_-]*uniform"),
            ("sleepwear", r"睡衣|睡裙|睡袍|睡眠服|nightgown|nightdress|pajama|pyjama|sleepwear|bedtime outfit"),
            ("swimwear", r"泳装|泳衣|比基尼|swimsuit|swimwear|bikini"),
            ("sportswear", r"运动服|健身服|瑜伽服|球衣|sportswear|activewear|gym wear|jersey"),
            ("formalwear", r"礼服|晚礼服|正装|燕尾服|西装|tuxedo|formalwear|formal attire|evening gown|\bsuit\b"),
            ("homewear", r"居家服|家居服|家常服|宅家服|homewear|loungewear"),
            ("daily_outfit", r"今日穿搭|当天基础穿搭|当天穿搭|日常穿搭|today'?s outfit|daily outfit"),
        )
        matches: list[tuple[str, int, int, str]] = []
        for category, pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.I):
                resolved_category = category
                if category == "homewear" and match.group(0).lower() == "loungewear":
                    context = text[max(0, match.start() - 40) : match.end() + 40]
                    if "bedtime" in context:
                        resolved_category = "sleepwear"
                matches.append((resolved_category, match.start(), match.end(), match.group(0)))
        matches.sort(key=lambda item: (item[1], item[2]))
        return matches

    @classmethod
    def _photo_outfit_category_from_text(cls, value: Any) -> str:
        matches = cls._photo_outfit_category_matches(value)
        return matches[0][0] if matches else ""

    @staticmethod
    def _photo_reference_scene_categories_from_text(value: Any) -> list[str]:
        text = re.sub(r"\s+", "", str(value or "")).lower()
        categories: list[str] = []
        mappings = (
            ("home", ("在家", "家里", "居家", "宅家", "home")),
            ("bedroom", ("卧室", "床边", "睡前", "刚起床", "bedroom", "bedtime")),
            ("school", ("上学", "校园", "教室", "校门", "school", "campus")),
            ("office", ("上班", "公司", "办公室", "office", "workplace")),
            ("outdoor", ("外出", "通勤", "逛街", "街头", "旅行", "outdoor", "commute")),
            ("formal_event", ("宴会", "舞会", "典礼", "正式场合", "banquet", "ceremony")),
            ("sport", ("运动", "健身", "跑步", "瑜伽", "球场", "gym", "sport")),
            ("beach", ("海边", "沙滩", "泳池", "beach", "pool")),
        )
        for category, tokens in mappings:
            if any(token in text for token in tokens):
                categories.append(category)
        return categories

    @staticmethod
    def _photo_reference_preset_for_category(category: str) -> str:
        return {
            "sleepwear": "居家睡衣",
            "homewear": "居家服",
            "cosplay": "COS自拍",
            "school_uniform": "校服人像",
            "formalwear": "礼服人像",
            "swimwear": "泳装人像",
            "sportswear": "运动服人像",
            "daily_outfit": "日常穿搭",
            "custom_outfit": "日常穿搭",
        }.get(str(category or "").strip().lower(), "")

    @staticmethod
    def _photo_reference_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None or str(value).strip() == "":
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on", "是", "开启", "锁定"}

    def _normalize_photo_reference_candidate_metadata(self, item: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(item or {})
        note = _single_line(normalized.get("note") or normalized.get("description"), 700)
        kind = _single_line(normalized.get("kind"), 40).lower() or "library"
        explicit_roles = normalized.get("reference_roles", normalized.get("reference_role"))
        roles = self._photo_reference_normalize_roles(explicit_roles)
        raw_category = normalized.get("outfit_category") or normalized.get("wardrobe_category")
        if not raw_category and isinstance(normalized.get("wardrobe_categories"), (list, tuple)):
            raw_category = next(iter(normalized.get("wardrobe_categories") or []), "")
        category = _single_line(raw_category, 40).lower()
        if not category:
            category = self._photo_outfit_category_from_text(note)
        if not roles:
            if kind == "persona":
                roles = ["identity"]
            elif kind in {"daily_outfit", "recent_sent_photo"}:
                roles = ["identity", "outfit"]
                if kind == "recent_sent_photo":
                    roles.extend(["scene", "continuity"])
            elif re.search(r"仅(?:用于)?(?:人设|身份|脸|发型)|只(?:参考|用于)(?:人设|身份|脸|发型)|identity only", note, flags=re.I):
                roles = ["identity"]
            elif category:
                roles = ["identity", "outfit"]
            else:
                roles = ["identity"]
        if kind == "daily_outfit" and not category:
            category = "daily_outfit"
        scene_values = normalized.get("scene_categories", normalized.get("scene_tags"))
        if isinstance(scene_values, (list, tuple, set)):
            scene_categories = [
                _single_line(value, 40).lower()
                for value in scene_values
                if _single_line(value, 40)
            ]
        else:
            scene_categories = self._photo_reference_scene_categories_from_text(scene_values or note)
        time_values = normalized.get("time_categories", normalized.get("time_tags"))
        if isinstance(time_values, (list, tuple, set)):
            time_categories = [
                _single_line(value, 40).lower()
                for value in time_values
                if _single_line(value, 40)
            ]
        else:
            time_categories = [
                _single_line(value, 40).lower()
                for value in re.split(r"[,，、/|\s]+", str(time_values or ""))
                if _single_line(value, 40)
            ]
        lock_default = self._photo_reference_bool(
            normalized.get("outfit_lock_default"),
            default=bool("outfit" in roles and (category or kind in {"daily_outfit", "recent_sent_photo"})),
        )
        preferred_preset = _single_line(
            normalized.get("preferred_preset") or normalized.get("preset"),
            60,
        ) or self._photo_reference_preset_for_category(category)
        normalized.update(
            {
                "kind": kind,
                "note": note,
                "reference_roles": list(dict.fromkeys(roles)),
                "outfit_category": category,
                "outfit_lock_default": lock_default,
                "scene_categories": list(dict.fromkeys(scene_categories)),
                "time_categories": list(dict.fromkeys(time_categories)),
                "preferred_preset": preferred_preset,
                "metadata_source": _single_line(normalized.get("metadata_source"), 30)
                or ("configured" if explicit_roles is not None or normalized.get("outfit_category") else "inferred_note"),
            }
        )
        return normalized

    def _photo_reference_library_entries(self) -> list[dict[str, Any]]:
        if runtime_persona_setting(self, "photo_reference_catalog", None) is None:
            loaded = load_catalog(
                [],
                catalog_version=0,
                legacy_library=runtime_persona_setting(self, "photo_reference_library", []),
                preset_names=self._photo_generation_scene_presets().keys(),
            )
            entries = [project_reference_candidate(item) for item in loaded.references if item.kind == "library"]
            for index, entry in enumerate(entries):
                raw_items = runtime_persona_setting(self, "photo_reference_library", []) or []
                raw_item = raw_items[index] if isinstance(raw_items, list) and index < len(raw_items) else None
                entry["_config_format"] = "dict" if isinstance(raw_item, dict) else "text"
            return entries
        return [
            project_reference_candidate(item)
            for item in (runtime_persona_setting(self, "photo_reference_catalog", ()) or ())
            if isinstance(item, PhotoReference) and item.kind == "library"
        ]

    def _photo_reference_local_path(self, source: str) -> str:
        raw = _path_text(source, 1000)
        if not raw or re.match(r"^https?://", raw, flags=re.I):
            return ""
        candidates = [Path(raw).expanduser()]
        if not candidates[0].is_absolute():
            candidates.append(Path(self.data_dir) / raw)
        for candidate in candidates:
            try:
                path = candidate.resolve()
                if path.exists() and path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                    return str(path)
            except (OSError, ValueError):
                continue
        return ""

    def _daily_outfit_reference_image_path(self) -> str:
        item = self.data.get("daily_outfit_photo") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(item, dict):
            return ""
        if _single_line(item.get("date"), 20) != _today_key():
            return ""
        raw = _path_text(item.get("path"), 1000)
        if not raw:
            return ""
        try:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = Path(self.data_dir) / raw
            path = path.resolve()
        except Exception:
            path = Path(raw)
        try:
            if not path.exists() or not path.is_file():
                return ""
        except (OSError, ValueError):
            return ""
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            return ""
        return str(path)

    def _photo_persona_reference_image_for_kind(self, workflow_kind: str, *, allow_daily_outfit: bool = True) -> str:
        if not bool(runtime_persona_setting(self, "enable_photo_reference_image", False)):
            return ""
        if str(workflow_kind or "").strip().lower() not in {"selfie", "portrait", "自拍", "人像"}:
            return ""
        if allow_daily_outfit:
            outfit_path = self._daily_outfit_reference_image_path()
            if outfit_path:
                return outfit_path
        return self._photo_persona_reference_image_path()

    async def _photo_persona_reference_image_for_kind_async(
        self,
        workflow_kind: str,
        *,
        allow_daily_outfit: bool = True,
        requester_user_id: str = "",
        request_text: str = "",
        ambient_context: str = "",
        selection_context: str = "",
        suggested_scene_preset: str = "",
        continuity_key: str = "",
    ) -> str:
        if not bool(getattr(self, "enable_photo_reference_image", False)):
            return ""
        if str(workflow_kind or "").strip().lower() not in {
            "selfie",
            "portrait",
            "自拍",
            "人像",
        }:
            return ""
        selector = getattr(self, "_select_photo_reference_candidate_async", None)
        if not callable(selector):
            return ""
        try:
            selected = await selector(
                workflow_kind,
                allow_daily_outfit=allow_daily_outfit,
                requester_user_id=requester_user_id,
                request_text=request_text,
                ambient_context=ambient_context,
                selection_context=selection_context,
                suggested_scene_preset=suggested_scene_preset,
                continuity_key=continuity_key,
            )
        except TypeError:
            selected = await selector(
                workflow_kind,
                allow_daily_outfit=allow_daily_outfit,
                request_text=request_text,
                ambient_context=ambient_context,
                selection_context=selection_context,
            )
        if isinstance(selected, SelectionResult):
            selected = selected.selected
        return _path_text(selected.get("path") if isinstance(selected, dict) else "", 1000)

    async def _photo_persona_reference_image_path_async(self) -> str:
        if not bool(runtime_persona_setting(self, "enable_photo_reference_image", False)):
            return ""
        local_path = self._photo_persona_reference_image_path()
        if local_path:
            return local_path
        catalog = runtime_persona_setting(self, "photo_reference_catalog", None)
        if catalog is None:
            raw = _path_text(
                runtime_persona_setting(self, "photo_persona_reference_image_path", ""),
                1000,
            )
        else:
            persona = next(
                (
                    item
                    for item in (catalog or ())
                    if isinstance(item, PhotoReference) and item.kind == "persona"
                ),
                None,
            )
            raw = _path_text(persona.source if persona is not None else "", 1000)
        if not raw or not re.match(r"^https?://", raw, flags=re.I):
            return ""
        resolver = getattr(self, "_photo_reference_source_to_stable_path", None)
        if not callable(resolver):
            return ""
        try:
            stable_path = await resolver(raw, stem="config_url_reference")
        except Exception as exc:
            logger.info("[PrivateCompanion] 配置页人设参考图 URL 下载失败: %s url=%s", _single_line(exc, 120), _single_line(raw, 120))
            return ""
        if not stable_path:
            logger.info("[PrivateCompanion] 配置页人设参考图 URL 未能转为本地参考图: url=%s", _single_line(raw, 120))
            return ""
        setter = getattr(self, "_set_photo_reference_config_path", None)
        if callable(setter):
            try:
                result = setter(stable_path)
                if hasattr(result, "__await__"):
                    result = await result
                if result is False:
                    logger.info(
                        "[PrivateCompanion] 配置页人设参考图 URL 已下载但配置保存返回失败: path=%s",
                        _single_line(stable_path, 160),
                    )
            except Exception as exc:
                logger.info("[PrivateCompanion] 配置页人设参考图 URL 已下载但回写失败: %s path=%s", _single_line(exc, 120), _single_line(stable_path, 160))
        logger.info("[PrivateCompanion] 配置页人设参考图 URL 已缓存为本地文件: path=%s", _single_line(stable_path, 160))
        return stable_path

    def _photo_reference_config_value(self, item: dict[str, Any], source: str = "") -> Any:
        persisted_source = _path_text(source or item.get("source"), 1000)
        note = _single_line(item.get("note"), 500)
        if item.get("_config_format") != "dict":
            return f"{persisted_source} || {note}" if note else persisted_source
        return {
            "path": persisted_source,
            "note": note,
            "reference_roles": list(item.get("reference_roles") or []),
            "outfit_category": _single_line(item.get("outfit_category"), 40),
            "outfit_lock_default": bool(item.get("outfit_lock_default")),
            "scene_categories": list(item.get("scene_categories") or []),
            "preferred_preset": _single_line(item.get("preferred_preset"), 60),
        }

    def _photo_reference_asset_records(self) -> list[dict[str, Any]]:
        raw = self.data.get("photo_reference_assets") if isinstance(getattr(self, "data", None), dict) else []
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw if isinstance(raw, list) else []:
            normalized = normalize_reference_asset(item)
            if not normalized or normalized["id"] in seen:
                continue
            seen.add(normalized["id"])
            records.append(normalized)
        return records

    def _photo_reference_asset_path(self, asset: dict[str, Any]) -> str:
        source = _path_text(asset.get("path") or asset.get("source"), 1200)
        if not source:
            return ""
        resolver = getattr(self, "_photo_reference_local_path", None)
        if callable(resolver):
            try:
                resolved = _path_text(resolver(source), 1200)
                if resolved:
                    return resolved
            except Exception:
                pass
        try:
            path = Path(source).expanduser().resolve()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                return str(path)
        except (OSError, ValueError):
            pass
        return ""

    def _photo_reference_relation_owner_ids(self, requester_user_id: str, request_text: str) -> set[str]:
        owners: set[str] = set()
        raw_requester = _single_line(requester_user_id, 80)
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        if raw_requester:
            owners.add(raw_requester)
            if callable(canonicalizer):
                try:
                    canonical = _single_line(canonicalizer(raw_requester), 80)
                    if canonical:
                        owners.add(canonical)
                except Exception:
                    pass
        profiles = self.data.get("worldbook_member_profiles") if isinstance(getattr(self, "data", None), dict) else {}
        text = re.sub(r"\s+", "", str(request_text or "")).lower()
        if not isinstance(profiles, dict) or not text:
            return owners
        for profile_id, profile in profiles.items():
            if not isinstance(profile, dict) or profile.get("enabled", True) is False:
                continue
            tokens = [profile_id, profile.get("name"), *(profile.get("aliases") or []), *(profile.get("observed_names") or [])]
            if any(len(re.sub(r"\s+", "", str(token or ""))) >= 2 and re.sub(r"\s+", "", str(token or "")).lower() in text for token in tokens):
                owners.add(str(profile_id))
        return owners

    def _photo_reference_relation_asset_candidates(self, *, requester_user_id: str, request_text: str) -> list[dict[str, Any]]:
        owners = self._photo_reference_relation_owner_ids(requester_user_id, request_text)
        if not owners:
            return []
        candidates: list[dict[str, Any]] = []
        for asset in self._photo_reference_asset_records():
            if asset.get("scope") != "relation_user" or asset.get("owner_id") not in owners or asset.get("enabled") is False:
                continue
            path = self._photo_reference_asset_path(asset)
            if not path:
                continue
            roles = list(asset.get("reference_roles") or ("identity",))
            candidates.append({
                "id": asset.get("id"),
                "kind": "relation_user",
                "scope": "relation_user",
                "owner_id": asset.get("owner_id"),
                "path": path,
                "source": asset.get("path"),
                "title": asset.get("title"),
                "note": asset.get("note"),
                "tags": list(asset.get("tags") or []),
                "reference_roles": roles,
                "available_reference_roles": roles,
                "priority": max(650, _safe_int(asset.get("priority"), 0, -1000)),
                "metadata_source": "relation_user",
            })
        return candidates

    def _photo_reference_role_asset_candidates(self, *, request_text: str) -> list[dict[str, Any]]:
        """Resolve setting/relationship-card references only for an explicit role context.

        Role cards describe people other than Bot.  Loading their images for every
        selfie would make an otherwise single-person request ambiguous, so the
        asset is eligible only when the current request names the role/name or
        clearly asks for a group frame.
        """
        if not bool(runtime_persona_setting(self, "enable_bot_relationship_network", False)):
            return []
        cards = self._normalize_bot_relationship_cards(
            runtime_persona_setting(self, "bot_relationship_cards", [])
        )
        if not cards:
            return []
        request_compact = re.sub(r"\s+", "", str(request_text or "")).casefold()
        group_requested = _photo_group_request_matches(request_text)
        role_context: dict[str, dict[str, str]] = {}
        for raw_card in cards:
            parts = [_single_line(part, 200) for part in raw_card.split(" || ", 2)]
            role_name = parts[0] if parts else ""
            if not role_name:
                continue
            owner_id = normalize_reference_owner_id("relation_role", role_name)
            if not owner_id:
                continue
            relation = parts[1] if len(parts) > 1 else ""
            tokens = [role_name, relation]
            explicit_hit = any(
                len(re.sub(r"\s+", "", str(token or ""))) >= 2
                and re.sub(r"\s+", "", str(token or "")).casefold() in request_compact
                for token in tokens
            )
            if explicit_hit or group_requested:
                role_context[owner_id] = {
                    "role_name": role_name,
                    "relationship": relation,
                    "appearance": parts[2] if len(parts) > 2 else "",
                    "explicit_mention": "1" if explicit_hit else "0",
                }
        if not role_context:
            return []
        candidates: list[dict[str, Any]] = []
        for asset in self._photo_reference_asset_records():
            if asset.get("scope") != "relation_role" or asset.get("enabled") is False:
                continue
            owner_id = str(asset.get("owner_id") or "")
            context = role_context.get(owner_id)
            if not context:
                continue
            path = self._photo_reference_asset_path(asset)
            if not path:
                continue
            roles = list(asset.get("reference_roles") or ("identity",))
            candidates.append(
                {
                    "id": asset.get("id"),
                    "kind": "relation_role",
                    "scope": "relation_role",
                    "owner_id": owner_id,
                    "path": path,
                    "source": asset.get("path"),
                    "title": asset.get("title"),
                    "note": asset.get("note"),
                    "tags": list(asset.get("tags") or []),
                    "reference_roles": roles,
                    "available_reference_roles": roles,
                    "priority": max(700, _safe_int(asset.get("priority"), 0, -1000)),
                    "metadata_source": "relation_role",
                    "role_name": context["role_name"],
                    "relationship": context["relationship"],
                    "role_appearance": context["appearance"],
                    "role_explicit_mention": context["explicit_mention"] == "1",
                    "group_photo_requested": group_requested,
                }
            )
        return candidates

    def _photo_reference_knowledge_asset_candidates(self, *, request_text: str, ambient_context: str) -> list[dict[str, Any]]:
        selected = {
            str(item or "").strip()
            for item in (runtime_persona_setting(self, "roleplay_knowledge_source_ids", None) or [])
            if str(item or "").strip().startswith(("kb:", "doc:"))
        }
        if not selected:
            return []
        combined = re.sub(r"\s+", "", f"{request_text}\n{ambient_context}").lower()
        candidates: list[dict[str, Any]] = []
        for asset in self._photo_reference_asset_records():
            if asset.get("scope") != "knowledge" or asset.get("enabled") is False:
                continue
            owner = str(asset.get("owner_id") or "")
            if owner.startswith("doc:"):
                parts = owner.split(":", 2)
                if owner not in selected and (len(parts) < 3 or f"kb:{parts[1]}" not in selected):
                    continue
            elif owner not in selected:
                continue
            tokens = reference_asset_tokens(asset)
            if not tokens or not any(token in combined for token in tokens):
                continue
            path = self._photo_reference_asset_path(asset)
            if not path:
                continue
            roles = list(asset.get("reference_roles") or ("scene", "style"))
            candidates.append({
                "id": asset.get("id"),
                "kind": "knowledge_reference",
                "scope": "knowledge",
                "owner_id": owner,
                "path": path,
                "source": asset.get("path"),
                "title": asset.get("title"),
                "note": asset.get("note"),
                "tags": list(asset.get("tags") or []),
                "reference_roles": roles,
                "available_reference_roles": roles,
                "priority": max(520, _safe_int(asset.get("priority"), 0, -1000)),
                "metadata_source": "knowledge_reference",
                "knowledge_context_match": True,
            })
        return candidates

    async def _photo_reference_candidates_async(
        self,
        *,
        allow_daily_outfit: bool = True,
        requester_user_id: str = "",
        request_text: str = "",
        ambient_context: str = "",
        scoped_only: bool = False,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        canonical_mode = runtime_persona_setting(self, "photo_reference_catalog", None) is not None
        if canonical_mode:
            catalog = tuple(runtime_persona_setting(self, "photo_reference_catalog", ()) or ())
        else:
            catalog = load_catalog(
                [],
                catalog_version=0,
                legacy_persona=runtime_persona_setting(
                    self, "photo_persona_reference_image_path", ""
                ),
                legacy_library=runtime_persona_setting(self, "photo_reference_library", []),
                preset_names=self._photo_generation_scene_presets().keys(),
            ).references
        updated_catalog = list(catalog)
        catalog_changed = False
        resolver = getattr(self, "_photo_reference_source_to_stable_path", None)
        for index, item in enumerate(catalog):
            if not isinstance(item, PhotoReference) or item.kind != "library":
                continue
            source = item.source
            path = self._photo_reference_local_path(source)
            if not path and re.match(r"^https?://", source, flags=re.I) and callable(resolver):
                try:
                    path = await resolver(source, stem=item.id)
                except Exception as exc:
                    logger.info(
                        "[PrivateCompanion] 参考图库远程图片下载失败: item=%s error=%s",
                        item.id,
                        _single_line(exc, 120),
                    )
                if path:
                    updated_catalog[index] = replace(item, source=path)
                    catalog_changed = True
            if path:
                candidates.append(project_reference_candidate(item, resolved_source=path))
        if catalog_changed:
            setter = getattr(
                self,
                "_set_photo_reference_catalog_config" if canonical_mode else "_set_photo_reference_library_config",
                None,
            )
            if callable(setter):
                try:
                    payload: Any = updated_catalog
                    if not canonical_mode:
                        payload = [
                            {
                                "path": item.source,
                                "note": item.note,
                                "reference_roles": list(item.reference_roles),
                                "outfit_category": item.outfit_category,
                                "outfit_lock_default": item.outfit_lock_default,
                                "scene_categories": list(item.scene_categories),
                                "preferred_preset": item.preferred_preset,
                            }
                            for item in updated_catalog
                            if isinstance(item, PhotoReference) and item.kind == "library"
                        ]
                    result = setter(payload)
                    if hasattr(result, "__await__"):
                        result = await result
                    if result is False:
                        logger.info("[PrivateCompanion] 参考图库远程图片已下载但配置保存返回失败")
                except Exception as exc:
                    logger.info(
                        "[PrivateCompanion] 参考图库远程图片已下载但回写失败: %s",
                        _single_line(exc, 120),
                    )

        if allow_daily_outfit:
            outfit_path = self._daily_outfit_reference_image_path()
            if outfit_path:
                daily_reference = build_daily_outfit_reference(
                    outfit_path,
                    note="今天生成的外出穿搭；仅在画面明确承接今天外出、通勤、上学、逛街或展示当日穿搭时使用，在家、卧室、睡前、刚起床等场景不要使用",
                    preset_names=self._photo_generation_scene_presets().keys(),
                )
                candidates.append(project_reference_candidate(daily_reference, resolved_source=outfit_path))
        persona_path = await self._photo_persona_reference_image_path_async()
        if persona_path and not any(item.get("kind") == "persona" for item in candidates):
            persona = next(
                (
                    item
                    for item in catalog
                    if isinstance(item, PhotoReference) and item.kind == "persona"
                ),
                None,
            )
            if persona is not None:
                candidates.append(project_reference_candidate(persona, resolved_source=persona_path))
            else:
                candidates.append(
                    {
                        "id": "persona",
                        "kind": "persona",
                        "path": persona_path,
                        "source": persona_path,
                        "note": "Bot persona identity reference",
                        "reference_roles": ["identity"],
                        "available_reference_roles": ["identity"],
                        "priority": 400,
                        "metadata_source": "legacy_persona",
                        "outfit_lock_default": False,
                    }
                )
        candidates.extend(
            self._photo_reference_role_asset_candidates(
                request_text=request_text,
            )
        )
        candidates.extend(
            self._photo_reference_relation_asset_candidates(
                requester_user_id=requester_user_id,
                request_text=request_text,
            )
        )
        candidates.extend(
            self._photo_reference_knowledge_asset_candidates(
                request_text=request_text,
                ambient_context=ambient_context,
            )
        )
        if scoped_only:
            candidates = [
                item
                for item in candidates
                if item.get("kind") in {"relation_user", "relation_role", "knowledge_reference"}
            ]
        return candidates

    @staticmethod
    def _photo_reference_candidate_score(
        candidate: dict[str, Any],
        request_text: str,
        ambient_context: str,
        *,
        schedule_history_context: str = "",
        wardrobe_intent: PhotoWardrobeIntent,
        requested_outfit_category: str = "",
    ) -> float:
        request_context = re.sub(r"\s+", "", str(request_text or "")).lower()
        ambient = re.sub(r"\s+", "", str(ambient_context or "")).lower()
        schedule_history = re.sub(r"\s+", "", str(schedule_history_context or "")).lower()
        note = re.sub(r"\s+", "", str(candidate.get("note") or "")).lower()
        kind = candidate.get("kind")
        score = 2.0 if kind == "persona" else 1.0
        if kind == "relation_role":
            # A named role is a stronger signal than an unrelated persona or
            # library image; group intent is still a softer, contextual signal.
            score += 6.0 if candidate.get("role_explicit_mention") else 3.0
        categories = (
            ("home", ("在家", "家里", "居家", "宿舍", "公寓", "卧室", "房间", "客厅", "宅家", "居家室内", "室内日常")),
            ("sleep", ("睡衣", "睡前", "起床", "刚醒", "床上", "夜晚休息")),
            ("outdoor", ("外出", "通勤", "上学", "上班", "逛街", "商场", "街头", "旅行")),
            ("sport", ("运动", "健身", "跑步", "瑜伽", "泳装", "游泳")),
            ("formal", ("正式", "礼服", "宴会", "约会", "聚会", "舞会")),
            ("cos", ("cos", "cosplay", "角色扮演", "制服", "表演服")),
        )
        for name, words in categories:
            request_hit = any(word in request_context for word in words)
            ambient_hit = any(word in ambient for word in words)
            history_hit = any(word in schedule_history for word in words)
            note_hit = any(word in note for word in words)
            if (request_hit or ambient_hit) and candidate.get("kind") == "daily_outfit" and name in {"home", "sleep"}:
                score -= 20.0
            elif note_hit:
                if request_hit:
                    score += 12.0
                if ambient_hit:
                    score += 6.0
                if history_hit:
                    score += 2.0
        candidate_category = str(candidate.get("outfit_category") or "").strip().lower()
        outfit_bearing = "outfit" in set(candidate.get("reference_roles") or ())
        if not outfit_bearing:
            candidate_category = ""
        requested_category = (
            _single_line(requested_outfit_category, 40).lower()
            or wardrobe_intent.target_category
        )
        excluded_categories = set(wardrobe_intent.excluded_categories)
        if candidate_category and candidate_category in excluded_categories:
            score -= 40.0
        elif candidate_category and candidate_category == requested_category:
            score += 18.0
        elif requested_category and candidate_category and candidate_category != "daily_outfit":
            score -= 6.0
        if requested_category == "custom_outfit" and outfit_bearing and bool(candidate.get("outfit_lock_default")):
            score -= 8.0
        structured_scenes = {
            str(value or "").strip().lower()
            for value in (candidate.get("scene_categories") or [])
            if str(value or "").strip()
        }
        def scene_categories(text: str) -> set[str]:
            scenes: set[str] = set()
            if any(token in text for token in ("在家", "家里", "居家", "宿舍", "卧室", "居家室内")):
                scenes.add("home")
            if any(token in text for token in ("卧室", "床边", "睡前", "刚起床")):
                scenes.add("bedroom")
            if any(token in text for token in ("上学", "校园", "教室", "校门")):
                scenes.add("school")
            if any(token in text for token in ("外出", "通勤", "逛街", "街头", "旅行")):
                scenes.add("outdoor")
            if any(token in text for token in ("办公室", "办公", "公司", "工作场所", "office", "workplace")):
                scenes.add("office")
            if any(token in text for token in ("正式场合", "宴会", "婚礼", "舞会", "典礼", "formal event", "banquet")):
                scenes.add("formal_event")
            if any(token in text for token in ("运动", "健身", "跑步", "瑜伽", "球场", "体育馆", "gym", "sport")):
                scenes.add("sport")
            if any(token in text for token in ("海边", "海滩", "沙滩", "泳池", "beach", "seaside", "pool")):
                scenes.add("beach")
            return scenes

        if structured_scenes & scene_categories(request_context):
            score += 10.0
        if structured_scenes & scene_categories(ambient):
            score += 4.0
        if structured_scenes & scene_categories(schedule_history):
            score += 2.0
        structured_times = {
            str(value or "").strip().lower()
            for value in (candidate.get("time_categories") or [])
            if str(value or "").strip()
        }

        def time_categories(text: str) -> set[str]:
            times: set[str] = set()
            mappings = (
                ("morning", ("清晨", "早晨", "早上", "晨间", "morning", "sunrise")),
                ("daytime", ("白天", "日间", "daytime", "daylight")),
                ("afternoon", ("下午", "午后", "afternoon")),
                ("evening", ("傍晚", "黄昏", "日落", "evening", "sunset")),
                ("night", ("夜晚", "晚上", "深夜", "夜景", "night")),
                ("bedtime", ("睡前", "临睡", "bedtime")),
            )
            for category, tokens in mappings:
                if any(token in text for token in tokens):
                    times.add(category)
            return times

        if structured_times & time_categories(request_context):
            score += 8.0
        if structured_times & time_categories(ambient):
            score += 3.0
        if structured_times & time_categories(schedule_history):
            score += 1.0
        for token in re.split(r"[，,。；;、/|：:\s]+", note):
            if len(token) >= 2 and token in request_context:
                score += min(6.0, float(len(token)))
            elif len(token) >= 2 and token in ambient:
                score += min(3.0, float(len(token)) / 2.0)
            elif len(token) >= 2 and token in schedule_history:
                score += min(1.0, float(len(token)) / 4.0)
        return score

    async def _select_photo_reference_candidate_async(
        self,
        workflow_kind: str,
        *,
        allow_daily_outfit: bool = True,
        requester_user_id: str = "",
        request_text: str = "",
        ambient_context: str = "",
        schedule_history_context: str = "",
        selection_context: str = "",
        suggested_scene_preset: str = "",
        continuity_key: str = "",
        wardrobe_intent: PhotoWardrobeIntent | None = None,
        trace_id: str = "",
        candidate_overrides: Any = None,
        selection_provider_id: str = "",
        selection_strict_provider: bool = False,
        return_selection_result: bool = False,
    ) -> dict[str, Any] | SelectionResult:
        def empty_selection(reason: str) -> dict[str, Any] | SelectionResult:
            if return_selection_result:
                return SelectionResult(None, (), "none", reason)
            return {}

        using_candidate_overrides = candidate_overrides is not None
        if not using_candidate_overrides and not bool(
            runtime_persona_setting(self, "enable_photo_reference_image", False)
        ):
            return empty_selection("reference_feature_disabled")
        normalized_workflow = str(workflow_kind or "").strip().lower()
        portrait_workflow = normalized_workflow in {"selfie", "portrait", "自拍", "人像"}
        scoped_context = bool(requester_user_id) or bool(
            self._photo_reference_knowledge_asset_candidates(
                request_text=request_text,
                ambient_context=ambient_context,
            )
        ) or bool(self._photo_reference_role_asset_candidates(request_text=request_text))
        if not portrait_workflow and not scoped_context and not using_candidate_overrides:
            return empty_selection("workflow_does_not_use_reference")
        if using_candidate_overrides:
            candidates = []
            for raw_candidate in candidate_overrides or ():
                if not isinstance(raw_candidate, dict):
                    continue
                candidate = self._normalize_photo_reference_candidate_metadata(dict(raw_candidate))
                if not candidate.get("path") and candidate.get("source"):
                    candidate["path"] = candidate["source"]
                candidates.append(candidate)
        else:
            try:
                candidates = await self._photo_reference_candidates_async(
                    allow_daily_outfit=allow_daily_outfit,
                    requester_user_id=requester_user_id,
                    request_text=request_text,
                    ambient_context=ambient_context,
                    scoped_only=not portrait_workflow,
                )
            except TypeError:
                # Keep compatibility with lightweight test/integration adapters that
                # still expose the original one-argument candidate loader.
                candidates = await self._photo_reference_candidates_async(
                    allow_daily_outfit=allow_daily_outfit,
                )
        if not candidates:
            return empty_selection("no_candidates")
        legacy_context = str(selection_context or "").strip()
        if legacy_context:
            looks_like_ambient_context = bool(
                re.search(
                    r"(?:^|[；;，,])\s*(?:时间|状态|当前日程|日程|情绪|可分享碎片|"
                    r"当前位置|当前场景|天气背景|今日穿搭|当天基础穿搭|当天穿搭|日常穿搭)\s*[：:]",
                    legacy_context,
                    flags=re.I,
                )
            )
            if not request_text and not ambient_context:
                if looks_like_ambient_context:
                    ambient_context = legacy_context
                else:
                    request_text = legacy_context
            elif not ambient_context:
                ambient_context = legacy_context
        wardrobe_intent = wardrobe_intent or analyze_photo_wardrobe(request_text)
        suggested_scene_preset = _single_line(suggested_scene_preset, 80)
        suggested_category = ""
        available_presets = self._photo_generation_scene_presets()
        if (
            not wardrobe_intent.target_category
            and suggested_scene_preset
            and suggested_scene_preset in available_presets
        ):
            suggested_category = self._photo_outfit_category_from_text(
                suggested_scene_preset
            )
        requested_category = wardrobe_intent.target_category or suggested_category
        excluded_categories = set(wardrobe_intent.excluded_categories)
        request_scenes, request_times, request_excluded_scenes, request_excluded_times = (
            parse_photo_reference_context_categories(request_text)
        )
        suggested_scenes, suggested_times, suggested_excluded_scenes, suggested_excluded_times = (
            parse_photo_reference_context_categories(suggested_scene_preset)
        )
        ambient_scenes, ambient_times, ambient_excluded_scenes, ambient_excluded_times = (
            parse_photo_reference_context_categories(ambient_context)
        )
        # Hard eligibility follows the strongest current signal. Historical schedule
        # text remains a weak score/prompt hint and must never override this turn.
        if request_scenes or request_excluded_scenes:
            requested_scene_categories = request_scenes
            excluded_scene_categories = request_excluded_scenes
        elif suggested_scenes or suggested_excluded_scenes:
            requested_scene_categories = suggested_scenes
            excluded_scene_categories = suggested_excluded_scenes
        else:
            requested_scene_categories = ambient_scenes
            excluded_scene_categories = ambient_excluded_scenes
        if request_times or request_excluded_times:
            requested_time_categories = request_times
            excluded_time_categories = request_excluded_times
        elif suggested_times or suggested_excluded_times:
            requested_time_categories = suggested_times
            excluded_time_categories = suggested_excluded_times
        else:
            requested_time_categories = ambient_times
            excluded_time_categories = ambient_excluded_times

        seen_candidate_ids: set[str] = set()
        for index, item in enumerate(candidates, start=1):
            base_id = _single_line(item.get("id"), 120) or f"candidate-{index}"
            candidate_id = base_id
            suffix = 2
            while candidate_id in seen_candidate_ids:
                candidate_id = f"{base_id}#{suffix}"
                suffix += 1
            item["id"] = candidate_id
            seen_candidate_ids.add(candidate_id)

        policy_result = select_photo_reference(
            {
                "request_text": request_text,
                "outfit_category": requested_category,
                "scene_categories": requested_scene_categories,
                "time_categories": requested_time_categories,
                "excluded_scene_categories": excluded_scene_categories,
                "excluded_time_categories": excluded_time_categories,
            },
            candidates,
        )
        policy_matches = {item.candidate_id: item for item in policy_result.candidates}
        candidate_policy_exclusions: dict[str, set[str]] = {}
        eligible_candidates: list[dict[str, Any]] = []
        for item in candidates:
            item_id = str(item.get("id") or "")
            reasons = set(policy_matches.get(item_id).excluded if item_id in policy_matches else ())
            candidate_policy_exclusions[item_id] = reasons
            if not reasons:
                eligible_candidates.append(item)

        scored_candidates = [
            (
                item,
                self._photo_reference_candidate_score(
                    item,
                    request_text,
                    ambient_context,
                    schedule_history_context=schedule_history_context,
                    wardrobe_intent=wardrobe_intent,
                    requested_outfit_category=requested_category,
                ),
            )
            for item in candidates
        ]
        def responsible_outfit_category(item: dict[str, Any]) -> str:
            if "outfit" not in set(item.get("reference_roles") or ()):
                return ""
            return str(item.get("outfit_category") or "").strip().lower()

        normal_scored = [
            pair
            for pair in scored_candidates
            if not candidate_policy_exclusions.get(str(pair[0].get("id") or ""))
            and responsible_outfit_category(pair[0]) not in excluded_categories
            and (
                not requested_category
                or not responsible_outfit_category(pair[0])
                or (
                    requested_category != "custom_outfit"
                    and responsible_outfit_category(pair[0]) == requested_category
                )
            )
        ]
        fallback = max(normal_scored, key=lambda pair: pair[1])[0] if normal_scored else None
        selected = fallback
        selection_source = "rule_fallback"
        selection_reason = "model_not_attempted" if eligible_candidates else "no_eligible_reference"
        model_reply = ""
        provider_id = _single_line(selection_provider_id, 160)
        provider_selector = getattr(self, "_task_provider", None)
        if not provider_id and callable(provider_selector):
            provider_id = provider_selector(
                getattr(self, "photo_prompt_provider_id", ""),
                getattr(self, "fast_response_provider_id", ""),
                getattr(self, "llm_provider_id", ""),
                getattr(self, "mai_style_provider_id", ""),
            )
        llm_call = getattr(self, "_llm_call", None)
        specialized_candidate = any(
            bool(item.get("outfit_lock_default"))
            or any(role in {"outfit", "scene", "continuity"} for role in (item.get("reference_roles") or []))
            for item in eligible_candidates
        )
        needs_model_choice = len(eligible_candidates) > 1 or specialized_candidate
        model_attempted = False
        model_selected_id = ""
        if (request_text or ambient_context or schedule_history_context) and needs_model_choice and callable(llm_call):
            model_attempted = True
            selection_reason = "model_invalid_response"
            options = "\n".join(
                f"{index}. id={item['id']}；角色={_single_line(item.get('role_name'), 80) or 'Bot/未指定'}；"
                f"关系={_single_line(item.get('relationship'), 80) or 'none'}；职责={','.join(item.get('reference_roles') or []) or 'identity'}；"
                f"服装类别={_single_line(item.get('outfit_category'), 40) or 'none'}；"
                f"场景类别={','.join(sorted(str(value) for value in (item.get('scene_categories') or []) if str(value).strip())) or 'none'}；"
                f"时间类别={','.join(sorted(str(value) for value in (item.get('time_categories') or []) if str(value).strip())) or 'none'}；"
                f"选用策略={_single_line(item.get('selection_eligibility'), 40) or 'matching_only'}；"
                f"排除场景={','.join(sorted(str(value) for value in (item.get('excluded_scene_categories') or []) if str(value).strip())) or 'none'}；"
                f"排除时间={','.join(sorted(str(value) for value in (item.get('excluded_time_categories') or []) if str(value).strip())) or 'none'}；"
                f"默认锁服装={bool(item.get('outfit_lock_default'))}；注释={_single_line(item.get('note'), 360)}"
                for index, item in enumerate(eligible_candidates, start=1)
            )
            none_option = "\n0. 不使用这些候选参考图，按当前要求生成全新画面"
            prompt = f"""
你在为角色生图选择一张人物参考图。结合最终画面需求中的日程、位置、当前场景和服装需求，按管理员给每张图的用途注释判断。
优先选择用途更具体且与当前场景兼容的参考图；只有没有更具体的场景或服装参考时，才选择基础人物身份图。
严格遵守候选的选用策略、排除场景与排除时间；条件不匹配时输出 0，不要为了使用参考图而曲解用户原话。
明确处于家里、卧室、睡前或刚起床时，优先在适用的居家服/睡衣参考中选择；只有明确外出、通勤、上学、逛街或展示今日穿搭时才选今日穿搭。
当前要求明确否定某类服装时，不得选择以该服装为职责的参考图；即使它是唯一候选，也应输出 0。普通换装或自定义衣服没有匹配参考时，可选身份图或输出 0，不要让旧衣服反向覆盖新要求。
用户原始要求高于环境上下文；两者冲突时必须按用户原始要求选图，不能让日程或位置覆盖用户明确要求。
若用户没有明确服装要求，但结构化场景预设给出了服装类别，且候选中存在同类别服装参考，优先选择该服装参考，不要改选基础身份图。结构化预设只用于补足空白，不得覆盖用户明确要求。
当天已发生日程只可作为较弱的经历、服装和连续性线索，不代表当前位置或当前活动。不得用历史中的旧地点覆盖当前环境；用户原始要求和当前环境始终优先于历史日程。
不要仅凭疲惫、揉眼睛、电脑桌等间接描述猜测地点或服装；场景不明确时保持保守，不要虚构居家或外出状态。
若候选带有“角色”和“关系”，且用户在本轮明确点名该角色或关系，优先选择对应的关系角色参考图；它只代表该角色本人，不要把该身份转移给 Bot。没有明确点名角色时，不要因为关系卡文字而选择关系角色参考图。
只输出候选编号，不要解释。

【最终画面需求】
{_single_line(request_text, 1200)}

【环境上下文】
{_single_line(ambient_context, 800) or "无"}

【结构化场景预设】
{suggested_scene_preset or "无"}

【当天已发生日程】
{_single_line(schedule_history_context, 1200) or "无"}

【候选参考图】
{options}{none_option}
            """.strip()
            try:
                llm_kwargs = {
                    "max_tokens": 12,
                    "provider_id": provider_id or None,
                    "task": "photo_reference_selection",
                }
                if selection_strict_provider:
                    llm_kwargs["strict_provider"] = True
                raw = await llm_call(prompt, **llm_kwargs)
                model_reply = _single_line(raw, 80)
                match = re.search(r"(?<!\d)(\d{1,2})(?!\d)", model_reply)
                choice = int(match.group(1)) if match else -1
                if match and choice == 0:
                    selected = None
                    selection_source = "model"
                    selection_reason = "fresh_image_requested"
                elif match and 1 <= choice <= len(eligible_candidates):
                    proposed = eligible_candidates[choice - 1]
                    model_selected_id = str(proposed.get("id") or "")
                    proposed_category = responsible_outfit_category(proposed)
                    if proposed_category and proposed_category in excluded_categories:
                        selected = None
                        selection_source = "semantic_exclusion"
                        selection_reason = "model_selected_explicitly_excluded_outfit"
                    elif (
                        requested_category
                        and proposed_category
                        and (
                            requested_category == "custom_outfit"
                            or proposed_category != requested_category
                        )
                    ):
                        selected = fallback
                        selection_source = "semantic_user_request"
                        selection_reason = "model_selected_incompatible_user_outfit"
                    elif (
                        requested_category
                        and not proposed_category
                        and isinstance(fallback, dict)
                        and responsible_outfit_category(fallback) == requested_category
                    ):
                        selected = fallback
                        selection_source = "semantic_scene_preset"
                        selection_reason = "model_ignored_matching_outfit_reference"
                    else:
                        selected = proposed
                        selection_source = "model"
                        selection_reason = "valid_candidate_number"
                elif not model_reply:
                    selection_reason = "model_empty_response"
                elif match:
                    selection_reason = "model_candidate_out_of_range"
            except Exception as exc:
                selection_reason = f"model_error:{type(exc).__name__}"
                logger.info(
                    "[PrivateCompanion] 参考图库模型选图失败，使用规则兜底: error=%s",
                    _single_line(exc, 120),
                )
        elif len(candidates) == 1 and not specialized_candidate:
            selection_source = "single_candidate"
            selection_reason = "only_one_candidate"
        elif not (request_text or ambient_context or schedule_history_context):
            selection_reason = "empty_selection_context"
        elif not callable(llm_call):
            selection_reason = "model_unavailable"

        score_summary = ",".join(
            f"{_single_line(item.get('id'), 40)}={score:g}"
            for item, score in scored_candidates
        )
        logger.info(
            "[PrivateCompanion] 参考图库候选评分: fallback=%s scores=%s request=%s ambient=%s",
            fallback.get("id") if isinstance(fallback, dict) else "none",
            score_summary,
            _single_line(request_text, 180),
            _single_line(ambient_context, 120),
        )
        logger.info(
            "[PrivateCompanion] 参考图库已选图: source=%s reason=%s id=%s kind=%s fallback=%s "
            "model_reply=%s path=%s note=%s candidates=%s",
            selection_source,
            selection_reason,
            selected.get("id") if isinstance(selected, dict) else "none",
            selected.get("kind") if isinstance(selected, dict) else "none",
            fallback.get("id") if isinstance(fallback, dict) else "none",
            model_reply or "-",
            _single_line(selected.get("path"), 260) if isinstance(selected, dict) else "-",
            _single_line(selected.get("note"), 160) if isinstance(selected, dict) else "-",
            len(candidates),
        )
        def structured_exclusions(item: dict[str, Any]) -> tuple[str, ...]:
            reasons = set(candidate_policy_exclusions.get(str(item.get("id") or ""), set()))
            if responsible_outfit_category(item) in excluded_categories:
                reasons.add("outfit")
            return tuple(sorted(reasons))

        structured_matches = tuple(
            CandidateMatch(
                candidate_id=str(item.get("id") or ""),
                score=float(score),
                rank=index,
                matched=tuple(policy_matches.get(str(item.get("id") or "")).matched) if str(item.get("id") or "") in policy_matches else tuple(),
                excluded=structured_exclusions(item),
                reason="formal_model_selection" if selection_source == "model" else selection_reason,
            )
            for index, (item, score) in enumerate(
                sorted(scored_candidates, key=lambda pair: (-pair[1], str(pair[0].get("id") or ""))),
                start=1,
            )
        )
        structured_selection = SelectionResult(
            selected=selected if isinstance(selected, dict) else None,
            candidates=structured_matches,
            selection_source=selection_source,
            selection_reason=selection_reason,
            fallback_id=str(fallback.get("id") or "") if isinstance(fallback, dict) else "",
            model_attempted=model_attempted,
            model_selected_id=model_selected_id,
        )
        self._append_photo_generation_trace_event(
            trace_id,
            "reference_candidates",
            data={
                "candidates": [
                    {
                        "id": item.get("id"),
                        "kind": item.get("kind"),
                        "path": item.get("path"),
                        "roles": list(item.get("reference_roles") or ()),
                        "outfit_category": item.get("outfit_category"),
                        "outfit_lock_default": bool(item.get("outfit_lock_default")),
                        "scene_categories": list(item.get("scene_categories") or ()),
                        "time_categories": list(item.get("time_categories") or ()),
                        "excluded_scene_categories": list(item.get("excluded_scene_categories") or ()),
                        "excluded_time_categories": list(item.get("excluded_time_categories") or ()),
                        "selection_eligibility": item.get("selection_eligibility") or "matching_only",
                        "policy_exclusions": sorted(candidate_policy_exclusions.get(str(item.get("id") or ""), set())),
                        "metadata_source": item.get("metadata_source"),
                        "score": score,
                    }
                    for item, score in scored_candidates
                ],
                "rule_fallback_id": fallback.get("id") if isinstance(fallback, dict) else "",
                "selected_id": selected.get("id") if isinstance(selected, dict) else "",
                "model_reply": model_reply,
                "selection_source": selection_source,
                "selection_reason": selection_reason,
                "selection_result": structured_selection.to_dict(),
                "schedule_history_context": _single_line(schedule_history_context, 800),
                "schedule_history_used": bool(str(schedule_history_context or "").strip()),
            },
        )
        if return_selection_result:
            return structured_selection
        return self._normalize_photo_reference_candidate_metadata(selected) if isinstance(selected, dict) else {}

    async def _analyze_photo_reference_intent_async(
        self,
        request_text: str,
        *,
        workflow_kind: str,
        has_explicit_reference: bool,
    ) -> ReferenceIntent:
        rule_intent = analyze_reference_intent(
            request_text,
            has_explicit_reference=has_explicit_reference,
            workflow_kind=workflow_kind,
        )
        llm_call = getattr(self, "_llm_call", None)
        if (
            not has_explicit_reference
            or rule_intent.source != "conservative"
            or not callable(llm_call)
        ):
            return rule_intent
        compact_request = _single_line(request_text, 1200).lower().strip(" ，,。.!！?？；;")
        if re.fullmatch(
            r"(?:参考(?:一下|下)?|参考(?:这个|这张|这张图)(?:一下)?|"
            r"照着(?:这个|这张|这张图)(?:来|画)?|按(?:照)?(?:这个|这张|这张图)(?:来|画)?)",
            compact_request,
        ):
            return rule_intent

        provider_selector = getattr(self, "_task_provider", None)
        provider_id = ""
        if callable(provider_selector):
            provider_id = provider_selector(
                getattr(self, "photo_prompt_provider_id", ""),
                getattr(self, "fast_response_provider_id", ""),
                getattr(self, "llm_provider_id", ""),
                getattr(self, "mai_style_provider_id", ""),
            )

        prompt = f"""
分析用户对显式参考图的职责要求，只输出一个 JSON 对象：
{{"requested_roles":[],"excluded_roles":[],"continuity_mode":"ambiguous","confidence":0.0}}
roles 只能是 identity、outfit、pose、scene、style、continuity、source。
continuity_mode 只能是 continuation、edit、new_topic、ambiguous。
否定表达放进 excluded_roles，不能同时作为 requested_roles。
无法确定时 confidence 必须低于 0.7；不要猜测服装、场景或连续性。

用户要求：{_single_line(request_text, 1200)}
        """.strip()
        try:
            raw = await llm_call(
                prompt,
                max_tokens=180,
                provider_id=provider_id or None,
                task="photo_reference_intent",
            )
            match = re.search(r"\{[\s\S]*\}", str(raw or ""))
            payload = json.loads(match.group(0)) if match else {}
            if not isinstance(payload, dict):
                return rule_intent
            requested_set = {
                str(role or "").strip().lower()
                for role in (payload.get("requested_roles") or [])
            }
            excluded_set = {
                str(role or "").strip().lower()
                for role in (payload.get("excluded_roles") or [])
            }
            requested = tuple(
                role
                for role in REFERENCE_ROLES
                if role in requested_set and role not in excluded_set
            )
            excluded = tuple(role for role in REFERENCE_ROLES if role in excluded_set)
            mode = _single_line(payload.get("continuity_mode"), 30).lower()
            if mode not in CONTINUITY_MODES:
                mode = "ambiguous"
            confidence = _safe_float(payload.get("confidence"), 0.0, 0.0, 1.0)
        except Exception as exc:
            logger.debug(
                "[PrivateCompanion] 参考职责模型解析失败，使用保守规则: %s",
                _single_line(exc, 120),
            )
            return rule_intent
        if confidence < 0.7:
            return ReferenceIntent(("identity",), (), "ambiguous", confidence, "model_conservative")
        return ReferenceIntent(requested or ("identity",), excluded, mode, confidence, "model")

    async def _select_photo_reference_image_async(
        self,
        workflow_kind: str,
        *,
        allow_daily_outfit: bool = True,
        request_text: str = "",
        ambient_context: str = "",
        selection_context: str = "",
        suggested_scene_preset: str = "",
    ) -> str:
        """Return the selected reference path for legacy image-only callers."""
        selected = await self._select_photo_reference_candidate_async(
            workflow_kind,
            allow_daily_outfit=allow_daily_outfit,
            request_text=request_text,
            ambient_context=ambient_context,
            selection_context=selection_context,
            suggested_scene_preset=suggested_scene_preset,
        )
        return str(selected.get("path") or "") if isinstance(selected, dict) else ""

    async def _photo_persona_reference_image_for_kind_async(
        self,
        workflow_kind: str,
        *,
        allow_daily_outfit: bool = True,
        request_text: str = "",
        ambient_context: str = "",
        selection_context: str = "",
        suggested_scene_preset: str = "",
    ) -> str:
        """Compatibility facade used by extension bridges and legacy workflows."""
        return await self._select_photo_reference_image_async(
            workflow_kind,
            allow_daily_outfit=allow_daily_outfit,
            request_text=request_text,
            ambient_context=ambient_context,
            selection_context=selection_context,
            suggested_scene_preset=suggested_scene_preset,
        )

    async def _select_photo_reference_plan_async(
        self,
        workflow_kind: str,
        *,
        reference_intent: ReferenceIntent,
        wardrobe_intent: PhotoWardrobeIntent | None = None,
        requested_outfit_category: str | None = None,
        allow_daily_outfit: bool = True,
        requester_user_id: str = "",
        session_key: str = "",
        request_text: str = "",
        ambient_context: str = "",
        schedule_history_context: str = "",
        suggested_scene_preset: str = "",
        continuity_key: str = "",
        explicit_reference_paths: Any = (),
        require_existing_paths: bool = False,
        trace_id: str = "",
    ) -> PhotoReferencePlan:
        if reference_intent.continuity_mode == "new_topic":
            return build_photo_reference_plan(reference_intent, ())

        paths: list[str] = []
        raw_paths = explicit_reference_paths
        if isinstance(raw_paths, str):
            raw_paths = (raw_paths,)
        for raw_path in raw_paths or ():
            path = _path_text(raw_path, 1000)
            if path and path not in paths:
                paths.append(path)

        candidates: list[dict[str, Any]] = []
        indexed_roles = analyze_indexed_reference_roles(
            request_text,
            image_count=len(paths),
        )
        has_indexed_roles = any(indexed_roles)
        indexed_edit_has_source = any(
            "source" in roles for roles in indexed_roles
        )
        for index, path in enumerate(paths):
            if require_existing_paths and not os.path.isfile(path):
                continue
            candidate = await self._photo_reference_candidate_for_path_async(
                path,
                workflow_kind=workflow_kind,
                allow_daily_outfit=allow_daily_outfit,
                continuity_key=continuity_key,
            )
            if candidate:
                candidate = dict(candidate)
                candidate["available_reference_roles"] = list(
                    candidate.get("reference_roles") or ()
                )
                candidate["id"] = f"explicit_reference_{index + 1}" if len(paths) > 1 else "explicit_reference"
                if reference_intent.continuity_mode == "edit":
                    assigned_roles = list(indexed_roles[index]) if has_indexed_roles else ["source"]
                    if has_indexed_roles and index == 0 and not indexed_edit_has_source:
                        assigned_roles.insert(0, "source")
                    candidate["kind"] = "source" if "source" in assigned_roles else "explicit"
                    candidate["reference_roles"] = list(dict.fromkeys(assigned_roles))
                else:
                    candidate["kind"] = "explicit"
                    if has_indexed_roles:
                        candidate["reference_roles"] = list(indexed_roles[index])
                    else:
                        candidate["reference_roles"] = [
                            role
                            for role in reference_intent.requested_roles
                            if role not in {"continuity", "source"}
                        ]
                candidates.append(candidate)

        if (
            not candidates
            and not paths
            and "source" not in reference_intent.requested_roles
        ):
            selected = await self._select_photo_reference_candidate_async(
                workflow_kind,
                allow_daily_outfit=allow_daily_outfit,
                requester_user_id=requester_user_id,
                request_text=request_text,
                ambient_context=ambient_context,
                schedule_history_context=schedule_history_context,
                suggested_scene_preset=suggested_scene_preset,
                wardrobe_intent=wardrobe_intent,
                trace_id=trace_id,
            )
            if selected:
                selected_candidates: list[dict[str, Any]] = []
                resolved_role_candidates = self._photo_reference_role_asset_candidates(
                    request_text=request_text,
                )
                role_candidates = [
                    item
                    for item in resolved_role_candidates
                    if item.get("role_explicit_mention")
                    and item.get("group_photo_requested")
                ]
                if (
                    not role_candidates
                    and selected.get("kind") == "relation_role"
                    and selected.get("group_photo_requested")
                ):
                    role_candidates = [selected]
                if role_candidates:
                    unique_role_candidates: list[dict[str, Any]] = []
                    seen_role_owners: set[str] = set()
                    for role_candidate in sorted(
                        role_candidates,
                        key=lambda item: -_safe_int(item.get("priority"), 0, -1000),
                    ):
                        owner_id = str(role_candidate.get("owner_id") or "")
                        if not owner_id or owner_id in seen_role_owners:
                            continue
                        seen_role_owners.add(owner_id)
                        unique_role_candidates.append(role_candidate)
                    role_candidates = unique_role_candidates
                group_role_requested = bool(role_candidates)
                if group_role_requested:
                    # A named relationship-role group shot should retain both
                    # Bot's identity and the named role when the backend can
                    # accept multiple references.  The projection layer still
                    # handles one-image backends and emits a textual fallback.
                    persona_candidate = next(
                        (
                            item
                            for item in await self._photo_reference_candidates_async(
                                request_text=request_text,
                                requester_user_id=requester_user_id,
                                ambient_context=ambient_context,
                                allow_daily_outfit=allow_daily_outfit,
                            )
                            if item.get("kind") == "persona"
                        ),
                        None,
                    )
                    if persona_candidate:
                        persona_candidate = dict(persona_candidate)
                        persona_candidate["priority"] = max(
                            760,
                            _safe_int(persona_candidate.get("priority"), 0, -1000),
                        )
                        selected_candidates.append(persona_candidate)
                    elif selected.get("kind") in {
                        "persona",
                        "library",
                        "daily_outfit",
                        "recent_sent_photo",
                    }:
                        bot_candidate = dict(selected)
                        bot_candidate["priority"] = max(
                            760,
                            _safe_int(bot_candidate.get("priority"), 0, -1000),
                        )
                        selected_candidates.append(bot_candidate)
                    selected_candidates.extend(role_candidates[:4])
                if not selected_candidates:
                    selected_candidates = [selected]
                for candidate in selected_candidates:
                    if (
                        candidate.get("kind") == "knowledge_reference"
                        and reference_intent.source == "workflow_default"
                        and "identity" not in set(candidate.get("reference_roles") or ())
                    ):
                        candidate = dict(candidate)
                        candidate["reference_roles"] = [
                            "identity",
                            *(role for role in (candidate.get("reference_roles") or ()) if role != "identity"),
                        ]
                        candidate["available_reference_roles"] = list(candidate["reference_roles"])
                    if all(
                        str(candidate.get("id") or "") != str(existing.get("id") or "")
                        for existing in candidates
                    ):
                        candidates.append(candidate)
        plan_intent = reference_intent
        if not plan_intent.requested_roles and candidates:
            scoped_roles: set[str] = set()
            for candidate in candidates:
                if candidate.get("kind") in {"relation_user", "relation_role"}:
                    scoped_roles.update(candidate.get("reference_roles") or ("identity",))
                elif candidate.get("kind") == "knowledge_reference":
                    scoped_roles.update(candidate.get("reference_roles") or ("scene", "style"))
            scoped_roles.intersection_update(REFERENCE_ROLES)
            if scoped_roles:
                plan_intent = ReferenceIntent(
                    tuple(role for role in REFERENCE_ROLES if role in scoped_roles),
                    reference_intent.excluded_roles,
                    reference_intent.continuity_mode,
                    reference_intent.confidence,
                    "scoped_context",
                )
        if reference_intent.continuity_mode == "edit" and has_indexed_roles:
            requested_roles = set(reference_intent.requested_roles)
            for candidate in candidates:
                requested_roles.update(candidate.get("reference_roles") or ())
            plan_intent = ReferenceIntent(
                tuple(role for role in REFERENCE_ROLES if role in requested_roles),
                reference_intent.excluded_roles,
                reference_intent.continuity_mode,
                reference_intent.confidence,
                reference_intent.source,
            )
        if requested_outfit_category is None:
            requested_outfit_category = (
                str(getattr(wardrobe_intent, "target_category", "") or "")
                .strip()
                .lower()
            )
        else:
            requested_outfit_category = str(requested_outfit_category).strip().lower()
        reference_outfit_excluded = explicitly_excludes_reference_outfit(request_text)
        if reference_intent.source == "workflow_default" and candidates and not paths:
            requested_roles = set(reference_intent.requested_roles)
            for candidate in candidates:
                candidate_roles = {
                    str(role or "").strip().lower()
                    for role in (candidate.get("reference_roles") or ())
                }
                candidate_category = (
                    str(candidate.get("outfit_category") or "").strip().lower()
                )
                if (
                    bool(candidate.get("outfit_lock_default"))
                    and "outfit" in candidate_roles
                    and "outfit" not in reference_intent.excluded_roles
                    and not reference_outfit_excluded
                    and (
                        not requested_outfit_category
                        or (
                            requested_outfit_category != "custom_outfit"
                            and candidate_category == requested_outfit_category
                        )
                    )
                ):
                    requested_roles.add("outfit")
            if requested_roles != set(reference_intent.requested_roles):
                plan_intent = ReferenceIntent(
                    tuple(role for role in REFERENCE_ROLES if role in requested_roles),
                    reference_intent.excluded_roles,
                    reference_intent.continuity_mode,
                    reference_intent.confidence,
                    reference_intent.source,
                )
        matching_outfit_candidates: list[tuple[dict[str, Any], bool]] = []
        for candidate in candidates:
            candidate_roles = {
                str(role or "").strip().lower()
                for role in (candidate.get("reference_roles") or ())
            }
            uses_available_outfit_role = (
                "outfit" not in candidate_roles
                and candidate.get("kind") == "explicit"
                and not has_indexed_roles
                and reference_intent.continuity_mode != "edit"
                and "outfit"
                in {
                    str(role or "").strip().lower()
                    for role in (candidate.get("available_reference_roles") or ())
                }
            )
            declared_roles = candidate_roles | (
                {"outfit"} if uses_available_outfit_role else set()
            )
            if (
                "outfit" in declared_roles
                and str(candidate.get("outfit_category") or "").strip().lower()
                == requested_outfit_category
            ):
                matching_outfit_candidates.append(
                    (candidate, uses_available_outfit_role)
                )
        matching_outfit_reference = bool(matching_outfit_candidates)
        if (
            requested_outfit_category
            and requested_outfit_category != "custom_outfit"
            and matching_outfit_reference
            and not reference_outfit_excluded
        ):
            for candidate, uses_available_outfit_role in matching_outfit_candidates:
                if uses_available_outfit_role:
                    candidate_roles = {
                        str(role or "").strip().lower()
                        for role in (candidate.get("reference_roles") or ())
                    }
                    candidate_roles.add("outfit")
                    candidate["reference_roles"] = [
                        role for role in REFERENCE_ROLES if role in candidate_roles
                    ]
            requested_roles = set(plan_intent.requested_roles)
            excluded_roles = set(plan_intent.excluded_roles)
            requested_roles.add("outfit")
            excluded_roles.discard("outfit")
            plan_intent = replace(
                plan_intent,
                requested_roles=tuple(
                    role for role in REFERENCE_ROLES if role in requested_roles
                ),
                excluded_roles=tuple(
                    role for role in REFERENCE_ROLES if role in excluded_roles
                ),
            )
        plan = build_photo_reference_plan(plan_intent, candidates)
        if (
            not plan.bindings
            and self._photo_persona_fallback_allowed(
                workflow_kind,
                reference_intent,
            )
        ):
            persona_path = await self._photo_persona_reference_image_path_async()
            if persona_path:
                fallback_plan = build_photo_reference_plan(
                    reference_intent,
                    (
                        {
                            "id": "persona",
                            "kind": "persona",
                            "path": persona_path,
                            "reference_roles": ["identity"],
                        },
                    ),
                )
                if fallback_plan.bindings:
                    plan = fallback_plan
        return plan

    async def _photo_reference_candidate_from_plan_binding_async(
        self,
        binding: Any,
        *,
        workflow_kind: str,
        allow_daily_outfit: bool,
        continuity_key: str,
    ) -> dict[str, Any]:
        path = _path_text(getattr(binding, "path", ""), 1000)
        if not path:
            return {}
        snapshot = getattr(binding, "candidate", None)
        candidate = dict(snapshot) if isinstance(snapshot, dict) else {}
        if not candidate:
            candidate = await self._photo_reference_candidate_for_path_async(
                path,
                workflow_kind=workflow_kind,
                allow_daily_outfit=allow_daily_outfit,
                continuity_key=continuity_key,
            )
        if not candidate:
            return {}
        normalized = dict(candidate)
        normalized["reference_roles"] = list(getattr(binding, "roles", ()) or ())
        normalized["ignored_reference_roles"] = list(getattr(binding, "ignore", ()) or ())
        normalized["outfit_lock_default"] = bool(
            normalized.get("outfit_lock_default") and "outfit" in normalized["reference_roles"]
        )
        return self._normalize_photo_reference_candidate_metadata(normalized)

    def _extract_action_image_path(self, action_context: str) -> str:
        text = str(action_context or "")
        match = re.search(r"(?:图片路径|真实图片文件)[:：]\s*(.+)", text)
        if not match:
            return ""
        path = match.group(1).strip().splitlines()[0].strip()
        return path if path and os.path.exists(path) else ""

    def _extract_action_photo_caption(self, action_context: str) -> str:
        text = str(action_context or "")
        match = re.search(r"(?:画面|图片画面|画面草稿)[:：]\s*(.+)", text)
        if not match:
            return ""
        return _single_line(match.group(1).splitlines()[0], 220)

    def _extract_action_photo_subject_owner(self, action_context: str) -> str:
        text = str(action_context or "")
        match = re.search(r"(?:图片主体归属|画面主体归属)[:：]\s*([^\r\n]+)", text)
        if not match:
            return ""
        return _normalize_photo_subject_owner(match.group(1))

    def _build_outbound_chain(
        self,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
    ) -> list[Any]:
        chain: list[Any] = []
        if text:
            chain.append(Plain(text))
        for component in extra_components or []:
            if component is not None:
                chain.append(component)
        if image_path and os.path.exists(image_path):
            try:
                chain.append(Image.fromFileSystem(image_path))
            except AttributeError:
                chain.append(Image.from_file_system(image_path))
        if not chain:
            chain.append(Plain(""))
        return chain

    def _parse_message_session(self, umo: str) -> MessageSession | None:
        try:
            return MessageSession.from_str(str(umo or ""))
        except Exception:
            return None

    def _platform_instance_id(self, platform: Any | None) -> str:
        if platform is None:
            return ""
        try:
            meta = platform.meta()
        except Exception:
            return ""
        return str(getattr(meta, "id", "") or getattr(meta, "name", "") or "").strip()

    def _session_for_platform(self, session: MessageSession, platform: Any | None = None) -> MessageSession:
        platform_id = self._platform_instance_id(platform) or str(getattr(session, "platform_id", "") or "")
        return MessageSession(
            platform_name=platform_id,
            message_type=self._message_type_for_session(session),
            session_id=str(getattr(session, "session_id", "") or ""),
        )

    def _get_platform_for_session(self, session: MessageSession) -> Any | None:
        platform_id = str(getattr(session, "platform_id", "") or "")
        manager = getattr(self.context, "platform_manager", None)
        if not platform_id or not manager:
            return None
        platforms = []
        try:
            platforms = list(manager.get_insts())
        except Exception:
            platforms = list(getattr(manager, "platform_insts", []) or [])
        for platform in platforms:
            try:
                meta = platform.meta()
            except Exception:
                continue
            if getattr(meta, "id", "") == platform_id or getattr(meta, "name", "") == platform_id:
                return platform
        return None

    def _message_type_for_session(self, session: MessageSession) -> MessageType:
        msg_type = getattr(session, "message_type", MessageType.FRIEND_MESSAGE)
        if isinstance(msg_type, MessageType):
            return msg_type
        msg_type_text = str(msg_type or "")
        if "Group" in msg_type_text or "GROUP" in msg_type_text:
            return MessageType.GROUP_MESSAGE
        return MessageType.FRIEND_MESSAGE

    def _format_send_exception(self, exc: Exception | BaseException | None) -> str:
        if exc is None:
            return ""
        text = _single_line(str(exc), 180)
        if text:
            return f"{exc.__class__.__name__}: {text}"
        return repr(exc)

    @staticmethod
    def _is_onebot_event_checker_send_rejection(error: Any) -> bool:
        """Identify the NTQQ sendMsg rejection shared by every aiocqhttp send route."""
        text = str(error or "").strip().lower()
        compact = re.sub(r"\s+", "", text)
        has_retcode = any(
            token in compact
            for token in ("retcode=1200", "retcode:1200", "'retcode':1200", '\"retcode\":1200')
        )
        return bool(
            has_retcode
            and "eventcheckerfailed" in compact
            and ("sendmsg" in compact or "nodeikernelmsgservice" in compact)
        )

    @staticmethod
    def _onebot_event_checker_rejection_summary() -> str:
        return "QQ/NTQQ 拒绝发送（retcode=1200，EventChecker sendMsg）；目标可能暂时不可私聊、好友状态已变化，或 QQ 客户端正处于异常状态"

    def _describe_send_target(self, umo: str, session: MessageSession | None, platform: Any | None) -> str:
        if session is None:
            return f"umo={_single_line(umo, 140) or '-'} session=unparsed platform=-"
        platform_id = _single_line(getattr(session, "platform_id", ""), 60)
        session_id = _single_line(getattr(session, "session_id", ""), 80)
        message_type = _single_line(getattr(session, "message_type", ""), 60)
        platform_desc = "found" if platform else "missing"
        if platform:
            platform_desc = _single_line(self._platform_instance_id(platform), 80) or platform.__class__.__name__
        return (
            f"umo={_single_line(umo, 140) or '-'} "
            f"platform_id={platform_id or '-'} type={message_type or '-'} session_id={session_id or '-'} platform={platform_desc}"
        )

    def _apply_proactive_tts_message_scope(self, event: Any, chain: list[Any]) -> bool:
        feature_enabled = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        tts_enabled = (
            feature_enabled("enable_tts_enhancement")
            if callable(feature_enabled)
            else bool(runtime_persona_setting(self, "enable_tts_enhancement", False))
        )
        if (
            not tts_enabled
            or str(
                runtime_persona_setting(self, "tts_message_scope", "replies_only")
                or "replies_only"
            ).lower()
            != "replies_and_proactive"
        ):
            return False
        if any(isinstance(component, Record) for component in chain) or any(
            bool(getattr(component, "_private_companion_skip_tts_enhancement", False))
            for component in chain
        ):
            return False
        try:
            setattr(event, "_private_companion_tts_request_applied", True)
            setattr(event, "_private_companion_tts_forced_by_message_scope", True)
        except Exception:
            return False
        return True

    async def _trigger_proactive_decorating_hooks(self, umo: str, chain: list[Any]) -> list[Any]:
        if not runtime_persona_setting(self, "enable_proactive_decorating_hooks", True) or not chain:
            return chain
        session = self._parse_message_session(umo)
        if not session:
            return chain
        platform = self._get_platform_for_session(session)
        if not platform:
            return chain
        try:
            message_obj = AstrBotMessage()
            message_obj.type = self._message_type_for_session(session)
            message_obj.self_id = str(getattr(session, "session_id", "") or "")
            message_obj.session_id = str(getattr(session, "session_id", "") or "")
            message_obj.message_id = f"private_companion_proactive_{uuid.uuid4().hex}"
            message_obj.sender = MessageMember(user_id=message_obj.session_id)
            message_obj.message = chain
            message_obj.message_str = ""
            message_obj.raw_message = None
            message_obj.timestamp = int(time.time())
            event = AstrMessageEvent("", message_obj, platform.meta(), message_obj.session_id)
            event.set_result(self._build_result_from_chain(chain))
            setattr(event, "_private_companion_proactive_delivery_umo", umo)
            if self._apply_proactive_tts_message_scope(event, chain):
                logger.info(
                    "[PrivateCompanion] 主动消息按 TTS 生效范围进入强化链: session=%s",
                    _single_line(umo, 120) or "unknown",
                )
            for component in chain:
                raw_full_text = getattr(component, "_private_companion_proactive_full_text", "")
                if not raw_full_text:
                    continue
                setattr(event, "_private_companion_proactive_full_text", raw_full_text)
                setattr(
                    event,
                    "_private_companion_proactive_segment_index",
                    max(0, int(getattr(component, "_private_companion_proactive_segment_index", 0) or 0)),
                )
                setattr(
                    event,
                    "_private_companion_proactive_segment_count",
                    max(1, int(getattr(component, "_private_companion_proactive_segment_count", 1) or 1)),
                )
                break
            if any(
                bool(getattr(component, "_private_companion_skip_tts_enhancement", False))
                for component in chain
            ):
                setattr(event, "_private_companion_skip_tts_enhancement", "proactive_prebuilt_voice")
        except Exception as e:
            logger.debug("[PrivateCompanion] 构造主动消息装饰事件失败,跳过 hooks: %s", e)
            return chain
        try:
            handlers = star_handlers_registry.get_handlers_by_event_type(
                EventType.OnDecoratingResultEvent
            )
        except Exception as e:
            logger.debug("[PrivateCompanion] 获取装饰 hooks 失败: %s", e)
            return chain
        for handler in handlers:
            try:
                await handler.handler(event)
            except Exception as e:
                logger.warning(
                    "[PrivateCompanion] 主动消息装饰 hook 失败: %s: %s",
                    getattr(handler, "handler_full_name", "unknown"),
                    e,
                )
        is_stopped = getattr(event, "is_stopped", None)
        if callable(is_stopped):
            try:
                if is_stopped():
                    return []
            except Exception:
                pass
        result = event.get_result()
        processed = getattr(result, "chain", None) if result is not None else None
        if processed is None:
            return []
        processed_chain = list(processed or [])
        return self._filter_decorated_proactive_chain(chain, processed_chain)

    def _proactive_plain_segment_component(
        self,
        text: str,
        *,
        full_text: str = "",
        index: int = 0,
        count: int = 1,
        suppress_tts: bool = False,
    ) -> Plain:
        comp = Plain(text)
        clean_full = _single_line(full_text, max(1200, len(str(full_text or "")) + 32))
        if clean_full:
            try:
                object.__setattr__(comp, "_private_companion_proactive_full_text", clean_full)
                object.__setattr__(comp, "_private_companion_proactive_segment_index", max(0, int(index)))
                object.__setattr__(comp, "_private_companion_proactive_segment_count", max(1, int(count)))
            except Exception:
                pass
        if suppress_tts:
            try:
                object.__setattr__(comp, "_private_companion_skip_tts_enhancement", True)
            except Exception:
                pass
        return comp

    def _filter_decorated_proactive_chain(self, original_chain: list[Any], processed_chain: list[Any]) -> list[Any]:
        if not processed_chain:
            return []

        filtered: list[Any] = []
        removed_any = False
        for component in processed_chain:
            if isinstance(component, Plain):
                text = self._plain_component_text(component)
                if self._is_proactive_delivery_receipt_text(text):
                    removed_any = True
                    continue
                cleaned = self._strip_proactive_delivery_receipt_lines(text)
                if not cleaned:
                    removed_any = True
                    continue
                if cleaned != text:
                    removed_any = True
                    filtered.append(Plain(cleaned))
                else:
                    filtered.append(component)
                continue
            filtered.append(component)

        if filtered:
            return filtered
        return [] if removed_any else processed_chain

    @staticmethod
    def _plain_component_text(component: Any) -> str:
        for attr in ("text", "content", "message"):
            value = getattr(component, attr, None)
            if isinstance(value, str):
                return value
        return str(component or "")

    @staticmethod
    def _contains_inline_image_tag(text: str) -> bool:
        return bool(re.search(r"<img\b[^>]*\bsrc\s*=", str(text or ""), flags=re.IGNORECASE))

    @staticmethod
    def _is_proactive_delivery_receipt_text(text: str) -> bool:
        raw = _single_line(text, 240)
        if not raw:
            return False
        compact = re.sub(r"[\s。.!！?？,，；;:：、~～\"'“”‘’（）()【】\[\]]+", "", raw).lower()
        if not compact:
            return False
        if compact in {
            "已发送",
            "发送成功",
            "发送完成",
            "发送完毕",
            "已成功发送",
            "消息已发送",
            "消息发送成功",
            "messagesent",
            "sent",
            "我主动开口了",
            "我主动发了一段语音",
            "我主动分享了一点东西",
            "我主动做了一次小互动",
        }:
            return True
        if re.fullmatch(r"(?:图|图片|照片)(?:好|好了|生成好了|出来了|完成了)[啦了]*", compact):
            return True
        if re.fullmatch(r"(?:生图|出图|图片生成)(?:完成|好了|成功)[啦了]*", compact):
            return True
        if re.search(r"(?:还在|正在|继续)?(?:排队|队列|等待生成|等图|等图片|等它出图)", compact):
            return True
        if re.match(r"^(?:已经|已)(?:发|发送)过去[啦了]?(?:等(?:着|他|你|对方)|等回复|等回我)?$", compact):
            return True
        if re.match(r"^等(?:着)?(?:他|你|对方)?回(?:我|复)?[啦了]*$", compact):
            return True
        if compact.startswith("消息已送达"):
            return True
        if re.match(r"^这是.{0,80}(?:发的|发送的|收到的).{0,80}(?:消息|打招呼|问候|回复)", compact):
            return True
        if re.match(r"^这(?:条|是).{0,80}(?:语气|内容|消息).{0,80}$", compact):
            return True
        receipt_prefixes = (
            "消息已发送给",
            "消息发送给",
            "已发送给",
            "已经发送给",
            "已向",
            "已经向",
        )
        receipt_descriptors = (
            "讲的是",
            "说的是",
            "内容是",
            "内容就是",
            "发的是",
            "转述的是",
            "分享的是",
            "告诉的是",
        )
        if compact.startswith(receipt_prefixes) and any(token in compact for token in receipt_descriptors):
            return True
        long_receipt_markers = (
            ("已经把", "转给"),
            ("已把", "转给"),
            ("已经将", "转给"),
            ("已将", "转给"),
            ("已经发给", "就假装"),
            ("已经发送给", "就假装"),
            ("就假装", "语气很自然"),
            ("随手分享", "语气很自然"),
        )
        if any(all(token in raw for token in pair) for pair in long_receipt_markers):
            return True
        if (
            any(token in compact for token in ("视频链接转给", "链接转给", "消息转给", "内容转给"))
            and any(token in compact for token in ("已经", "已", "完成", "成功"))
        ):
            return True
        return (
            len(compact) <= 32
            and any(token in compact for token in ("发送给用户", "发给用户", "发送给对方", "发给对方", "发出去了"))
            and any(token in compact for token in ("已", "已经", "完成", "成功"))
        )

    @staticmethod
    def _is_proactive_instruction_leak_text(text: str) -> bool:
        raw = _single_line(text, 360)
        if not raw:
            return False
        compact = re.sub(r"[\s。.!！?？,，；;:：、~～\"'“”‘’（）()【】\[\]<>《》]+", "", raw).lower()
        if not compact:
            return False
        exact_leaks = {
            "直接在当前对话中输出这条主动消息",
            "请直接在当前对话中输出这条主动消息",
            "在当前对话中输出这条主动消息",
            "直接输出这条主动消息",
            "输出这条主动消息",
            "发送这条主动消息",
            "sendthisproactivemessage",
            "outputthisproactivemessage",
        }
        if compact in exact_leaks:
            return True
        has_proactive_target = "主动消息" in raw or "proactive message" in raw.lower()
        has_delivery_command = any(
            token in compact
            for token in (
                "直接输出",
                "请输出",
                "输出这条",
                "输出本条",
                "直接发送",
                "请发送",
                "发送这条",
                "发出这条",
                "sendthis",
                "outputthis",
            )
        )
        has_instruction_context = any(
            token in compact
            for token in (
                "当前对话",
                "当前聊天",
                "本轮对话",
                "用户对话",
                "聊天窗口",
                "给用户",
                "touser",
                "currentchat",
                "currentconversation",
            )
        )
        if has_proactive_target and has_delivery_command and (has_instruction_context or len(compact) <= 36):
            return True
        if len(compact) <= 44 and has_delivery_command and has_instruction_context and any(
            token in compact for token in ("消息", "正文", "文本", "content", "message")
        ):
            return True
        return False

    def _strip_proactive_delivery_receipt_lines(self, text: str) -> str:
        kept: list[str] = []
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if self._is_proactive_delivery_receipt_text(line):
                continue
            kept.append(line)
        return "\n".join(kept).strip()

    def _validate_proactive_outbound_candidate(
        self,
        text: str,
        *,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        reason: str = "",
        action: str = "",
        source: str = "send",
    ) -> dict[str, Any]:
        raw = str(text or "").strip()
        has_media = bool(_path_text(image_path, 1000)) or bool(extra_components)
        if not raw:
            if has_media:
                return {"decision": "send", "text": "", "reason": ""}
            return {"decision": "drop", "text": "", "reason": "主动行为没有产出可发送内容", "hard": True}
        if self._looks_like_internal_provider_error_text(raw):
            return {"decision": "drop", "text": "", "reason": "主动正文是模型/工具调用失败信息", "hard": True}

        kept_lines: list[str] = []
        removed_leak = False
        for raw_line in raw.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if (
                self._is_proactive_delivery_receipt_text(line)
                or self._is_proactive_instruction_leak_text(line)
                or self._framework_agent_meta_summary_leak(line)
            ):
                removed_leak = True
                continue
            kept_lines.append(line)
        if removed_leak:
            cleaned = "\n".join(kept_lines).strip()
            if cleaned:
                return {"decision": "rewrite", "text": cleaned, "reason": "已清理主动正文中的内部提示词/执行回执残留"}
            if has_media:
                return {"decision": "rewrite", "text": "", "reason": "已清理主动正文中的内部提示词/执行回执残留"}
            return {"decision": "drop", "text": "", "reason": "主动正文只剩内部提示词/执行回执残留", "hard": True}

        if self._is_proactive_delivery_receipt_text(raw):
            return {"decision": "drop", "text": "", "reason": "主动正文是工具/执行状态回执", "hard": True}
        if self._is_proactive_instruction_leak_text(raw):
            return {"decision": "drop", "text": "", "reason": "主动正文疑似内部提示词/发送指令泄漏", "hard": True}
        if self._framework_agent_meta_summary_leak(raw):
            return {"decision": "drop", "text": "", "reason": "主动正文疑似工具循环/内部发送摘要泄漏", "hard": True}

        return {"decision": "send", "text": raw, "reason": ""}

    def _proactive_archive_context_text(self, text: str) -> bool:
        cleaned = _single_line(text, 500)
        if not cleaned:
            return False
        if "【主动承接占位】" in cleaned or "下一条是 Bot 主动发出的内容" in cleaned:
            return True
        if self._is_proactive_delivery_receipt_text(cleaned):
            return True
        return False

    @staticmethod
    def _strip_leading_sentence_boundary_artifacts(text: str) -> str:
        cleaned = str(text or "").strip()
        cleaned = re.sub(r"^(?:[。！？!?；;，,、：:]+[\s\u3000]*)+", "", cleaned).strip()
        return cleaned

    def _forward_sender_id_for_segments(self, event: Any | None = None) -> str:
        if event is not None:
            try:
                sender_id = _single_line(self._event_self_id(event), 40)
                if sender_id:
                    return sender_id
            except Exception:
                pass
        for sender_id in self._known_bot_self_ids():
            if sender_id:
                return sender_id
        return "0"

    def _forward_nodes_for_segments(self, segments: list[str], *, event: Any | None = None) -> list[dict[str, Any]]:
        sender_name = _single_line(
            runtime_persona_setting(self, "bot_name", ""), 40
        ) or "PrivateCompanion"
        sender_id = self._forward_sender_id_for_segments(event)
        nodes: list[dict[str, Any]] = []
        for segment in segments:
            text = str(segment or "").strip()
            if not text:
                continue
            nodes.append(
                {
                    "type": "node",
                    "data": {
                        "name": sender_name,
                        "uin": sender_id,
                        "content": [{"type": "text", "data": {"text": text}}],
                    },
                }
            )
        return nodes

    def _clean_forward_segment_texts(self, segments: list[str]) -> list[str]:
        cleaned: list[str] = []
        for segment in segments:
            text = re.sub(r"</?t{2,}s\b[^>]*>", "", str(segment or ""), flags=re.IGNORECASE).strip()
            text = self._strip_leading_sentence_boundary_artifacts(text)
            if text:
                cleaned.append(text)
        return cleaned

    def _onebot_forward_action_result_ok(self, result: Any) -> bool:
        if result is None:
            return True
        if isinstance(result, dict):
            status = str(result.get("status") or result.get("result") or "").strip().lower()
            if status in {"failed", "fail", "error", "nok"}:
                return False
            retcode = result.get("retcode", result.get("code", None))
            if retcode is not None:
                try:
                    return int(retcode) == 0
                except Exception:
                    return False
            data = result.get("data")
            if isinstance(data, dict) and any(data.get(key) for key in ("message_id", "forward_id", "res_id", "resid")):
                return True
            return any(result.get(key) for key in ("message_id", "forward_id", "res_id", "resid"))
        return bool(result)

    async def _call_onebot_forward_action(self, client: Any, action: str, **params: Any) -> bool:
        for attr in ("call_action", "call_api", "api"):
            func = getattr(client, attr, None)
            if not callable(func):
                continue
            try:
                result = func(action, **params)
            except TypeError:
                try:
                    result = func(action, params)
                except Exception as exc:
                    if self._delivery_outcome_is_uncertain(exc):
                        self._log_uncertain_onebot_submission(action, exc)
                        return True
                    continue
            except Exception as exc:
                if self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True
                continue
            try:
                if hasattr(result, "__await__"):
                    result = await result
            except Exception as exc:
                if self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True
                continue
            if self._onebot_forward_action_result_ok(result):
                return True
        func = getattr(client, action, None)
        if callable(func):
            try:
                result = func(**params)
            except Exception as exc:
                if self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True
                return False
            try:
                if hasattr(result, "__await__"):
                    result = await result
            except Exception as exc:
                if self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True
                return False
            return self._onebot_forward_action_result_ok(result)
        return False

    @staticmethod
    def _looks_like_python_traceback_text(text: Any) -> bool:
        """Only treat a complete Python stack trace as an outbound error leak.

        Technical conversations commonly mention ``traceback`` or explain a
        schema.  Those words alone are not an error.  A real Python traceback
        has the header, at least one frame, and a terminating exception line.
        """
        raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not raw:
            return False
        has_header = bool(re.search(r"(?im)^\s*traceback \(most recent call last\):\s*$", raw))
        has_frame = bool(re.search(r"(?im)^\s*file \"[^\n\"]+\", line \d+(?:, in .+)?\s*$", raw))
        has_exception = bool(
            re.search(
                r"(?im)^\s*(?:[a-z_][\w.]*(?:error|exception|warning)|assertionerror|keyboardinterrupt|systemexit)(?::|$)",
                raw,
            )
        )
        return has_header and has_frame and has_exception

    def _framework_error_leak_kind(self, text: Any) -> str:
        """Return a safe classifier code for a real framework leak, if any."""
        if self._looks_like_python_traceback_text(text):
            return "python_traceback"
        if self._looks_like_internal_provider_error_text(text):
            return "provider_error"
        cleaned = _single_line(text, 1000).lower()
        if any(
            marker in cleaned
            for marker in (
                "error occurred while processing agent request",
                "sqlite3.operationalerror",
                "database is locked",
                "sqlalche.me/e/20/e3q8",
                "model do not support image input",
            )
        ):
            return "framework_error"
        if self._framework_agent_meta_summary_leak(str(text or "")):
            return "tool_loop"
        return ""

    async def _send_segmented_forward_message(
        self,
        *,
        target_type: str,
        target_id: str,
        segments: list[str],
        event: Any | None = None,
        source: str = "",
    ) -> bool:
        send_as_forward = self._segmented_setting(
            "send_as_forward",
            chat_type=target_type,
            default=False,
        )
        if not bool(send_as_forward):
            return False
        target_type = str(target_type or "").strip().lower()
        target_id = _single_line(target_id, 80)
        if target_type not in {"private", "group"} or not target_id:
            return False
        raw_segments = [_redact_outbound_secrets(item, self).strip() for item in segments if str(item or "").strip()]
        if len(raw_segments) <= 1:
            return False
        if runtime_persona_setting(self, "enable_tts_enhancement", False) and any(
            re.search(r"</?t{2,}s\b", item, flags=re.IGNORECASE) for item in raw_segments
        ):
            logger.info("[PrivateCompanion] 分段合并消息跳过 TTS 内容: source=%s target=%s:%s", source or "unknown", target_type, target_id)
            return False
        cleaned_segments = self._clean_forward_segment_texts(raw_segments)
        if len(cleaned_segments) <= 1:
            return False
        hit = self._forbidden_recall_hit("\n".join(cleaned_segments))
        if hit:
            logger.warning(
                "[PrivateCompanion] 分段合并消息命中违禁词，已拦截发送: source=%s target=%s:%s word=%s",
                source or "unknown",
                target_type,
                target_id,
                _single_line(hit, 40),
            )
            return False
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return False
        nodes = self._forward_nodes_for_segments(cleaned_segments, event=event)
        if len(nodes) <= 1:
            return False
        target_value: Any = target_id
        try:
            target_value = int(target_id)
        except Exception:
            pass
        if target_type == "group":
            attempts = [
                ("send_group_forward_msg", {"group_id": target_value, "messages": nodes}),
                ("send_group_forward_msg", {"group_id": target_value, "nodes": nodes}),
                ("send_forward_msg", {"group_id": target_value, "messages": nodes}),
                ("send_forward_msg", {"group_id": target_value, "nodes": nodes}),
            ]
        else:
            attempts = [
                ("send_private_forward_msg", {"user_id": target_value, "messages": nodes}),
                ("send_private_forward_msg", {"user_id": target_value, "nodes": nodes}),
                ("send_forward_msg", {"user_id": target_value, "messages": nodes}),
                ("send_forward_msg", {"user_id": target_value, "nodes": nodes}),
            ]
        for action, params in attempts:
            if await self._call_onebot_forward_action(client, action, **params):
                self._confirm_outbound_delivery(
                    "",
                    [Plain(segment) for segment in cleaned_segments],
                )
                logger.info(
                    "[PrivateCompanion] 分段消息已合并转发发送: source=%s target=%s:%s segments=%s",
                    source or "unknown",
                    target_type,
                    target_id,
                    len(cleaned_segments),
                )
                return True
        logger.info(
            "[PrivateCompanion] 分段合并转发发送不可用，回退普通分段: source=%s target=%s:%s segments=%s",
            source or "unknown",
            target_type,
            target_id,
            len(cleaned_segments),
        )
        return False

    async def _send_segmented_proactive_forward_message(self, umo: str, segments: list[str], *, source: str = "proactive") -> bool:
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("merged_forward", umo=umo):
            return False
        session = self._parse_message_session(umo)
        if not session:
            return False
        target_id = _single_line(getattr(session, "session_id", ""), 80)
        if not target_id:
            return False
        target_type = "group" if self._message_type_for_session(session) == MessageType.GROUP_MESSAGE else "private"
        return await self._send_segmented_forward_message(
            target_type=target_type,
            target_id=target_id,
            segments=segments,
            source=source,
        )

    async def _send_segmented_event_forward_message(self, event: AstrMessageEvent, segments: list[str], *, source: str = "decorating_result") -> bool:
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("merged_forward", event=event):
            return False
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                user_id = _single_line(event.get_sender_id(), 80)
                if user_id:
                    return await self._send_segmented_forward_message(
                        target_type="private",
                        target_id=user_id,
                        segments=segments,
                        event=event,
                        source=source,
                    )
        except Exception:
            pass
        group_id = self._extract_group_id_from_event(event)
        if group_id:
            return await self._send_segmented_forward_message(
                target_type="group",
                target_id=group_id,
                segments=segments,
                event=event,
                source=source,
            )
        return False

    def _segmented_chat_scope_allows(self, chat_type: str) -> bool:
        chat_type = str(chat_type or "").strip().lower()
        if chat_type not in {"private", "group"}:
            chat_type = "private"
        if bool(
            runtime_persona_setting(self, "enable_segmented_proactive_chat_profiles", False)
        ):
            return bool(
                getattr(
                    self,
                    f"segmented_proactive_{chat_type}_enabled",
                    True,
                )
            )
        scope = str(
            runtime_persona_setting(self, "segmented_proactive_chat_scope", "all") or "all"
        ).strip().lower()
        if scope not in {"all", "private", "group"}:
            scope = "all"
        return scope == "all" or scope == chat_type

    def _segmented_chat_type_for_umo(self, umo: str) -> str:
        session = self._parse_message_session(umo)
        if session and self._message_type_for_session(session) == MessageType.GROUP_MESSAGE:
            return "group"
        return "private"

    def _segmented_chat_type_for_event(self, event: AstrMessageEvent) -> str:
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return "private"
        except Exception:
            pass
        return "group" if self._extract_group_id_from_event(event) else "private"

    def _segmented_setting(
        self,
        name: str,
        *,
        event: AstrMessageEvent | None = None,
        umo: str = "",
        chat_type: str = "",
        default: Any = None,
    ) -> Any:
        normalized_name = str(name or "").strip()
        fallback = getattr(self, f"segmented_proactive_{normalized_name}", default)
        if not bool(
            runtime_persona_setting(self, "enable_segmented_proactive_chat_profiles", False)
        ):
            return fallback
        resolved_chat_type = str(chat_type or "").strip().lower()
        if resolved_chat_type not in {"private", "group"}:
            resolved_chat_type = (
                self._segmented_chat_type_for_event(event)
                if event is not None
                else self._segmented_chat_type_for_umo(umo)
            )
        return getattr(
            self,
            f"segmented_proactive_{resolved_chat_type}_{normalized_name}",
            fallback,
        )

    def _segmented_scope_allows_umo(self, umo: str) -> bool:
        return self._segmented_chat_scope_allows(self._segmented_chat_type_for_umo(umo))

    def _segmented_scope_allows_event(self, event: AstrMessageEvent) -> bool:
        return self._segmented_chat_scope_allows(self._segmented_chat_type_for_event(event))

    async def _onebot_messages_from_chain(self, chain: list[Any]) -> tuple[list[dict[str, Any]], str]:
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent

            messages = await AiocqhttpMessageEvent._parse_onebot_json(MessageChain(chain))
            return list(messages or []), ""
        except Exception as exc:
            return [], self._format_send_exception(exc)

    async def _send_chain_components_via_onebot_direct(
        self,
        umo: str,
        session: MessageSession | None,
        chain: list[Any],
    ) -> tuple[bool, str]:
        if session is None:
            return False, "UMO 无法解析，不能使用 OneBot 原生兜底"
        target_id = _single_line(getattr(session, "session_id", ""), 80)
        if not target_id or not target_id.isdigit():
            return False, f"session_id 不是纯数字，不能使用 OneBot 原生兜底: {target_id or '-'}"
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return False, "没有找到可用的 aiocqhttp/OneBot 客户端"
        messages, parse_error = await self._onebot_messages_from_chain(chain)
        if not messages:
            return False, parse_error or "消息链无法转换为 OneBot 消息段"
        target_value: Any = target_id
        try:
            target_value = int(target_id)
        except Exception:
            pass
        is_group = self._message_type_for_session(session) == MessageType.GROUP_MESSAGE
        action = "send_group_msg" if is_group else "send_private_msg"
        params = {"group_id": target_value, "message": messages} if is_group else {"user_id": target_value, "message": messages}
        ok, error = await self._call_onebot_action_with_error(
            client,
            action,
            at_most_once=True,
            **params,
        )
        if ok:
            logger.info(
                "[PrivateCompanion] 主动消息已通过 OneBot 原生兜底发送: action=%s target=%s segments=%s umo=%s",
                action,
                target_id,
                len(messages),
                _single_line(umo, 140),
            )
            return True, ""
        return False, error or f"OneBot 原生动作 {action} 返回失败"

    async def _send_chain_components(
        self,
        umo: str,
        chain: list[Any],
        *,
        apply_decorating_hooks: bool = True,
    ) -> bool:
        bot_scope_checker = getattr(self, "_bot_scope_allows_umo", None)
        if callable(bot_scope_checker) and not bot_scope_checker(umo):
            logger.info(
                "[PrivateCompanion] Bot 作用域已跳过后台投递: umo=%s",
                _single_line(umo, 140),
            )
            return False
        chain_redactor = getattr(self, "_redact_outbound_chain_secrets", None)
        if callable(chain_redactor):
            chain, redacted = chain_redactor(chain)
            if redacted:
                logger.error("[PrivateCompanion] 主动发送前检测到敏感凭据并已脱敏: umo=%s stage=before_hooks", _single_line(umo, 120))
        hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(chain))
        if hit:
            logger.warning(
                "[PrivateCompanion] 主动待发送消息命中违禁词，已拦截发送: umo=%s word=%s",
                umo,
                _single_line(hit, 40),
            )
            notifier = getattr(self, "_schedule_reply_interception_forward", None)
            if callable(notifier):
                notifier(
                    "proactive_block",
                    source="主动发送组件校验",
                    reason=f"命中违禁词：{_single_line(hit, 40)}",
                    source_session=umo,
                    before=self._chain_text_for_forbidden_recall(chain),
                )
            return False
        processed_chain = (
            await self._trigger_proactive_decorating_hooks(umo, chain)
            if apply_decorating_hooks
            else list(chain)
        )
        if not processed_chain:
            notifier = getattr(self, "_schedule_reply_interception_forward", None)
            if callable(notifier):
                notifier(
                    "proactive_block",
                    source="主动发送装饰钩子",
                    reason="装饰钩子清空了待发送消息",
                    source_session=umo,
                    before=self._chain_text_for_forbidden_recall(chain),
                )
            return False
        if callable(chain_redactor):
            processed_chain, redacted = chain_redactor(processed_chain)
            if redacted:
                logger.error("[PrivateCompanion] 主动装饰后检测到敏感凭据并已脱敏: umo=%s stage=after_hooks", _single_line(umo, 120))
        tts_chain_guard = getattr(self, "_sanitize_outbound_tts_chain_without_event", None)
        if callable(tts_chain_guard):
            processed_chain = await tts_chain_guard(processed_chain, umo=umo)
            if not processed_chain:
                notifier = getattr(self, "_schedule_reply_interception_forward", None)
                if callable(notifier):
                    notifier("proactive_block", source="主动发送 TTS 校验", reason="TTS 校验清空了待发送消息", source_session=umo)
                return False
        hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(processed_chain))
        if hit:
            logger.warning(
                "[PrivateCompanion] 主动装饰后消息命中违禁词，已拦截发送: umo=%s word=%s",
                umo,
                _single_line(hit, 40),
            )
            notifier = getattr(self, "_schedule_reply_interception_forward", None)
            if callable(notifier):
                notifier(
                    "proactive_block",
                    source="主动装饰后校验",
                    reason=f"装饰后命中违禁词：{_single_line(hit, 40)}",
                    source_session=umo,
                    before=self._chain_text_for_forbidden_recall(processed_chain),
                )
            return False
        session = self._parse_message_session(umo)
        platform = self._get_platform_for_session(session) if session else None
        precise_error: Exception | None = None
        if runtime_persona_setting(self, "enable_precise_platform_send", True) and session and platform:
            status = getattr(platform, "status", None)
            if status is not None and status != PlatformStatus.RUNNING:
                logger.warning("[PrivateCompanion] 目标平台未运行,跳过主动发送: %s", umo)
                notifier = getattr(self, "_schedule_reply_interception_forward", None)
                if callable(notifier):
                    notifier(
                        "proactive_block",
                        source="主动发送平台校验",
                        reason="目标平台未运行",
                        source_session=umo,
                        before=self._chain_text_for_forbidden_recall(processed_chain),
                    )
                raise RuntimeError(f"目标平台未运行，无法发送主动消息: {_single_line(umo, 140)}")
            try:
                session_obj = self._session_for_platform(session, platform)
                precise_result = await platform.send_by_session(session_obj, MessageChain(processed_chain))
                if precise_result is not False:
                    self._confirm_outbound_delivery(umo, processed_chain)
                    return True
                precise_error = RuntimeError("精确平台发送返回 False（平台未接受消息）")
                logger.warning(
                    "[PrivateCompanion] 精确平台发送未被目标平台接受,回退核心发送: target=%s",
                    self._describe_send_target(umo, session, platform),
                )
            except Exception as e:
                precise_error = e
                if self._is_onebot_event_checker_send_rejection(e):
                    summary = self._onebot_event_checker_rejection_summary()
                    logger.info(
                        "[PrivateCompanion] 主动发送被 QQ/NTQQ 底层拒绝，停止对同一 sendMsg 链路的立即重复尝试: target=%s",
                        self._describe_send_target(umo, session, platform),
                    )
                    raise RuntimeError(summary) from e
                if self._delivery_outcome_is_uncertain(e):
                    logger.warning(
                        "[PrivateCompanion] 精确平台发送回执不确定，为避免同一主动消息立即重复发送，本次按已提交处理: target=%s error=%s",
                        self._describe_send_target(umo, session, platform),
                        self._format_send_exception(e),
                    )
                    self._confirm_outbound_delivery(umo, processed_chain)
                    return True
                logger.warning(
                    "[PrivateCompanion] 精确平台发送失败,回退核心发送: target=%s error=%s",
                    self._describe_send_target(umo, session, platform),
                    self._format_send_exception(e),
                )
        core_error: Exception | None = None
        core_result: Any = None
        core_session: str | MessageSession = umo
        if session and platform:
            core_session = self._session_for_platform(session, platform)
        try:
            core_result = await self.context.send_message(core_session, self._build_result_from_chain(processed_chain))
            if core_result is not False:
                self._confirm_outbound_delivery(umo, processed_chain)
                return True
            platform_supports = getattr(self, "_platform_supports", None)
            if not callable(platform_supports) or platform_supports("onebot_actions", umo=umo):
                logger.warning(
                    "[PrivateCompanion] 主动核心发送未找到匹配平台,尝试 OneBot 原生兜底: target=%s",
                    self._describe_send_target(umo, session, platform),
                )
            else:
                logger.warning(
                    "[PrivateCompanion] 主动核心发送未被官方平台接受,不使用 OneBot 原生兜底: target=%s",
                    self._describe_send_target(umo, session, platform),
                )
        except Exception as e:
            core_error = e
            if self._is_onebot_event_checker_send_rejection(e):
                logger.info(
                    "[PrivateCompanion] 主动核心发送被 QQ/NTQQ 底层拒绝，停止同链立即重试: target=%s",
                    self._describe_send_target(umo, session, platform),
                )
                raise RuntimeError(self._onebot_event_checker_rejection_summary()) from e
            if self._delivery_outcome_is_uncertain(e):
                logger.warning(
                    "[PrivateCompanion] 主动核心发送回执不确定，为避免 OneBot 兜底重复发送，本次按已提交处理: target=%s error=%s",
                    self._describe_send_target(umo, session, platform),
                    self._format_send_exception(e),
                )
                self._confirm_outbound_delivery(umo, processed_chain)
                return True
            target = self._describe_send_target(umo, session, platform)
            precise_text = self._format_send_exception(precise_error) or "未尝试或未失败"
            fallback_text = self._format_send_exception(e)
            logger.warning(
                "[PrivateCompanion] 主动核心发送失败: target=%s precise_error=%s fallback_error=%s",
                target,
                precise_text,
                fallback_text,
            )
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("onebot_actions", umo=umo):
            target = self._describe_send_target(umo, session, platform)
            precise_text = self._format_send_exception(precise_error) or "未尝试或未失败"
            fallback_text = self._format_send_exception(core_error) if core_error is not None else (
                "AstrBot 核心发送返回 False（平台未找到或官方通道拒绝）"
            )
            raise RuntimeError(
                f"主动消息发送失败: {target}; precise={precise_text}; fallback={fallback_text}; 当前平台不使用 OneBot 原生兜底"
            ) from core_error
        direct_ok, direct_error = await self._send_chain_components_via_onebot_direct(umo, session, processed_chain)
        if direct_ok:
            self._confirm_outbound_delivery(umo, processed_chain)
            return True
        if self._is_onebot_event_checker_send_rejection(direct_error):
            raise RuntimeError(self._onebot_event_checker_rejection_summary())
        target = self._describe_send_target(umo, session, platform)
        precise_text = self._format_send_exception(precise_error) or "未尝试或未失败"
        if core_error is not None:
            fallback_text = self._format_send_exception(core_error)
        elif core_result is False:
            fallback_text = "AstrBot 核心发送返回 False（未找到匹配平台或平台拒绝发送）"
        else:
            fallback_text = "未尝试或未失败"
        logger.warning(
            "[PrivateCompanion] 主动发送兜底也失败: target=%s precise_error=%s fallback_error=%s direct_error=%s",
            target,
            precise_text,
            fallback_text,
            direct_error,
        )
        raise RuntimeError(
            f"主动消息发送失败: {target}; precise={precise_text}; fallback={fallback_text}; direct={direct_error}"
        ) from core_error

    async def _send_media_proactive_chain(
        self,
        umo: str,
        text: str,
        image_path: str = "",
        *,
        extra_components: list[Any] | None = None,
        quote_message_id: str = "",
        disable_segmenting: bool = False,
        media_delivery_mode: str = "separate_after",
        require_complete_text_before_media: bool = False,
    ) -> _ProactiveSendOutcome:
        trigger_message_id = _single_line(quote_message_id, 120)
        delivered_segments: list[str] = []
        complete = True
        image_delivered = False
        extra_components_delivered = 0
        primary_complete = False
        failure_note = ""

        def outcome(*, note: str = "") -> _ProactiveSendOutcome:
            delivered_text = "\n".join(item for item in delivered_segments if item).strip()
            delivered = bool(delivered_text or image_delivered or extra_components_delivered)
            resolved_note = _single_line(note or failure_note, 240)
            return _ProactiveSendOutcome(
                delivered=delivered,
                complete=bool(delivered and complete and not resolved_note),
                delivered_text=delivered_text,
                image_delivered=image_delivered,
                extra_components_delivered=extra_components_delivered,
                note=resolved_note,
                primary_complete=primary_complete,
            )

        outbound_components = [
            component for component in (extra_components or []) if component is not None
        ]
        has_prebuilt_voice = any(
            isinstance(component, Record) for component in outbound_components
        )
        if self._contains_inline_image_tag(text):
            image_path = ""
            outbound_components = []
        if text:
            await self._maybe_send_input_status(umo, text)
        if media_delivery_mode == "same_message":
            platform_supports = getattr(self, "_platform_supports", None)
            platform_quote = not callable(platform_supports) or platform_supports(
                "reply_quote",
                umo=umo,
            )
            if quote_message_id and not platform_quote:
                logger.info(
                    "[PrivateCompanion] 当前平台不支持主动引用，正文与表情同链发送已降级为普通发送: umo=%s",
                    _single_line(umo, 140),
                )
                quote_message_id = ""
            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(
                trigger_message_id
            )
            if recalled_message_id:
                logger.info(
                    "[PrivateCompanion] 触发消息已撤回，取消主动正文与表情同链发送: umo=%s message_id=%s",
                    umo,
                    recalled_message_id,
                )
                complete = False
                return outcome(note="触发消息已撤回")
            combined_chain = self._build_outbound_chain(
                text,
                image_path,
                extra_components=outbound_components,
            )
            combined_chain = self._with_optional_reply(
                combined_chain,
                quote_message_id,
            )
            sent = await self._send_chain_components(umo, combined_chain)
            if sent:
                delivered_segments.append(text)
                image_delivered = bool(image_path and os.path.exists(image_path))
                extra_components_delivered = len(outbound_components)
                primary_complete = True
            else:
                complete = False
            return outcome(
                note="" if sent else "主动正文与表情同链发送未被平台接受"
            )
        platform_supports = getattr(self, "_platform_supports", None)
        platform_segmented = not callable(platform_supports) or platform_supports("segmented_reply", umo=umo)
        platform_quote = not callable(platform_supports) or platform_supports("reply_quote", umo=umo)
        if quote_message_id and not platform_quote:
            logger.info(
                "[PrivateCompanion] 当前平台不支持主动引用，已降级为普通发送: umo=%s",
                _single_line(umo, 140),
            )
            quote_message_id = ""
        segments = self._split_proactive_text(
            text,
            umo=umo,
            image_path="",
            extra_components=None,
            disable_segmenting=disable_segmenting or not platform_segmented or not self._segmented_scope_allows_umo(umo),
        )
        if len(segments) > 1:
            logger.info(
                "[PrivateCompanion] 主动媒体文本已分段: umo=%s segments=%s lengths=%s",
                _single_line(umo, 140),
                len(segments),
                [len(segment) for segment in segments],
            )
        if quote_message_id and segments and self._quote_skip_reason_for_short_reply(segments[0]):
            quote_message_id = ""

        image_exists = bool(image_path and os.path.exists(image_path))
        path_image_component: Any | None = None
        if image_exists:
            image_chain = self._build_outbound_chain("", image_path)
            path_image_component = next(
                (component for component in image_chain if isinstance(component, Image)),
                None,
            )
            image_exists = path_image_component is not None

        leading_components: list[Any] = []
        trailing_components: list[Any] = []
        for component in outbound_components:
            if component_kind(component) in {"voice", "at", "reply"}:
                leading_components.append(component)
            else:
                trailing_components.append(component)

        source_chain: list[Any] = list(leading_components)
        if segments:
            source_chain.append(Plain(text))
        source_chain.extend(trailing_components)
        if path_image_component is not None:
            source_chain.append(path_image_component)
        if quote_message_id:
            source_chain = self._with_optional_reply(source_chain, quote_message_id)

        strategies = component_strategies_from_owner(self)
        strategies["reaction"] = (
            "inline" if media_delivery_mode == "same_message" else "separate"
        )
        chunks, _changed, _split_changed, _full_text = plan_component_chunks(
            source_chain,
            plain_type=Plain,
            split_text=lambda _value: list(segments),
            strategies=strategies,
            component_order=component_order_from_owner(self),
            classify=component_kind,
        )

        primary_components: list[Plain] = []
        for chunk in chunks:
            for component_index, component in enumerate(chunk):
                if not isinstance(component, Plain) or len(primary_components) >= len(segments):
                    continue
                segment_index = len(primary_components)
                segment_component = self._proactive_plain_segment_component(
                    segments[segment_index],
                    full_text=text,
                    index=segment_index,
                    count=len(segments),
                    suppress_tts=has_prebuilt_voice,
                )
                try:
                    object.__setattr__(
                        segment_component,
                        "_private_companion_proactive_primary_text",
                        True,
                    )
                except Exception:
                    pass
                chunk[component_index] = segment_component
                primary_components.append(segment_component)

        has_media = bool(outbound_components or image_exists)
        if has_media:
            logger.info(
                "[PrivateCompanion] 主动媒体已按组件策略规划: text_segments=%s chunks=%s image=%s extra_components=%s strategies=%s",
                len(segments),
                len(chunks),
                image_exists,
                len(outbound_components),
                strategies,
            )
        if not chunks:
            complete = False
            return outcome(note="主动正文与媒体均为空")

        remaining_extra_components = list(outbound_components)
        delivered_primary_count = 0

        def chunk_primary_texts(chunk: list[Any]) -> list[str]:
            return [
                str(getattr(component, "text", "") or "").strip()
                for component in chunk
                if isinstance(component, Plain)
                and bool(
                    getattr(
                        component,
                        "_private_companion_proactive_primary_text",
                        False,
                    )
                )
                and str(getattr(component, "text", "") or "").strip()
            ]

        for chunk_index, chunk in enumerate(chunks):
            primary_texts = chunk_primary_texts(chunk)
            chunk_has_reaction = any(
                component_kind(component) == "reaction" for component in chunk
            )
            primary_complete = bool(
                segments and delivered_primary_count >= len(segments)
            )
            if (
                require_complete_text_before_media
                and chunk_has_reaction
                and not primary_complete
            ):
                complete = False
                return outcome(note="主动正文未完整送达，已跳过表情图片")

            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(
                trigger_message_id
            )
            if recalled_message_id:
                logger.info(
                    "[PrivateCompanion] 触发消息已撤回，停止主动组件发送: umo=%s message_id=%s chunk=%s/%s",
                    umo,
                    recalled_message_id,
                    chunk_index + 1,
                    len(chunks),
                )
                complete = False
                return outcome(note=f"第 {chunk_index + 1} 条发送前触发消息已撤回")

            try:
                sent = await self._send_chain_components(umo, chunk)
            except Exception as exc:
                has_delivered_content = bool(
                    delivered_segments or image_delivered or extra_components_delivered
                )
                has_future_primary = any(
                    chunk_primary_texts(candidate)
                    for candidate in chunks[chunk_index + 1 :]
                )
                if not primary_texts and has_future_primary:
                    complete = False
                    failure_note = failure_note or (
                        f"第 {chunk_index + 1} 条组件发送失败：{_single_line(exc, 160)}"
                    )
                    logger.warning(
                        "[PrivateCompanion] 主动前置组件发送失败，继续发送正文: umo=%s chunk=%s error=%s",
                        _single_line(umo, 140),
                        chunk_index + 1,
                        _single_line(exc, 180),
                    )
                    continue
                if not has_delivered_content:
                    raise
                complete = False
                logger.warning(
                    "[PrivateCompanion] 主动组件部分送达后后续发送失败，不再整条重试: umo=%s chunk=%s error=%s",
                    _single_line(umo, 140),
                    chunk_index + 1,
                    _single_line(exc, 180),
                )
                return outcome(
                    note=f"第 {chunk_index + 1} 条发送失败：{_single_line(exc, 160)}"
                )

            if not sent:
                complete = False
                failure_note = failure_note or f"第 {chunk_index + 1} 条未被平台接受"
                continue

            if primary_texts:
                delivered_segments.extend(primary_texts)
                delivered_primary_count += len(primary_texts)
            if path_image_component is not None and any(
                component is path_image_component for component in chunk
            ):
                image_delivered = True
            for sent_component in chunk:
                matched_index = next(
                    (
                        index
                        for index, candidate in enumerate(remaining_extra_components)
                        if sent_component is candidate
                    ),
                    -1,
                )
                if matched_index >= 0:
                    remaining_extra_components.pop(matched_index)
                    extra_components_delivered += 1

            primary_complete = bool(
                segments and delivered_primary_count >= len(segments)
            )
            if primary_texts and any(
                chunk_primary_texts(candidate)
                for candidate in chunks[chunk_index + 1 :]
            ):
                await asyncio.sleep(
                    await self._calc_segmented_proactive_interval(primary_texts[-1], umo=umo)
                )

        primary_complete = bool(
            segments and delivered_primary_count >= len(segments)
        )
        return outcome()

    @staticmethod
    def _normalize_reaction_expression_delivery_mode(value: Any) -> str:
        mode = str(value or "separate_after").strip().lower().replace("-", "_")
        aliases = {
            "after": "separate_after",
            "separate": "separate_after",
            "separate_after_text": "separate_after",
            "inline": "same_message",
            "current_chain": "same_message",
            "same_chain": "same_message",
            "before": "separate_before",
            "separate_before_text": "separate_before",
        }
        normalized = aliases.get(mode, mode)
        if normalized in {"separate_after", "same_message", "separate_before"}:
            return normalized
        return "separate_after"

    def _build_proactive_reaction_event(
        self,
        *,
        umo: str,
        user_id: str,
        visible_text: str,
    ) -> Any:
        extras: dict[str, Any] = {}
        event = SimpleNamespace(
            unified_msg_origin=umo,
            message_str=visible_text,
            extras=extras,
        )
        event.get_sender_id = lambda: user_id
        event.get_message_str = lambda: visible_text
        event.is_private_chat = lambda: True
        event.get_extra = lambda key: extras.get(key)
        event.set_extra = lambda key, value: extras.__setitem__(key, value)
        return event

    async def _prepare_proactive_reaction_attachment(
        self,
        umo: str,
        visible_text: str,
    ) -> tuple[Any | None, dict[str, Any] | None]:
        entry = self._pop_proactive_reaction_intent(umo)
        intent = entry.get("intent") if isinstance(entry.get("intent"), dict) else {}
        user_id = _single_line(entry.get("user_id"), 160)
        if (
            not intent
            or not user_id
            or not self._proactive_reaction_expression_enabled("message")
        ):
            return None, None
        visible_checker = getattr(self, "_reaction_expression_has_visible_text", None)
        if callable(visible_checker) and not visible_checker(visible_text):
            return None, None

        event = self._build_proactive_reaction_event(
            umo=_single_line(umo, 240),
            user_id=user_id,
            visible_text=str(visible_text or ""),
        )
        preauthorize = getattr(self, "_preauthorize_reaction_expression_prompt", None)
        prepare = getattr(self, "_pc_reaction_expression_impl", None)
        settle = getattr(self, "_settle_reaction_expression_attachment_data", None)
        if not callable(preauthorize) or not callable(prepare) or not callable(settle):
            return None, None
        try:
            if not await preauthorize(event):
                return None, None
            raw_prepared = await prepare(
                event,
                query=_single_line(intent.get("provider_query"), 500),
                context=_single_line(intent.get("context"), 1000)
                or _single_line(visible_text, 700),
                meme_only=True,
                send=True,
                purpose=_single_line(intent.get("purpose"), 120),
                emotion=_single_line(intent.get("emotion"), 80),
                intensity=_safe_int(intent.get("intensity"), 0, 0, 5),
                candidate_queries=intent.get("candidate_queries", []),
                attach_only=True,
            )
            prepared = json.loads(raw_prepared)
        except Exception as exc:
            pending = getattr(
                event,
                "_private_companion_reaction_expression_pending_attachment",
                None,
            )
            if isinstance(pending, dict):
                await settle(pending, sent=False, reason="attachment_prepare_failed")
            logger.warning(
                "[PrivateCompanion] 主动表情附件准备失败,继续发送纯文字: error_type=%s",
                type(exc).__name__,
            )
            return None, None
        if not isinstance(prepared, dict) or prepared.get("decision") != "attach":
            return None, None

        pending = getattr(
            event,
            "_private_companion_reaction_expression_pending_attachment",
            None,
        )
        image_path = _path_text(prepared.get("path"), 1000)
        if not isinstance(pending, dict) or not image_path or not os.path.isfile(image_path):
            if isinstance(pending, dict):
                await settle(pending, sent=False, reason="attachment_file_missing")
            return None, None
        try:
            builder = getattr(self, "_build_reaction_image_component", None)
            if callable(builder):
                image_component = builder(event, image_path)
            else:
                try:
                    image_component = Image.fromFileSystem(image_path)
                except AttributeError:
                    image_component = Image.from_file_system(image_path)
        except Exception as exc:
            await settle(pending, sent=False, reason="attachment_component_failed")
            logger.warning(
                "[PrivateCompanion] 主动表情图片组件构建失败,继续发送纯文字: error_type=%s",
                type(exc).__name__,
            )
            return None, None

        pending["attached"] = True
        pending["component"] = image_component
        runtime_logger = getattr(self, "_log_reaction_expression_event", None)
        if callable(runtime_logger):
            runtime_logger(
                event,
                stage="attachment",
                decision="accepted",
                reason="attachment_appended",
                scope="private",
                found=True,
                sent=False,
                image_id=prepared.get("image_id"),
                confidence=prepared.get("confidence"),
                cache_hit=prepared.get("cache_hit"),
                latency_ms=prepared.get("lookup_latency_ms"),
                match_basis=pending.get("match_basis"),
            )
        return image_component, pending

    async def _settle_proactive_reaction_attachment(
        self,
        pending: dict[str, Any] | None,
        *,
        sent: bool,
        reason: str,
    ) -> None:
        if not isinstance(pending, dict):
            return
        settle = getattr(self, "_settle_reaction_expression_attachment_data", None)
        if not callable(settle):
            return
        try:
            await settle(pending, sent=sent, reason=reason)
        except Exception as exc:
            # Delivery state is authoritative. A bookkeeping failure must not
            # make the caller retry content that the platform already received.
            logger.warning(
                "[PrivateCompanion] 主动表情发送结算失败,不改变消息投递结果: "
                "sent=%s reason=%s error_type=%s",
                bool(sent),
                _single_line(reason, 80),
                type(exc).__name__,
            )

    async def _proactive_persona_delivery_allowed(self, target_umo: str) -> bool:
        """Revalidate the scheduled persona immediately before platform I/O."""
        validator = getattr(self, "_validate_proactive_persona_delivery", None)
        if not callable(validator):
            return True

        multi_persona = bool(getattr(self, "enable_multi_persona_mode", False))
        active_getter = getattr(self, "_active_persona_scope", None)
        scheduled_persona_id = ""
        if callable(active_getter):
            try:
                scheduled_persona_id = str(active_getter() or "").strip()
            except Exception:
                scheduled_persona_id = ""
        if not multi_persona and not scheduled_persona_id:
            effective_getter = getattr(self, "_effective_plugin_persona_id", None)
            if callable(effective_getter):
                try:
                    scheduled_persona_id = str(effective_getter() or "").strip()
                except Exception:
                    scheduled_persona_id = ""
        if not multi_persona and not scheduled_persona_id:
            primary_getter = getattr(self, "_primary_persona_id", None)
            try:
                scheduled_persona_id = str(
                    primary_getter()
                    if callable(primary_getter)
                    else getattr(self, "plugin_specific_persona_id", "")
                ).strip()
            except Exception:
                scheduled_persona_id = ""

        try:
            result = validator(target_umo, scheduled_persona_id)
            if inspect.isawaitable(result):
                result = await result
            allowed = bool(result.get("ok")) if isinstance(result, dict) else bool(result)
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 主动消息最终人格一致性校验失败: multi=%s persona=%s umo=%s error=%s",
                multi_persona,
                _single_line(scheduled_persona_id, 96) or "-",
                _single_line(target_umo, 140) or "-",
                _single_line(exc, 160),
            )
            return not multi_persona

        if allowed:
            return True
        reason_code = _single_line(result.get("reason_code"), 80) if isinstance(result, dict) else ""
        action = _single_line(result.get("action"), 40) if isinstance(result, dict) else ""
        logger.warning(
            "[PrivateCompanion] 主动投递因人格不一致被取消: multi=%s persona=%s umo=%s action=%s reason=%s",
            multi_persona,
            _single_line(scheduled_persona_id, 96) or "-",
            _single_line(target_umo, 140) or "-",
            action or "blocked",
            reason_code or "validator_rejected",
        )
        return False

    @collect_proactive_delivery
    async def _send_proactive_message_chain(
        self,
        umo: str,
        text: str,
        image_path: str = "",
        *,
        extra_components: list[Any] | None = None,
        quote_message_id: str = "",
        disable_segmenting: bool = False,
    ) -> _ProactiveSendOutcome:
        if not await self._proactive_persona_delivery_allowed(umo):
            return _ProactiveSendOutcome(False, False, note="主动投递人格已变化，已取消发送")
        # Recheck at the final delivery boundary. A realtime call may start
        # after a proactive candidate was planned but before it is sent.
        busy_context_getter = getattr(self, "_busy_reply_proactive_block_context", None)
        if callable(busy_context_getter):
            try:
                busy_context = busy_context_getter({}, now=_now_ts())
            except TypeError:
                busy_context = busy_context_getter({}, now=_now_ts(), umo=umo)
            except Exception:
                busy_context = {}
            if isinstance(busy_context, dict) and busy_context.get("kind") == "external_realtime":
                logger.info(
                    "[PrivateCompanion] 实时共同活动期间在最终发送边界取消主动消息: umo=%s",
                    _single_line(umo, 140),
                )
                return _ProactiveSendOutcome(False, False, note="实时共同活动期间已取消主动消息")
        trigger_message_id = _single_line(quote_message_id, 120)
        placeholder_cleaner = getattr(self, "_sanitize_orphan_tts_placeholders", None)
        if callable(placeholder_cleaner):
            cleaned_text = placeholder_cleaner(text)
            if cleaned_text != text:
                logger.warning(
                    "[PrivateCompanion] 主动发送前清理孤儿 TTS 占位符: umo=%s before=%s after=%s",
                    _single_line(umo, 120),
                    _single_line(text, 120),
                    _single_line(cleaned_text, 120),
                )
                text = cleaned_text
        reaction_pending: dict[str, Any] | None = None
        reaction_delivery_mode = self._normalize_reaction_expression_delivery_mode(
            runtime_persona_setting(self, "reaction_expression_delivery_mode", "separate_after")
        )
        has_existing_media = bool(
            image_path
            or extra_components
            or (text and self._contains_inline_image_tag(text))
        )
        if has_existing_media:
            self._clear_proactive_reaction_intent(umo)
        else:
            reaction_component, reaction_pending = await self._prepare_proactive_reaction_attachment(
                umo,
                text,
            )
            if reaction_component is not None:
                try:
                    object.__setattr__(
                        reaction_component,
                        "_private_companion_reaction_expression",
                        True,
                    )
                except Exception:
                    pass
                if isinstance(reaction_pending, dict):
                    reaction_pending["delivery_mode"] = reaction_delivery_mode
                if reaction_delivery_mode == "separate_before":
                    try:
                        reaction_sent = bool(
                            await self._send_chain_components(
                                umo,
                                [reaction_component],
                            )
                        )
                    except Exception as exc:
                        reaction_sent = False
                        logger.warning(
                            "[PrivateCompanion] 主动表情先行发送失败，继续发送正文: "
                            "umo=%s error_type=%s",
                            _single_line(umo, 140),
                            type(exc).__name__,
                        )
                    await self._settle_proactive_reaction_attachment(
                        reaction_pending,
                        sent=reaction_sent,
                        reason="delivered" if reaction_sent else "delivery_failed",
                    )
                    reaction_pending = None
                else:
                    extra_components = [reaction_component]
        if has_existing_media or image_path or extra_components:
            try:
                outcome = await self._send_media_proactive_chain(
                    umo,
                    text,
                    image_path,
                    extra_components=extra_components,
                    quote_message_id=quote_message_id,
                    disable_segmenting=disable_segmenting,
                    media_delivery_mode=(
                        reaction_delivery_mode
                        if reaction_pending is not None
                        else "separate_after"
                    ),
                    require_complete_text_before_media=bool(
                        reaction_pending is not None
                        and reaction_delivery_mode == "separate_after"
                    ),
                )
            except Exception:
                await self._settle_proactive_reaction_attachment(
                    reaction_pending,
                    sent=False,
                    reason=(
                        "primary_not_delivered"
                        if reaction_pending is not None
                        and reaction_delivery_mode == "separate_after"
                        else "delivery_failed"
                    ),
                )
                raise
            if reaction_pending is not None:
                reaction_sent = bool(outcome.extra_components_delivered)
                settlement_reason = (
                    "delivered"
                    if reaction_sent
                    else "primary_not_delivered"
                    if reaction_delivery_mode == "separate_after"
                    and not outcome.primary_complete
                    else "delivery_failed"
                )
                await self._settle_proactive_reaction_attachment(
                    reaction_pending,
                    sent=reaction_sent,
                    reason=settlement_reason,
                )
            return outcome
        if text:
            await self._maybe_send_input_status(umo, text)
        segments = self._split_proactive_text(
            text,
            umo=umo,
            image_path="",
            extra_components=None,
            disable_segmenting=disable_segmenting or not self._segmented_scope_allows_umo(umo),
        )
        if len(segments) > 1:
            logger.info(
                "[PrivateCompanion] 主动文本已分段: umo=%s segments=%s lengths=%s",
                _single_line(umo, 140),
                len(segments),
                [len(segment) for segment in segments],
            )
        if len(segments) <= 1:
            outbound_text = segments[0] if segments else text
            if not str(outbound_text or "").strip():
                return _ProactiveSendOutcome(False, False, note="主动正文为空")
            if quote_message_id and self._quote_skip_reason_for_short_reply(outbound_text):
                quote_message_id = ""
            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
            if recalled_message_id:
                logger.info("[PrivateCompanion] 触发消息已撤回，取消主动消息发送: umo=%s message_id=%s", umo, recalled_message_id)
                return _ProactiveSendOutcome(False, False, note="触发消息已撤回")
            sent = await self._send_chain_components(
                umo,
                self._with_optional_reply(
                    [
                        self._proactive_plain_segment_component(outbound_text, full_text=text, index=0, count=1)
                    ],
                    quote_message_id,
                ),
            )
            return _ProactiveSendOutcome(
                delivered=bool(sent),
                complete=bool(sent),
                delivered_text=outbound_text if sent else "",
                note="" if sent else "主动发送组件被取消或清空",
            )
        recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
        if recalled_message_id:
            logger.info("[PrivateCompanion] 触发消息已撤回，取消主动合并分段发送: umo=%s message_id=%s", umo, recalled_message_id)
            return _ProactiveSendOutcome(False, False, note="触发消息已撤回")
        if await self._send_segmented_proactive_forward_message(umo, segments, source="proactive_text"):
            return _ProactiveSendOutcome(True, True, delivered_text="\n".join(segments).strip())
        delivered_segments: list[str] = []
        complete = True
        for index, segment in enumerate(segments):
            if index == 0 and quote_message_id and self._quote_skip_reason_for_short_reply(segment):
                quote_message_id = ""
            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
            if recalled_message_id:
                logger.info("[PrivateCompanion] 触发消息已撤回，停止主动消息分段发送: umo=%s message_id=%s index=%s", umo, recalled_message_id, index + 1)
                return _ProactiveSendOutcome(
                    bool(delivered_segments),
                    False,
                    delivered_text="\n".join(delivered_segments).strip(),
                    note=f"第 {index + 1} 段发送前触发消息已撤回",
                )
            segment_comp = self._proactive_plain_segment_component(segment, full_text=text, index=index, count=len(segments))
            chain = self._with_optional_reply([segment_comp], quote_message_id) if index == 0 else [segment_comp]
            try:
                sent = await self._send_chain_components(umo, chain)
            except Exception as exc:
                if not delivered_segments:
                    raise
                logger.warning(
                    "[PrivateCompanion] 主动文本部分送达后后续分段失败，不再整条重试: umo=%s index=%s error=%s",
                    _single_line(umo, 140),
                    index + 1,
                    _single_line(exc, 180),
                )
                return _ProactiveSendOutcome(
                    True,
                    False,
                    delivered_text="\n".join(delivered_segments).strip(),
                    note=f"第 {index + 1} 段发送失败：{_single_line(exc, 160)}",
                )
            if sent:
                delivered_segments.append(segment)
            else:
                complete = False
            quote_message_id = ""
            if index < len(segments) - 1:
                try:
                    interval = await self._calc_segmented_proactive_interval(segment, umo=umo)
                except TypeError:
                    interval = await self._calc_segmented_proactive_interval(segment)
                await asyncio.sleep(interval)
        delivered_text = "\n".join(delivered_segments).strip()
        return _ProactiveSendOutcome(
            delivered=bool(delivered_text),
            complete=bool(delivered_text and complete),
            delivered_text=delivered_text,
            note="" if complete else "部分分段被发送钩子取消或清空",
        )

    def _build_outbound_result(
        self,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
    ) -> Any:
        chain = self._build_outbound_chain(text, image_path, extra_components=extra_components)
        return self._build_result_from_chain(chain)

    def _build_proactive_archive_user_prompt(
        self,
        *,
        reason: str,
        action: str,
        motive: str = "",
        action_summary: str = "",
    ) -> str:
        # AstrBot history is stored as user/assistant pairs, so proactive sends
        # need a tiny synthetic user side. Keep it neutral: internal reason,
        # motive and action details stay in plugin state instead of visible chat.
        return "【主动承接占位】用户还没发来新消息；下一条是 Bot 主动发出的内容。后续如果用户回应，顺着上一条主动消息自然接住就好。"

    @staticmethod
    def _proactive_component_is_image(component: Any) -> bool:
        return isinstance(component, Image) or bool(
            getattr(component, "_private_companion_reaction_expression", False)
        )

    @staticmethod
    def _proactive_components_contain_image(components: list[Any] | None) -> bool:
        return any(
            ProactiveMessageMixin._proactive_component_is_image(component)
            for component in (components or [])
        )

    def _build_actual_proactive_delivery_summary(
        self,
        *,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        original_summary: str = "",
    ) -> str:
        parts: list[str] = []
        visible_text = self._visible_text_without_tts_reading(text, limit=320)
        if visible_text:
            parts.append(f"文字消息：{visible_text}")

        image_count = int(bool(image_path)) + sum(
            1
            for component in (extra_components or [])
            if self._proactive_component_is_image(component)
        )
        if image_count:
            photo_caption = ""
            if "：" in str(original_summary or "") or ":" in str(original_summary or ""):
                photo_caption = _single_line(
                    re.split(r"[:：]", str(original_summary), maxsplit=1)[-1],
                    220,
                )
            image_label = "图片" if image_count == 1 else f"{image_count} 张图片"
            if photo_caption and photo_caption not in {"发图", "图片", "photo_text"}:
                parts.append(f"{image_label}：{photo_caption}")
            else:
                parts.append(f"{image_label}已发送")

        voice_count = sum(
            1 for component in (extra_components or []) if isinstance(component, Record)
        )
        if voice_count:
            parts.append("语音消息已发送" if voice_count == 1 else f"{voice_count} 条语音消息已发送")

        other_count = sum(
            1
            for component in (extra_components or [])
            if not self._proactive_component_is_image(component)
            and not isinstance(component, Record)
        )
        if other_count:
            parts.append(f"{other_count} 个附加消息组件已发送")
        return _single_line("；".join(parts), 500)

    def _reconcile_proactive_delivery_metadata(
        self,
        *,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        action: str = "message",
        action_summary: str = "",
        delivery_complete: bool = True,
    ) -> tuple[str, str, bool]:
        delivered_photo = bool(image_path) or self._proactive_components_contain_image(extra_components)
        if delivery_complete:
            return action or "message", action_summary, delivered_photo

        action_parts = [part.strip() for part in str(action or "").split("+") if part.strip()]
        removed_media = False
        if not delivered_photo and "photo_text" in action_parts:
            action_parts = [part for part in action_parts if part != "photo_text"]
            removed_media = True
        delivered_voice = any(isinstance(component, Record) for component in (extra_components or []))
        if not delivered_voice and "voice" in action_parts:
            action_parts = [part for part in action_parts if part != "voice"]
            removed_media = True
        if removed_media and text and "message" not in action_parts:
            action_parts.insert(0, "message")
        actual_action = "+".join(action_parts) or ("message" if text else action or "message")
        actual_summary = self._build_actual_proactive_delivery_summary(
            text=text,
            image_path=image_path,
            extra_components=extra_components,
            original_summary=action_summary,
        )
        return actual_action, actual_summary or "主动消息仅部分送达。", delivered_photo

    def _build_proactive_archive_assistant_text(
        self,
        *,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        action_summary: str = "",
        photo_subject_owner: str = "",
    ) -> str:
        original_is_receipt = self._is_proactive_delivery_receipt_text(text)
        message_text = self._visible_text_without_tts_reading(text, limit=1000)
        attachment_notes: list[str] = []
        history_image_count = 0
        history_record_count = 0
        if image_path:
            history_image_count += 1
            photo_caption = ""
            if "：" in str(action_summary or "") or ":" in str(action_summary or ""):
                photo_caption = _single_line(re.split(r"[:：]", str(action_summary), maxsplit=1)[-1], 220)
            if photo_caption and photo_caption not in {"发图", "图片", "photo_text"}:
                attachment_notes.append(f"图片画面：{photo_caption}")
            normalized_owner = _normalize_photo_subject_owner(photo_subject_owner)
            if normalized_owner:
                attachment_notes.append(f"图片主体：{_photo_subject_owner_prompt_label(normalized_owner)}")
        if extra_components:
            tts_notes: list[str] = []
            note_builder = getattr(self, "_tts_component_log_note", None)
            image_components = [
                comp
                for comp in extra_components
                if self._proactive_component_is_image(comp)
            ]
            for comp in extra_components:
                if isinstance(comp, Record) and callable(note_builder):
                    note = _single_line(note_builder(comp), 220)
                    if note:
                        tts_notes.append(note)
            if image_components:
                history_image_count += len(image_components)
                photo_caption = ""
                if "：" in str(action_summary or "") or ":" in str(action_summary or ""):
                    photo_caption = _single_line(re.split(r"[:：]", str(action_summary), maxsplit=1)[-1], 220)
                if photo_caption and photo_caption not in {"发图", "图片", "photo_text"}:
                    attachment_notes.append(f"图片画面：{photo_caption}")
                normalized_owner = _normalize_photo_subject_owner(photo_subject_owner)
                if normalized_owner:
                    attachment_notes.append(f"图片主体：{_photo_subject_owner_prompt_label(normalized_owner)}")
            if tts_notes:
                attachment_notes.extend(tts_notes[:3])
            record_count = sum(1 for comp in extra_components if isinstance(comp, Record))
            history_record_count += record_count
            other_count = len(extra_components) - len(image_components) - record_count
            if other_count > 0:
                attachment_notes.append(f"随消息发送了 {other_count} 个附加消息组件")
        if attachment_notes:
            suffix = "（" + ",".join(attachment_notes) + "）"
            message_text = f"{message_text}{suffix}" if message_text else suffix
        media_marker = _format_history_media_marker(
            images=history_image_count,
            records=history_record_count,
        )
        if media_marker:
            message_text = f"{message_text}\n{media_marker}" if message_text else media_marker
        if message_text:
            return message_text
        if original_is_receipt:
            return ""
        return _single_line(action_summary, 160) or "主动向用户发送了一条消息。"

    async def _archive_proactive_message_to_conversation(
        self,
        *,
        user: dict[str, Any],
        user_prompt: str,
        assistant_response: str,
        umo: str = "",
    ) -> bool:
        umo = str(umo or user.get("umo") or "").strip()
        if not umo or not assistant_response:
            return False
        for attempt in range(4):
            try:
                user_msg_obj = UserMessageSegment(content=str(user_prompt or ""))
                assistant_msg_obj = AssistantMessageSegment(content=str(assistant_response or ""))
                async def _write():
                    conv_id = await self._ensure_conversation_id_for_umo(umo, title="Private Companion 主动消息")
                    if not conv_id:
                        return False
                    await self.context.conversation_manager.add_message_pair(
                        cid=conv_id,
                        user_message=user_msg_obj,
                        assistant_message=assistant_msg_obj,
                    )
                    return True

                written = await self._conversation_db_operation("archive_proactive_message", _write)
                if not written:
                    logger.warning("[PrivateCompanion] 主动消息存档失败: 无法获取或创建 AstrBot 会话 history umo=%s", _single_line(umo, 140))
                    return False
                if attempt > 0:
                    logger.info("[PrivateCompanion] 主动消息写入 AstrBot 会话历史成功: %s retry=%s", umo, attempt)
                else:
                    logger.info("[PrivateCompanion] 已将主动消息写入 AstrBot 会话历史: %s", umo)
                return True
            except Exception as e:
                text = str(e or "").lower()
                if ("database is locked" in text or "sqlite3.operationalerror" in text) and attempt < 3:
                    await asyncio.sleep(0.25 * (attempt + 1))
                    continue
                logger.warning("[PrivateCompanion] 主动消息写入会话历史失败: %s", e)
                return False
        return False

    def _format_story_plan_for_prompt(self) -> str:
        plan = self.data.get("daily_story_plan", {})
        if not isinstance(plan, dict) or plan.get("date") != _today_key():
            return "（暂无）"
        lines = []
        now_minutes = self._environment_now_minutes()
        events = plan.get("today_events", [])
        if isinstance(events, list) and events:
            nearby_events = [
                item for item in events
                if isinstance(item, dict) and self._story_item_relevant_to_now(item, now_minutes)
            ][:6]
            if nearby_events:
                lines.append("附近可能发生：")
                for item in nearby_events:
                    lines.append(f"- {item.get('window', '')}｜{item.get('event', '')}｜{item.get('mood', '')}")
        proactive = plan.get("proactive_events", [])
        if isinstance(proactive, list) and proactive:
            nearby_proactive = [
                item for item in proactive
                if isinstance(item, dict) and self._story_item_relevant_to_now(item, now_minutes, future_minutes=240)
            ][:6]
            if nearby_proactive:
                lines.append("附近主动计划：")
                for item in nearby_proactive:
                    lines.append(
                        f"- {item.get('window', '')}｜{item.get('reason', '')}｜{item.get('action', 'message')}｜"
                        f"{item.get('why', '')}｜{item.get('topic', '')}｜{item.get('motive', '')}｜"
                        f"{item.get('scene', '')}｜{item.get('tone', '')}｜{item.get('impulse', '')}"
                    )
        long_term = plan.get("long_term_events", [])
        if isinstance(long_term, list) and long_term:
            lines.append("长线事件：")
            for item in long_term[:4]:
                if isinstance(item, dict):
                    lines.append(
                        f"- {item.get('title', '')}｜{item.get('status', '')}｜"
                        f"{item.get('tendency', '')}｜{item.get('next_hint', '')}"
                    )
        return "\n".join(lines) if lines else "（暂无）"

    def _story_item_relevant_to_now(
        self,
        item: dict[str, Any],
        now_minutes: int,
        *,
        past_minutes: int = 90,
        future_minutes: int = 180,
    ) -> bool:
        start, end = self._parse_window_minutes(str(item.get("window") or ""))
        if start is None or end is None:
            return False
        candidates = [(start, end)]
        if end < start:
            candidates = [(start, end + 24 * 60), (start - 24 * 60, end)]
        for item_start, item_end in candidates:
            if item_end >= now_minutes - past_minutes and item_start <= now_minutes + future_minutes:
                return True
        return False

    def _format_plan_item_for_prompt(self, item: dict[str, Any] | None) -> str:
        if not isinstance(item, dict):
            return "（暂无）"
        phase = _single_line(item.get("temporal_phase"), 16).lower()
        eligibility = _single_line(item.get("fact_eligibility"), 48).lower()
        status = _single_line(item.get("status"), 24).lower()
        commitment = _single_line(item.get("commitment_level"), 24).lower()
        is_unverified_future = phase == "future" or (status in {"planned", "unknown", ""} and eligibility in {"", "none"})
        if is_unverified_future and commitment not in {"confirmed", "routine"}:
            return f"{_single_line(item.get('time'), 12)}｜临近时段可能有安排"
        if phase == "future" and status in {"planned", ""} and commitment in {"confirmed", "routine"}:
            label = _single_line(item.get("title") or item.get("activity"), 80)
            label = re.split(r"[，,。；;：:]", label, maxsplit=1)[0].strip()[:50]
            prefix = "按安排" if commitment == "confirmed" else "照平常"
            return "｜".join(part for part in (_single_line(item.get("time"), 12), f"{prefix}{label}") if part)
        parts = [
            str(item.get("time", "")).strip(),
            str(item.get("activity", "")).strip(),
            f"情绪：{item.get('mood', '')}".strip(),
        ]
        seed = _single_line(item.get("message_seed"), 120)
        if seed:
            parts.append(f"可分享碎片：{seed}")
        return "｜".join(part for part in parts if part)

    @staticmethod
    def _truncate_proactive_context(text: str, limit: int = 2600) -> str:
        source = str(text or "").strip()
        if len(source) <= limit:
            return source
        units = [item.strip() for item in re.split(r"(?<=[。！？!?])\s*|\n+", source) if item.strip()]
        kept: list[str] = []
        size = 0
        for unit in units:
            extra = len(unit) + (1 if kept else 0)
            if size + extra > limit:
                break
            kept.append(unit)
            size += extra
        if kept:
            return "\n".join(kept)
        return source[:limit].rstrip() + "…"

    @staticmethod
    def _truncate_proactive_text(text: str, limit: int = 260) -> str:
        source = str(text or "").strip()
        if len(source) <= limit:
            return source
        cut = source[:limit]
        boundaries = [cut.rfind(mark) for mark in "。！？!?…"]
        boundary = max(boundaries, default=-1)
        if boundary >= max(20, int(limit * 0.55)):
            return cut[: boundary + 1].rstrip()
        return cut.rstrip() + "…"

    def _sanitize_proactive_text(self, text: str) -> str:
        cleaned = str(text or "").strip()
        cleaned = re.sub(r"<img\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"</img>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"<[^>\n]{0,200}>", "", cleaned)
        cleaned = cleaned.replace("[图片]", "").replace("【图片】", "")
        cleaned = cleaned.replace("（图片已送达）", "").replace("(图片已送达)", "")
        emotion_cleaner = getattr(self, "_strip_visible_tts_emotion_cues", None)
        if callable(emotion_cleaner):
            cleaned = emotion_cleaner(cleaned)
        identity_cleaner = getattr(self, "_strip_internal_identity_anchors", None)
        if callable(identity_cleaner):
            cleaned = identity_cleaner(cleaned)
        cleaned = re.sub(r"^```(?:text)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip().strip('"').strip("'")
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        lines = []
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if re.fullmatch(r"[（(].{0,40}语音消息.{0,20}[)）]", line):
                continue
            if re.match(r"^(?:图片发过去了|希望他看到的时候|然后过了好一会儿)", line):
                continue
            if self._is_proactive_instruction_leak_text(line):
                continue
            if re.match(r"^[（(].{0,80}(?:翻了个身|裹紧了些|眼睛微微眯起来).*[）)]$", line):
                continue
            line = self._strip_parenthetical_stage_directions(line)
            if not line:
                continue
            lines.append(line)
        if not lines:
            return ""
        return self._truncate_proactive_text("\n".join(lines[:3]), 260)

    def _strip_parenthetical_stage_directions(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        stage_tokens = (
            "搅", "夹", "咬", "嚼", "喝", "抿", "吞", "放下", "拿起",
            "叹", "笑", "眨", "盯", "看", "望", "低头", "抬头", "偏头",
            "小声", "轻轻", "慢慢", "默默", "皱眉", "挑眉", "眯眼",
            "伸手", "缩", "靠", "蹭", "戳", "敲", "揉", "摸", "抱",
            "翻身", "裹", "坐", "站", "躺", "走", "晃", "顿了顿",
        )

        def _replace(match: re.Match[str]) -> str:
            inner = (match.group(1) or "").strip()
            if not inner:
                return ""
            if any(token in inner for token in stage_tokens):
                return ""
            return match.group(0)

        cleaned = re.sub(r"^[（(]\s*[^()（）\n]{1,50}\s*[）)]\s*", "", cleaned)
        cleaned = re.sub(r"[（(]\s*([^()（）\n]{1,50})\s*[）)]", _replace, cleaned)
        return self._strip_leading_sentence_boundary_artifacts(re.sub(r"\s+", " ", cleaned).strip())

    def _normalize_proactive_sentence_flow(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        # Protect URLs from sentence-flow tokenization: a URL is one atomic
        # unit and must never be split on ASCII dots or receive an appended
        # sentence punctuation. Swap each URL out for a NUL placeholder and
        # restore it right before returning.
        url_placeholders: list[str] = []

        def _protect_url(match: re.Match[str]) -> str:
            url_placeholders.append(match.group(0))
            return f"\x00URL{len(url_placeholders) - 1}\x00"

        cleaned = re.sub(r"https?://[^\s，。！？!?；;、]+", _protect_url, cleaned)
        cleaned = self._strip_unsupported_proactive_agreement(cleaned)
        cleaned = self._trim_abrupt_closing_topic_shift(cleaned)
        cleaned = _normalize_outbound_punctuation_flow(cleaned)
        cleaned = cleaned.replace("！?", "！？").replace("？!", "？！")
        cleaned = re.sub(
            r"([A-Za-z0-9_\-]{1,40})[。！？!?]\s+(呢|呀|啊|嘛|吧|哦|喔|诶)(?=[，,。！？!?~～\s]|$)",
            r"\1\2",
            cleaned,
        )
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        raw_units: list[str] = []
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            raw_units.extend(self._split_proactive_sentence_units(line))

        if not raw_units:
            return ""

        merged: list[str] = []
        continuation_prefixes = (
            "又", "还", "也", "就", "才", "只是", "但", "但是", "不过", "然后", "所以",
            "有点", "有一点", "不想", "没想", "想着", "顺手",
        )
        for unit in raw_units:
            unit = unit.strip(" ,，、")
            if not unit:
                continue
            is_continuation = unit.startswith(continuation_prefixes)
            if merged and is_continuation:
                merged[-1] = merged[-1].rstrip("。！？!?；;，,") + "，" + unit
            else:
                merged.append(unit)

        normalized = [self._ensure_chat_sentence_punctuation(item) for item in merged]
        normalized = [item for item in normalized if item]
        if len(normalized) <= 3:
            result = self._truncate_proactive_text("\n".join(normalized), 260)
        else:
            head = normalized[:2]
            tail_sentences = normalized[2:4]
            result = self._truncate_proactive_text("\n".join(head + tail_sentences), 260)
        for idx, url in enumerate(url_placeholders):
            result = result.replace(f"\x00URL{idx}\x00", url)
        return result

    def _group_share_text_has_life_sidecar(self, text: str) -> bool:
        cleaned = _single_line(text, 500)
        if "群" not in cleaned:
            return False
        life_tokens = (
            "课", "老师", "同学", "作业", "草稿纸", "书", "笔", "桌", "窗", "路上",
            "小猫", "猫", "饭", "吃", "喝", "杯", "天气", "雨", "太阳", "云", "风",
            "困", "饿", "刚刚", "刚才", "这会儿",
        )
        return any(token in cleaned for token in life_tokens)

    def _strip_unsupported_proactive_agreement(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        patterns = (
            r"^(?:哈哈|哈|嘿|嗯嗯|嗯|唔|诶|欸)[，,。.\s]*(?:我也觉得|确实|对吧|是吧|真的)[，,。.\s]*",
            r"^(?:我也觉得|确实|对吧|是吧|真的)[，,。.\s]*",
            r"^(?:哈哈|哈)[，,。.\s]*(?=(?:今天|刚刚|刚才|现在|窗外|路上|云|天气|太阳|雨|风))",
        )
        for pattern in patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
        return cleaned or str(text or "").strip()

    def _trim_performative_self_state_tail(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        tail_clause_match = re.search(
            r"([，,；;。！？!?]\s*)"
            r"((?:我)?(?:刚刚|刚才|刚|这会儿|现在)?"
            r"(?:在|还在|正|正在)?"
            r"[^。！？!?；;\n，,]{0,14}"
            r"(?:发呆|发怔|晃神|躺着|趴着|盯着|望着|看着天花板|看天花板|刷手机|摸鱼|犯困|醒着|睡不着|放空|走神|缓神)"
            r"[^。！？!?；;\n，,]{0,18}"
            r"(?:来着|而已|呢|啦|。|！|？|~|～)?$)",
            cleaned,
        )
        if tail_clause_match:
            kept = cleaned[: tail_clause_match.start()].strip(" ,，、；;")
            if kept:
                result = self._finish_trimmed_proactive_text(kept)
                logger.info(
                    "[PrivateCompanion] 主动消息已去除刻意状态尾巴: before=%s after=%s",
                    _single_line(cleaned, 160),
                    _single_line(result, 160),
                )
                return result
        units: list[str] = []
        for line in cleaned.splitlines():
            line = line.strip()
            if line:
                units.extend(self._split_proactive_sentence_units(line))
        units = [unit.strip(" ,，、") for unit in units if unit.strip(" ,，、")]
        if len(units) <= 1:
            return cleaned
        tail = units[-1].strip()
        if not tail:
            return cleaned
        asks_user = bool(_ASK_USER_PROACTIVE_PATTERN.search(tail))
        if asks_user:
            return cleaned
        performative_tail = bool(
            re.search(
                r"^(?:我)?(?:刚刚|刚才|刚|这会儿|现在)?"
                r"(?:在|还在|正|正在)?"
                r"[^。！？!?；;\n]{0,12}"
                r"(?:发呆|躺着|趴着|盯着|望着|看着天花板|看天花板|刷手机|摸鱼|犯困|醒着|睡不着|放空|走神|缓神)"
                r"[^。！？!?；;\n]{0,18}"
                r"(?:来着|而已|呢|啦|。|！|？|~|～)?$",
                tail,
            )
        )
        if not performative_tail:
            return cleaned
        kept = units[:-1]
        if not kept:
            return cleaned
        result = "\n".join(self._finish_trimmed_proactive_text(unit) for unit in kept if unit)
        result = result.strip()
        if result:
            logger.info(
                "[PrivateCompanion] 主动消息已去除刻意状态尾巴: before=%s after=%s",
                _single_line(cleaned, 160),
                _single_line(result, 160),
            )
            return result
        return cleaned

    def _proactive_status_inventory_kind(self, unit: str) -> str:
        cleaned = _single_line(unit, 140).strip(" ，,。！？!?~～")
        if not cleaned:
            return ""
        opener_match = re.match(r"^([\w\u4e00-\u9fffぁ-んァ-ヶー]{1,8})[，,]\s*", cleaned)
        if opener_match:
            opener_text = opener_match.group(1)
            if len(opener_text) <= 4 and not re.search(r"(窗外|外面|雨声|风声|天气|今天|现在|刚刚|刚才)", opener_text):
                cleaned = cleaned[opener_match.end():].strip()
        if re.search(r"(你|主人).{0,12}(吗|呢|呀|要不要|有没有)", cleaned):
            return ""
        if re.search(r"(窗外|外面|雨声|风声|雨|下雨|小雨|大雨|风|云|天色|阳光|太阳|月亮|路灯|杯沿|书页|桌边)", cleaned):
            return "scene"
        if re.search(r"^(?:我)?(?:刚刚|刚才|刚|才|已经|这会儿)?(?:洗漱|洗完|洗澡|刷牙|起床|醒|到家|出门|回家|吃完|喝完|写完|收拾完|换好|换完)", cleaned):
            return "routine"
        if re.search(r"^(?:我)?(?:今天|现在|刚刚|刚才|刚|才)?(?:穿了|穿着|换了|换成|披了|套了|戴了|拿了)", cleaned):
            return "clothing"
        if re.search(r"(舒服|安静|困|累|清醒|迷糊|开心|烦|平稳|舒服)", cleaned) and len(cleaned) <= 22:
            return "state"
        return ""

    def _is_conversational_proactive_text(self, text: str) -> bool:
        """判断主动消息是否为对话式消息，而非状态清单。

        状态清单收束（_trim_proactive_status_inventory）只应作用于「纯陈述、多段自报」的
        消息；含问句、颜文字/表情或直接问用户的消息是对话，收束会丢掉称呼、问句与情绪，
        破坏一句话的完整性（例如一条「场景铺垫＋颜文字＋问句」的完整问候被削成只剩一句
        场景描述）。
        """
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        if _ASK_USER_PROACTIVE_PATTERN.search(cleaned):
            return True
        for unit in self._split_proactive_sentence_units(cleaned):
            unit = unit.strip(" ，,、")
            if not unit:
                continue
            # 问句：以问号结尾，或结尾带疑问语气词（覆盖省略主语的问句）
            if re.search(r"[？?]$", unit) or re.search(r"[吗呢呀么]$", unit):
                return True
            # 颜文字/表情：括号内含非中文散文字符的短表达式，如 (≧▽≦) (◍•ᴗ•◍)
            if re.search(r"[（(][^（）()\n]*[^一-鿿，。！？、\s][^（）()\n]*[)）]", unit):
                return True
        return False

    def _trim_proactive_status_inventory(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        units: list[str] = []
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if line:
                units.extend(self._split_proactive_sentence_units(line))
        units = [unit.strip(" ，,、") for unit in units if unit.strip(" ，,、")]
        if len(units) < 2:
            return cleaned
        kinds = [self._proactive_status_inventory_kind(unit) for unit in units]
        inventory_count = sum(1 for kind in kinds if kind)
        if inventory_count < 2:
            return cleaned
        if len(units) == 2 and inventory_count < len(units):
            return cleaned
        # 对话式消息（含问句/颜文字/直接问用户）不是状态清单，收束会破坏其完整性，跳过
        if self._is_conversational_proactive_text(cleaned):
            return cleaned
        opener = ""
        opener_match = re.match(r"^([\w\u4e00-\u9fffぁ-んァ-ヶー]{1,4}[，,])", units[0])
        if opener_match:
            opener = opener_match.group(1)
        priority = {"scene": 4, "clothing": 3, "routine": 2, "state": 1}
        best_index = max(
            range(len(units)),
            key=lambda index: (priority.get(kinds[index], 0), index),
        )
        chosen = units[best_index].strip()
        if opener:
            chosen = re.sub(r"^[\w\u4e00-\u9fffぁ-んァ-ヶー]{1,4}[，,]\s*", "", chosen).strip()
            if chosen:
                chosen = f"{opener}{chosen}"
        chosen = self._ensure_chat_sentence_punctuation(chosen)
        if chosen and chosen != cleaned:
            logger.info(
                "[PrivateCompanion] 主动消息已收束状态清单: before=%s after=%s",
                _single_line(cleaned, 180),
                _single_line(chosen, 160),
            )
            return chosen
        return cleaned

    def _finish_trimmed_proactive_text(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        lines = [line.strip(" ,，、；;") for line in cleaned.splitlines() if line.strip(" ,，、；;")]
        if not lines:
            return ""
        return "\n".join(self._ensure_chat_sentence_punctuation(line) for line in lines)

    def _has_abrupt_closing_topic_shift(self, text: str, *, inbound_text: str = "") -> bool:
        original = str(text or "").strip()
        if not original:
            return False
        trimmed = self._trim_abrupt_closing_topic_shift(original, inbound_text=inbound_text)
        return bool(trimmed and trimmed != original)

    def _trim_abrupt_closing_topic_shift(self, text: str, *, inbound_text: str = "") -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        units = []
        for line in cleaned.splitlines():
            line = line.strip()
            if line:
                units.extend(self._split_proactive_sentence_units(line))
        units = [unit.strip(" ,，、") for unit in units if unit.strip(" ,，、")]
        if len(units) <= 1:
            return cleaned
        inbound = _single_line(inbound_text, 260)
        inbound_is_sleep_context = bool(re.search(r"(晚安|睡了|睡觉|做梦|好梦|困了|休息|先睡|去睡|早点睡)", inbound))
        closing_index = -1
        for index, unit in enumerate(units):
            if re.search(r"(晚安|好梦|做个梦|做梦|睡吧|睡觉|去睡|早点睡|休息吧|明天见|(?:先)?(?:别|不)(?:吵|打扰|烦))", unit):
                closing_index = index
                break
        if closing_index < 0 or closing_index >= len(units) - 1:
            return cleaned
        tail = "".join(units[closing_index + 1 :])
        if not tail:
            return cleaned
        tail_continues_closing = bool(re.search(r"(梦|睡|晚安|明天|醒来|休息|被窝|枕头|月亮|星星)", tail))
        if tail_continues_closing and inbound_is_sleep_context:
            return cleaned
        abrupt_markers = (
            "今天", "刚刚", "刚才", "现在", "天气", "云", "太阳", "雨", "风", "作业", "阅读",
            "视频", "新闻", "群里", "书柜", "日程", "吃", "喝", "路上", "窗外", "看到", "觉得",
        )
        looks_abrupt = any(marker in tail for marker in abrupt_markers) or len(tail) >= 6
        if not looks_abrupt:
            return cleaned
        kept = units[: closing_index + 1]
        result = "\n".join(self._ensure_chat_sentence_punctuation(unit) for unit in kept if unit)
        return result.strip() or cleaned

    def _ensure_chat_sentence_punctuation(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        if re.search(r"[。！？!?…~～]$", cleaned):
            return cleaned
        question_tokens = (
            "吗", "嘛", "么", "什么", "怎么", "咋", "有没有", "是不是", "要不要",
            "忙什么", "吃东西了吗", "睡了吗", "醒了吗", "你呢", "你那边呢", "你那里呢", "你那儿呢",
        )
        if any(token in cleaned for token in question_tokens):
            return cleaned + "？"
        soft_endings = ("呀", "啦", "嘛", "呢", "吧", "哦", "喔", "诶", "啊")
        if cleaned.endswith(soft_endings):
            return cleaned + "。"
        return cleaned + "。"

    def _split_proactive_sentence_units(self, text: str) -> list[str]:
        cleaned = str(text or "").strip()
        if not cleaned:
            return []
        units: list[str] = []
        for part in [item.strip() for item in re.split(r"\s+", cleaned) if item.strip()]:
            if re.search(r"[。！？!?；;…~～]", part):
                matches = re.findall(r"[^。！？!?；;…~～]+[。！？!?；;…~～]+|[^。！？!?；;…~～]+$", part)
                units.extend(match.strip() for match in matches if match.strip())
            else:
                units.append(part)
        return units

    def _soften_social_proactive_text(self, text: str, *, action: str = "message") -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        cleaned = self._strip_parenthetical_stage_directions(cleaned)
        if not cleaned:
            return ""
        cleaned = self._trim_proactive_status_inventory(cleaned)
        cleaned = self._trim_performative_self_state_tail(cleaned)
        cleaned = re.sub(r"^(?:早上好|早安|上午好|中午好|午安|下午好|晚上好)[,,\s]*", "", cleaned)

        _SOCIAL_REPLACEMENTS = [
            ("刷一下存在感", ""),
            ("冒个泡", ""),
            ("冒个头", ""),
            ("顺手冒了个头", ""),
            ("没什么大不了的,就是", ""),
            ("没什么大道理,就是", ""),
            ("免得你又忘了我", ""),
            ("最近忙不忙？", ""),
            ("最近忙不忙", ""),
            ("数据有意思吗？", ""),
            ("数据有意思吗", ""),
            ("发现你好像在忙。", ""),
            ("发现你好像在忙", ""),
            ("请注意休息", "记得歇会儿"),
        ]
        for old, new in _SOCIAL_REPLACEMENTS:
            cleaned = cleaned.replace(old, new)

        cleaned = re.sub(r"你在忙(.{0,24})吗？感觉你[^。！？\n]*专注[^。！？\n]*[。！？]?", r"还在忙\1啊。", cleaned)
        cleaned = re.sub(r"你在忙(.{0,24})吗？", r"还在忙\1啊。", cleaned)
        cleaned = re.sub(r"感觉你[^。！？\n]*专注[^。！？\n]*[。！？]?", "", cleaned)
        cleaned = re.sub(r"感觉你[^。！？\n]{0,28}呢[。！？]?", "", cleaned)
        cleaned = re.sub(r"(?:我看你|看你)又?在忙", "还在忙", cleaned)

        _ACTION_SPECIFIC_REPLACEMENTS = {
            "screen_peek": [
                ("逻辑分支的工作", "那个逻辑分支"),
                ("工作吗？", "啊。"),
                ("工作啊。", "啊。"),
                ("感觉你投入的样子很专注呢。", ""),
                ("感觉你很投入呢。", ""),
                ("还在忙啊。", "还没从那边抬头啊。"),
            ],
            "poke": [
                ("我就戳一下", "就戳你一下"),
                ("所以来戳你一下", "所以来碰你一下"),
            ],
            "voice": [
                ("给你留了句语音。", "给你留了句语音。"),
                ("刚给你留了句语音,", "刚给你留了句语音,"),
            ],
            "photo_text": [
                ("路边的植物看着很有生机,给你拍了张照片。", "路边那点绿刚好有点顺眼。"),
                ("给你拍了张照片。", ""),
                ("给你拍了张照片", ""),
                ("给你拍了照片", ""),
                ("发给你啦。", ""),
            ],
        }
        if action in _ACTION_SPECIFIC_REPLACEMENTS:
            for old, new in _ACTION_SPECIFIC_REPLACEMENTS[action]:
                cleaned = cleaned.replace(old, new)
        if "photo_text" in action:
            cleaned = re.sub(r"^(?:今天天气[^。！？\n]{0,30}[,,])", "", cleaned)
            cleaned = cleaned.replace("（图片已送达）", "").replace("(图片已送达)", "")

        cleaned = re.sub(r"(?:来找你一下[,,、\s]*){2,}", "来找你一下,", cleaned)
        cleaned = re.sub(r"^[嗨哈喂欸诶]{1,2}[,,\s]+", "", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = re.sub(r"([。！？])\1+", r"\1", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = re.sub(r"^[,。！？、\s]+", "", cleaned)
        cleaned = self._strip_parenthetical_stage_directions(cleaned)
        return cleaned

    def _deemphasize_state_report_preamble(self, text: str, *, reason: str = "") -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        if reason == "important_date_share":
            return cleaned

        date_report_patterns = (
            r"^(?:今天|现在)(?:是)?[^。！？\n]{0,18}(?:五一|劳动节|周末|休息日|假期|放假)[^。！？\n]{0,24}[。！？,\s]*",
            r"^(?:今天|现在)[^。！？\n]{0,16}(?:不用|不用去|不需要)(?:上学|上班|工作|补课)[^。！？\n]{0,18}[。！？,\s]*",
        )
        for pattern in date_report_patterns:
            cleaned = re.sub(pattern, "", cleaned)

        cleaned = re.sub(r"(?:所以|因此)[,，、\s]*(?=我|先|就|你)", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = re.sub(r"^[,，。！？、\s]+", "", cleaned)
        return cleaned

    def _choose_proactive_message(
        self,
        user: dict[str, Any],
        name: str,
        planned_reason: str = "",
    ) -> tuple[str, str]:
        """Pick the proactive reason and return an internal intent note.

        The second value is deliberately not outbound copy. The actual message
        must still be generated by the framework chain and pass send review.
        """
        state = self.data.get("daily_state", {})
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        can_do = self.data.get("can_do", [])
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        mood = _single_line(state.get("mood_bias") if isinstance(state, dict) else "平稳", 20)
        active_conditions = state.get("conditions", []) if isinstance(state, dict) else []

        reasons = [planned_reason] if planned_reason else []
        if self._is_quiet_time() and self._has_active_insomnia_state():
            reasons.append("insomnia_night")
        if active_conditions and random.random() < 0.45:
            reasons.append("quiet_care")
        if energy < 45 and random.random() < 0.55:
            reasons.append("quiet_care")
        share_probability = runtime_persona_setting(self, "proactive_share_probability", 45)
        activity_share_blocked = False
        block_checker = getattr(self, "_activity_share_duplicate_block_remaining", None)
        if callable(block_checker):
            try:
                activity_share_blocked = block_checker(user) > 0
            except Exception:
                activity_share_blocked = False
        if can_do and not activity_share_blocked and random.random() < max(0.05, min(0.85, share_probability)):
            reasons.append("activity_share")
        if self.data.get("bot_diaries") and random.random() < max(0.08, share_probability * 0.55):
            reasons.append("diary_share")
        upcoming_dates = self._get_relevant_important_dates()
        if upcoming_dates and random.random() < 0.35:
            reasons.append("important_date_share")
        if (
            current_item
            and runtime_persona_setting(self, "include_schedule_in_messages", True)
            and random.random() < 0.22
        ):
            reasons.append("background_schedule")
        if not reasons:
            reasons.append("check_in")
        elif _safe_int(user.get("ignored_streak"), 0, 0) <= 0 and random.random() < 0.12:
            reasons.append("check_in")
        reason = planned_reason if planned_reason and self._is_reason_allowed_now(planned_reason, user) else random.choice(reasons)

        if reason == "insomnia_night":
            return reason, "夜间清醒时的一句短开场；根据人格决定是安静自述、轻轻靠近还是不打扰地留一句，不报时、不追问、不拉长。"

        if reason == "special_day_greeting":
            return reason, "特别日子的低压力关系问候；只留一个真诚而具体的祝愿，不堆砌节日话术。"

        if reason == "quiet_care":
            return reason, "低能量或状态余波下的轻量问候；只给一个具体切口，不写成关心清单。"

        if reason == "morning_greeting":
            return reason, "当前时段的首次早间开口；贴近早晨片段，只做普通问候，不问早餐、吃了吗或吃什么，等用户回应后再关心。"

        if reason == "noon_greeting":
            return reason, "午间短开口；可以围绕吃饭、午休或短暂放松，不催促。"

        if reason == "evening_greeting":
            return reason, "傍晚或夜间收尾时的一句轻开口；不汇报日程，不追问。"


        if reason == "activity_share":
            activity = _single_line(random.choice(can_do), 40) if isinstance(can_do, list) and can_do else "刚才那点小事"
            return reason, f"围绕可做事项“{activity}”分享一个很小的进展或片段；不要写成自证或汇报。"

        if reason == "diary_share":
            fragment = self._pick_diary_fragment()
            if fragment:
                return reason, f"可引用日记碎片“{_single_line(fragment, 80)}”；只取一句自然分享，不写成报告。"

        if reason == "important_date_share" and upcoming_dates:
            entry = upcoming_dates[0]
            days = _safe_int(entry.get("_days_until"), 0)
            title = _single_line(entry.get("title"), 40)
            note = _single_line(entry.get("note"), 80)
            if days == 0:
                detail = f"今天是「{title}」"
            else:
                detail = f"「{title}」还有 {days} 天"
            if note:
                detail = f"{detail}；备注：{note}"
            return reason, f"重要日期提醒：{detail}；一句说清，不责备用户。"

        if reason == "background_schedule" and current_item:
            activity = _single_line(current_item.get("activity"), 40)
            seed = self._deemphasize_state_report_preamble(
                _single_line(current_item.get("message_seed"), 60),
                reason=reason,
            )
            detail = "；".join(part for part in (activity, seed) if part)
            return reason, f"当前日程片段：{detail or '没有明确片段'}；只取一个生活切口，不逐项汇报。"

        style = _single_line(
            user.get("style") or runtime_persona_setting(self, "default_style", "温柔"),
            24,
        )
        style_hint = f"；参考语气偏好：{style}" if style else ""
        return "check_in", f"无明确来源时的轻量开场{style_hint}；优先贴近关系事实、当前状态或当前日程，不使用固定模板。"


def _install_external_image_runtime_compatibility() -> None:
    """Expose historical private methods from the split runtime.

    No image implementation is copied into the host package. This adapter only
    preserves imports used by older integrations and the existing regression
    suite while production generation continues through the extension API.
    """
    runtime_type: Any = None
    for module_name in (
        "data.plugins.astrbot_plugin_image_companion.image_runtime",
        "astrbot_plugin_image_companion.image_runtime",
    ):
        try:
            module = importlib.import_module(module_name)
            runtime_type = getattr(module, "ProactiveMessageMixin", None)
            if runtime_type is not None:
                break
        except ImportError:
            continue
    if runtime_type is None:
        return
    protected = {
        "_generate_photo_image",
        "_generate_photo_image_legacy",
        "_generate_photo_image_result",
    }
    for name, value in vars(runtime_type).items():
        if name in protected or name.startswith("__"):
            continue
        if not hasattr(ProactiveMessageMixin, name):
            setattr(ProactiveMessageMixin, name, value)


_install_external_image_runtime_compatibility()
