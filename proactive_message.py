# -*- coding: utf-8 -*-
"""
ProactiveMessageMixin — 主动消息生成、动作执行和发送链路
"""
from __future__ import annotations

import asyncio
import base64
import binascii
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
_MINIMAX_REFERENCE_IMAGE_MAX_BYTES = 10 * 1024 * 1024

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
                    getattr(self, "proactive_chat_bridge_collision_window_seconds", 90),
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
        if not bool(getattr(self, "enable_proactive_chat_integration", True)):
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
                self._save_data_sync()
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
        if not bool(getattr(self, "enable_proactive_chat_integration", True)):
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
        review_mode = str(getattr(self, "proactive_chat_bridge_review_mode", "local") or "local").strip().lower()
        full_reviewer = getattr(self, "_review_proactive_message_send_decision", None)
        if (
            review_mode == "follow_proactive_review"
            and bool(getattr(self, "enable_proactive_message_review", True))
            and callable(full_reviewer)
            and str(getattr(self, "proactive_review_mode", "full") or "full").strip().lower() != "local_only"
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
        if not bool(getattr(self, "enable_proactive_chat_integration", True)):
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
                    )
                topic_recorder = getattr(self, "_remember_proactive_topic", None)
                if callable(topic_recorder):
                    topic_recorder(current, text=visible or text, topic="Proactive Chat", motive="即时主动触发")
                self._save_data_sync()
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
            self._save_data_sync()
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
        if not getattr(self, "enable_web_exploration", False):
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
        if not self.enable_news_integration:
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
        if not self.enable_news_integration:
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
        if not self.enable_news_integration:
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
        if not self.enable_news_integration:
            return "新闻阅读功能没有开启。"
        if not self.enable_ai_daily_watch:
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
            f"- 新闻集成：{'开启' if self.enable_news_integration else '关闭'}",
            f"- AI日报/早报追踪：{'开启' if self.enable_ai_daily_watch else '关闭'}",
            f"- 状态：{status_labels.get(status, status)}",
        ]
        sources = ai_state.get("sources") if isinstance(ai_state.get("sources"), list) else []
        configured_sources = str(getattr(self, "ai_daily_sources", "") or "").strip()
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
        if not self.enable_news_integration:
            lines.append("提示：新闻集成关闭时不会执行抓取。")
        elif not self.enable_ai_daily_watch:
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

    async def _narrate_action_context(self, action: str, action_context: str) -> str:
        if not self.narration_provider_id:
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
            provider_id=self.narration_provider_id,
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
        parsed: list[tuple[int, dict[str, Any]]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            minute = self._parse_hhmm_to_minutes(item.get("time"))
            if minute is None:
                continue
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
                getattr(self, "default_nickname", ""),
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
        return "；".join(parts)

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
            str(getattr(self, "persona_proactive_voice_prompt", "") or ""),
            str(getattr(self, "persona_conversation_voice_prompt", "") or ""),
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
        if not isinstance(user, dict) or self._private_user_role(user) != "friend":
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
            getattr(self, "enable_reaction_expression_experiment", False)
            and getattr(self, "reaction_expression_private_enabled", True)
            and getattr(self, "reaction_expression_proactive_enabled", True)
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
                getattr(self, "reaction_expression_trigger_probability", 0.2)
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
            getattr(self, "reaction_expression_trigger_probability", 0.2)
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
                getattr(self, "reaction_expression_candidate_limit", 6),
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
        timer_hint = self._format_llm_timer_context(user)
        time_guard = self._proactive_time_guard_hint(reason, current_item)
        deferred_share_tense_hint = self._deferred_immediate_share_tense_hint(user, action)
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
                        age_str = f"（{age:.0f}天前）" if age is not None else ""
                        loop_texts.append(f"- {content_preview}{age_str}")
                    open_loops_hint = (
                        "【未完成话题候选】\n"
                        "这些只是可选候选，不是必须提起的任务。只有当本轮主动动机、当前话题或最近私聊比较贴合，"
                        "或者你本来就是想兑现这件事时，才轻轻带一句；如果不贴，就先放着，不必为了它改变本轮话题。\n"
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
        prompt = self.proactive_prompt_template or self._default_proactive_prompt_template()
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
            "{{open_loops_hint}}": open_loops_hint,
            "{{current_time}}": current_time,
        }
        for key, value in replacements.items():
            prompt = prompt.replace(key, value)
        if unanswered_count >= 2 and "连续未回应时的成文边界" not in prompt:
            prompt = (
                f"{prompt.rstrip()}\n\n"
                "【连续未回应时的成文边界】\n"
                "- 这次优先只表达一个完整意思，用一句自然短句或两个紧密相连的短分句说完。\n"
                "- 不要把近况、提问和叮嘱叠在同一条里；更适合分享后自然收住，不要求对方回复。\n"
                "- 如果原本想说的内容较多，应重新组织成完整短句，绝不能留下主谓宾未完成的半句话。"
            )
        persona_marker = "<!-- private_companion_proactive_persona_v1 -->"
        if persona and persona_marker not in prompt:
            prompt = (
                f"{prompt.rstrip()}\n\n{persona_marker}\n"
                "【当前主动消息必须遵循的人格】\n"
                f"{persona[:2600]}\n"
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
            "- 这一轮只生成要发给当前私聊对象的一句正文，不调用任何转述、私聊发送、群发、QQ空间或生图发送工具。\n"
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
        unanswered_count = _safe_int(user.get("ignored_streak"), 0, 0)
        if unanswered_count >= 2:
            lines.append(
                "对方已连续多次没有回应：只保留一个完整意思，优先改写为一句自然短句；"
                "不要同时堆叠近况、提问和叮嘱，任何收短都必须保证句意完整，不能留下半句话。"
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
            lines.append("今天是用户明确允许记住的生日，是一年一次的轻量仪式。先送出真诚、具体、低压力的祝福；不要提系统、记录、年龄、出生年份或精确日期，不承诺永远陪伴，也不要求回复或追问庆祝安排。若带图，正文只自然递出，不描述制作过程。")
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
                lambda: getattr(self, "plugin_specific_persona_id", ""),
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
        max_steps: int = 20,
    ) -> str:
        cache_key = str(umo or "")
        self._framework_captured_send_cache.pop(cache_key, None)
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
                        self._filter_incompatible_proactive_framework_tools(req)
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
        mode = str(getattr(self, "proactive_history_context_mode", "compact") or "compact").strip().lower()
        if mode not in {"recent_only", "compact", "expanded"}:
            mode = "compact"
        recent_count = _safe_int(
            getattr(self, "proactive_history_recent_raw_count", 8),
            8,
            1,
            50,
        )
        max_chars = _safe_int(
            getattr(self, "proactive_history_max_chars", 6000),
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
                lines.append(f"{self.bot_name}: {last_bot}")
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
            min(1.0, _safe_float(getattr(self, "proactive_review_hard_risk_threshold", default_hard_risk), default_hard_risk)),
        )
        low_score_threshold = max(
            0.0,
            min(1.0, _safe_float(getattr(self, "proactive_review_low_score_threshold", 0.34), 0.34)),
        )
        pressure_threshold = max(
            0.0,
            min(1.0, _safe_float(getattr(self, "proactive_review_pressure_threshold", 0.55), 0.55)),
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
        if source_link and source_link not in cleaned:
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
                for field in (
                    "title",
                    "headline",
                    "topic",
                    "summary",
                    "impression",
                    "comment",
                    "review",
                    "source",
                    "selected_source",
                    "source_title",
                    "selected_link",
                    "source_url",
                    "link",
                    "url",
                    "bvid",
                ):
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
        strength = str(getattr(self, "proactive_review_strength", "lenient") or "lenient").strip().lower()
        return strength if strength in {"lenient", "balanced", "strict"} else "lenient"

    def _effective_proactive_review_mode(self) -> str:
        mode = str(getattr(self, "proactive_review_mode", "full") or "full").strip().lower()
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
            idle_minutes = idle_getter(user) if callable(idle_getter) else _safe_int(getattr(self, "idle_minutes", 20), 20)
        except Exception:
            idle_minutes = _safe_int(getattr(self, "idle_minutes", 20), 20)
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
            idle_minutes = idle_getter(user) if callable(idle_getter) else _safe_int(getattr(self, "idle_minutes", 20), 20)
        except Exception:
            idle_minutes = _safe_int(getattr(self, "idle_minutes", 20), 20)
        try:
            min_interval = interval_getter(user) if callable(interval_getter) else _safe_int(getattr(self, "min_interval_minutes", 80), 80)
        except Exception:
            min_interval = _safe_int(getattr(self, "min_interval_minutes", 80), 80)
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
        now: float | None = None,
    ) -> str:
        note_text = _single_line(note, 120)
        if not note_text or not re.search(r"(早安|今早|早上|睡前|晚安)", note_text):
            return ""
        check_now = _now_ts() if now is None else now
        now_minutes = datetime.fromtimestamp(check_now).hour * 60 + datetime.fromtimestamp(check_now).minute
        if "睡前" in note_text or "晚安" in note_text:
            stale_after = 9 * 60
        else:
            stale_after = 12 * 60
        if now_minutes < stale_after:
            return ""
        recent_private_at = max(
            _safe_float(user.get("last_user_message_at"), 0),
            _safe_float(user.get("last_private_seen"), 0),
        )
        if recent_private_at > 0 and check_now - recent_private_at < 45 * 60:
            return ""
        return "复核理由沿用了过期早间/睡前语境，已改按当前运行态放行"

    def _normalize_proactive_review_decision_policy(
        self,
        user: dict[str, Any],
        payload: dict[str, Any],
        *,
        strength: str,
        source: str = "model",
    ) -> dict[str, Any]:
        """Normalize the final proactive content gate to send, rewrite, or drop."""
        if not isinstance(payload, dict):
            return {"decision": "send", "reason": "empty review result; local safety gate allowed the message"}
        decision = str(payload.get("decision") or "send").strip().lower()
        note = _single_line(payload.get("reason"), 120)
        reviewed_text = str(payload.get("text") or "").strip()
        if decision == "defer":
            decision = "drop"
            note = _single_line(f"{note or 'candidate is not suitable now'}; final content gate drops instead of deferring", 120)
        if decision not in {"send", "rewrite", "drop"}:
            decision = "send"
        if decision == "rewrite" and not reviewed_text:
            decision = "drop"
            note = _single_line(f"{note or 'rewrite result is empty'}; candidate dropped", 120)
        if decision == "rewrite" and reviewed_text:
            recipient_name = _single_line(user.get("nickname"), 40) if isinstance(user, dict) else ""
            reviewed_text, repaired_address = self._repair_proactive_recipient_address(
                reviewed_text,
                user,
                recipient_name,
            )
            remaining_wrong_address = self._wrong_proactive_recipient_address(
                reviewed_text,
                user,
                recipient_name,
            )
            if remaining_wrong_address:
                decision = "drop"
                reviewed_text = ""
                note = f"最终改写含其他用户专属称呼：{remaining_wrong_address}"
            elif repaired_address:
                note = _single_line(f"{note or 'final rewrite'}; corrected recipient address {repaired_address}", 120)
        return {
            "decision": decision,
            "text": reviewed_text if decision == "rewrite" else "",
            "reason": note or "proactive final content gate",
            "hard": bool(payload.get("hard")),
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
        review_context = _single_line(action_summary, 240)
        if image_path:
            review_context = _single_line(f"{review_context}\n真实图片文件：{image_path}", 360)
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
        review_enabled = bool(getattr(self, "enable_proactive_message_review", True))
        review_mode = self._effective_proactive_review_mode()
        if not review_enabled or review_mode == "local_only":
            local_mode_label = "仅本地检查模式" if review_enabled and review_mode == "local_only" else "主动模型终审未启用"
            if local_decision in {"drop", "defer"}:
                return self._normalize_proactive_review_decision_policy(user, local, strength=strength, source="local")
            if local_decision == "rewrite":
                local_rewrite_text = str(local.get("text") or "").strip()
                if local_rewrite_text:
                    local_result = self._normalize_proactive_review_decision_policy(
                        user,
                        local,
                        strength=strength,
                        source="local",
                    )
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
            return self._normalize_proactive_review_decision_policy(user, local, strength=strength, source="local")
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
                rewritten_reference = self._sanitize_action_boundaries(
                    self._sanitize_proactive_text(rewritten_reference),
                    reason=reason,
                    action=action,
                    action_context=review_context,
                    has_real_image=bool(image_path) or "真实图片文件：" in review_context or "图片路径：" in review_context,
                )
                rewritten_reference = self._normalize_proactive_sentence_flow(rewritten_reference)
                post_rewrite_check = self._external_share_source_consistency_decision(
                    user,
                    rewritten_reference,
                    reason=reason,
                    topic=topic,
                    motive=motive,
                    action_context=review_context,
                )
                if post_rewrite_check:
                    safe_reference = _single_line(local.get("reference_text"), 300)
                    logger.info(
                        "[PrivateCompanion] 主动外界分享人格润色后仍与来源不一致，已使用确定性来源文本: reason=%s before=%s after=%s",
                        _single_line(post_rewrite_check.get("reason"), 120),
                        _single_line(rewritten_reference, 140),
                        _single_line(safe_reference, 140),
                    )
                    rewritten_reference = safe_reference
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
            return local
        if local_decision == "drop":
            return self._normalize_proactive_review_decision_policy(user, local, strength=strength, source="local")
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
reason={reason or "check_in"}; action={action or "message"}; topic={_single_line(topic, 80) or "none"}; motive={_single_line(motive, 120) or "none"}; summary={_single_line(action_summary, 80) or "none"}

[Full persona]
{persona[:2600] if persona else "(No explicit persona was resolved. Preserve the candidate instead of inventing a new voice.)"}

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
        review_provider_id = self._task_provider(self.response_review_provider_id, self.mai_style_provider_id)
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
            return self._normalize_proactive_review_decision_policy(user, local, strength=strength, source="local")
        payload = self._parse_json_object(raw)
        if not isinstance(payload, dict):
            return self._normalize_proactive_review_decision_policy(user, local, strength=strength, source="local")
        decision = str(payload.get("decision") or "").strip().lower()
        if decision not in {"send", "rewrite", "drop"}:
            return self._normalize_proactive_review_decision_policy(user, local, strength=strength, source="local")
        reviewed_text = str(payload.get("text") or "").strip()
        note = _single_line(payload.get("reason"), 120)
        original_decision = decision
        if decision == "rewrite":
            if not reviewed_text:
                return self._normalize_proactive_review_decision_policy(user, local, strength=strength, source="local")
            reviewed_text = self._sanitize_action_boundaries(
                self._sanitize_proactive_text(reviewed_text),
                reason=reason,
                action=action,
                action_context=review_context,
                has_real_image=bool(image_path) or "真实图片文件：" in review_context or "图片路径：" in review_context,
            )
            reviewed_text = self._normalize_proactive_sentence_flow(reviewed_text)
            if re.fullmatch(
                r"[嗯哦唔呃诶欸啊呀哎噢喔哈]+[。！？!?…~～]*",
                re.sub(r"\s+", "", reviewed_text),
            ):
                logger.warning(
                    "[PrivateCompanion] 主动发送前复核改写退化为单独语气词，已保留原候选: before=%s after=%s",
                    _single_line(text, 120),
                    _single_line(reviewed_text, 40),
                )
                return {
                    "decision": "send",
                    "text": "",
                    "reason": "复核改写丢失原消息用途，保留完整候选",
                }
            reviewed_text, repaired_address = self._repair_proactive_recipient_address(
                reviewed_text,
                user,
                _single_line(user.get("nickname"), 40),
            )
            if repaired_address:
                logger.warning(
                    "[PrivateCompanion] 主动发送前复核改写已纠正串用户称呼: user=%s wrong=%s",
                    _single_line(user.get("user_id"), 40),
                    repaired_address,
                )
            if not reviewed_text:
                return self._normalize_proactive_review_decision_policy(user, local, strength=strength, source="local")
            meta_leak_checker = getattr(self, "_response_review_meta_leak_reason", None)
            if callable(meta_leak_checker) and meta_leak_checker(reviewed_text):
                return self._normalize_proactive_review_decision_policy(user, local, strength=strength, source="local")
            if self._framework_agent_meta_summary_leak(reviewed_text):
                return {
                    "decision": "drop",
                    "text": "",
                    "reason": "主动候选疑似工具循环/内部发送摘要泄漏",
                }
            if len(reviewed_text) > max(len(text) + 60, 240):
                return self._normalize_proactive_review_decision_policy(user, local, strength=strength, source="local")
            if re.search(r"(提示词|系统|JSON|模型|工具调用|主动消息|无文字|附加组件)", reviewed_text, re.IGNORECASE):
                return self._normalize_proactive_review_decision_policy(user, local, strength=strength, source="local")
            if reason in {"bili_video_share", "news_share", "web_exploration_share"}:
                rewritten_source_issue = self._external_share_source_consistency_decision(
                    user,
                    reviewed_text,
                    reason=reason,
                    topic=topic,
                    motive=motive,
                    action_context=review_context,
                )
                if rewritten_source_issue:
                    original_source_issue = self._external_share_source_consistency_decision(
                        user,
                        text,
                        reason=reason,
                        topic=topic,
                        motive=motive,
                        action_context=review_context,
                    )
                    if original_source_issue is None:
                        reviewed_text = text
                        note = "终审改写破坏了真实来源，已恢复复核前原文"
                    else:
                        source_text = str(rewritten_source_issue.get("source_text") or "").strip()
                        safe_reference = self._external_share_fallback_reference(source_text)
                        if not safe_reference:
                            return {
                                "decision": "drop",
                                "text": "",
                                "reason": "终审改写后的来源不一致且无法恢复真实来源",
                                "hard": True,
                            }
                        reviewed_text = safe_reference
                        note = "终审改写破坏了真实来源，已恢复确定性来源文本"
        normalized_payload = self._normalize_proactive_review_decision_policy(
            user,
            {
                "decision": decision,
                "text": reviewed_text,
                "reason": note,
            },
            strength=strength,
            source="model",
        )
        decision = str(normalized_payload.get("decision") or decision).strip().lower()
        reviewed_text = str(normalized_payload.get("text") or reviewed_text or "").strip()
        note = _single_line(normalized_payload.get("reason") or note, 120)
        if decision == "send" and str(local.get("decision") or "") == "rewrite" and str(local.get("text") or "").strip():
            reviewed_text = str(local.get("text") or "").strip()
            decision = "rewrite"
            note = _single_line(note or local.get("reason") or "本地轻改写后放行", 120)
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
        }

    def _pop_framework_captured_send_payload(
        self,
        umo: str,
    ) -> tuple[str, str, list[Any]]:
        captured = self._framework_captured_send_cache.pop(str(umo or ""), [])
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
        if not self.enable_llm_proactive_message:
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
        if not reference:
            reference = f"自然地向{name or '对方'}主动说一句与当前状态有关、低压力且无需立即回复的话。"
        reference = sanitize_relationship_source(reference, "proactive_fallback.reference")
        if not reference:
            reference = f"自然地向{name or '对方'}主动说一句低压力且无需立即回复的话。"
        return await self._rewrite_reference_reply_with_persona(
            reference,
            scene=f"主动开口；原因={reason or 'check_in'}；动作={action or 'message'}",
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
        if self._should_drop_vague_generic_proactive(user, reason=reason, action=action, action_context=action_context, text=cleaned):
            return "", "泛化主动缺少具体由头"
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
        if not bool(getattr(self, "enable_proactive_message_review", True)):
            logger.info(
                "[PrivateCompanion] 主动消息疑似回复空气,主动终审关闭已丢弃: flags=%s text=%s",
                ",".join(flags),
                _single_line(cleaned, 120),
            )
            return ""
        mode = self._effective_proactive_review_mode()
        if mode == "local_only":
            logger.info(
                "[PrivateCompanion] 主动消息疑似回复空气,仅本地保护已丢弃: flags=%s text=%s",
                ",".join(flags),
                _single_line(cleaned, 120),
            )
            return ""
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
{persona[:2600] if persona else "（没有解析到显式人格；尽量保留原文语气，不要另造一种通用陪伴人格）"}

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
""".strip()
        started = time.perf_counter()
        rewritten = await self._llm_call(
            prompt,
            max_tokens=180,
            provider_id=self._task_provider(self.response_review_provider_id, self.mai_style_provider_id),
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
            _single_line(getattr(self, "default_nickname", ""), 16),
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
        proactive_voice = str(getattr(self, "persona_proactive_voice_prompt", "") or "")
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
            "bot_name": self.bot_name,
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
            self._save_data_sync()
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
        if not bool(getattr(self, "enable_screen_glance_action", False)) or not bool(
            getattr(self, "enable_goodnight_screen_check", False)
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
            min(180, _safe_int(getattr(self, "goodnight_screen_check_delay_minutes", 45), 45, 1, 180)),
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
        if not bool(getattr(self, "enable_screen_glance_action", False)):
            return "screen_glance_disabled"
        if not bool(getattr(self, "enable_goodnight_screen_check", False)):
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
            self._save_data_sync()

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
                self._save_data_sync()

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
                    self._save_data_sync()
                    continue
                name = _single_line(user.get("nickname"), 40) or user_id

            state = await self._classify_goodnight_screen_activity(user_id, user, name=name)
            async with self._data_lock:
                current = self._get_user(user_id)
                current["goodnight_screen_check_state"] = state
                current["goodnight_screen_check_result_at"] = _now_ts()
                self._save_data_sync()
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
                    self._save_data_sync()
                    continue
                current["proactive_sending"] = True
                current["proactive_sending_started_at"] = _now_ts()
                user = current
                name = _single_line(current.get("nickname"), 40) or user_id
                umo = _single_line(current.get("umo"), 240)
                self._save_data_sync()

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
                    self._save_data_sync()
            finally:
                async with self._data_lock:
                    current = self._get_user(user_id)
                    current["proactive_sending"] = False
                    current["proactive_sending_started_at"] = 0
                    self._save_data_sync()

    async def _run_screen_peek_action(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        *,
        quota_exempt: bool = False,
    ) -> str:
        if not self.enable_screen_glance_action:
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
            self._save_data_sync()
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
        cooldown_seconds = max(0, _safe_int(getattr(self, "poke_action_cooldown_minutes", 30), 30, 0, 1440)) * 60
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
        if not self.enable_poke_action:
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
                self._save_data_sync()
                reserved = True
            for index in range(poke_count):
                await self._send_single_poke(client, user_id=user_id, group_id=group_id)
                if index + 1 < poke_count:
                    await asyncio.sleep(random.uniform(0.35, 0.9))
            async with self._data_lock:
                current = self._get_user(user_id)
                current["last_poke_action_at"] = _now_ts()
                current["poke_action_inflight_until"] = 0
                self._save_data_sync()
            if poke_count <= 1:
                return f"poke：已轻轻戳了 {name} 一下\n主动原因：{reason}"
            return f"poke：已轻轻连着戳了 {name} {poke_count} 下\n主动原因：{reason}"
        except Exception as e:
            if reserved:
                try:
                    async with self._data_lock:
                        current = self._get_user(user_id)
                        current["poke_action_inflight_until"] = 0
                        self._save_data_sync()
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
        if not self.enable_voice_action:
            return {"success": False, "context": "voice：未启用", "extra_components": [], "summary": "语音"}
        target = str(user.get("umo") or "").strip()
        if not target:
            return {"success": False, "context": "voice：缺少目标会话,无法发送语音", "extra_components": [], "summary": "语音"}
        voice_text = await self._build_voice_note_text(user, name, reason, target=target)
        components, audio_note = await self._create_voice_record_component(target, voice_text)
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
        return {
            "success": True,
            "context": (
                "voice：已生成真实语音\n"
                f"语音内容：{self._strip_tts_markup(voice_text)}\n"
                f"真实语音文件：{audio_note}"
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
        user_id = str(user_id or parsed_user_id or "").strip()
        if not parsed_user_id or parsed_user_id != user_id or not user_id.isdigit():
            return
        tasks = getattr(self, "_passive_input_status_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self._passive_input_status_tasks = tasks
        old_task = tasks.get(user_id)
        if isinstance(old_task, asyncio.Task) and not old_task.done():
            old_task.cancel()
        task = asyncio.create_task(self._passive_input_status_loop(user_id))
        tasks[user_id] = task
        try:
            setattr(event, "private_companion_input_status_user_id", user_id)
        except Exception:
            pass

        def _cleanup(done_task: asyncio.Task) -> None:
            current = tasks.get(user_id)
            if current is done_task:
                tasks.pop(user_id, None)

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
        variants = (
            ("set_diy_online_status", {"face_id": 21, "face_type": 1, "wording": custom_text}),
            ("set_diy_online_status", {"face_id": "21", "face_type": "1", "wording": custom_text}),
            ("set_diy_online_status", {"faceId": 21, "faceType": 1, "wording": custom_text}),
            ("set_diy_online_status", {"id": 21, "face_type": 1, "wording": custom_text}),
            ("set_diy_online_status", {"wording": custom_text}),
            ("set_diy_online_status", {"face_id": 21, "text": custom_text}),
            ("set_diy_online_status", {"faceId": 21, "text": custom_text}),
            ("set_diy_online_status", {"id": 21, "text": custom_text}),
            ("set_diy_online_status", {"text": custom_text}),
        )
        for action, params in variants:
            if await self._call_onebot_action(client, action, **params):
                return True, f"自定义状态：{custom_text}"
        return False, f"平台不支持自定义状态：{custom_text}"

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
            self._save_data_sync()

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
                spoken = _single_line(spoken, self.voice_action_max_chars)
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
                    provider_id=self._task_provider(self.voice_prompt_provider_id, self.mai_style_provider_id),
                    task="voice_repair",
                )
                if repaired:
                    spoken = str(repaired).strip()
                    if "<tts>" not in spoken:
                        spoken = _single_line(spoken, self.voice_action_max_chars)
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
3. 整体要短,适合私聊语音,不像朗读稿,也不要太正式；纯中文可控制在 {self.voice_action_max_chars} 个字以内。
4. 可以有一点嘴硬、黏人、藏着的想念,但不要把喜欢说满。
5. 不要提 AI、模型、插件、TTS、语音合成这些词。
""".strip()
        text = await self._llm_call(
            prompt,
            max_tokens=120,
            provider_id=self._task_provider(self.voice_prompt_provider_id, self.mai_style_provider_id),
            task="voice",
        )
        spoken = str(text or "").strip()
        if not spoken:
            spoken = random.choice(VOICE_FALLBACK_TEMPLATES)
        if "<tts>" not in spoken:
            spoken = _single_line(spoken, self.voice_action_max_chars)
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
                provider_id=self._task_provider(self.voice_prompt_provider_id, self.mai_style_provider_id),
                task="voice_repair",
            )
            if repaired:
                spoken = str(repaired).strip()
                if "<tts>" not in spoken:
                    spoken = _single_line(spoken, self.voice_action_max_chars)
                    spoken = re.sub(r"[“”\"'`]", "", spoken).strip()
        return spoken

    async def _create_voice_record_component(self, target: str, spoken_text: str) -> tuple[list[Any], str]:
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
            record = await record_builder(
                spoken_text,
                tts_provider,
                provider_settings,
                config,
                source_text=spoken_text,
                source="private_companion",
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
        if getattr(self, "enable_tts_enhancement", False):
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
        if has_kana and has_cjk and getattr(self, "tts_voice_language", "ja") != "zh":
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
        if not self.enable_photo_text_action:
            return "photo_text：未启用"
        if self._private_user_role(user) == "friend":
            return "photo_text：次要用户关系不使用主动生图或复用主要用户图片,不能假装已经拍照"
        load_defer_note = self._photo_text_load_defer_note("photo_text", force_refresh=True)
        if load_defer_note:
            return f"photo_text：{load_defer_note},不能假装已经拍照"
        if not self._photo_text_available(user):
            return "photo_text：今日发图额度已用完或生图后端不可用,不能假装已经拍照"
        if not self._photo_text_available():
            return "photo_text：当前没有可用的生图后端,不能假装已经拍照"

        scene = await self._build_photo_scene_prompt(user, name, reason)
        workflow_kind = scene.get("kind", "text2img")
        subject_owner = _normalize_photo_subject_owner(scene.get("subject_owner"))
        if not subject_owner:
            subject_owner = "bot" if bool(scene.get("use_persona_reference")) or workflow_kind == "selfie" else "scene"
        session_key = str(user.get("umo") or user.get("user_id") or name)
        continuity_key = self._compose_photo_continuity_key(session_key, user.get("user_id"))
        reference_image_path = ""
        if bool(scene.get("use_persona_reference")):
            # Character-bearing photo_text scenes need identity continuity even when
            # their rendering workflow is text2img rather than selfie.
            reference_image_path = await self._photo_persona_reference_image_for_kind_async(
                "selfie",
                allow_daily_outfit=True,
                request_text=scene["prompt"],
            )
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
                    self._note_photo_generation_attempt(str(user.get("user_id") or ""), image_path="")
                    self._save_data_sync()
            return (
                "photo_text：生图失败,不能假装已经拍照\n"
                f"画面草稿：{scene['caption']}\n"
                f"失败原因：{_single_line(workflow_note, 160)}"
                + ("\n本次已计入今日生图尝试额度,避免接口失败时反复请求。" if counted_attempt else "")
            )
        async with self._data_lock:
            self._note_photo_generation_attempt(str(user.get("user_id") or ""), image_path=image_path)
            self._save_data_sync()
        scene_context_line = _single_line(scene.get("scene_context"), 500)
        return (
            f"photo_text：已通过 {backend_name} 生成真实图片\n"
            f"图片类型：{workflow_kind}\n"
            f"后端：{backend_name}\n"
            f"图片路径：{image_path}\n"
            f"画面：{scene['caption']}\n"
            f"图片主体归属：{subject_owner}\n"
            f"人物参考图：{'已使用' if reference_image_path else '未使用'}\n"
            + (f"统一情境：{scene_context_line}\n" if scene_context_line else "")
            + f"生图提示：{_single_line(scene['prompt'], 240)}"
        )

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
        if not force and not getattr(self, "enable_daily_outfit_photo", False):
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
        if not self.enable_photo_text_action:
            return await self._record_daily_outfit_photo_result(today, "", "主动拍照/生图未开启")
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
            self._save_data_sync()
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
        rotation_days = _safe_int(getattr(self, "daily_outfit_rotation_days", 10), 10, 1, 30)
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
        custom = _single_line(getattr(self, "daily_outfit_photo_prompt", ""), 220)
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
                    _single_line(part, 220) for part in positive if _single_line(part, 220)
                ),
                negative=", ".join(negative),
                protected=True,
            )
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
        persona = str(getattr(self, "schedule_persona_prompt", "") or "")
        recognition = str(getattr(self, "private_image_self_recognition_hint", "") or "")
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
                f"preferred={_single_line(getattr(self, 'photo_generation_backend', ''), 30) or 'auto'} "
                f"comfyui={self._comfyui_photo_available()} "
                f"sdgen={self._sdgen_photo_available()} "
                f"external={self._external_photo_available()} "
                f"external_queue={len(endpoints)} "
                f"external_queue_items={';'.join(endpoint_bits) or '-'} "
                f"backup_note={_single_line(self._backup_external_photo_unavailable_note(), 80) or '-'} "
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
            f"preferred={_single_line(getattr(self, 'photo_generation_backend', ''), 30) or 'auto'} "
            f"comfyui={self._comfyui_photo_available()} "
            f"sdgen={self._sdgen_photo_available()} "
            f"external={self._external_photo_available()} "
            f"external_platform={self._resolved_external_image_api_platform()} "
            f"external_model={_single_line(getattr(self, 'external_image_api_model', ''), 80) or '-'} "
            f"external_size={_single_line(getattr(self, 'external_image_api_size', ''), 40) or '-'} "
            f"external_base={external_base or '-'} "
            f"backup_external={self._backup_external_photo_available()} "
            f"backup_platform={_single_line(getattr(self, 'backup_external_image_api_platform', ''), 30) or '-'} "
            f"backup_model={_single_line(getattr(self, 'backup_external_image_api_model', ''), 80) or '-'} "
            f"backup_base={backup_base or '-'} "
            f"backup_note={_single_line(self._backup_external_photo_unavailable_note(), 80) or '-'} "
            f"tool_call={self._custom_tool_photo_available()} "
            f"tool_name={_single_line(getattr(self, 'custom_photo_tool_name', ''), 80) or '-'}"
        )

    def _photo_generation_trace_max_bytes(self) -> int:
        max_kb = _safe_int(
            getattr(self, "photo_generation_trace_max_size_kb", 0),
            0,
        )
        return max(0, min(102400, max_kb)) * 1024

    def _photo_generation_trace_backup_count(self) -> int:
        return max(
            0,
            min(
                20,
                _safe_int(getattr(self, "photo_generation_trace_backup_count", 5), 5),
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

            item = {
                "schema_version": 2,
                "ts": _now_ts(),
                "trace": _single_line(trace_id, 40),
                "session": _single_line(session_key, 340),
                "continuity_key": self._normalize_photo_continuity_key(continuity_key),
                "kind": _single_line(workflow_kind, 30),
                "backend": _single_line(backend, 80),
                "ok": bool(ok),
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
            self._save_data_sync()
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
        self._save_data_sync()
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
                    "schema_version": 3,
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
                self._save_data_sync()
                return
        except Exception as exc:
            logger.debug("[PrivateCompanion] 标注最近生图记录失败: %s", _single_line(exc, 120))

    def _apply_photo_generation_fixed_prompt(self, prompt_text: str) -> str:
        prompt = str(prompt_text or "").strip()
        fixed = _single_line(getattr(self, "photo_generation_fixed_prompt", ""), 500)
        if not fixed:
            return prompt
        if fixed in prompt:
            return _single_line(prompt, 1800)
        return _single_line(f"{prompt}\n\nAdditional fixed prompt: {fixed}".strip(), 1800)

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
            getattr(self, "photo_generation_prompt_format", "traditional")
        )

    def _photo_generation_prompt_format_instruction(self) -> str:
        mode = self._photo_generation_prompt_format_mode()
        if mode == "nai":
            return (
                "使用 NAI（NovelAI 4/4.5）联动写法：以英文 danbooru 风格标签为主、逗号分隔，精简到能讲清构图即可，不堆砌重复或无意义 tag。"
                "加权用花括号 {tag} 提升、方括号 [tag] 降低，可叠层；也可用 权重::标签:: 对一个或多个标签整体加权（示例 1.5::red dress, long dress::），"
                "可以使用较高权重值（2、5 甚至 10 以上）强调关键元素。移除物体或翻转概念用负向权重（示例 -1::unwanted object::）；"
                "混合 3 个以上画师风格时可加 -2::artist collaboration:: 降低鬼图概率。已知二次元角色用 角色名 (作品名) 形式（示例 texas the omertosa (arknights)），"
                "特征不全时多补几个描述词；情绪词有效，可加入增强表情。多角色（最多 6 名）时每个角色分别用 {人物 [该角色的画风/动作/神态/外貌 tags] 人物} 包裹，"
                "块内两个占位符“人物”不能删除，可用 {位置中} {位置左} {位置右上} 等位置标签（5x5 共 25 种）指定站位，角色专属负面词条写 ntags = [tags]；"
                "角色互动动作用 source#/target#/mutual# 前缀（示例 source#hug 发起拥抱, target#hug 被拥抱, mutual#hug 互相拥抱）。"
                "需要画面英文文字用 Text: 内容, ；不需要文字加 no text, 。直接输出可投喂生图后端的提示词字符串，"
                "不要输出 Positive prompt/Negative prompt 标题，也不要写解释性段落。"
            )
        if mode == "natural_language":
            return (
                "使用自然语言描述：用连贯、具体的英文句子描述主体、外观、动作、场景、光线、镜头、构图和风格；"
                "不要输出标签堆、权重语法或 Positive prompt/Negative prompt 标题。需要避免的内容可在末句用 Avoid ... 自然表达。"
            )
        return (
            "使用传统文生图提示词：英文短词组和逗号分隔标签，按主体、外观、服装、场景、光线、镜头、构图、风格排列；"
            "使用 Positive prompt: ... Negative prompt: ... 结构，不要写解释性段落。"
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
        presets.update(self._parse_photo_generation_scene_presets(getattr(self, "photo_generation_scene_presets", "")))
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
        *,
        workflow_kind: str,
        prompt_text: str,
        request_text: str = "",
        session_key: str,
        continuity_key: str = "",
        requester_user_id: str = "",
        requester_is_private: bool | None = None,
        reference_image_path: str = "",
        reference_image_paths: Any = (),
        image_size: str = "",
        allow_daily_outfit_reference: bool = True,
        requested_scene_preset: str = "",
        suggested_scene_preset: str = "",
        workflow_default_scene_preset: str = "",
        prompt_sections: tuple[PhotoPromptSection, ...] | None = None,
        prompt_format: str = "",
    ) -> tuple[str, str, str]:
        started = time.time()
        trace_id = self._photo_generation_trace_id(session_key, workflow_kind)
        original_prompt_text = str(prompt_text or "").strip()
        prompt_format = (
            self._normalize_photo_generation_prompt_format(prompt_format)
            if prompt_format
            else self._photo_generation_prompt_format_mode()
        )
        request_text = str(request_text or original_prompt_text).strip()
        requester_user_id = _single_line(requester_user_id, 80)
        if requester_is_private is None:
            requester_is_private = bool(
                re.search(r"(?:friendmessage|privatemessage|private)", str(session_key or ""), flags=re.I)
            )
        self._append_photo_generation_trace_event(
            trace_id,
            "request_received",
            data={
                "request_text": request_text,
                "prompt_text": original_prompt_text,
                "image_size": image_size,
            },
            context={
                "session": session_key,
                "continuity_key": continuity_key,
                "workflow_kind": workflow_kind,
                "request_text": request_text,
                "prompt_format": prompt_format,
            },
        )
        reference_image_path = _path_text(reference_image_path, 1000)
        explicit_reference_paths: list[str] = []
        raw_reference_paths = reference_image_paths
        if isinstance(raw_reference_paths, str):
            raw_reference_paths = (raw_reference_paths,)
        for raw_path in (reference_image_path, *(raw_reference_paths or ())):
            path = _path_text(raw_path, 1000)
            if path and path not in explicit_reference_paths:
                explicit_reference_paths.append(path)
        explicit_reference_supplied = bool(explicit_reference_paths)
        # requested_scene_preset is the documented tool/generator contract.
        # Keep the short-lived suggested_scene_preset name as a compatibility alias.
        suggested_scene_preset = _single_line(
            requested_scene_preset or suggested_scene_preset,
            80,
        )
        workflow_default_scene_preset = _single_line(workflow_default_scene_preset, 80)
        wardrobe_intent = analyze_photo_wardrobe(request_text)
        user_requested_outfit_category = (
            str(wardrobe_intent.target_category or "").strip().lower()
        )
        reference_intent = await self._analyze_photo_reference_intent_async(
            request_text,
            has_explicit_reference=bool(explicit_reference_paths),
            workflow_kind=workflow_kind,
        )
        self._append_photo_generation_trace_event(
            trace_id,
            "reference_intent_analyzed",
            data={
                "requested_roles": list(reference_intent.requested_roles),
                "excluded_roles": list(reference_intent.excluded_roles),
                "continuity_mode": reference_intent.continuity_mode,
                "confidence": reference_intent.confidence,
                "source": reference_intent.source,
            },
        )
        base_prompt = self._apply_photo_generation_prompt_format(
            original_prompt_text,
            prompt_format=prompt_format,
        )
        current_user_request = wardrobe_intent.positive_text
        current_user_exclusions = wardrobe_intent.exclusion_text
        normalized_kind = str(workflow_kind or "").strip().lower()
        scene_context_before = ""
        schedule_history_context = ""
        if normalized_kind in {"selfie", "portrait", "自拍", "人像"}:
            schedule_history_context = self._photo_reference_schedule_history_context()
            if allow_daily_outfit_reference:
                if not requester_user_id:
                    try:
                        scene_context_before = self._photo_generation_selfie_schedule_scene_hint(
                            "",
                            include_dialogue_outfit=bool(requester_is_private),
                        )
                    except TypeError:
                        scene_context_before = self._photo_generation_selfie_schedule_scene_hint()
                else:
                    try:
                        scene_context_before = self._photo_generation_selfie_schedule_scene_hint(
                            requester_user_id,
                            include_dialogue_outfit=bool(requester_is_private),
                        )
                    except TypeError:
                        scene_context_before = self._photo_generation_selfie_schedule_scene_hint()
        dialogue_outfit_match = re.search(
            r"对话最新服装\s*[：:]\s*(.+?)(?=；|;|$)",
            scene_context_before,
        )
        dialogue_outfit_instruction = _single_line(
            dialogue_outfit_match.group(1) if dialogue_outfit_match else "",
            180,
        )
        if (
            dialogue_outfit_instruction
            and normalized_kind in {"selfie", "portrait", "自拍", "人像"}
            and not wardrobe_intent.target_category
            and not wardrobe_intent.excluded_categories
        ):
            # Expand a short “continue/take another one” request with the
            # latest dialogue outfit before wardrobe/reference selection. This
            # keeps the user-facing prompt intact while preventing a daily
            # outfit image from silently restoring an older costume.
            continuity_request = (
                f"{request_text}\n角色当前仍穿着：{dialogue_outfit_instruction}；"
                "除本轮明确要求外保持最近对话中的人物与场景连续。"
            ).strip()
            wardrobe_intent = merge_photo_wardrobe_continuity(
                wardrobe_intent,
                continuity_request,
            )
        current_user_request = wardrobe_intent.positive_text
        current_user_exclusions = wardrobe_intent.exclusion_text
        selection_request = self._photo_prompt_clip(
            "\n\n".join(
                part
                for part in (
                    current_user_request or request_text,
                    f"Explicit wardrobe exclusions: {current_user_exclusions}" if current_user_exclusions else "",
                )
                if part
            ),
            1800,
            preserve_tail=True,
        )
        reference_plan = await self._select_photo_reference_plan_async(
            workflow_kind,
            reference_intent=reference_intent,
            wardrobe_intent=wardrobe_intent,
            allow_daily_outfit=allow_daily_outfit_reference,
            requester_user_id=requester_user_id,
            session_key=session_key,
            request_text=selection_request,
            ambient_context=scene_context_before,
            schedule_history_context=schedule_history_context,
            suggested_scene_preset=suggested_scene_preset,
            continuity_key=continuity_key,
            explicit_reference_paths=explicit_reference_paths,
            require_existing_paths=True,
            trace_id=trace_id,
            requested_outfit_category=user_requested_outfit_category,
        )
        self._append_photo_generation_trace_event(
            trace_id,
            "reference_plan_built",
            data={
                "primary_reference_id": reference_plan.primary_reference_id,
                "selection_reason": reference_plan.selection_reason,
                "fallback_reason": reference_plan.fallback_reason,
                "bindings": [
                    {
                        "reference_id": binding.reference_id,
                        "path": binding.path,
                        "roles": list(binding.roles),
                        "priority": binding.priority,
                        "preserve": list(binding.preserve),
                        "ignore": list(binding.ignore),
                    }
                    for binding in reference_plan.bindings
                ],
            },
        )
        backend_reference_capacity = self._photo_reference_backend_max_images(
            workflow_kind,
            requested_images=len(reference_plan.bindings),
        )
        submitted_bindings, plan_text_fallback = project_reference_plan_for_backend(
            reference_plan,
            max_images=backend_reference_capacity,
        )
        submitted_reference_id_set = {binding.reference_id for binding in submitted_bindings}
        omitted_role_names = [
            _single_line((binding.candidate or {}).get("role_name"), 80)
            for binding in reference_plan.bindings
            if binding.reference_id not in submitted_reference_id_set
            and isinstance(binding.candidate, dict)
            and binding.candidate.get("kind") == "relation_role"
            and _single_line(binding.candidate.get("role_name"), 80)
        ]
        if omitted_role_names:
            role_capacity_fallback = (
                "Relationship-role reference capacity fallback: the backend could not submit the visual reference for "
                f"{', '.join(dict.fromkeys(omitted_role_names))}. Do not claim an exact visual identity match for the omitted role; "
                "follow the explicit text request conservatively and do not transfer another submitted person's face or body to that role."
            )
            plan_text_fallback = "\n".join(
                part for part in (plan_text_fallback, role_capacity_fallback) if part
            )
        self._append_photo_generation_trace_event(
            trace_id,
            "reference_plan_projected",
            data={
                "backend_capacity": backend_reference_capacity,
                "capacity_kind": "routing_capability_envelope",
                "planned_reference_ids": [
                    binding.reference_id for binding in submitted_bindings
                ],
                "submitted_reference_ids": [binding.reference_id for binding in submitted_bindings],
                "text_fallback": plan_text_fallback,
            },
        )
        reference_candidate: dict[str, Any] = {}
        reference_image_path = ""
        submitted_reference_entries: list[tuple[Any, dict[str, Any], str]] = []
        for binding in submitted_bindings:
            candidate = await self._photo_reference_candidate_from_plan_binding_async(
                binding,
                workflow_kind=workflow_kind,
                allow_daily_outfit=allow_daily_outfit_reference,
                continuity_key=continuity_key,
            )
            candidate_path = _path_text(candidate.get("path"), 1000)
            if candidate and candidate_path:
                submitted_reference_entries.append((binding, candidate, candidate_path))
        submitted_bindings = tuple(entry[0] for entry in submitted_reference_entries)
        reference_image_paths = [entry[2] for entry in submitted_reference_entries]
        if submitted_reference_entries:
            reference_candidate = submitted_reference_entries[0][1]
            reference_image_path = submitted_reference_entries[0][2]
        if (
            not reference_candidate
            and self._photo_persona_fallback_allowed(
                workflow_kind,
                reference_intent,
            )
        ):
            fallback_path = await self._photo_persona_reference_image_path_async()
            if fallback_path:
                fallback_candidate = {
                    "id": "persona",
                    "kind": "persona",
                    "path": fallback_path,
                    "source": fallback_path,
                    "reference_roles": ["identity"],
                    "outfit_lock_default": False,
                }
                fallback_plan = build_photo_reference_plan(
                    reference_intent,
                    (fallback_candidate,),
                )
                fallback_bindings, fallback_text = project_reference_plan_for_backend(
                    fallback_plan,
                    max_images=backend_reference_capacity,
                )
                if fallback_bindings:
                    reference_plan = fallback_plan
                    submitted_bindings = fallback_bindings
                    plan_text_fallback = fallback_text
                    reference_candidate = fallback_candidate
                    reference_image_path = fallback_path
                    submitted_reference_entries = [
                        (fallback_bindings[0], fallback_candidate, fallback_path)
                    ]
                    reference_image_paths = [fallback_path]
        if len(submitted_reference_entries) > 1:
            responsibilities = "; ".join(
                f"reference image {index}: {', '.join(binding.roles)}"
                + (
                    f" for relationship role {_single_line(candidate.get('role_name'), 80)}"
                    if candidate.get("kind") == "relation_role"
                    else ""
                )
                for index, (binding, candidate, _path) in enumerate(
                    submitted_reference_entries,
                    start=1,
                )
            )
            multi_reference_instruction = (
                "Submitted visual reference responsibilities: "
                f"{responsibilities}. Use each image only for its assigned roles; "
                "do not copy unrelated clothing, pose, scene, or style details. For a relationship-role image, preserve that role's identity and do not transfer it to Bot."
            )
            plan_text_fallback = "\n".join(
                part for part in (multi_reference_instruction, plan_text_fallback) if part
            )

        structured_reference_gate, structured_reference_plan, structured_reference_status = (
            self._q5_prepare_structured_reference_plan(
                generation_id=trace_id,
                workflow_kind=workflow_kind,
                prompt_text=original_prompt_text or base_prompt,
                reference_candidate=reference_candidate,
                explicit_reference_supplied=explicit_reference_supplied,
            )
        )
        if structured_reference_plan:
            # Do not retain legacy paths in the prompt/trace route once the
            # managed sink is selected. The gate alone holds usable file paths.
            reference_candidate = self._q5_managed_reference_candidate(
                structured_reference_plan
            )
            reference_image_path = ""
            reference_image_paths = []
            submitted_reference_entries = []
            submitted_bindings = ()
            plan_text_fallback = ""
            reference_plan = build_photo_reference_plan(reference_intent, ())

        wardrobe = resolve_photo_wardrobe_decision(
            workflow_kind=workflow_kind,
            prompt_text=original_prompt_text or base_prompt,
            intent=wardrobe_intent,
            reference=reference_candidate,
            scene_context=scene_context_before,
            suggested_scene_preset=suggested_scene_preset,
            workflow_default_scene_preset=workflow_default_scene_preset,
            base_prompt=base_prompt,
            available_presets=self._photo_generation_scene_presets().keys(),
        )
        self._append_photo_generation_trace_event(
            trace_id,
            "wardrobe_resolved",
            data=wardrobe.as_dict(),
        )
        base_prompt = wardrobe.base_prompt
        scene_context_after = wardrobe.scene_context
        removed_conflicts = list(wardrobe.adjustments)
        preset_names = list(wardrobe.selected_presets)
        preset_section, preset_names = self._apply_photo_generation_scene_presets(
            "",
            workflow_kind,
            preset_names=preset_names,
        )
        continuity_section, recent_continuity_reference = self._photo_generation_recent_continuity_constraint(
            workflow_kind,
            reference_image_path=reference_image_path,
            continuity_key=continuity_key,
            wardrobe=wardrobe,
        )
        initial_sections = list(prompt_sections or ())
        if not initial_sections:
            # Keep natural NAI exclusions negative without restoring context removed from base_prompt.
            user_positive, user_negative = self._photo_generation_semantic_prompt_parts(
                base_prompt
            )
            if not user_positive and not user_negative:
                user_positive, user_negative = self._photo_prompt_split_formatted(base_prompt)
            initial_sections.append(
                PhotoPromptSection(
                    name="user_request",
                    source="user_request",
                    positive=user_positive,
                    negative=user_negative,
                    protected=True,
                )
            )
        wardrobe_positive, wardrobe_negative = self._photo_generation_reference_wardrobe_section(
            reference_candidate,
            wardrobe,
        )
        compact_scene_hint = self._photo_generation_compact_scene_hint(scene_context_after)
        scene_section = self._photo_generation_selfie_scene_constraint(
            workflow_kind,
            compact_scene_hint,
            has_reference=bool(reference_candidate),
        )
        role_reference_submitted = any(
            candidate.get("kind") == "relation_role"
            for _binding, candidate, _path in submitted_reference_entries
        )
        group_role_reference_supplied = role_reference_submitted and _photo_group_request_matches(request_text)
        composition_positive, composition_negative = self._photo_generation_composition_sections(
            workflow_kind,
            request_text,
            allow_group_photo=(
                explicit_reference_supplied
                or group_role_reference_supplied
            ),
        )
        subject_count_positive, subject_count_negative = self._photo_generation_subject_count_contract(
            workflow_kind,
            request_text,
            explicit_reference_supplied=(
                explicit_reference_supplied
                or group_role_reference_supplied
            ),
        )
        edit_positive, edit_negative = self._photo_generation_edit_contract(workflow_kind)
        role_reference_names = [
            _single_line(candidate.get("role_name"), 80)
            for _binding, candidate, _path in submitted_reference_entries
            if candidate.get("kind") == "relation_role" and _single_line(candidate.get("role_name"), 80)
        ]
        role_reference_positive = ""
        if role_reference_names:
            named_roles = "、".join(dict.fromkeys(role_reference_names))
            role_reference_positive = (
                "Named relationship-role reference(s) attached: "
                f"{named_roles}. Preserve each attached image as the visual identity of that named role, "
                "never transfer its face, body, hair, or clothing identity to Bot. "
                + (
                    "The current request explicitly asks for a shared frame, so keep Bot and each named role as distinct people in one coherent scene."
                    if group_role_reference_supplied
                    else "The current request names this role, so show that role only when the request asks it to appear; otherwise keep the role off-camera."
                )
            )
        fixed_prompt = str(getattr(self, "photo_generation_fixed_prompt", "") or "").strip()
        generated_sections = (
            PhotoPromptSection(
                name="wardrobe_decision",
                source="wardrobe_decision",
                positive=wardrobe_positive,
                negative=wardrobe_negative,
            ),
            PhotoPromptSection(
                name="managed_reference_constraints",
                source="managed_reference",
                positive=(
                    structured_reference_plan.prompt_constraint
                    if structured_reference_plan
                    else ""
                ),
                protected=True,
            ),
            PhotoPromptSection(
                name="reference_plan_fallback",
                source="reference_fallback",
                positive=plan_text_fallback,
            ),
            PhotoPromptSection(
                name="relationship_role_reference",
                source="composition",
                positive=role_reference_positive,
            ),
            PhotoPromptSection(
                name="scene_context",
                source="scene_context",
                positive=scene_section,
            ),
            PhotoPromptSection(
                name="scene_preset",
                source="preset",
                positive=preset_section,
            ),
            PhotoPromptSection(
                name="global_fixed_prompt",
                source="fixed_prompt",
                positive=f"Additional fixed prompt: {fixed_prompt}"
                if fixed_prompt
                else "",
                protected=True,
            ),
            PhotoPromptSection(
                name="edit_contract",
                source="edit_contract",
                positive=edit_positive,
                negative=edit_negative,
            ),
            PhotoPromptSection(
                name="composition",
                source="composition",
                positive=composition_positive,
                negative=composition_negative,
            ),
            PhotoPromptSection(
                name="subject_count",
                source="composition",
                positive=subject_count_positive,
                negative=subject_count_negative,
            ),
            PhotoPromptSection(
                name="recent_continuity",
                source="recent_continuity",
                positive=continuity_section,
            ),
        )
        context_before = tuple((*initial_sections, *generated_sections))
        resolved_context = resolve_photo_prompt_context(
            wardrobe=wardrobe,
            sections=context_before,
            prompt_format=prompt_format,
            workflow_kind=workflow_kind,
            reference=reference_candidate or None,
        )
        prompt_text = resolved_context.final_prompt
        complete_prompt_text = resolved_context.complete_prompt
        reference_candidate = dict(resolved_context.reference or {})
        if structured_reference_plan:
            # The public generation record retains only the managed asset ID and
            # role projection. Its paths remain inside ReferenceAssetGate.
            reference_candidate = self._q5_managed_reference_candidate(
                structured_reference_plan
            )
            reference_image_path = ""
            reference_image_paths = []
            submitted_reference_entries = []
            submitted_bindings = ()
        else:
            reference_image_path = _path_text(reference_candidate.get("path"), 1000)
            if not reference_candidate:
                submitted_reference_entries = []
                reference_image_paths = []
                submitted_bindings = ()
            elif submitted_reference_entries:
                submitted_reference_entries[0] = (
                    submitted_reference_entries[0][0],
                    reference_candidate,
                    reference_image_path,
                )
                reference_image_paths = [entry[2] for entry in submitted_reference_entries]
        try:
            reference_exists = bool(reference_image_path and Path(reference_image_path).is_file())
        except (OSError, ValueError):
            reference_exists = False
        valid_submitted_entries: list[tuple[Any, dict[str, Any], str]] = []
        for entry in submitted_reference_entries:
            try:
                if Path(entry[2]).is_file():
                    valid_submitted_entries.append(entry)
            except (OSError, ValueError):
                continue
        submitted_reference_entries = valid_submitted_entries
        submitted_bindings = tuple(entry[0] for entry in submitted_reference_entries)
        reference_image_paths = [entry[2] for entry in submitted_reference_entries]
        reference_image_path = reference_image_paths[0] if reference_image_paths else ""
        submitted_reference_ids = tuple(
            binding.reference_id for binding in submitted_bindings
        )
        reference_fallback = evaluate_reference_fallback(
            reference_intent,
            reference_plan,
            submitted_reference_ids=submitted_reference_ids,
        )
        if structured_reference_plan:
            reference_fallback = ReferenceFallback((), (), (), "")
        def section_log_key(section: PhotoPromptSection, used: dict[str, int]) -> str:
            base = _single_line(section.name, 80) or section.source
            used[base] = used.get(base, 0) + 1
            return base if used[base] == 1 else f"{base}#{used[base]}"

        prompt_sections_for_log: dict[str, str] = {}
        prompt_sections_after: dict[str, Any] = {}
        after_names: dict[str, int] = {}
        for section in resolved_context.prompt_sections:
            key = section_log_key(section, after_names)
            if section.positive or section.negative:
                prompt_sections_for_log[key] = "\n".join(
                    part for part in (section.positive, section.negative) if str(part or "").strip()
                )
            prompt_sections_after[key] = {
                "source": section.source,
                "positive": section.positive,
                "negative": section.negative,
                "protected": section.protected,
            }
        detected_conflict_details = [dict(item) for item in resolved_context.detected_conflicts]
        removed_conflict_details = [dict(item) for item in resolved_context.removed_conflicts]
        residual_conflict_details = [dict(item) for item in resolved_context.residual_conflicts]
        conflicts = [
            f"{item.get('source')}:{item.get('rule')}:{item.get('category')}"
            for item in resolved_context.detected_conflicts
        ]
        removed_conflicts.extend(
            f"{item.get('source')}:{item.get('rule')}:{item.get('category')}:{item.get('action')}"
            for item in resolved_context.removed_conflicts
        )
        if resolved_context.reference_removed:
            item = resolved_context.reference_removed
            removed_conflicts.append(
                f"reference:{item.get('rule')}:{item.get('category')}:reference_removed"
            )
            recent_continuity_reference = False
        residual_conflicts = [
            f"{item.get('source')}:{item.get('rule')}:{item.get('category')}"
            for item in resolved_context.residual_conflicts
        ]
        prompt_sections_before: dict[str, Any] = {}
        before_names: dict[str, int] = {}
        for section in context_before:
            prompt_sections_before[section_log_key(section, before_names)] = {
                "source": section.source,
                "positive": section.positive,
                "negative": section.negative,
                "protected": section.protected,
            }
        prompt_path, prompt_hash = self._write_photo_prompt_debug_file(
            trace_id=trace_id,
            session_key=session_key,
            workflow_kind=workflow_kind,
            base_prompt=base_prompt,
            scene_context_before=scene_context_before,
            scene_context_after=scene_context_after,
            reference=reference_candidate,
            reference_intent=reference_intent,
            reference_plan=reference_plan,
            reference_fallback=reference_fallback,
            wardrobe=wardrobe,
            presets=preset_names,
            prompt_sections_before=prompt_sections_before,
            prompt_sections=prompt_sections_for_log,
            prompt_sections_after=prompt_sections_after,
            final_prompt=complete_prompt_text,
            submitted_prompt=prompt_text,
            conflicts=conflicts,
            removed_conflicts=removed_conflicts,
            residual_conflicts=residual_conflicts,
            detected_conflict_details=detected_conflict_details,
            removed_conflict_details=removed_conflict_details,
            residual_conflict_details=residual_conflict_details,
            reference_removed=resolved_context.reference_removed,
            sanitizer_version=resolved_context.sanitizer_version,
            suggested_scene_preset=suggested_scene_preset,
            prompt_format=prompt_format,
        )
        self._append_photo_generation_trace_event(
            trace_id,
            "prompt_composed",
            data={
                "prompt_format": prompt_format,
                "prompt": complete_prompt_text,
                "submitted_prompt": prompt_text,
                "prompt_hash": prompt_hash,
                "submitted_prompt_hash": hashlib.sha256(
                    str(prompt_text or "").encode("utf-8", "ignore")
                ).hexdigest(),
                "prompt_path": prompt_path,
                "sections": prompt_sections_for_log,
                "conflicts": conflicts,
                "removed_conflicts": removed_conflicts,
                "residual_conflicts": residual_conflicts,
                "sanitizer_version": resolved_context.sanitizer_version,
            },
        )
        if scene_context_after:
            logger.info(
                "[PrivateCompanion] 自拍生图已加入当前日程地点约束: trace=%s session=%s hint=%s reference=%s",
                trace_id,
                _single_line(session_key, 80),
                _single_line(scene_context_after, 180),
                bool(reference_image_path),
            )
        preferred = self.photo_generation_backend
        self._append_photo_generation_trace_event(
            trace_id,
            "backend_selected",
            data={
                "preferred": preferred,
                "prompt_format": prompt_format,
                "reference_count": len(reference_image_paths),
                "structured_reference_status": structured_reference_status,
                "structured_reference_count": len(structured_reference_plan.assets)
                if structured_reference_plan
                else 0,
                "structured_reference_asset_ids": [
                    item.asset_id for item in structured_reference_plan.assets
                ]
                if structured_reference_plan
                else [],
                "structured_reference_roles": [
                    item.role for item in structured_reference_plan.assets
                ]
                if structured_reference_plan
                else [],
                "image_size": image_size,
            },
        )
        logger.info(
            "[PrivateCompanion] 生图开始: trace=%s session=%s kind=%s prompt_format=%s presets=%s prompt_chars=%s prompt_hash=%s prompt_debug=%s "
            "reference=%s reference_exists=%s reference_id=%s reference_kind=%s reference_roles=%s "
            "wardrobe_version=%s wardrobe_rule=%s wardrobe_mode=%s wardrobe_category=%s outfit_locked=%s "
            "preset_hint=%s preset_source=%s suggestion_status=%s selected_presets=%s adjustments=%s daily_outfit_removed=%s continuity_reference=%s "
            "sanitizer_version=%s conflicts=%s removed_conflicts=%s residual_conflicts=%s reference_removed=%s "
            "image_size=%s prompt_preview_chars=%s prompt_preview=%s %s",
            trace_id,
            _single_line(session_key, 80),
            _single_line(workflow_kind, 30),
            prompt_format,
            ",".join(preset_names) or "-",
            len(str(prompt_text or "")),
            prompt_hash[:16],
            _single_line(prompt_path, 160) or "-",
            bool(reference_image_path),
            reference_exists,
            _single_line(reference_candidate.get("id"), 60) or "-",
            _single_line(reference_candidate.get("kind"), 40) or "-",
            ",".join(reference_candidate.get("reference_roles") or []) or "-",
            wardrobe.decision_version,
            wardrobe.rule_id or "none",
            wardrobe.mode or "none",
            wardrobe.category or "none",
            wardrobe.lock_outfit,
            wardrobe.suggested_preset or "-",
            wardrobe.preset_source or "none",
            wardrobe.suggestion_status or "none",
            ",".join(wardrobe.selected_presets) or "-",
            ",".join(wardrobe.adjustments) or "-",
            wardrobe.remove_daily_outfit_context,
            recent_continuity_reference,
            resolved_context.sanitizer_version,
            ",".join(conflicts) or "-",
            ",".join(removed_conflicts) or "-",
            ",".join(residual_conflicts) or "-",
            bool(resolved_context.reference_removed),
            _single_line(image_size, 40) or "-",
            180,
            _single_line(prompt_text, 180),
            self._photo_generation_backend_config_summary(),
        )

        def finish(
            backend: str,
            image_path: str,
            note: str,
            *,
            reference_submitted: bool = False,
            structured_reference_submitted: bool = False,
        ) -> tuple[str, str, str]:
            elapsed_ms = int((time.time() - started) * 1000)
            image_path = _path_text(image_path, 1000)
            effective_submitted_reference_ids: tuple[str, ...] = ()
            if structured_reference_submitted and structured_reference_plan:
                effective_submitted_reference_ids = tuple(
                    item.asset_id for item in structured_reference_plan.assets
                )
                effective_reference_fallback = ReferenceFallback((), (), (), "")
            elif reference_submitted:
                submitted_count = self._photo_reference_submitted_count_from_note(
                    note,
                    len(submitted_reference_ids),
                )
                effective_submitted_reference_ids = tuple(
                    submitted_reference_ids[:submitted_count]
                )
                effective_reference_fallback = evaluate_reference_fallback(
                    reference_intent,
                    reference_plan,
                    submitted_reference_ids=effective_submitted_reference_ids,
                )
            else:
                effective_reference_fallback = evaluate_reference_fallback(
                    reference_intent,
                    reference_plan,
                    submitted_reference_ids=effective_submitted_reference_ids,
                )
            if (
                effective_reference_fallback.message
                and effective_reference_fallback.message not in str(note or "")
            ):
                note = (
                    f"{_single_line(note, 260)}；{effective_reference_fallback.message}"
                ).strip("；")
            output_exists = False
            if image_path:
                try:
                    output_exists = Path(image_path).is_file()
                except (OSError, ValueError):
                    output_exists = False
                if not output_exists:
                    invalid_path = image_path
                    image_path = ""
                    detail = f"后端返回的图片文件不存在：{_single_line(invalid_path, 160)}"
                    note = f"{_single_line(note, 260)}；{detail}" if note else detail
                    logger.warning(
                        "[PrivateCompanion] 生图后端返回无效图片路径: trace=%s backend=%s path=%s",
                        trace_id,
                        _single_line(backend, 60),
                        _single_line(invalid_path, 180),
                    )
            ok = bool(image_path and output_exists)
            reference_used = bool(
                ok
                and effective_submitted_reference_ids
                and (
                    structured_reference_submitted
                    or (reference_exists and reference_submitted)
                )
            )
            self._append_photo_generation_trace_event(
                trace_id,
                "output_validated",
                status="ok" if ok else "error",
                data={
                    "backend": backend,
                    "image_path": image_path,
                    "note": note,
                    "output_exists": output_exists,
                    "reference_submitted": reference_submitted,
                    "submitted_reference_ids": list(
                        effective_submitted_reference_ids
                    ),
                    "reference_used": reference_used,
                },
            )
            self._record_recent_photo_generation(
                trace_id=trace_id,
                session_key=session_key,
                continuity_key=continuity_key,
                workflow_kind=workflow_kind,
                backend=backend,
                ok=ok,
                prompt_text=complete_prompt_text,
                image_path=image_path,
                note=note,
                reference_image_path=reference_image_path,
                image_size=image_size,
                elapsed_ms=elapsed_ms,
                presets=preset_names,
                reference_used=reference_used,
                reference_candidate=reference_candidate,
                reference_intent=reference_intent,
                reference_plan=reference_plan,
                reference_fallback=effective_reference_fallback,
                submitted_reference_ids=(
                    effective_submitted_reference_ids if reference_used else ()
                ),
                wardrobe=wardrobe,
                prompt_hash=prompt_hash,
                submitted_prompt_hash=hashlib.sha256(
                    str(prompt_text or "").encode("utf-8", "ignore")
                ).hexdigest(),
                prompt_path=prompt_path,
                complete_prompt_length=len(str(complete_prompt_text or "")),
                submitted_prompt_length=len(str(prompt_text or "")),
                prompt_sections=prompt_sections_for_log,
                conflicts=conflicts,
                removed_conflicts=removed_conflicts,
                residual_conflicts=residual_conflicts,
                reference_removed=resolved_context.reference_removed,
                sanitizer_version=resolved_context.sanitizer_version,
                detected_conflict_details=detected_conflict_details,
                removed_conflict_details=removed_conflict_details,
                residual_conflict_details=residual_conflict_details,
                suggested_scene_preset=suggested_scene_preset,
                prompt_format=prompt_format,
            )
            self._append_photo_generation_trace_event(
                trace_id,
                "completed" if ok else "failed",
                status="ok" if ok else "error",
                data={
                    "backend": backend,
                    "image_path": image_path,
                    "note": note,
                    "elapsed_ms": elapsed_ms,
                    "reference_used": reference_used,
                    "output_exists": output_exists,
                },
                context={"backend": backend},
            )
            logger.info(
                "[PrivateCompanion] 生图结束: trace=%s ok=%s backend=%s elapsed=%sms reference_used=%s note=%s %s",
                trace_id,
                ok,
                _single_line(backend, 60),
                elapsed_ms,
                reference_used,
                _single_line(note, 220),
                self._photo_generation_file_detail(image_path),
            )
            return backend, image_path, note

        if residual_conflicts:
            logger.error(
                "[PrivateCompanion] 生图服装上下文仍有残留冲突，已停止调用后端: trace=%s residual=%s",
                trace_id,
                ",".join(residual_conflicts),
            )
            return finish(
                "上下文清理",
                "",
                "生图上下文仍存在未能安全清理的服装冲突，已停止调用后端",
            )

        if structured_reference_plan:
            if not self._comfyui_photo_available():
                return finish(
                    "ComfyUI",
                    "",
                    "Q5 受管参考素材只能由 ComfyUI 工作流消费；ComfyUI 后端不可用或未配置",
                )
            busy_state = self._local_photo_generation_busy_state(force_refresh=True)
            if busy_state:
                return finish(
                    "ComfyUI",
                    "",
                    f"Q5 受管参考素材未提交：电脑高负荷（{busy_state.get('reason') or '负载偏高'}）",
                )
            workflow_name = self._choose_photo_workflow_name(workflow_kind)
            if not workflow_name:
                return finish(
                    "ComfyUI",
                    "",
                    f"Q5 受管参考素材未提交：未配置 {workflow_kind} 对应的 ComfyUI 工作流",
                )
            reference_asset_ticket = structured_reference_gate.issue(
                structured_reference_plan,
                backend="comfyui",
            ) if structured_reference_gate else None
            if reference_asset_ticket is None:
                return finish("ComfyUI", "", "Q5 受管参考素材票据签发失败")
            image_path, note = await self._run_comfyui_photo_workflow(
                workflow_name,
                prompt_text,
                session_key=session_key,
                reference_asset_gate=structured_reference_gate,
                reference_asset_ticket=reference_asset_ticket,
                generation_id=trace_id,
                structured_reference_count=len(structured_reference_plan.assets),
            )
            return finish(
                "ComfyUI",
                image_path,
                note,
                structured_reference_submitted="已使用" in str(note or "") and "受管参考素材" in str(note or ""),
            )

        if "source" in reference_fallback.missing_roles:
            return finish("参考图", "", reference_fallback.message)

        if reference_image_path and not reference_exists:
            return finish("参考图", "", f"参考图路径不可用或文件不存在：{_single_line(reference_image_path, 160)}")

        if preferred == "comfyui":
            if not self._comfyui_photo_available():
                return finish("ComfyUI", "", "ComfyUI 后端不可用或未配置")
            busy_state = self._local_photo_generation_busy_state(force_refresh=True)
            if busy_state:
                return finish("ComfyUI", "", f"电脑高负荷,已跳过本地生图（{busy_state.get('reason') or '负载偏高'}）")
            workflow_name = self._choose_photo_workflow_name(workflow_kind)
            if not workflow_name:
                return finish("ComfyUI", "", f"未配置 {workflow_kind} 对应的 ComfyUI 工作流")
            image_path, note = await self._run_comfyui_photo_workflow(
                workflow_name,
                prompt_text,
                session_key=session_key,
                reference_image_path=reference_image_path,
                reference_image_paths=reference_image_paths,
            )
            return finish(
                "ComfyUI",
                image_path,
                note,
                reference_submitted=bool(reference_image_paths),
            )
        if preferred == "sdgen":
            if not self._sdgen_photo_available():
                return finish("SDGen", "", "SDGen 插件不可用或未配置")
            if reference_image_path:
                return finish("SDGen", "", "SDGen 后端不支持参考图输入，请改用在线参考图接口或 ComfyUI images=1 自拍工作流")
            busy_state = self._local_photo_generation_busy_state(force_refresh=True)
            if busy_state:
                return finish("SDGen", "", f"电脑高负荷,已跳过本地生图（{busy_state.get('reason') or '负载偏高'}）")
            image_path, note = await self._run_sdgen_photo_generation(
                prompt_text,
                session_key=session_key,
            )
            return finish("SDGen", image_path, note)
        if preferred == "tool_call":
            if not self._custom_tool_photo_available():
                return finish("函数工具", "", "自定义生图函数工具不可用或未配置")
            image_path, note = await self._run_custom_tool_photo_generation(
                prompt_text,
                session_key=session_key,
                workflow_kind=workflow_kind,
                reference_image_path=reference_image_path,
                image_size=image_size,
            )
            if image_path:
                return finish(
                    "函数工具",
                    image_path,
                    note,
                    reference_submitted=bool(reference_image_path),
                )
            can_fallback = self._external_photo_available() or self._comfyui_photo_available() or (not reference_image_path and self._sdgen_photo_available())
            if not can_fallback:
                return finish("函数工具", "", note)
            logger.info("[PrivateCompanion] 函数工具生图未成功,继续尝试可用后端回退: trace=%s note=%s", trace_id, _single_line(note, 180))
        if preferred == "external":
            if not self._external_photo_available():
                return finish("在线图片 API", "", "在线图片 API 后端不可用或未配置")
            image_path, note = await self._run_external_photo_generation(
                prompt_text,
                session_key=session_key,
                reference_image_path=reference_image_path,
                reference_image_paths=reference_image_paths,
                image_size=image_size,
            )
            if image_path:
                return finish(
                    "在线图片 API",
                    image_path,
                    note,
                    reference_submitted=bool(reference_image_path),
                )
            can_fallback = self._comfyui_photo_available() or (not reference_image_path and self._sdgen_photo_available())
            if not can_fallback:
                return finish("在线图片 API", "", note)
            logger.info("[PrivateCompanion] 固定在线图片 API 未成功,继续尝试可用后端回退: trace=%s note=%s", trace_id, _single_line(note, 180))
        external_note = ""
        if preferred == "external":
            external_note = note
        elif self._external_photo_available():
            image_path, note = await self._run_external_photo_generation(
                prompt_text,
                session_key=session_key,
                reference_image_path=reference_image_path,
                reference_image_paths=reference_image_paths,
                image_size=image_size,
            )
            if image_path:
                return finish(
                    "在线图片 API",
                    image_path,
                    note,
                    reference_submitted=bool(reference_image_path),
                )
            external_note = note
            logger.info("[PrivateCompanion] 生图后端回退: trace=%s backend=external note=%s", trace_id, _single_line(note, 180))
        else:
            external_note = "在线图片 API 后端不可用或未配置"
            logger.info("[PrivateCompanion] 生图后端跳过: trace=%s backend=external note=%s", trace_id, external_note)
        if self._comfyui_photo_available():
            busy_state = self._local_photo_generation_busy_state(force_refresh=True)
            if busy_state:
                comfyui_note = f"电脑高负荷,已跳过本地生图（{busy_state.get('reason') or '负载偏高'}）"
                logger.info("[PrivateCompanion] 生图后端跳过: trace=%s backend=comfyui note=%s", trace_id, _single_line(comfyui_note, 180))
            else:
                workflow_name = self._choose_photo_workflow_name(workflow_kind)
                if workflow_name:
                    image_path, note = await self._run_comfyui_photo_workflow(
                        workflow_name,
                        prompt_text,
                        session_key=session_key,
                        reference_image_path=reference_image_path,
                        reference_image_paths=reference_image_paths,
                    )
                    if image_path:
                        return finish(
                            "ComfyUI",
                            image_path,
                            note,
                            reference_submitted=bool(reference_image_paths),
                        )
                    comfyui_note = note
                    logger.info("[PrivateCompanion] 生图后端回退: trace=%s backend=comfyui note=%s", trace_id, _single_line(note, 180))
                else:
                    comfyui_note = f"未配置 {workflow_kind} 对应的 ComfyUI 工作流"
                    logger.info("[PrivateCompanion] 生图后端跳过: trace=%s backend=comfyui note=%s", trace_id, _single_line(comfyui_note, 180))
        else:
            comfyui_note = "ComfyUI 后端不可用或未配置"
            logger.info("[PrivateCompanion] 生图后端跳过: trace=%s backend=comfyui note=%s", trace_id, comfyui_note)
        if reference_image_path:
            sdgen_note = "本次需要参考图，已跳过不支持参考图输入的 SDGen 纯文生图"
            logger.info("[PrivateCompanion] 生图后端跳过: trace=%s backend=sdgen note=%s", trace_id, sdgen_note)
        elif self._sdgen_photo_available():
            busy_state = self._local_photo_generation_busy_state(force_refresh=True)
            if busy_state:
                sdgen_note = f"电脑高负荷,已跳过本地生图（{busy_state.get('reason') or '负载偏高'}）"
                logger.info("[PrivateCompanion] 生图后端跳过: trace=%s backend=sdgen note=%s", trace_id, _single_line(sdgen_note, 180))
            else:
                image_path, note = await self._run_sdgen_photo_generation(
                    prompt_text,
                    session_key=session_key,
                )
                if image_path:
                    return finish("SDGen", image_path, note)
                sdgen_note = note
                logger.info("[PrivateCompanion] 生图后端回退: trace=%s backend=sdgen note=%s", trace_id, _single_line(note, 180))
        else:
            sdgen_note = "SDGen 插件不可用或未配置"
            logger.info("[PrivateCompanion] 生图后端跳过: trace=%s backend=sdgen note=%s", trace_id, sdgen_note)
        return finish("生图链路", "", f"在线图片 API 失败：{external_note}；ComfyUI 失败：{comfyui_note}；SDGen 失败：{sdgen_note}")

    async def _generate_photo_image_result(self, **kwargs: Any) -> PhotoGenerationResult:
        backend, image_path, note = await self._generate_photo_image(**kwargs)
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
        topic_hint = _single_line(user.get("planned_proactive_topic"), 60)
        motive_hint = _single_line(user.get("planned_proactive_motive"), 120)
        schedule_context = self._format_plan_item_for_prompt(current_item)
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
        if getattr(self, "enable_bot_relationship_network", False):
            card_lines: list[str] = []
            for raw_card in self._normalize_bot_relationship_cards(
                getattr(self, "bot_relationship_cards", [])
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
        text = await self._llm_call(
            prompt,
            max_tokens=260,
            provider_id=self._task_provider(self.photo_prompt_provider_id, self.mai_style_provider_id),
            task="photo_prompt",
        )
        payload = self._extract_json_payload(text or "")
        if isinstance(payload, dict):
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
                use_persona_reference = False
        else:
            kind = "text2img"
            image_prompt = _single_line(text, 600)
            caption = image_prompt
            # When the scene model is unavailable, prefer a stable character photo
            # for ordinary proactive sharing instead of allowing an arbitrary face.
            use_persona_reference = reason != "birthday_celebration"
        if kind not in {"selfie", "portrait", "自拍", "人像", "text2img", "scene", "photo", "风景"}:
            kind = "text2img"
        if kind in {"portrait", "自拍", "人像"}:
            kind = "selfie"
        if kind in {"scene", "photo", "风景"}:
            kind = "text2img"
        if kind == "selfie":
            use_persona_reference = True
        elif isinstance(payload, dict) and payload.get("use_persona_reference") is None:
            character_text = f"{image_prompt} {caption}"
            use_persona_reference = bool(
                re.search(
                    r"\b(?:girl|woman|female|character|portrait|solo|face|hairstyle|outfit)\b",
                    character_text,
                    flags=re.I,
                )
                or any(token in character_text for token in ("人物", "角色", "女孩", "少女", "自拍", "人像", "穿搭"))
            )
        if not image_prompt:
            current = schedule_context
            image_prompt = (
                f"社交媒体随手拍,当前背景：{current},温柔自然的生活感,"
                f"清晰构图,柔和光线,{style_instruction}"
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
        style = str(self.photo_generation_style or "真实").strip()
        if style == "二次元":
            return "二次元", "日系二次元插画风,人物与场景干净细腻,保留生活感,不要写实摄影质感"
        if style == "其他":
            custom = _single_line(self.photo_generation_style_custom_prompt, 200)
            if custom:
                return "其他", custom
            return "其他", "保持统一审美风格,自然生活感,避免默认写实照片风格"
        return "真实", "真实摄影风格,像手机随手拍到的生活照片,光线自然,细节可信"

    def _q5_structured_reference_assets_enabled(self) -> bool:
        return bool(getattr(self, "enable_p5_structured_reference_assets", False))

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
            getattr(self, "photo_structured_reference_assets", []),
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
        catalog = getattr(self, "photo_reference_catalog", None)
        if catalog is None:
            raw = _path_text(getattr(self, "photo_persona_reference_image_path", ""), 1000)
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
        if getattr(self, "photo_reference_catalog", None) is None:
            loaded = load_catalog(
                [],
                catalog_version=0,
                legacy_library=getattr(self, "photo_reference_library", []),
                preset_names=self._photo_generation_scene_presets().keys(),
            )
            entries = [project_reference_candidate(item) for item in loaded.references if item.kind == "library"]
            for index, entry in enumerate(entries):
                raw_items = getattr(self, "photo_reference_library", []) or []
                raw_item = raw_items[index] if isinstance(raw_items, list) and index < len(raw_items) else None
                entry["_config_format"] = "dict" if isinstance(raw_item, dict) else "text"
            return entries
        return [
            project_reference_candidate(item)
            for item in (getattr(self, "photo_reference_catalog", ()) or ())
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
        if not bool(getattr(self, "enable_photo_reference_image", False)):
            return ""
        if str(workflow_kind or "").strip().lower() not in {"selfie", "portrait", "自拍", "人像"}:
            return ""
        if allow_daily_outfit:
            outfit_path = self._daily_outfit_reference_image_path()
            if outfit_path:
                return outfit_path
        return self._photo_persona_reference_image_path()

    async def _photo_persona_reference_image_path_async(self) -> str:
        if not bool(getattr(self, "enable_photo_reference_image", False)):
            return ""
        local_path = self._photo_persona_reference_image_path()
        if local_path:
            return local_path
        catalog = getattr(self, "photo_reference_catalog", None)
        if catalog is None:
            raw = _path_text(getattr(self, "photo_persona_reference_image_path", ""), 1000)
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
        if not bool(getattr(self, "enable_bot_relationship_network", False)):
            return []
        cards = self._normalize_bot_relationship_cards(
            getattr(self, "bot_relationship_cards", [])
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
            for item in (getattr(self, "roleplay_knowledge_source_ids", None) or [])
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
        canonical_mode = getattr(self, "photo_reference_catalog", None) is not None
        if canonical_mode:
            catalog = tuple(getattr(self, "photo_reference_catalog", ()) or ())
        else:
            catalog = load_catalog(
                [],
                catalog_version=0,
                legacy_persona=getattr(self, "photo_persona_reference_image_path", ""),
                legacy_library=getattr(self, "photo_reference_library", []),
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
        if not using_candidate_overrides and not bool(getattr(self, "enable_photo_reference_image", False)):
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

    def _comfyui_photo_reference_capacity(
        self,
        workflow_kind: str,
        *,
        requested_images: int,
    ) -> int:
        requested = max(1, _safe_int(requested_images, 1, 1))
        module = self._get_comfyui_module()
        workflow_name = self._choose_photo_workflow_name(workflow_kind)
        if module is None or not workflow_name:
            return 1
        try:
            workflow_dir = module._get_workflow_dir()
        except Exception:
            return 1
        for image_count in range(requested, 0, -1):
            try:
                workflow_file = module.find_workflow_file(
                    workflow_name,
                    1,
                    image_count,
                    0,
                    workflow_dir,
                )
            except Exception:
                workflow_file = ""
            if not workflow_file:
                workflow_file, _text_count, _matched_images = (
                    self._find_photo_workflow_with_text_count(
                        module,
                        workflow_dir,
                        workflow_name,
                        image_count=image_count,
                    )
                )
            if workflow_file:
                return image_count
        # Keep the historical single-image contract when workflow metadata cannot
        # be inspected. The adapter still refuses to silently drop a reference.
        return 1

    def _photo_reference_backend_max_images(
        self,
        workflow_kind: str,
        *,
        requested_images: int = 0,
    ) -> int:
        preferred = _single_line(
            getattr(self, "photo_generation_backend", ""),
            40,
        ).lower()
        if preferred == "sdgen":
            return 0
        if preferred == "comfyui":
            return self._comfyui_photo_reference_capacity(
                workflow_kind,
                requested_images=requested_images,
            )
        if preferred == "external":
            multi_capacity = self._external_image_multi_reference_capacity(
                requested_images,
            )
            if multi_capacity > 1:
                return multi_capacity
            return 1
        if preferred == "tool_call":
            return 1

        availability = (
            ("_external_photo_available", 1),
            ("_comfyui_photo_available", None),
            ("_custom_tool_photo_available", 1),
            ("_sdgen_photo_available", 0),
        )
        for method_name, capacity in availability:
            checker = getattr(self, method_name, None)
            if not callable(checker):
                continue
            try:
                if checker():
                    if capacity is None:
                        return self._comfyui_photo_reference_capacity(
                            workflow_kind,
                            requested_images=requested_images,
                        )
                    if method_name == "_external_photo_available":
                        multi_capacity = self._external_image_multi_reference_capacity(
                            requested_images,
                        )
                        if multi_capacity > 1:
                            return multi_capacity
                    return capacity
            except Exception:
                continue
        # Current reference-capable adapters accept one reference_image_path.
        return 1

    @staticmethod
    def _external_image_model_supports_multi_reference(model_name: Any) -> bool:
        model = str(model_name or "").strip().lower()
        return bool(
            re.search(
                r"(?:^|[-_/:.])gpt[-_]?image[-_]?2(?:$|[-_/:.])",
                model,
            )
        )

    def _external_image_endpoint_multi_reference_capacity(
        self,
        endpoint: dict[str, Any],
        requested_images: int,
    ) -> int:
        requested = max(1, _safe_int(requested_images, 1, 1))
        if not isinstance(endpoint, dict):
            return 1
        try:
            platform = self._resolved_external_image_api_platform_for_values(
                configured=endpoint.get("platform", "auto"),
                base_url=endpoint.get("base_url", ""),
                model_name=endpoint.get("model", ""),
            )
        except Exception:
            platform = ""
        if platform != "openai" or not self._external_image_model_supports_multi_reference(
            endpoint.get("model")
        ):
            return 1
        # Keep an application-side ceiling below the API maximum so a role-card
        # group request cannot create an unexpectedly large multipart body.
        return min(requested, 8)

    def _external_image_api_capacity_endpoint_snapshot(self) -> list[dict[str, Any]]:
        configured = getattr(self, "external_image_api_endpoints", None)
        normalizer = getattr(self, "_normalize_external_image_api_endpoints", None)
        if isinstance(configured, list) and configured:
            try:
                normalized = normalizer(configured) if callable(normalizer) else configured
                return [dict(item) for item in normalized if isinstance(item, dict)]
            except Exception:
                return []

        config = getattr(self, "config", None)
        config_getter = getattr(self, "_cfg_raw", None)

        def get_value(key: str, default: Any = None) -> Any:
            if config is None:
                return default
            if callable(config_getter):
                try:
                    return config_getter(config, key, default)
                except Exception:
                    pass
            try:
                return config.get(key, default)
            except Exception:
                return default

        configured_raw = get_value("external_image_api_endpoints", [])
        if isinstance(configured_raw, list) and configured_raw:
            try:
                normalized = normalizer(configured_raw) if callable(normalizer) else configured_raw
                return [dict(item) for item in normalized if isinstance(item, dict)]
            except Exception:
                return []

        legacy = [
            {
                "name": "主在线 API",
                "enabled": True,
                "platform": get_value("external_image_api_platform", "auto"),
                "base_url": get_value("EXTERNAL_IMAGE_API_BASE_URL", ""),
                "api_key": get_value("EXTERNAL_IMAGE_API_KEY", ""),
                "model": get_value("EXTERNAL_IMAGE_API_MODEL", ""),
            },
            {
                "name": "备选在线 API",
                "enabled": bool(get_value("enable_backup_external_image_api", False)),
                "platform": get_value("backup_external_image_api_platform", "auto"),
                "base_url": get_value("BACKUP_EXTERNAL_IMAGE_API_BASE_URL", ""),
                "api_key": get_value("BACKUP_EXTERNAL_IMAGE_API_KEY", ""),
                "model": get_value("BACKUP_EXTERNAL_IMAGE_API_MODEL", ""),
            },
        ]
        if not any(
            str(item.get("base_url") or "").strip()
            or str(item.get("api_key") or "").strip()
            or str(item.get("model") or "").strip()
            for item in legacy
        ):
            return []
        try:
            normalized = normalizer(legacy) if callable(normalizer) else legacy
            return [dict(item) for item in normalized if isinstance(item, dict)]
        except Exception:
            return legacy

    def _external_image_multi_reference_capacity(
        self,
        requested_images: int = 0,
        *,
        endpoint: dict[str, Any] | None = None,
        inspect_queue: bool = True,
    ) -> int:
        """Return the GPT Image 2 capacity without reading mutable endpoint runtime.

        Planning uses the capability envelope of the configured queue so a later
        GPT Image 2 fallback still receives every planned reference.  The actual
        adapter passes ``inspect_queue=False`` and projects again for the endpoint
        currently being called.
        """
        requested = max(1, _safe_int(requested_images, 1, 1))
        if endpoint is not None:
            return self._external_image_endpoint_multi_reference_capacity(
                endpoint,
                requested,
            )
        if inspect_queue:
            endpoints = self._external_image_api_capacity_endpoint_snapshot()
            usable = [
                item
                for item in endpoints
                if isinstance(item, dict)
                and not self._external_image_api_endpoint_unavailable_note(item)
            ]
            if usable:
                return max(
                    self._external_image_endpoint_multi_reference_capacity(
                        item,
                        requested,
                    )
                    for item in usable
                )
        active_endpoint = {
            "platform": getattr(self, "external_image_api_platform", "auto"),
            "base_url": getattr(self, "external_image_api_base_url", ""),
            "model": getattr(self, "external_image_api_model", ""),
        }
        return self._external_image_endpoint_multi_reference_capacity(
            active_endpoint,
            requested,
        )

    @staticmethod
    def _photo_reference_prompt_for_backend_capacity(
        prompt_text: str,
        *,
        planned_count: int,
        submitted_count: int,
    ) -> str:
        planned = max(0, int(planned_count or 0))
        submitted = max(0, min(planned, int(submitted_count or 0)))
        if not planned or submitted >= planned:
            return str(prompt_text or "")
        missing = (
            str(submitted + 1)
            if submitted + 1 == planned
            else f"{submitted + 1}-{planned}"
        )
        availability = (
            "Reference attachment availability override: this backend call received "
            f"only reference image(s) 1-{submitted}; planned reference image(s) {missing} "
            "are not attached. Treat earlier statements that those missing images were "
            "submitted or attached as planning metadata only. Follow their textual role "
            "descriptions conservatively, do not claim an exact visual match for an absent "
            "identity, and never transfer the face, body, hair, or clothing identity from an "
            "attached person to a missing relationship role."
        )
        return "\n\n".join(part for part in (str(prompt_text or "").strip(), availability) if part)

    @staticmethod
    def _photo_reference_submitted_count_from_note(note: Any, default: int) -> int:
        fallback = max(0, int(default or 0))
        text = str(note or "")
        capacity_match = re.search(
            r"(?:当前(?:端点|工作流)仅支持|实际提交)\s*(\d+)\s*/\s*\d+\s*张参考图",
            text,
        )
        if capacity_match:
            return min(fallback, max(0, int(capacity_match.group(1))))
        used_match = re.search(r"已使用\s*(\d+)\s*张(?:本地)?参考图", text)
        if used_match:
            return min(fallback, max(0, int(used_match.group(1))))
        if "已使用本地人设参考图" in text:
            return min(fallback, 1)
        return fallback

    async def _select_photo_reference_image_async(
        self,
        workflow_kind: str,
        *,
        allow_daily_outfit: bool = True,
        request_text: str = "",
        ambient_context: str = "",
        selection_context: str = "",
        suggested_scene_preset: str = "",
        continuity_key: str = "",
    ) -> str:
        selected = await self._select_photo_reference_candidate_async(
            workflow_kind,
            allow_daily_outfit=allow_daily_outfit,
            request_text=request_text,
            ambient_context=ambient_context,
            selection_context=selection_context,
            suggested_scene_preset=suggested_scene_preset,
            continuity_key=continuity_key,
        )
        return str(selected.get("path") or "") if selected else ""

    async def _photo_reference_candidate_for_path_async(
        self,
        reference_image_path: str,
        *,
        workflow_kind: str,
        allow_daily_outfit: bool = True,
        continuity_key: str = "",
    ) -> dict[str, Any]:
        path = _path_text(reference_image_path, 1000)
        if not path:
            return {}
        normalized_kind = str(workflow_kind or "").strip().lower()
        if normalized_kind in {"edit", "改图", "修图", "重绘", "p图"}:
            return self._normalize_photo_reference_candidate_metadata(
                {
                    "id": "explicit_reference",
                    "path": path,
                    "source": path,
                    "kind": "source",
                    "note": "用户本轮明确提供或引用的改图原图",
                    "reference_roles": ["source"],
                    "outfit_lock_default": False,
                    "metadata_source": "runtime",
                }
            )
        candidates = await self._photo_reference_candidates_async(
            allow_daily_outfit=allow_daily_outfit,
        )
        recent = self._recent_sent_photo_continuity_candidate(continuity_key)
        if recent:
            candidates.insert(0, recent)
        for candidate in candidates:
            if self._photo_reference_paths_equal(path, candidate.get("path", "")):
                return self._normalize_photo_reference_candidate_metadata(candidate)
        kind = "explicit"
        # An otherwise unclassified user image establishes identity only.
        roles = ["identity"]
        return self._normalize_photo_reference_candidate_metadata(
            {
                "id": "explicit_reference",
                "path": path,
                "source": path,
                "kind": kind,
                "note": "用户本轮明确提供或引用的参考图",
                "reference_roles": roles,
                "outfit_lock_default": False,
                "metadata_source": "runtime",
            }
        )

    async def _photo_persona_reference_image_for_kind_async(
        self,
        workflow_kind: str,
        *,
        allow_daily_outfit: bool = True,
        request_text: str = "",
        ambient_context: str = "",
        selection_context: str = "",
        suggested_scene_preset: str = "",
        continuity_key: str = "",
    ) -> str:
        return await self._select_photo_reference_image_async(
            workflow_kind,
            allow_daily_outfit=allow_daily_outfit,
            request_text=request_text,
            ambient_context=ambient_context,
            selection_context=selection_context,
            suggested_scene_preset=suggested_scene_preset,
            continuity_key=continuity_key,
        )

    async def _run_comfyui_photo_workflow(
        self,
        workflow_name: str,
        prompt_text: str,
        session_key: str,
        reference_image_path: str = "",
        image_size: str = "",
        reference_image_paths: Any = (),
        reference_asset_gate: ReferenceAssetGate | None = None,
        reference_asset_ticket: ReferenceAssetTicket | None = None,
        generation_id: str = "",
        structured_reference_count: int = 0,
    ) -> tuple[str, str]:
        module = self._get_comfyui_module()
        if module is None:
            return "", "ComfyUI 插件不可用"
        config = getattr(module, "_plugin_config", None)
        if not config:
            return "", "ComfyUI 插件配置不可用"
        try:
            server_ip, client_id = module._get_server_config(config)
            workflow_dir = module._get_workflow_dir()
            use_structured_assets = bool(
                reference_asset_gate is not None
                and reference_asset_ticket is not None
                and _single_line(generation_id, 120)
                and _safe_int(structured_reference_count, 0, 0) > 0
            )
            raw_reference_paths = () if use_structured_assets else reference_image_paths
            if isinstance(raw_reference_paths, str):
                raw_reference_paths = (raw_reference_paths,)
            requested_reference_paths: list[str] = []
            valid_reference_paths: list[str] = []
            raw_paths = () if use_structured_assets else (reference_image_path, *(raw_reference_paths or ()))
            for raw_path in raw_paths:
                path = _path_text(raw_path, 1000)
                if not path or path in requested_reference_paths:
                    continue
                requested_reference_paths.append(path)
                if os.path.isfile(path):
                    valid_reference_paths.append(path)
            use_reference_image = bool(valid_reference_paths)
            if requested_reference_paths and not use_reference_image:
                return "", "ComfyUI 提交前参考图已不可用，已停止纯文工作流回退"
            workflow_file = ""
            text_count = 1
            image_count = 0
            planned_reference_count = (
                min(
                    MAX_INPUT_ASSETS,
                    max(1, _safe_int(structured_reference_count, 1, 1)),
                )
                if use_structured_assets
                else len(valid_reference_paths)
            )
            if use_reference_image or use_structured_assets:
                required_images = planned_reference_count
                for candidate_count in range(required_images, 0, -1):
                    candidate_file = module.find_workflow_file(
                        workflow_name,
                        1,
                        candidate_count,
                        0,
                        workflow_dir,
                    )
                    candidate_text_count = 1
                    matched_images = candidate_count if candidate_file else 0
                    if not candidate_file:
                        candidate_file, candidate_text_count, matched_images = self._find_photo_workflow_with_text_count(
                            module,
                            workflow_dir,
                            workflow_name,
                            image_count=candidate_count,
                        )
                    if candidate_file:
                        workflow_file = candidate_file
                        text_count = candidate_text_count
                        image_count = min(candidate_count, max(1, matched_images))
                        break
                if not workflow_file:
                    reference_label = "Q5 受管参考素材" if use_structured_assets else f"已找到 {required_images} 张参考图"
                    return "", (
                        f"{reference_label}，但 ComfyUI 工作流 {workflow_name} "
                        "没有匹配的 images>=1 输入。请至少配置单图自拍/改图工作流，"
                        "避免回退成不使用参考图的纯文生图。"
                    )
                if image_count < required_images:
                    logger.info(
                        "[PrivateCompanion] ComfyUI 按现有工作流容量降级参考图: workflow=%s planned=%s submitted=%s",
                        _single_line(workflow_name, 80),
                        required_images,
                        image_count,
                    )
            if not workflow_file:
                workflow_file = module.find_workflow_file(
                    workflow_name,
                    1,
                    0,
                    0,
                    workflow_dir,
                )
                image_count = 0
            if not workflow_file:
                workflow_file, text_count, image_count = self._find_photo_workflow_with_text_count(
                    module,
                    workflow_dir,
                    workflow_name,
                    image_count=0,
                )
            if not workflow_file:
                need = f"images={planned_reference_count}" if (use_reference_image or use_structured_assets) else "images=0"
                return "", f"未找到匹配工作流 {workflow_name}（需要 texts>=1、{need}、videos=0）"
            submitted_prompt_text = self._photo_reference_prompt_for_backend_capacity(
                prompt_text,
                planned_count=planned_reference_count,
                submitted_count=image_count,
            )
            logger.info(
                "[PrivateCompanion] ComfyUI 生图提交准备: workflow=%s file=%s text_count=%s image_count=%s reference=%s wait=%ss prompt_preview=%s",
                _single_line(workflow_name, 80),
                _single_line(workflow_file, 160),
                text_count,
                image_count,
                use_reference_image or use_structured_assets,
                self.comfyui_photo_wait_seconds,
                _single_line(submitted_prompt_text, 180),
            )
            debug = bool(
                getattr(config, "debug_mode", False)
                if not isinstance(config, dict)
                else config.get("debug_mode", False)
            )
            workflow = module.ComfyUIWorkflow(server_ip, client_id)
            workflow.load_workflow_api(workflow_file)
            input_images: list[str] = []
            reference_note = ""
            if use_structured_assets:
                if image_count <= 0:
                    return "", "Q5 受管参考素材工作流未声明有效图片输入容量"
                input_images, ticket_status = reference_asset_gate.consume(
                    reference_asset_ticket,
                    generation_id=generation_id,
                    backend="comfyui",
                    capacity=image_count,
                )
                if ticket_status != "ok" or not input_images:
                    return "", f"Q5 受管参考素材票据无效：{ticket_status}"
                reference_note = f"；已使用 {len(input_images)} 张受管参考素材"
                if len(input_images) < planned_reference_count:
                    reference_note += f"；当前工作流仅支持 {len(input_images)}/{planned_reference_count} 张受管参考素材"
            elif use_reference_image:
                input_images = list(valid_reference_paths[:image_count]) if image_count > 0 else []
            if input_images and not use_structured_assets:
                reference_note = f"；已使用 {len(input_images)} 张本地参考图"
                if len(input_images) < planned_reference_count:
                    reference_note += f"；当前工作流仅支持 {len(input_images)}/{planned_reference_count} 张参考图"
            elif use_reference_image and not use_structured_assets:
                return "", (
                    f"已找到参考图，但 ComfyUI 工作流 {workflow_name} 实际没有接收图片输入。"
                    "请检查工作流 images 参数数量。"
                )
            prompt_id = await workflow.submit_only(
                input_images,
                [submitted_prompt_text] * max(1, text_count),
                [],
                debug=debug,
            )
            logger.info(
                "[PrivateCompanion] ComfyUI 生图已提交: workflow=%s prompt_id=%s input_images=%s text_count=%s",
                _single_line(workflow_name, 80),
                _single_line(prompt_id, 80),
                len(input_images),
                text_count,
            )
            deadline = _now_ts() + self.comfyui_photo_wait_seconds
            while _now_ts() < deadline:
                url, file_type, texts = await module._get_result_for_prompt(server_ip, prompt_id)
                if url and file_type == "image":
                    temp_path = await module._download_image_to_temp(url)
                    if not temp_path:
                        return "", "工作流完成但图片下载失败"
                    persistent_path = await module._save_image_to_persistent_path(
                        temp_path,
                        session_key or "private_companion",
                    )
                    logger.info(
                        "[PrivateCompanion] ComfyUI 生图完成: prompt_id=%s temp=%s persistent=%s note=%s",
                        _single_line(prompt_id, 80),
                        _single_line(temp_path, 160),
                        _single_line(persistent_path, 160),
                        reference_note or "ok",
                    )
                    return persistent_path or temp_path, "ok" + reference_note
                if url and file_type != "image":
                    logger.info(
                        "[PrivateCompanion] ComfyUI 生图输出类型不符: prompt_id=%s file_type=%s url=%s",
                        _single_line(prompt_id, 80),
                        _single_line(file_type, 40),
                        _single_line(url, 160),
                    )
                    return "", f"工作流输出不是图片：{file_type}"
                await asyncio.sleep(2)
            logger.info(
                "[PrivateCompanion] ComfyUI 生图等待超时: workflow=%s prompt_id=%s wait=%ss",
                _single_line(workflow_name, 80),
                _single_line(prompt_id, 80),
                self.comfyui_photo_wait_seconds,
            )
            return "", f"等待 ComfyUI 结果超时（{self.comfyui_photo_wait_seconds}s）"
        except Exception as e:
            logger.warning(f"[PrivateCompanion] photo_text 生图失败: {e}", exc_info=True)
            return "", str(e)

    def _find_sdgen_plugin(self) -> Any | None:
        try:
            getter = getattr(getattr(self, "context", None), "get_registered_star", None)
            if callable(getter):
                for name in ("SDGen", "astrbot_plugin_sdgen"):
                    plugin = getter(name)
                    if plugin is not None and callable(getattr(plugin, "_call_t2i_api", None)):
                        return plugin
        except Exception:
            pass
        for obj in gc.get_objects():
            try:
                cls = obj.__class__
                module = str(getattr(cls, "__module__", ""))
                if "astrbot_plugin_sdgen" not in module:
                    continue
                if callable(getattr(obj, "_call_t2i_api", None)):
                    return obj
            except Exception:
                continue
        return None

    async def _run_sdgen_photo_generation(
        self,
        prompt_text: str,
        *,
        session_key: str,
    ) -> tuple[str, str]:
        plugin = self._find_sdgen_plugin()
        if plugin is None:
            return "", "SDGen 插件不可用"
        checker = getattr(plugin, "_check_webui_available", None)
        if callable(checker):
            try:
                available, status = await checker()
                if not available:
                    return "", f"Stable Diffusion WebUI 不可用（status={status}）"
            except Exception as exc:
                return "", f"检查 Stable Diffusion WebUI 失败：{_single_line(exc, 120)}"
        try:
            positive_prompt = prompt_text
            build_prompt = getattr(plugin, "_build_positive_prompt", None)
            if callable(build_prompt):
                positive_prompt = build_prompt(prompt_text, "")
            logger.info(
                "[PrivateCompanion] SDGen 生图提交: session=%s prompt_preview=%s positive_chars=%s",
                _single_line(session_key, 80),
                _single_line(prompt_text, 180),
                len(str(positive_prompt or "")),
            )
            response = await plugin._call_t2i_api(positive_prompt)
            images = response.get("images") if isinstance(response, dict) else None
            if not isinstance(images, list) or not images:
                logger.info("[PrivateCompanion] SDGen 生图未返回图片: response_type=%s", type(response).__name__)
                return "", "SDGen 未返回图片"
            image = str(images[0] or "").strip()
            if not image:
                logger.info("[PrivateCompanion] SDGen 生图返回空图片字段")
                return "", "SDGen 返回空图片"
            config = getattr(plugin, "config", {}) or {}
            try:
                enable_upscale = bool(config.get("enable_upscale", False))
            except Exception:
                enable_upscale = False
            processor = getattr(plugin, "_apply_image_processing", None)
            if enable_upscale and callable(processor):
                image = await processor(image)
            if "," in image and image.lower().lstrip().startswith("data:image"):
                image = image.split(",", 1)[1].strip()
            image_bytes = base64.b64decode(image)
            path = await self._save_external_generated_image(
                image_bytes,
                session_key=session_key,
                ext=".png",
            )
            logger.info(
                "[PrivateCompanion] SDGen 生图完成: images=%s path=%s bytes=%s",
                len(images),
                _single_line(path, 160),
                len(image_bytes),
            )
            return path, "ok" if path else "保存 SDGen 图片失败"
        except Exception as e:
            logger.warning(f"[PrivateCompanion] SDGen 生图失败: {e}", exc_info=True)
            return "", str(e)

    async def _run_custom_tool_photo_generation(
        self,
        prompt_text: str,
        *,
        session_key: str,
        workflow_kind: str = "text2img",
        reference_image_path: str = "",
        image_size: str = "",
    ) -> tuple[str, str]:
        handler = self._find_custom_photo_tool_handler()
        if handler is None:
            return "", "未找到配置的生图函数工具"
        prompt_param = _single_line(getattr(self, "custom_photo_tool_prompt_param", "prompt"), 60) or "prompt"
        kind_param = _single_line(getattr(self, "custom_photo_tool_kind_param", ""), 60)
        reference_param = _single_line(getattr(self, "custom_photo_tool_reference_param", ""), 60)
        extra_params_text = _single_line(getattr(self, "custom_photo_tool_extra_params", ""), 2000)
        tool_name = _single_line(getattr(self, "custom_photo_tool_name", ""), 120)
        kwargs: dict[str, Any] = {prompt_param: prompt_text}
        if kind_param:
            kwargs[kind_param] = workflow_kind
        if reference_image_path and not reference_param:
            return "", (
                "已选择参考图，但自定义生图工具未配置参考图参数 custom_photo_tool_reference_param；"
                "已停止无参考图调用，避免生成结果丢失人物或服装一致性"
            )
        if reference_param and reference_image_path:
            kwargs[reference_param] = reference_image_path
        if extra_params_text:
            try:
                extra = json.loads(extra_params_text)
                if isinstance(extra, dict):
                    for ek, ev in extra.items():
                        if ek not in kwargs:
                            kwargs[ek] = ev
            except Exception:
                logger.warning("[PrivateCompanion] 自定义生图工具额外参数解析失败,已忽略: tool=%s", _single_line(tool_name, 80))
        try:
            session = self._parse_message_session(session_key)
            if session is None:
                session = MessageSession(
                    platform_name="private_companion",
                    message_type=MessageType.FRIEND_MESSAGE,
                    session_id=str(session_key or "custom_tool"),
                )
            event = SyntheticPrivateWakeEvent(
                context=self.context,
                session=session,
                message="",
                sender_name="PrivateCompanion",
            )
        except Exception as exc:
            logger.warning("[PrivateCompanion] 构造自定义工具事件失败: %s", _single_line(exc, 160))
            return "", f"无法为函数工具构造事件上下文：{_single_line(exc, 120)}"
        logger.info(
            "[PrivateCompanion] 自定义工具生图提交: tool=%s session=%s kind=%s prompt_chars=%s reference=%s params=%s",
            _single_line(tool_name, 80),
            _single_line(session_key, 80),
            _single_line(workflow_kind, 30),
            len(str(prompt_text or "")),
            bool(reference_image_path),
            list(kwargs.keys()),
        )
        try:
            result = await handler(event, **kwargs)
        except TypeError:
            try:
                result = await handler(event=event, **kwargs)
            except Exception as exc:
                logger.warning("[PrivateCompanion] 自定义工具生图调用失败(keyword): %s", _single_line(exc, 200))
                return "", f"函数工具调用失败：{_single_line(exc, 160)}"
        except Exception as exc:
            logger.warning("[PrivateCompanion] 自定义工具生图调用失败: %s", _single_line(exc, 200))
            return "", f"函数工具调用失败：{_single_line(exc, 160)}"
        result_text = str(result or "") if not isinstance(result, str) else result
        if not result_text.strip():
            return "", "函数工具返回空结果"
        image_path, parse_note = await self._parse_custom_tool_photo_result(result_text, session_key=session_key)
        if image_path:
            logger.info(
                "[PrivateCompanion] 自定义工具生图完成: tool=%s path=%s note=%s result_preview=%s",
                _single_line(tool_name, 80),
                _single_line(image_path, 180),
                _single_line(parse_note, 120),
                _single_line(result_text, 180),
            )
            return image_path, f"ok ({_single_line(tool_name, 60)})"
        logger.info(
            "[PrivateCompanion] 自定义工具生图未解析到图片: tool=%s result_preview=%s",
            _single_line(tool_name, 80),
            _single_line(result_text, 220),
        )
        return "", f"函数工具返回结果未包含可识别的图片路径或数据：{_single_line(result_text, 160)}"

    async def _parse_custom_tool_photo_result(
        self,
        result_text: str,
        *,
        session_key: str,
    ) -> tuple[str, str]:
        text = str(result_text or "").strip()
        if not text:
            return "", "结果为空"
        json_data: Any = None
        try:
            json_data = json.loads(text)
        except Exception:
            pass
        if isinstance(json_data, dict):
            for key in ("image_path", "path", "file_path", "file", "image", "image_url", "url", "image_base64", "base64", "data"):
                val = json_data.get(key)
                if not val:
                    continue
                val_str = str(val).strip()
                if not val_str:
                    continue
                resolved, note = await self._resolve_custom_tool_image_candidate(val_str, session_key=session_key)
                if resolved:
                    return resolved, note
            for val in json_data.values():
                if not isinstance(val, str):
                    continue
                resolved, note = await self._resolve_custom_tool_image_candidate(val, session_key=session_key)
                if resolved:
                    return resolved, note
        url_matches = re.findall(r'https?://[^\s"\'<>\]\\]+', text)
        for url in url_matches:
            resolved, note = await self._resolve_custom_tool_image_candidate(url, session_key=session_key)
            if resolved:
                return resolved, note
        path_matches = re.findall(r'(?:[/\\][A-Za-z]:[\\/]|[A-Za-z]:[\\/]|/[\w\-./]+|[\w\-./]+\.(?:png|jpg|jpeg|webp|gif|bmp))', text)
        for path_str in path_matches:
            resolved, note = await self._resolve_custom_tool_image_candidate(path_str, session_key=session_key)
            if resolved:
                return resolved, note
        b64_match = re.search(r'(?:data:image/[a-z]+;base64,)?([A-Za-z0-9+/]{256,}={0,2})', text)
        if b64_match:
            b64_data = b64_match.group(1)
            try:
                image_bytes = base64.b64decode(b64_data)
                if image_bytes and len(image_bytes) > 100:
                    path = await self._save_external_generated_image(image_bytes, session_key=session_key, ext=".png")
                    if path:
                        return path, "base64 已保存"
            except Exception:
                pass
        return "", "未找到可识别的图片"

    async def _resolve_custom_tool_image_candidate(
        self,
        candidate: str,
        *,
        session_key: str,
    ) -> tuple[str, str]:
        text = str(candidate or "").strip().strip('"').strip("'").strip()
        if not text or len(text) < 3:
            return "", "候选为空"
        if text.lower().startswith(("http://", "https://")):
            path, note = await self._download_external_image_url(text, session_key=session_key)
            return path, note
        try:
            path = Path(text)
            if path.exists() and path.is_file():
                size = path.stat().st_size
                if size > 100:
                    return str(path), "本地文件"
        except Exception:
            pass
        if re.match(r'^[A-Za-z0-9+/]{256,}={0,2}$', text):
            try:
                image_bytes = base64.b64decode(text)
                if image_bytes and len(image_bytes) > 100:
                    path = await self._save_external_generated_image(image_bytes, session_key=session_key, ext=".png")
                    if path:
                        return path, "base64 已保存"
            except Exception:
                pass
        return "", "候选不可用"

    def _resolved_external_image_api_platform_for_values(
        self,
        *,
        configured: Any = "auto",
        base_url: Any = "",
        model_name: Any = "",
    ) -> str:
        normalizer = getattr(self, "_normalize_external_image_api_platform", None)
        platform = normalizer(configured) if callable(normalizer) else str(configured or "auto").strip().lower()
        raw_base = str(base_url or "").strip().lower()
        model = str(model_name or "").strip().lower()
        if platform in {"auto", "openai"} and (
            "apihub.agnes-ai.com" in raw_base or model.startswith("agnes-image-")
        ):
            return "agnes"
        minimax_official_host = any(
            host in raw_base
            for host in ("api.minimaxi.com", "api.minimax.io", "minimaxi.com", "minimax.io")
        )
        if (
            platform in {"auto", "openai"} and minimax_official_host
        ) or (
            platform == "auto" and model in {"image-01", "image-01-live"}
        ):
            return "minimax"
        if platform in {"openai", "agnes", "bailian", "modelscope", "doubao", "gemini", "sensenova", "minimax"}:
            return platform
        base = self._normalized_external_image_api_base_url(raw_base, platform=platform).lower()
        if "apihub.agnes-ai.com" in base or model.startswith("agnes-image-"):
            return "agnes"
        if "token.sensenova.cn" in base or model in {"senova-u1-fast", "sensenova-u1-fast"}:
            return "sensenova"
        if any(token in base for token in ("volces.com", "volcengine.com", "ark.cn-", "ark.ap-", "visual.volcengineapi.com")):
            return "doubao"
        if model.startswith(("doubao", "seedream")) or "seedream" in model:
            return "doubao"
        if any(token in base for token in ("generativelanguage.googleapis.com", "googleapis.com/v1beta/models")):
            return "gemini"
        if model.startswith(("gemini", "imagen")) or "nano-banana" in model:
            return "gemini"
        if "modelscope" in base or "api-inference" in base:
            return "modelscope"
        if any(token in base for token in ("dashscope.aliyuncs.com", "/services/aigc/", "/api/v1/tasks", "model-studio")):
            return "bailian"
        if model.startswith(("qwen-image", "wanx", "wan-", "wan2.", "wan2x", "wan")):
            return "bailian"
        return "openai"

    def _resolved_external_image_api_platform(self) -> str:
        return self._resolved_external_image_api_platform_for_values(
            configured=getattr(self, "external_image_api_platform", "auto"),
            base_url=getattr(self, "external_image_api_base_url", ""),
            model_name=getattr(self, "external_image_api_model", ""),
        )

    def _normalized_external_image_api_base_url(self, value: str = "", *, platform: str = "") -> str:
        raw = str(value or getattr(self, "external_image_api_base_url", "") or "").strip()
        if not raw:
            return ""
        if platform:
            resolved_platform = str(platform).strip().lower()
        else:
            normalizer = getattr(self, "_normalize_external_image_api_platform", None)
            configured = getattr(self, "external_image_api_platform", "auto")
            resolved_platform = normalizer(configured) if callable(normalizer) else str(configured or "auto").strip().lower()
        parsed = urlparse(raw)
        host = (parsed.netloc or "").strip().lower()
        path = (parsed.path or "").strip()

        if resolved_platform == "agnes" or host == "apihub.agnes-ai.com":
            scheme = parsed.scheme or "https"
            api_root = re.match(r"^(.*?/v1)(?:/images/(?:generations|edits))?$", path, flags=re.I)
            prefix = api_root.group(1) if api_root else "/v1"
            return f"{scheme}://{host or 'apihub.agnes-ai.com'}{prefix}"

        if resolved_platform == "minimax" or any(
            marker in host for marker in ("api.minimaxi.com", "api.minimax.io", "minimaxi.com", "minimax.io")
        ):
            scheme = parsed.scheme or "https"
            clean_path = path.rstrip("/")
            # MiniMax uses one JSON endpoint for both text-to-image and
            # reference-image generation. Normalize legacy slash variants to
            # the API root so the endpoint builder can append the canonical
            # ``/image_generation`` path exactly once.
            root_match = re.match(
                r"^(.*?/v1)(?:/(?:image_generation|image/generation|images/generations|images/edits))?$",
                clean_path,
                flags=re.I,
            )
            if root_match:
                prefix = root_match.group(1)
            elif re.search(r"/v1(?:/.*)?$", clean_path, flags=re.I):
                prefix = re.match(r"^(.*?/v1)", clean_path, flags=re.I).group(1)
            else:
                prefix = clean_path or "/v1"
                if not re.search(r"/v1$", prefix, flags=re.I):
                    prefix = prefix.rstrip("/") + "/v1"
            return f"{scheme}://{host}{prefix.rstrip('/')}"

        console_hosts = {
            "bailian.console.aliyun.com",
            "modelstudio.console.aliyun.com",
            "bailian.console.alibabacloud.com",
            "modelstudio.console.alibabacloud.com",
        }
        shared_host_by_region = {
            "cn-beijing": "dashscope.aliyuncs.com",
            "ap-southeast-1": "dashscope-intl.aliyuncs.com",
            "us-east-1": "dashscope-us.aliyuncs.com",
            "us-virginia": "dashscope-us.aliyuncs.com",
        }

        def _root_with_host(target_host: str) -> str:
            if not target_host:
                return raw.rstrip("/")
            scheme = parsed.scheme or "https"
            suffix = "/compatible-mode/v1" if resolved_platform in {"openai", "doubao"} else "/api/v1"
            return f"{scheme}://{target_host}{suffix}"

        if host in console_hosts:
            region = ""
            parts = [part for part in path.split("/") if part]
            if parts:
                first = parts[0].strip().lower()
                if re.fullmatch(r"[a-z]{2}(?:-[a-z0-9]+)+", first):
                    region = first
            if not region:
                fragment = str(parsed.fragment or "")
                region_match = re.search(r"\b(cn-beijing|ap-southeast-1|us-east-1|us-virginia)\b", raw, flags=re.I)
                if region_match:
                    region = region_match.group(1).lower()
                elif fragment:
                    fragment_match = re.search(r"\b(cn-beijing|ap-southeast-1|us-east-1|us-virginia)\b", fragment, flags=re.I)
                    if fragment_match:
                        region = fragment_match.group(1).lower()
            mapped_host = shared_host_by_region.get(region, "dashscope.aliyuncs.com")
            return _root_with_host(mapped_host)

        if host in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com", "dashscope-us.aliyuncs.com"} or host.endswith(".maas.aliyuncs.com"):
            if resolved_platform == "openai":
                if "/images/" in path or path.endswith("/chat/completions") or "/audio/" in path:
                    prefix_match = re.match(r"^(.*?/compatible-mode/v1)(?:/.*)?$", path, flags=re.I)
                    prefix = prefix_match.group(1) if prefix_match else "/compatible-mode/v1"
                    return f"{parsed.scheme or 'https'}://{host}{prefix}"
                return f"{parsed.scheme or 'https'}://{host}/compatible-mode/v1"
            if path.startswith("/api/v1"):
                api_root = re.match(r"^(.*?/api/v1)(?:/.*)?$", path, flags=re.I)
                if api_root:
                    return f"{parsed.scheme or 'https'}://{host}{api_root.group(1)}"
            return f"{parsed.scheme or 'https'}://{host}/api/v1"

        if resolved_platform == "modelscope":
            if host and "modelscope" in host:
                scheme = parsed.scheme or "https"
                if path.startswith("/v1"):
                    api_root = re.match(r"^(.*?/v1)(?:/.*)?$", path, flags=re.I)
                    if api_root:
                        return f"{scheme}://{host}{api_root.group(1)}"
                return f"{scheme}://{host}/v1"
            if re.search(r"/v1(?:/(?:images|tasks).*)?$", raw, flags=re.I):
                root = re.sub(r"/v1(?:/(?:images|tasks).*)?$", "/v1", raw, flags=re.I).rstrip("/")
                return root
        if resolved_platform == "doubao":
            if host and ("volces.com" in host or "volcengine.com" in host):
                scheme = parsed.scheme or "https"
                if path.startswith("/api/v3"):
                    api_root = re.match(r"^(.*?/api/v3)(?:/.*)?$", path, flags=re.I)
                    if api_root:
                        return f"{scheme}://{host}{api_root.group(1)}"
                return f"{scheme}://{host}/api/v3"
            if re.search(r"/api/v3(?:/images(?:/generations|/edits)?)?$", raw, flags=re.I):
                return re.sub(r"/api/v3(?:/images(?:/generations|/edits)?)?$", "/api/v3", raw, flags=re.I).rstrip("/")
        if resolved_platform == "gemini":
            if host and "googleapis.com" in host:
                scheme = parsed.scheme or "https"
                if "/models/" in path:
                    return f"{scheme}://{host}{path.rstrip('/')}"
                api_root = re.match(r"^(/v[0-9A-Za-z]+(?:beta)?)(?:/.*)?$", path, flags=re.I)
                prefix = api_root.group(1) if api_root else "/v1beta"
                return f"{scheme}://{host}{prefix}"
            if re.search(r"/v[0-9A-Za-z]+(?:beta)?(?:/models/.*)?$", raw, flags=re.I):
                return raw.rstrip("/")
        if resolved_platform == "bailian":
            if "/compatible-mode/v1" in raw.lower():
                return re.sub(r"/compatible-mode/v1(?:/.*)?$", "/api/v1", raw, flags=re.I).rstrip("/")
            task_root = re.sub(r"/tasks(?:/.*)?$", "", raw, flags=re.I)
            if task_root != raw:
                return task_root.rstrip("/")
        elif resolved_platform == "openai":
            if re.search(r"/api/v1(?:/services/aigc/.*)?$", raw, flags=re.I):
                root = re.sub(r"/api/v1(?:/services/aigc/.*)?$", "", raw, flags=re.I).rstrip("/")
                return f"{root}/compatible-mode/v1"

        return raw.rstrip("/")

    def _external_image_platform_label(self, platform: str = "") -> str:
        resolved = str(platform or self._resolved_external_image_api_platform() or "").strip().lower()
        if resolved == "bailian":
            return "阿里云百炼"
        if resolved == "modelscope":
            return "魔搭社区"
        if resolved == "doubao":
            return "豆包/火山方舟"
        if resolved == "gemini":
            return "Gemini"
        if resolved == "openai":
            return "OpenAI 兼容"
        if resolved == "agnes":
            return "Agnes Image"
        if resolved == "sensenova":
            return "SenseNova 日日新"
        if resolved == "minimax":
            return "MiniMax"
        return "在线图片 API"

    def _external_image_endpoint(self, endpoint_type: str = "generations") -> str:
        platform = self._resolved_external_image_api_platform()
        base = self._normalized_external_image_api_base_url(
            platform=platform
        ).rstrip("/")
        if not base:
            return ""
        if platform == "minimax":
            return f"{base}/image_generation"
        endpoint_type = str(endpoint_type or "generations").strip().lower()
        target = "edits" if endpoint_type == "edits" else "generations"
        if base.endswith(f"/images/{target}"):
            return base
        if base.endswith("/images/generations") or base.endswith("/images/edits"):
            return re.sub(r"/images/(?:generations|edits)$", f"/images/{target}", base)
        return f"{base}/images/{target}"

    def _external_image_endpoint_candidates(self, endpoint_type: str = "generations") -> list[str]:
        primary = self._external_image_endpoint(endpoint_type)
        if not primary:
            return []
        candidates = [primary]
        platform = self._resolved_external_image_api_platform()
        base = self._normalized_external_image_api_base_url(
            platform=platform
        ).rstrip("/")
        endpoint_type = str(endpoint_type or "generations").strip().lower()
        if platform == "minimax":
            return [primary]
        target = "edits" if endpoint_type == "edits" else "generations"
        lowered_base = base.lower()
        if (
            base
            and "/v1" not in lowered_base
            and "/compatible-mode/v1" not in lowered_base
            and not re.search(r"/images/(?:generations|edits)$", lowered_base)
        ):
            candidates.append(f"{base}/v1/images/{target}")
        deduped: list[str] = []
        for item in candidates:
            clean = str(item or "").strip()
            if clean and clean not in deduped:
                deduped.append(clean)
        return deduped

    def _sanitize_external_image_size(self, override: str = "") -> str:
        raw = str(override or self.external_image_api_size or "1024x1024").strip().lower()
        if re.fullmatch(r"\d{2,5}x\d{2,5}", raw):
            return raw
        return "1024x1024"

    @staticmethod
    def _normalize_agnes_image_ratio(value: Any) -> str:
        text = str(value or "").strip().lower().replace("：", ":").replace("/", ":")
        aliases = {
            "square": "1:1",
            "方形": "1:1",
            "portrait": "3:4",
            "竖版": "3:4",
            "landscape": "4:3",
            "横版": "4:3",
            "widescreen": "16:9",
            "宽屏": "16:9",
        }
        text = aliases.get(text, text)
        return text if text in {"1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9"} else ""

    def _agnes_image_size_and_ratio(self, image_size: str = "", prompt_text: str = "") -> tuple[str, str]:
        raw = str(image_size or getattr(self, "external_image_api_size", "") or "1K").strip()
        configured_ratio = self._normalize_agnes_image_ratio(getattr(self, "external_image_api_ratio", ""))
        inline = re.fullmatch(
            r"\s*([1-4])k(?:\s*[@|,;]\s*(1:1|3:4|4:3|16:9|9:16|2:3|3:2|21:9))?\s*",
            raw,
            flags=re.I,
        )
        inline_ratio = ""
        if inline:
            size = f"{inline.group(1)}K"
            inline_ratio = self._normalize_agnes_image_ratio(inline.group(2))
            ratio = inline_ratio or configured_ratio
        elif re.fullmatch(r"\d{2,5}x\d{2,5}", raw.lower()):
            size = raw.lower()
            ratio = configured_ratio
        else:
            size = "1K"
            ratio = configured_ratio

        prompt = _single_line(prompt_text, 1800).lower().replace("：", ":")
        prompt_ratio = ""
        ratio_match = re.search(r"(?<!\d)(21:9|16:9|9:16|3:4|4:3|2:3|3:2|1:1)(?!\d)", prompt)
        if ratio_match:
            prompt_ratio = self._normalize_agnes_image_ratio(ratio_match.group(1))
        elif re.search(r"\b(?:vertical|tall)\b|竖版|竖构图|纵向构图", prompt, flags=re.I):
            prompt_ratio = "3:4"
        elif re.search(r"\b(?:widescreen|ultrawide)\b|宽屏|超宽", prompt, flags=re.I):
            prompt_ratio = "16:9"
        ratio = configured_ratio or inline_ratio or prompt_ratio or ratio

        exact = re.fullmatch(r"(\d{2,5})x(\d{2,5})", size)
        if exact and ratio:
            width, height = int(exact.group(1)), int(exact.group(2))
            ratio_width, ratio_height = (int(part) for part in ratio.split(":"))
            if abs((width / max(1, height)) - (ratio_width / max(1, ratio_height))) > 0.08:
                longest = max(width, height)
                tier = 1 if longest <= 1400 else 2 if longest <= 2600 else 3 if longest <= 3800 else 4
                size = f"{tier}K"
        if re.fullmatch(r"\d{2,5}x\d{2,5}", size):
            # Agnes only documents ratio as a companion to 1K-4K tiers.
            ratio = ""
        return size, ratio

    def _sanitize_bailian_image_size(self, override: str = "") -> str:
        size = self._sanitize_external_image_size(override)
        return size.replace("x", "*")

    def _sanitize_sensenova_image_size(self, override: str = "") -> str:
        supported = (
            (1664, 2496), (2496, 1664), (1760, 2368), (2368, 1760),
            (1824, 2272), (2272, 1824), (2048, 2048), (2752, 1536),
            (1536, 2752), (3072, 1376), (1344, 3136),
        )
        raw = str(override or getattr(self, "external_image_api_size", "") or "2752x1536").strip().lower()
        match = re.fullmatch(r"(\d{2,5})x(\d{2,5})", raw)
        if not match:
            return "2752x1536"
        width, height = int(match.group(1)), int(match.group(2))
        if (width, height) in supported:
            return f"{width}x{height}"
        ratio = width / max(1, height)
        closest = min(supported, key=lambda item: abs((item[0] / item[1]) - ratio))
        return f"{closest[0]}x{closest[1]}"

    def _sensenova_image_model(self) -> str:
        model = str(getattr(self, "external_image_api_model", "") or "").strip()
        return "sensenova-u1-fast" if model.lower() == "senova-u1-fast" else model

    def _bailian_api_root(self) -> str:
        base = self._normalized_external_image_api_base_url(platform="bailian").rstrip("/")
        if not base:
            return ""
        known_suffixes = (
            "/services/aigc/multimodal-generation/generation",
            "/services/aigc/image-generation/generation",
            "/services/aigc/text2image/image-synthesis",
        )
        for suffix in known_suffixes:
            if base.endswith(suffix):
                return base[: -len(suffix)]
        task_match = re.match(r"^(https?://.+?/api/v1)/tasks(?:/.*)?$", base, flags=re.I)
        if task_match:
            return task_match.group(1).rstrip("/")
        api_match = re.match(r"^(https?://.+?/api/v1)(?:/.*)?$", base, flags=re.I)
        if api_match:
            return api_match.group(1).rstrip("/")
        if re.match(r"^https?://[^/]+$", base, flags=re.I):
            return f"{base}/api/v1"
        return base if base.endswith("/api/v1") else f"{base}/api/v1"

    def _bailian_multimodal_endpoint(self) -> str:
        base = str(self.external_image_api_base_url or "").strip().rstrip("/")
        if base.endswith("/services/aigc/multimodal-generation/generation"):
            return base
        root = self._bailian_api_root()
        return f"{root}/services/aigc/multimodal-generation/generation" if root else ""

    def _bailian_async_generation_endpoints(self) -> list[str]:
        base = str(self.external_image_api_base_url or "").strip().rstrip("/")
        root = self._bailian_api_root()
        candidates: list[str] = []
        for value in (
            base if base.endswith("/services/aigc/image-generation/generation") else "",
            base if base.endswith("/services/aigc/text2image/image-synthesis") else "",
            f"{root}/services/aigc/image-generation/generation" if root else "",
            f"{root}/services/aigc/text2image/image-synthesis" if root else "",
        ):
            cleaned = str(value or "").strip().rstrip("/")
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)
        return candidates

    def _bailian_task_endpoint(self, task_id: str) -> str:
        root = self._bailian_api_root()
        safe_task_id = quote(str(task_id or "").strip(), safe="")
        return f"{root}/tasks/{safe_task_id}" if root and safe_task_id else ""

    def _bailian_prefers_multimodal(self) -> bool:
        model = str(getattr(self, "external_image_api_model", "") or "").strip().lower()
        return "qwen-image" in model or "image-edit" in model

    def _modelscope_api_root(self) -> str:
        base = self._normalized_external_image_api_base_url(platform="modelscope").rstrip("/")
        if not base:
            return ""
        if base.endswith("/v1"):
            return base
        if re.match(r"^https?://[^/]+$", base, flags=re.I):
            return f"{base}/v1"
        return re.sub(r"/v1(?:/.*)?$", "/v1", base, flags=re.I).rstrip("/")

    def _modelscope_generation_endpoint(self) -> str:
        base = str(self.external_image_api_base_url or "").strip().rstrip("/")
        if base.endswith("/v1/images/generations"):
            return base
        root = self._modelscope_api_root()
        return f"{root}/images/generations" if root else ""

    def _modelscope_task_endpoint(self, task_id: str) -> str:
        root = self._modelscope_api_root()
        safe_task_id = quote(str(task_id or "").strip(), safe="")
        return f"{root}/tasks/{safe_task_id}" if root and safe_task_id else ""

    def _doubao_api_root(self) -> str:
        base = self._normalized_external_image_api_base_url(platform="doubao").rstrip("/")
        if not base:
            return ""
        if re.match(r"^https?://[^/]+$", base, flags=re.I):
            return f"{base}/api/v3"
        api_match = re.match(r"^(https?://.+?/api/v3)(?:/.*)?$", base, flags=re.I)
        if api_match:
            return api_match.group(1).rstrip("/")
        return base if base.endswith("/api/v3") else f"{base}/api/v3"

    def _doubao_generation_endpoint(self) -> str:
        base = str(self.external_image_api_base_url or "").strip().rstrip("/")
        if base.endswith("/images/generations"):
            return base
        root = self._doubao_api_root()
        return f"{root}/images/generations" if root else ""

    def _gemini_api_root(self) -> str:
        base = self._normalized_external_image_api_base_url(platform="gemini").rstrip("/")
        if not base:
            return ""
        if re.match(r"^https?://[^/]+$", base, flags=re.I):
            return f"{base}/v1beta"
        model_match = re.match(r"^(https?://.+?)/v[0-9A-Za-z]+(?:beta)?/models/.*$", base, flags=re.I)
        if model_match:
            return base
        api_match = re.match(r"^(https?://.+?/v[0-9A-Za-z]+(?:beta)?)(?:/.*)?$", base, flags=re.I)
        if api_match:
            return api_match.group(1).rstrip("/")
        return base

    def _gemini_generate_content_endpoint(self) -> str:
        base = str(self.external_image_api_base_url or "").strip().rstrip("/")
        model = quote(str(getattr(self, "external_image_api_model", "") or "").strip(), safe="-_.~")
        if not model:
            return ""
        if base.endswith(":generateContent"):
            return base
        if re.search(r"/models/[^/]+$", base, flags=re.I):
            return f"{base}:generateContent"
        root = self._gemini_api_root()
        if re.search(r"/models/[^/]+$", root, flags=re.I):
            return f"{root}:generateContent"
        return f"{root}/models/{model}:generateContent" if root else ""

    def _extract_gemini_inline_images(self, payload: Any) -> list[tuple[str, str]]:
        images: list[tuple[str, str]] = []

        def walk(value: Any, depth: int = 0) -> None:
            if depth > 10:
                return
            if isinstance(value, dict):
                inline = value.get("inlineData") or value.get("inline_data")
                if isinstance(inline, dict):
                    data = str(inline.get("data") or "").strip()
                    mime = str(inline.get("mimeType") or inline.get("mime_type") or "image/png").strip()
                    if data:
                        images.append((data, mime))
                file_data = value.get("fileData") or value.get("file_data")
                if isinstance(file_data, dict):
                    uri = str(file_data.get("fileUri") or file_data.get("file_uri") or "").strip()
                    if uri:
                        images.append((uri, ""))
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        walk(child, depth + 1)
            elif isinstance(value, list):
                for child in value:
                    walk(child, depth + 1)

        walk(payload)
        deduped: list[tuple[str, str]] = []
        seen: set[str] = set()
        for data, mime in images:
            if data in seen:
                continue
            seen.add(data)
            deduped.append((data, mime))
        return deduped

    def _external_payload_first_value(self, payload: Any, keys: tuple[str, ...], *, _depth: int = 0) -> Any:
        if payload is None or _depth > 8:
            return None
        if isinstance(payload, dict):
            for key in keys:
                if key in payload and payload.get(key) not in (None, ""):
                    return payload.get(key)
            for value in payload.values():
                found = self._external_payload_first_value(value, keys, _depth=_depth + 1)
                if found not in (None, ""):
                    return found
        elif isinstance(payload, list):
            for value in payload:
                found = self._external_payload_first_value(value, keys, _depth=_depth + 1)
                if found not in (None, ""):
                    return found
        return None

    def _external_payload_image_values(self, payload: Any, *, _depth: int = 0) -> list[Any]:
        if payload is None or _depth > 8:
            return []
        values: list[Any] = []
        image_keys = (
            "url",
            "image",
            "image_url",
            "imageUrl",
            "output_image",
            "outputImage",
            "result",
            "b64_json",
            "base64",
            "base64_image",
            "data_url",
        )
        image_list_keys = (
            "data",
            "images",
            "image_urls",
            "imageUrls",
            "output_images",
            "outputImages",
            "results",
            "outputs",
        )
        if isinstance(payload, dict):
            for key in image_keys:
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    values.append(value)
            for key in image_list_keys:
                value = payload.get(key)
                if isinstance(value, list):
                    for item in value:
                        values.extend(self._external_payload_image_values(item, _depth=_depth + 1))
                elif isinstance(value, dict):
                    values.extend(self._external_payload_image_values(value, _depth=_depth + 1))
                elif isinstance(value, str) and value.strip():
                    values.append(value)
            for value in payload.values():
                if isinstance(value, (dict, list)):
                    values.extend(self._external_payload_image_values(value, _depth=_depth + 1))
        elif isinstance(payload, list):
            for item in payload:
                values.extend(self._external_payload_image_values(item, _depth=_depth + 1))
        elif isinstance(payload, str):
            text = payload.strip()
            if text.startswith(("http://", "https://", "data:image")) or len(text) > 128:
                values.append(text)
        deduped: list[Any] = []
        seen: set[str] = set()
        for value in values:
            marker = str(value)
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(value)
        return deduped

    async def _reference_image_to_data_url(self, reference_image_path: str) -> str:
        path = Path(str(reference_image_path or ""))
        if not path.exists() or not path.is_file():
            return ""
        suffix = path.suffix.lower()
        mime_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(suffix)
        if not mime_type:
            return ""
        image_bytes = await asyncio.to_thread(path.read_bytes)
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    async def _materialize_external_image_value(
        self,
        image_value: Any,
        *,
        session_key: str,
        success_note: str = "ok",
    ) -> tuple[str, str]:
        if isinstance(image_value, dict):
            image_value = (
                image_value.get("url")
                or image_value.get("image")
                or image_value.get("image_url")
                or image_value.get("imageUrl")
                or image_value.get("output_image")
                or image_value.get("outputImage")
                or image_value.get("result")
                or image_value.get("b64_json")
                or image_value.get("base64")
                or image_value.get("base64_image")
                or image_value.get("data_url")
                or ""
            )
        raw = str(image_value or "").strip()
        if not raw:
            return "", "图片结果为空"
        if raw.lower().startswith("data:image") and "," in raw:
            header, encoded = raw.split(",", 1)
            if not re.fullmatch(r"data:image/[a-z0-9.+-]+;base64", header.strip(), flags=re.I):
                return "", "图片 data URI 格式无效"
            return await self._materialize_external_image_base64(
                encoded,
                session_key=session_key,
                success_note=success_note,
            )
        if re.fullmatch(r"[A-Za-z0-9+/=\\s]+", raw) and len(raw) > 128:
            return await self._materialize_external_image_base64(
                raw,
                session_key=session_key,
                success_note=success_note,
            )
        saved, note = await self._download_external_image_url(raw, session_key=session_key)
        return saved, success_note if saved else note

    @staticmethod
    def _external_image_extension_from_bytes(image_bytes: bytes) -> str:
        if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if image_bytes.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if len(image_bytes) >= 12 and image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
            return ".webp"
        return ""

    async def _materialize_external_image_base64(
        self,
        encoded: Any,
        *,
        session_key: str,
        success_note: str,
    ) -> tuple[str, str]:
        compact = re.sub(r"\s+", "", str(encoded or ""))
        if not compact:
            return "", "图片 base64 数据为空"
        max_encoded_chars = ((_EXTERNAL_IMAGE_MAX_BYTES + 2) // 3) * 4
        if len(compact) > max_encoded_chars:
            return "", f"在线图片数据过大（超过 {_EXTERNAL_IMAGE_MAX_BYTES // (1024 * 1024)} MB）"
        padded = compact + ("=" * (-len(compact) % 4))
        try:
            image_bytes = base64.b64decode(padded, validate=True)
        except (binascii.Error, ValueError):
            return "", "图片 base64 数据无效"
        if len(image_bytes) > _EXTERNAL_IMAGE_MAX_BYTES:
            return "", f"在线图片数据过大（超过 {_EXTERNAL_IMAGE_MAX_BYTES // (1024 * 1024)} MB）"
        ext = self._external_image_extension_from_bytes(image_bytes)
        if not ext:
            return "", "在线图片数据不是受支持的 PNG、JPEG 或 WebP 图片"
        saved = await self._save_external_generated_image(image_bytes, session_key=session_key, ext=ext)
        return saved, success_note if saved else "保存在线图片失败"

    async def _materialize_external_openai_image_item(
        self,
        item: dict[str, Any],
        *,
        session_key: str,
        success_note: str,
    ) -> tuple[str, str]:
        b64_value = str(item.get("b64_json") or "").strip()
        url_value = str(item.get("url") or "").strip()
        candidates: list[tuple[str, str]] = []
        if b64_value:
            normalized = b64_value
            if not b64_value.lower().startswith("data:image"):
                normalized = f"data:image/png;base64,{b64_value}"
            candidates.append(("b64_json", normalized))
        if url_value and url_value != b64_value:
            candidates.append(("url", url_value))
        if not candidates:
            return "", "在线图片 API 未返回 url 或 b64_json"

        last_note = "图片结果为空"
        for index, (source, candidate) in enumerate(candidates):
            saved, note = await self._materialize_external_image_value(
                candidate,
                session_key=session_key,
                success_note=success_note,
            )
            if saved:
                return saved, note
            last_note = note
            if index + 1 < len(candidates):
                logger.info(
                    "[PrivateCompanion] 在线图片 API 图片候选解析失败,尝试下一个: source=%s note=%s",
                    source,
                    self._external_image_diagnostic_text(note, 180),
                )
        return "", last_note

    def _external_image_model_misconfiguration_note_for_values(
        self,
        *,
        model_name: Any = "",
        configured_platform: Any = "auto",
        base_url: Any = "",
    ) -> str:
        model = _single_line(model_name, 120)
        if not model:
            return "未配置在线图片模型"
        lowered = model.lower()
        platform = self._resolved_external_image_api_platform_for_values(
            configured=configured_platform,
            base_url=base_url,
            model_name=model,
        )
        image_tokens = ("image", "img", "dall", "flux", "sd", "stable-diffusion", "midjourney", "mj", "kolors", "wanx", "wan", "seedream", "imagen", "nano-banana")
        text_model_prefixes = ("gpt-", "claude", "gemini", "deepseek", "qwen", "glm", "moonshot", "kimi", "yi-", "doubao")
        if platform == "bailian" and (lowered.startswith("qwen-image") or lowered.startswith("wan")):
            return ""
        if platform == "doubao" and (lowered.startswith(("doubao-seedream", "seedream")) or "seedream" in lowered):
            return ""
        if platform == "gemini" and any(token in lowered for token in ("image", "imagen", "nano-banana")):
            return ""
        if platform == "agnes":
            if lowered.startswith("agnes-image-"):
                return ""
            return "Agnes Image 必须填写图片模型 ID，例如 agnes-image-2.1-flash"
        if platform == "sensenova":
            if lowered in {"senova-u1-fast", "sensenova-u1-fast"}:
                return ""
            return "SenseNova 日日新信息图模型必须填写官方 Model ID：sensenova-u1-fast"
        if platform == "minimax":
            if lowered in {"image-01", "image-01-live"}:
                return ""
            return "MiniMax 图片接口必须填写官方 Model ID：image-01 或 image-01-live"
        if lowered.startswith(text_model_prefixes) and not any(token in lowered for token in image_tokens):
            return f"在线图片模型填成了文本/聊天模型：{model}。请改成该平台的图片模型名，例如支持 /images/generations 或 /images/edits 的模型。"
        return ""

    def _external_image_model_misconfiguration_note(self) -> str:
        return self._external_image_model_misconfiguration_note_for_values(
            model_name=getattr(self, "external_image_api_model", ""),
            configured_platform=getattr(self, "external_image_api_platform", "auto"),
            base_url=getattr(self, "external_image_api_base_url", ""),
        )

    def _external_image_diagnostic_text(self, value: Any, limit: int = 220) -> str:
        cleaned = _redact_outbound_secrets(value, self)
        try:
            header_values = self._external_image_custom_headers().values()
        except Exception:
            header_values = ()
        for raw_secret in header_values:
            secret = str(raw_secret or "").strip()
            if len(secret) >= 4:
                cleaned = cleaned.replace(secret, "[密钥已隐藏]")
        return _single_line(cleaned, limit)

    def _external_image_api_error_note(self, status: int, text: str, *, reference: bool = False, endpoint: str = "") -> str:
        diagnostic_text = getattr(self, "_external_image_diagnostic_text", None)
        if not callable(diagnostic_text):
            diagnostic_text = lambda value, limit=220: _single_line(_redact_outbound_secrets(value, self), limit)
        raw = diagnostic_text(text, 260)
        lowered = raw.lower()
        if "requires an image model" in lowered or "image model" in lowered and "got" in lowered:
            return self._external_image_model_misconfiguration_note() or (
                f"在线图片模型不支持 images 接口。当前模型：{_single_line(getattr(self, 'external_image_api_model', ''), 80) or '-'}，请改成图片模型。"
            )
        prefix = "参考图接口" if reference else ""
        safe_endpoint = diagnostic_text(endpoint, 180)
        endpoint_note = f"端点 {safe_endpoint} " if safe_endpoint else ""
        platform_getter = getattr(self, "_resolved_external_image_api_platform", None)
        platform = platform_getter() if callable(platform_getter) else ""
        if platform == "sensenova" and int(status) == 403:
            return (
                f"{prefix}{endpoint_note}HTTP 403：SenseNova 官方错误码表示当前请求语言不受支持。"
                "请把提示词改为中文或英文，并避免仅使用无法识别的混合符号/小语种后重试。"
                f"返回：{diagnostic_text(raw, 120)}"
            )
        if platform == "sensenova" and int(status) == 401:
            if "authorization not found" in lowered or "missing authorization" in lowered:
                return (
                    f"{prefix}{endpoint_note}HTTP 401：SenseNova 未识别到 Authorization。"
                    "应使用 Authorization: Bearer <控制台 API Key>。"
                )
            return (
                f"{prefix}{endpoint_note}HTTP 401：SenseNova 已收到 Bearer，但 API Key 无效或当前额度不足。"
                "请在 platform.sensenova.cn 控制台检查 Key 状态与 Token Plan。"
                f"返回：{diagnostic_text(raw, 120)}"
            )
        if int(status) in {401, 403}:
            if "authorization not found" in lowered or "missing authorization" in lowered:
                return (
                    f"{prefix}{endpoint_note}HTTP {status}：服务未识别到鉴权信息。"
                    "OpenAI 兼容链路应使用 Authorization: Bearer <API Key>；请检查自定义请求头是否覆盖或删除了 Authorization。"
                )
            permission_causes = "API Key 无效、已过期、被停用，或没有该图片模型/接口权限"
            if int(status) == 403:
                permission_causes += "；也可能命中账号/IP 白名单或网关 WAF"
            return (
                f"{prefix}{endpoint_note}HTTP {status}：鉴权已送达但服务拒绝访问。"
                f"常见原因：{permission_causes}。返回：{diagnostic_text(raw, 120)}"
            )
        if int(status) == 404:
            platform_getter = getattr(self, "_resolved_external_image_api_platform", None)
            if callable(platform_getter) and platform_getter() == "minimax":
                return (
                    f"{prefix}{endpoint_note}HTTP 404：未找到 MiniMax 图片接口。"
                    "官方路径是 /v1/image_generation，请不要使用 /v1/image/generation 或 /images/generations。"
                    f"返回：{diagnostic_text(raw, 120)}"
                )
            return (
                f"{prefix}{endpoint_note}HTTP 404：未找到生图接口。"
                "请检查在线 API 地址是否需要 /v1，或该代理是否支持 /images/generations。"
                f"返回：{diagnostic_text(raw, 120)}"
            )
        if self._external_image_api_is_transient_status(status):
            return (
                f"{prefix}{endpoint_note}HTTP {status}：上游生图服务临时失败或网关中断。"
                "这通常不是 Key、模型名或提示词的确定性错误；插件会自动短暂重试一次，"
                "若仍失败请稍后重试或配置备用图片 API。"
                f"返回：{diagnostic_text(raw, 140)}"
            )
        if platform == "agnes" and int(status) in {400, 422}:
            return (
                f"{prefix}{endpoint_note}HTTP {status}：Agnes Image 请求参数不符合模型要求。"
                "请确认模型为 agnes-image-2.1-flash，文生图包含 model/prompt/size；"
                "参考图放在 extra_body.image，response_format 放在 extra_body，且不要发送 tags。"
                f"返回：{diagnostic_text(raw, 140)}"
            )
        return f"{prefix}{endpoint_note}HTTP {status}: {diagnostic_text(raw, 180)}"

    @staticmethod
    def _external_image_api_is_transient_status(status: Any) -> bool:
        try:
            return int(status) in {500, 502, 503, 504}
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _external_image_api_transient_retry_delay(status: Any) -> float:
        return 1.5 if ProactiveMessageMixin._external_image_api_is_transient_status(status) else 0.0

    def _url_same_origin(self, left: str, right: str) -> bool:
        try:
            l_parsed = urlparse(str(left or ""))
            r_parsed = urlparse(str(right or ""))
        except Exception:
            return False
        return bool(
            l_parsed.scheme
            and r_parsed.scheme
            and l_parsed.scheme.lower() == r_parsed.scheme.lower()
            and l_parsed.netloc.lower() == r_parsed.netloc.lower()
        )

    def _external_image_download_headers(self, target_url: str) -> tuple[dict[str, str], str]:
        headers: dict[str, str] = {
            "User-Agent": "Mozilla/5.0 AstrBot PrivateCompanion/5.0.0",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        api_endpoint = self._external_image_endpoint("generations")
        api_base = str(getattr(self, "external_image_api_base_url", "") or "").strip()
        same_origin = self._url_same_origin(target_url, api_endpoint) or self._url_same_origin(target_url, api_base)
        custom_count = 0
        auth_sent = False
        if same_origin:
            if getattr(self, "external_image_api_key", ""):
                headers["Authorization"] = f"Bearer {self.external_image_api_key}"
                auth_sent = True
            for key, value in self._external_image_custom_headers().items():
                normalized = str(key or "").strip()
                if normalized.lower() in {"content-length", "content-type", "host", "connection", "transfer-encoding"}:
                    continue
                headers[normalized] = str(value)
                custom_count += 1
            if not any(key.lower() == "referer" for key in headers):
                referer = api_endpoint or api_base
                if referer:
                    headers["Referer"] = referer
        note = f"same_origin={same_origin},auth={auth_sent},custom={custom_count},referer={any(key.lower() == 'referer' for key in headers)}"
        return headers, note

    def _external_image_download_target(self, target_url: str) -> tuple[Any, bool]:
        """Preserve the exact encoding of temporary object-storage signed URLs."""
        target = str(target_url or "").strip()
        lowered = target.lower()
        signed_markers = (
            "x-amz-algorithm=",
            "x-amz-credential=",
            "x-amz-signature=",
            "x-oss-signature=",
        )
        if not any(marker in lowered for marker in signed_markers):
            return target, False
        try:
            from yarl import URL

            # aiohttp normally canonicalizes URLs. Re-quoting even one signed path or
            # query byte makes OSS/S3 reject an otherwise valid temporary URL with 403.
            return URL(target, encoded=True), True
        except Exception:
            return target, False

    def _external_image_timeout_note(self, *, reference: bool = False, download: bool = False, label: str = "") -> str:
        timeout_seconds = _safe_int(getattr(self, "external_image_api_timeout_seconds", 180), 180, 1)
        if label:
            target = label
        elif download:
            target = "下载在线图片结果"
        elif reference:
            target = "在线图片 API 参考图接口"
        else:
            target = "在线图片 API"
        return f"{target}超时（{timeout_seconds}秒内没有返回），可调高在线生图超时秒数或切换/回退本地生图后端"

    def _external_generated_image_output_path(self, *, session_key: str, ext: str = ".png") -> Path:
        safe_ext = ext if ext.startswith(".") else f".{ext}"
        if safe_ext.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            safe_ext = ".png"
        out_dir = Path(self.data_dir) / "generated_photos"
        out_dir.mkdir(parents=True, exist_ok=True)
        session_part = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(session_key or "private_companion"))[:60] or "private_companion"
        return out_dir / f"{session_part}_{self._environment_now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{safe_ext}"

    def _cleanup_generated_photo_files(self, *, protected_path: str | Path = "") -> dict[str, int]:
        result = {
            "scanned_files": 0,
            "removed_files": 0,
            "removed_bytes": 0,
            "removed_by_age": 0,
            "removed_by_size": 0,
            "removed_partial": 0,
            "remaining_bytes": 0,
        }
        if not bool(getattr(self, "enable_generated_photo_cleanup", True)):
            return result

        retention_days = _safe_int(getattr(self, "generated_photo_retention_days", 30), 30, 0, 3650)
        max_megabytes = _safe_int(getattr(self, "generated_photo_max_mb", 512), 512, 0, 10240)

        root = Path(self.data_dir) / "generated_photos"
        if not root.exists():
            return result
        try:
            protected = Path(protected_path).resolve(strict=False) if str(protected_path or "").strip() else None
        except (OSError, ValueError):
            protected = None

        now = time.time()
        age_cutoff = now - retention_days * 86400 if retention_days > 0 else 0.0
        partial_cutoff = now - 3600
        candidates: list[tuple[Path, float, int, bool]] = []

        def remove_file(path: Path, size: int, reason: str) -> bool:
            try:
                path.unlink()
            except OSError:
                return False
            result["removed_files"] += 1
            result["removed_bytes"] += max(0, size)
            result[reason] += 1
            return True

        try:
            entries = list(root.iterdir())
        except OSError:
            return result
        for path in entries:
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
                resolved = path.resolve(strict=False)
            except OSError:
                continue
            is_protected = protected is not None and resolved == protected
            if path.suffix.lower() == ".part":
                if not is_protected and stat.st_mtime < partial_cutoff:
                    remove_file(path, stat.st_size, "removed_partial")
                continue
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            result["scanned_files"] += 1
            if not is_protected and age_cutoff and stat.st_mtime < age_cutoff:
                if remove_file(path, stat.st_size, "removed_by_age"):
                    continue
            candidates.append((path, stat.st_mtime, max(0, stat.st_size), is_protected))

        total_bytes = sum(item[2] for item in candidates)
        max_bytes = max_megabytes * 1024 * 1024
        if max_bytes > 0 and total_bytes > max_bytes:
            for path, _mtime, size, is_protected in sorted(candidates, key=lambda item: item[1]):
                if total_bytes <= max_bytes:
                    break
                if is_protected:
                    continue
                if remove_file(path, size, "removed_by_size"):
                    total_bytes -= size
        result["remaining_bytes"] = max(0, total_bytes)
        if result["removed_files"]:
            logger.info(
                "[PrivateCompanion] 已清理生图归档: files=%s size=%.1fMB age=%s capacity=%s partial=%s retention=%sd max=%sMB",
                result["removed_files"],
                result["removed_bytes"] / (1024 * 1024),
                result["removed_by_age"],
                result["removed_by_size"],
                result["removed_partial"],
                retention_days,
                max_megabytes,
            )
        return result

    async def _maybe_cleanup_generated_photos(
        self,
        *,
        force: bool = False,
        protected_path: str | Path = "",
    ) -> dict[str, int]:
        if not bool(getattr(self, "enable_generated_photo_cleanup", True)):
            return {}
        now = time.time()
        last_cleanup = float(getattr(self, "_last_generated_photo_cleanup_ts", 0.0) or 0.0)
        if not force and now - last_cleanup < 600:
            return {}
        self._last_generated_photo_cleanup_ts = now
        try:
            return await asyncio.to_thread(
                self._cleanup_generated_photo_files,
                protected_path=protected_path,
            )
        except Exception as exc:
            logger.warning("[PrivateCompanion] 清理生图归档失败: %s", _single_line(exc, 180))
            return {}

    async def _save_external_generated_image(
        self,
        image_bytes: bytes,
        *,
        session_key: str,
        ext: str = ".png",
    ) -> str:
        if not image_bytes:
            return ""
        file_path = self._external_generated_image_output_path(session_key=session_key, ext=ext)
        await asyncio.to_thread(file_path.write_bytes, image_bytes)
        logger.info(
            "[PrivateCompanion] 生图文件已保存: session=%s path=%s bytes=%s",
            _single_line(session_key, 80),
            _single_line(str(file_path), 180),
            len(image_bytes),
        )
        await self._maybe_cleanup_generated_photos(protected_path=file_path)
        return str(file_path)

    def _external_image_download_proxy_url(self) -> str:
        proxy = str(getattr(self, "external_image_download_proxy", "") or "").strip()
        if not proxy:
            return ""
        try:
            parsed = urlparse(proxy)
        except Exception:
            parsed = None
        if not parsed or parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            logger.warning("[PrivateCompanion] 在线图片网络代理格式无效，已忽略")
            return ""
        return proxy

    async def _get_external_image_download_session(self, *, use_environment_proxy: bool) -> tuple[Any, bool]:
        import aiohttp

        session = getattr(self, "_external_image_download_session", None)
        session_trust_env = getattr(self, "_external_image_download_session_trust_env", None)
        if session is not None and not session.closed and session_trust_env == use_environment_proxy:
            return session, True

        lock = getattr(self, "_external_image_download_session_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._external_image_download_session_lock = lock
        async with lock:
            session = getattr(self, "_external_image_download_session", None)
            session_trust_env = getattr(self, "_external_image_download_session_trust_env", None)
            if session is not None and not session.closed and session_trust_env == use_environment_proxy:
                return session, True
            if session is not None and not session.closed:
                await session.close()
            connector = aiohttp.TCPConnector(limit=8, limit_per_host=4, ttl_dns_cache=300)
            session = aiohttp.ClientSession(connector=connector, trust_env=use_environment_proxy)
            self._external_image_download_session = session
            self._external_image_download_session_trust_env = use_environment_proxy
            return session, False

    async def _close_external_image_download_session(self) -> None:
        session = getattr(self, "_external_image_download_session", None)
        self._external_image_download_session = None
        self._external_image_download_session_trust_env = None
        if session is not None and not session.closed:
            await session.close()

    async def _download_external_image_url(self, url: str, *, session_key: str) -> tuple[str, str]:
        target = str(url or "").strip()
        if not target:
            return "", "图片地址为空"
        configured_timeout = _safe_int(getattr(self, "external_image_api_timeout_seconds", 180), 180, 20, 600)
        # Keep URL retrieval independent from the longer image-generation request.
        download_timeout = min(configured_timeout, 75)
        max_bytes = _EXTERNAL_IMAGE_MAX_BYTES
        started_at = time.perf_counter()
        temporary_path: Path | None = None
        try:
            import aiohttp

            headers, header_note = self._external_image_download_headers(target)
            request_target, signed_url_preserved = self._external_image_download_target(target)
            header_note = f"{header_note},signed_url_preserved={signed_url_preserved}"
            use_environment_proxy = bool(getattr(self, "external_image_download_use_environment_proxy", False))
            proxy_url = self._external_image_download_proxy_url()
            timeout = aiohttp.ClientTimeout(
                total=float(download_timeout),
                sock_connect=min(15.0, float(download_timeout)),
                sock_read=min(30.0, float(download_timeout)),
            )
            session, session_reused = await self._get_external_image_download_session(
                use_environment_proxy=use_environment_proxy,
            )
            request_options: dict[str, Any] = {"headers": headers, "timeout": timeout}
            if proxy_url:
                request_options["proxy"] = proxy_url
            async with session.get(request_target, **request_options) as response:
                if response.status >= 400:
                    logger.info(
                        "[PrivateCompanion] 下载在线生图结果失败: status=%s headers=%s url=%s",
                        response.status,
                        header_note,
                        self._external_image_diagnostic_text(target, 180),
                    )
                    return "", f"下载图片失败：HTTP {response.status}"
                content_type = str(response.headers.get("Content-Type", "") or "").lower()
                declared_size = _safe_int(response.headers.get("Content-Length"), 0, 0)
                if declared_size > max_bytes:
                    logger.info(
                        "[PrivateCompanion] 下载在线生图结果过大,已停止: bytes=%s limit=%s url=%s",
                        declared_size,
                        max_bytes,
                        self._external_image_diagnostic_text(target, 180),
                    )
                    return "", f"下载图片过大（超过 {max_bytes // (1024 * 1024)} MB）"
                ext = ".png"
                if "jpeg" in content_type or "jpg" in content_type:
                    ext = ".jpg"
                elif "webp" in content_type:
                    ext = ".webp"
                output_path = self._external_generated_image_output_path(session_key=session_key, ext=ext)
                temporary_path = output_path.with_name(f"{output_path.name}.part")
                received_size = 0
                with temporary_path.open("wb") as output:
                    async for chunk in response.content.iter_chunked(128 * 1024):
                        received_size += len(chunk)
                        if received_size > max_bytes:
                            logger.info(
                                "[PrivateCompanion] 下载在线生图结果超过大小上限,已停止: bytes=%s limit=%s url=%s",
                                received_size,
                                max_bytes,
                                self._external_image_diagnostic_text(target, 180),
                            )
                            return "", f"下载图片过大（超过 {max_bytes // (1024 * 1024)} MB）"
                        output.write(chunk)
                if received_size <= 0:
                    return "", "下载图片为空"
                await asyncio.to_thread(temporary_path.replace, output_path)
                temporary_path = None
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            logger.info(
                "[PrivateCompanion] 下载在线生图结果完成: status=%s content_type=%s bytes=%s elapsed=%sms reused=%s proxy=%s env_proxy=%s timeout=%ss url=%s",
                response.status,
                _single_line(content_type, 80),
                received_size,
                elapsed_ms,
                session_reused,
                bool(proxy_url),
                use_environment_proxy,
                download_timeout,
                self._external_image_diagnostic_text(target, 180),
            )
            logger.info(
                "[PrivateCompanion] 生图文件已保存: session=%s path=%s bytes=%s",
                _single_line(session_key, 80),
                _single_line(str(output_path), 180),
                received_size,
            )
            await self._maybe_cleanup_generated_photos(protected_path=output_path)
            return str(output_path), "ok"
        except asyncio.TimeoutError:
            note = f"下载在线图片结果超时（{download_timeout} 秒内未完成），请检查运行 Bot 的服务器是否能直连该图片 URL"
            logger.info(
                "[PrivateCompanion] 下载在线生图结果超时: url=%s note=%s",
                self._external_image_diagnostic_text(target, 180),
                note,
            )
            return "", note
        except Exception as e:
            safe_error = self._external_image_diagnostic_text(e, 220)
            logger.warning("[PrivateCompanion] 下载在线生图结果失败: %s", safe_error)
            return "", safe_error
        finally:
            if temporary_path is not None:
                try:
                    if temporary_path.exists():
                        await asyncio.to_thread(temporary_path.unlink)
                except Exception:
                    pass

    async def _run_bailian_multimodal_photo_generation(
        self,
        prompt_text: str,
        *,
        session_key: str,
        reference_image_path: str = "",
        image_size: str = "",
    ) -> tuple[str, str]:
        endpoint = self._bailian_multimodal_endpoint()
        if not endpoint:
            return "", "未配置百炼 API 地址"
        try:
            import aiohttp

            content: list[dict[str, Any]] = [{"type": "text", "text": prompt_text}]
            used_reference = False
            if reference_image_path:
                reference_value = reference_image_path
                if not re.match(r"^https?://", reference_value, flags=re.I):
                    reference_value = await self._reference_image_to_data_url(reference_value)
                if not reference_value:
                    return "", "百炼参考图无法读取或转换，已停止纯文生图回退"
                content.append({"type": "image", "image": reference_value})
                used_reference = True
            payload = {
                "model": self.external_image_api_model,
                "input": {"messages": [{"role": "user", "content": content}]},
                "parameters": {"size": self._sanitize_bailian_image_size(image_size)},
            }
            headers = {
                "Authorization": f"Bearer {self.external_image_api_key}",
                "Content-Type": "application/json",
            }
            headers.update(self._external_image_custom_headers())
            timeout = aiohttp.ClientTimeout(total=float(self.external_image_api_timeout_seconds))
            logger.info(
                "[PrivateCompanion] 百炼多模态生图提交: endpoint=%s model=%s size=%s reference=%s prompt_preview=%s",
                self._external_image_diagnostic_text(endpoint, 160),
                _single_line(self.external_image_api_model, 80),
                _single_line(payload["parameters"]["size"], 40),
                used_reference,
                _single_line(prompt_text, 180),
            )
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(endpoint, headers=headers, json=payload) as response:
                    text = await response.text()
                    logger.info(
                        "[PrivateCompanion] 百炼多模态生图响应: status=%s chars=%s preview=%s",
                        response.status,
                        len(text or ""),
                        self._external_image_diagnostic_text(text, 220),
                    )
                    if response.status >= 400:
                        return "", f"百炼多模态生图 HTTP {response.status}: {self._external_image_diagnostic_text(text, 180)}"
            data = self._extract_json_payload(text) if text else {}
            if not isinstance(data, dict):
                return "", "百炼多模态生图返回格式无效"
            output = data.get("output") if isinstance(data.get("output"), dict) else {}
            choices = output.get("choices")
            if not isinstance(choices, list) or not choices:
                return "", "百炼多模态生图未返回 choices"
            first_choice = choices[0] if isinstance(choices[0], dict) else {}
            message = first_choice.get("message") if isinstance(first_choice.get("message"), dict) else {}
            items = message.get("content")
            if isinstance(items, dict):
                items = [items]
            if not isinstance(items, list):
                return "", "百炼多模态生图未返回图片内容"
            for item in items:
                if not isinstance(item, dict):
                    continue
                image_value = (
                    item.get("image")
                    or item.get("image_url")
                    or item.get("url")
                    or (item.get("result") if isinstance(item.get("result"), str) else "")
                )
                if image_value:
                    note = "ok；已使用本地人设参考图" if used_reference else "ok"
                    return await self._materialize_external_image_value(
                        image_value,
                        session_key=session_key,
                        success_note=note,
                    )
            return "", "百炼多模态生图未返回图片地址"
        except asyncio.TimeoutError:
            note = self._external_image_timeout_note(label="百炼多模态生图接口")
            logger.info(
                "[PrivateCompanion] 百炼多模态生图超时: endpoint=%s model=%s timeout=%ss",
                self._external_image_diagnostic_text(endpoint, 160),
                _single_line(self.external_image_api_model, 80),
                _safe_int(getattr(self, "external_image_api_timeout_seconds", 180), 180, 1),
            )
            return "", note
        except Exception as e:
            safe_error = self._external_image_diagnostic_text(e, 220)
            logger.warning("[PrivateCompanion] 百炼多模态生图失败: %s", safe_error)
            return "", safe_error

    async def _run_bailian_async_photo_generation(
        self,
        prompt_text: str,
        *,
        session_key: str,
        image_size: str = "",
    ) -> tuple[str, str]:
        endpoints = self._bailian_async_generation_endpoints()
        if not endpoints:
            return "", "未配置百炼 API 地址"
        try:
            import aiohttp

            headers = {
                "Authorization": f"Bearer {self.external_image_api_key}",
                "Content-Type": "application/json",
                "X-DashScope-Async": "enable",
            }
            headers.update(self._external_image_custom_headers())
            payload = {
                "model": self.external_image_api_model,
                "input": {"prompt": prompt_text},
                "parameters": {"size": self._sanitize_bailian_image_size(image_size)},
            }
            timeout = aiohttp.ClientTimeout(total=float(self.external_image_api_timeout_seconds))
            task_id = ""
            submit_note = ""
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for endpoint in endpoints:
                    logger.info(
                        "[PrivateCompanion] 百炼异步生图提交: endpoint=%s model=%s size=%s prompt_preview=%s",
                        self._external_image_diagnostic_text(endpoint, 160),
                        _single_line(self.external_image_api_model, 80),
                        _single_line(payload["parameters"]["size"], 40),
                        _single_line(prompt_text, 180),
                    )
                    async with session.post(endpoint, headers=headers, json=payload) as response:
                        text = await response.text()
                        logger.info(
                            "[PrivateCompanion] 百炼异步生图响应: status=%s chars=%s preview=%s",
                            response.status,
                            len(text or ""),
                            self._external_image_diagnostic_text(text, 220),
                        )
                        if response.status >= 400:
                            submit_note = f"百炼异步生图 HTTP {response.status}: {self._external_image_diagnostic_text(text, 180)}"
                            if response.status in {404, 405}:
                                continue
                            return "", submit_note
                    data = self._extract_json_payload(text) if text else {}
                    if not isinstance(data, dict):
                        submit_note = "百炼异步生图返回格式无效"
                        continue
                    output = data.get("output") if isinstance(data.get("output"), dict) else {}
                    task_id = str(output.get("task_id") or data.get("task_id") or "").strip()
                    if task_id:
                        break
                    submit_note = _single_line(
                        output.get("message") or data.get("message") or "百炼异步生图未返回任务 ID",
                        180,
                    )
                if not task_id:
                    return "", submit_note or "百炼异步生图未返回任务 ID"

                task_endpoint = self._bailian_task_endpoint(task_id)
                if not task_endpoint:
                    return "", "无法生成百炼任务查询地址"

                deadline = time.monotonic() + float(self.external_image_api_timeout_seconds)
                while True:
                    if time.monotonic() >= deadline:
                        return "", self._external_image_timeout_note(label="百炼异步生图任务轮询")
                    task_headers = {
                        "Authorization": f"Bearer {self.external_image_api_key}",
                        **self._external_image_custom_headers(),
                    }
                    async with session.get(task_endpoint, headers=task_headers) as response:
                        text = await response.text()
                        logger.info(
                            "[PrivateCompanion] 百炼任务查询响应: status=%s task=%s chars=%s preview=%s",
                            response.status,
                            _single_line(task_id, 80),
                            len(text or ""),
                            self._external_image_diagnostic_text(text, 220),
                        )
                        if response.status >= 400:
                            return "", f"百炼任务查询 HTTP {response.status}: {self._external_image_diagnostic_text(text, 180)}"
                    data = self._extract_json_payload(text) if text else {}
                    if not isinstance(data, dict):
                        await asyncio.sleep(2)
                        continue
                    output = data.get("output") if isinstance(data.get("output"), dict) else {}
                    task_status = str(output.get("task_status") or data.get("task_status") or output.get("status") or "").strip().upper()
                    if task_status in {"SUCCEEDED", "SUCCESS"}:
                        results = output.get("results")
                        if isinstance(results, list):
                            for item in results:
                                if not isinstance(item, dict):
                                    continue
                                image_value = item.get("url") or item.get("image") or item.get("image_url")
                                if image_value:
                                    return await self._materialize_external_image_value(
                                        image_value,
                                        session_key=session_key,
                                        success_note="ok",
                                    )
                        choices = output.get("choices")
                        if isinstance(choices, list):
                            for item in choices:
                                if not isinstance(item, dict):
                                    continue
                                message = item.get("message") if isinstance(item.get("message"), dict) else {}
                                content = message.get("content")
                                if isinstance(content, dict):
                                    content = [content]
                                if not isinstance(content, list):
                                    continue
                                for part in content:
                                    if not isinstance(part, dict):
                                        continue
                                    image_value = part.get("image") or part.get("image_url") or part.get("url")
                                    if image_value:
                                        return await self._materialize_external_image_value(
                                            image_value,
                                            session_key=session_key,
                                            success_note="ok",
                                        )
                        return "", "百炼任务成功但未返回图片地址"
                    if task_status in {"FAILED", "FAIL", "CANCELED", "CANCELLED"}:
                        return "", _single_line(
                            output.get("message") or data.get("message") or "百炼异步生图任务失败",
                            180,
                        )
                    await asyncio.sleep(2)
        except asyncio.TimeoutError:
            note = self._external_image_timeout_note(label="百炼异步生图接口")
            logger.info(
                "[PrivateCompanion] 百炼异步生图超时: model=%s timeout=%ss",
                _single_line(self.external_image_api_model, 80),
                _safe_int(getattr(self, "external_image_api_timeout_seconds", 180), 180, 1),
            )
            return "", note
        except Exception as e:
            safe_error = self._external_image_diagnostic_text(e, 220)
            logger.warning("[PrivateCompanion] 百炼异步生图失败: %s", safe_error)
            return "", safe_error

    async def _run_modelscope_photo_generation(
        self,
        prompt_text: str,
        *,
        session_key: str,
        image_size: str = "",
    ) -> tuple[str, str]:
        endpoint = self._modelscope_generation_endpoint()
        if not endpoint:
            return "", "未配置魔搭社区 API 地址"
        try:
            import aiohttp

            headers = {
                "Authorization": f"Bearer {self.external_image_api_key}",
                "Content-Type": "application/json",
                "X-ModelScope-Async-Mode": "true",
                "X-ModelScope-Task-Type": "text-to-image-generation",
            }
            headers.update(self._external_image_custom_headers())
            payload = {
                "model": self.external_image_api_model,
                "prompt": prompt_text,
                "size": self._sanitize_external_image_size(image_size),
            }
            timeout = aiohttp.ClientTimeout(total=float(self.external_image_api_timeout_seconds))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                logger.info(
                    "[PrivateCompanion] 魔搭异步生图提交: endpoint=%s model=%s size=%s prompt_preview=%s",
                    self._external_image_diagnostic_text(endpoint, 160),
                    _single_line(self.external_image_api_model, 80),
                    _single_line(payload["size"], 40),
                    _single_line(prompt_text, 180),
                )
                async with session.post(endpoint, headers=headers, json=payload) as response:
                    text = await response.text()
                    logger.info(
                        "[PrivateCompanion] 魔搭生图提交响应: status=%s chars=%s preview=%s",
                        response.status,
                        len(text or ""),
                        self._external_image_diagnostic_text(text, 220),
                    )
                    if response.status >= 400:
                        return "", f"魔搭生图 HTTP {response.status}: {self._external_image_diagnostic_text(text, 180)}"
                data = self._extract_json_payload(text) if text else {}
                if not isinstance(data, dict):
                    return "", "魔搭生图返回格式无效"
                for image_value in self._external_payload_image_values(data):
                    return await self._materialize_external_image_value(
                        image_value,
                        session_key=session_key,
                        success_note="ok",
                    )
                task_id = str(
                    self._external_payload_first_value(data, ("task_id", "taskId", "id", "task"))
                    or ""
                ).strip()
                if not task_id:
                    message = self._external_payload_first_value(data, ("message", "msg", "error", "error_message"))
                    return "", _single_line(message or "魔搭生图未返回任务 ID 或图片结果", 180)

                task_endpoint = self._modelscope_task_endpoint(task_id)
                if not task_endpoint:
                    return "", "无法生成魔搭任务查询地址"
                deadline = time.monotonic() + float(self.external_image_api_timeout_seconds)
                while True:
                    if time.monotonic() >= deadline:
                        return "", self._external_image_timeout_note(label="魔搭异步生图任务轮询")
                    async with session.get(task_endpoint, headers=headers) as response:
                        poll_text = await response.text()
                        logger.info(
                            "[PrivateCompanion] 魔搭任务查询响应: status=%s task=%s chars=%s preview=%s",
                            response.status,
                            _single_line(task_id, 80),
                            len(poll_text or ""),
                            self._external_image_diagnostic_text(poll_text, 220),
                        )
                        if response.status >= 400:
                            return "", f"魔搭任务查询 HTTP {response.status}: {self._external_image_diagnostic_text(poll_text, 180)}"
                    poll_data = self._extract_json_payload(poll_text) if poll_text else {}
                    if not isinstance(poll_data, dict):
                        await asyncio.sleep(2)
                        continue
                    for image_value in self._external_payload_image_values(poll_data):
                        return await self._materialize_external_image_value(
                            image_value,
                            session_key=session_key,
                            success_note="ok",
                        )
                    status = str(
                        self._external_payload_first_value(
                            poll_data,
                            ("task_status", "taskStatus", "status", "state"),
                        )
                        or ""
                    ).strip().upper()
                    if status in {"SUCCEED", "SUCCEEDED", "SUCCESS", "COMPLETED", "FINISHED", "DONE"}:
                        return "", "魔搭任务成功但未返回图片地址"
                    if status in {"FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED"}:
                        message = self._external_payload_first_value(
                            poll_data,
                            ("message", "msg", "error", "error_message", "task_message"),
                        )
                        return "", _single_line(message or "魔搭异步生图任务失败", 180)
                    await asyncio.sleep(2)
        except asyncio.TimeoutError:
            note = self._external_image_timeout_note(label="魔搭异步生图接口")
            logger.info(
                "[PrivateCompanion] 魔搭异步生图超时: model=%s timeout=%ss",
                _single_line(self.external_image_api_model, 80),
                _safe_int(getattr(self, "external_image_api_timeout_seconds", 180), 180, 1),
            )
            return "", note
        except Exception as e:
            safe_error = self._external_image_diagnostic_text(e, 220)
            logger.warning("[PrivateCompanion] 魔搭异步生图失败: %s", safe_error)
            return "", safe_error

    async def _run_doubao_photo_generation(
        self,
        prompt_text: str,
        *,
        session_key: str,
        image_size: str = "",
    ) -> tuple[str, str]:
        endpoint = self._doubao_generation_endpoint()
        if not endpoint:
            return "", "未配置豆包/火山方舟 API 地址"
        try:
            import aiohttp

            payload = {
                "model": self.external_image_api_model,
                "prompt": prompt_text,
                "size": self._sanitize_external_image_size(image_size),
            }
            headers = {
                "Authorization": f"Bearer {self.external_image_api_key}",
                "Content-Type": "application/json",
            }
            headers.update(self._external_image_custom_headers())
            timeout = aiohttp.ClientTimeout(total=float(self.external_image_api_timeout_seconds))
            logger.info(
                "[PrivateCompanion] 豆包/火山方舟生图提交: endpoint=%s model=%s size=%s prompt_preview=%s",
                self._external_image_diagnostic_text(endpoint, 160),
                _single_line(self.external_image_api_model, 80),
                _single_line(payload["size"], 40),
                _single_line(prompt_text, 180),
            )
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(endpoint, headers=headers, json=payload) as response:
                    text = await response.text()
                    logger.info(
                        "[PrivateCompanion] 豆包/火山方舟生图响应: status=%s chars=%s preview=%s",
                        response.status,
                        len(text or ""),
                        self._external_image_diagnostic_text(text, 220),
                    )
                    if response.status >= 400:
                        return "", f"豆包/火山方舟生图 HTTP {response.status}: {self._external_image_diagnostic_text(text, 180)}"
            data = self._extract_json_payload(text) if text else {}
            if not isinstance(data, dict):
                return "", "豆包/火山方舟生图返回格式无效"
            for image_value in self._external_payload_image_values(data):
                return await self._materialize_external_image_value(
                    image_value,
                    session_key=session_key,
                    success_note="ok",
                )
            return "", "豆包/火山方舟生图未返回图片数据"
        except asyncio.TimeoutError:
            note = self._external_image_timeout_note(label="豆包/火山方舟生图接口")
            logger.info(
                "[PrivateCompanion] 豆包/火山方舟生图超时: model=%s timeout=%ss",
                _single_line(self.external_image_api_model, 80),
                _safe_int(getattr(self, "external_image_api_timeout_seconds", 180), 180, 1),
            )
            return "", note
        except Exception as e:
            safe_error = self._external_image_diagnostic_text(e, 220)
            logger.warning("[PrivateCompanion] 豆包/火山方舟生图失败: %s", safe_error)
            return "", safe_error

    async def _run_gemini_photo_generation(
        self,
        prompt_text: str,
        *,
        session_key: str,
        reference_image_path: str = "",
        image_size: str = "",
    ) -> tuple[str, str]:
        endpoint = self._gemini_generate_content_endpoint()
        if not endpoint:
            return "", "未配置 Gemini API 地址"
        try:
            import aiohttp

            prompt_parts: list[dict[str, Any]] = [{"text": prompt_text}]
            used_reference = False
            reference_value = ""
            if reference_image_path and os.path.exists(reference_image_path):
                reference_value = await self._reference_image_to_data_url(reference_image_path)
            elif re.match(r"^data:image/[a-z0-9.+-]+;base64,", str(reference_image_path or ""), flags=re.I):
                reference_value = str(reference_image_path or "").strip()
            if reference_image_path and not reference_value:
                return "", "Gemini 参考图无法读取或转换，已停止纯文生图回退"
            if reference_value and "," in reference_value:
                header, encoded = reference_value.split(",", 1)
                mime_match = re.match(r"data:(image/[a-z0-9.+-]+);base64", header, flags=re.I)
                prompt_parts.append(
                    {
                        "inlineData": {
                            "mimeType": mime_match.group(1) if mime_match else "image/png",
                            "data": encoded,
                        }
                    }
                )
                used_reference = True
            size_hint = self._sanitize_external_image_size(image_size)
            payload = {
                "contents": [{"role": "user", "parts": prompt_parts}],
                "generationConfig": {
                    "responseModalities": ["TEXT", "IMAGE"],
                },
            }
            if size_hint:
                payload["contents"][0]["parts"][0]["text"] = f"{prompt_text}\n\nTarget image size: {size_hint}."
            headers = {"Content-Type": "application/json"}
            headers.update(self._external_image_custom_headers())
            query = {}
            if not any(key.lower() in {"x-goog-api-key"} for key in headers):
                query["key"] = self.external_image_api_key
            else:
                query = {}
            if self.external_image_api_key and "key" not in query:
                headers.setdefault("x-goog-api-key", self.external_image_api_key)
            timeout = aiohttp.ClientTimeout(total=float(self.external_image_api_timeout_seconds))
            use_environment_proxy = bool(
                getattr(self, "external_image_download_use_environment_proxy", False)
            )
            proxy_url = self._external_image_download_proxy_url()
            proxy_mode = "explicit" if proxy_url else ("environment" if use_environment_proxy else "direct")
            logger.info(
                "[PrivateCompanion] Gemini 生图提交: endpoint=%s model=%s size=%s reference=%s proxy=%s prompt_preview=%s",
                self._external_image_diagnostic_text(endpoint, 160),
                _single_line(self.external_image_api_model, 80),
                _single_line(size_hint, 40),
                used_reference,
                proxy_mode,
                _single_line(prompt_text, 180),
            )
            request_options: dict[str, Any] = {
                "headers": headers,
                "params": query,
                "json": payload,
            }
            if proxy_url:
                request_options["proxy"] = proxy_url
            async with aiohttp.ClientSession(
                timeout=timeout,
                trust_env=use_environment_proxy,
            ) as session:
                async with session.post(endpoint, **request_options) as response:
                    text = await response.text()
                    logger.info(
                        "[PrivateCompanion] Gemini 生图响应: status=%s chars=%s preview=%s",
                        response.status,
                        len(text or ""),
                        self._external_image_diagnostic_text(text, 220),
                    )
                    if response.status >= 400:
                        return "", f"Gemini 生图 HTTP {response.status}: {self._external_image_diagnostic_text(text, 180)}"
            data = self._extract_json_payload(text) if text else {}
            if not isinstance(data, dict):
                return "", "Gemini 生图返回格式无效"
            for encoded, mime_type in self._extract_gemini_inline_images(data):
                if encoded.startswith(("http://", "https://", "data:image")):
                    return await self._materialize_external_image_value(
                        encoded,
                        session_key=session_key,
                        success_note="ok；已使用本地人设参考图" if used_reference else "ok",
                    )
                ext = ".png"
                lowered_mime = str(mime_type or "").lower()
                if "jpeg" in lowered_mime or "jpg" in lowered_mime:
                    ext = ".jpg"
                elif "webp" in lowered_mime:
                    ext = ".webp"
                image_bytes = base64.b64decode(encoded)
                saved = await self._save_external_generated_image(image_bytes, session_key=session_key, ext=ext)
                note = "ok；已使用本地人设参考图" if used_reference else "ok"
                return saved, note if saved else "保存 Gemini 图片失败"
            for image_value in self._external_payload_image_values(data):
                return await self._materialize_external_image_value(
                    image_value,
                    session_key=session_key,
                    success_note="ok；已使用本地人设参考图" if used_reference else "ok",
                )
            message = self._external_payload_first_value(data, ("message", "error", "error_message", "finishReason", "finish_reason"))
            return "", _single_line(message or "Gemini 生图未返回图片内容", 180)
        except asyncio.TimeoutError:
            note = self._external_image_timeout_note(label="Gemini 生图接口")
            logger.info(
                "[PrivateCompanion] Gemini 生图超时: model=%s timeout=%ss",
                _single_line(self.external_image_api_model, 80),
                _safe_int(getattr(self, "external_image_api_timeout_seconds", 180), 180, 1),
            )
            return "", note
        except Exception as e:
            safe_error = self._external_image_diagnostic_text(e, 220)
            logger.warning("[PrivateCompanion] Gemini 生图失败: %s", safe_error)
            return "", safe_error

    async def _run_external_photo_generation(
        self,
        prompt_text: str,
        *,
        session_key: str,
        reference_image_path: str = "",
        reference_image_paths: Any = (),
        image_size: str = "",
    ) -> tuple[str, str]:
        lock = getattr(self, "_external_image_api_runtime_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._external_image_api_runtime_lock = lock
        wait_started = time.monotonic()
        async with lock:
            waited_ms = int((time.monotonic() - wait_started) * 1000)
            if waited_ms > 500:
                logger.info(
                    "[PrivateCompanion] 在线图片 API 等待串行锁: session=%s waited=%sms",
                    _single_line(session_key, 80),
                    waited_ms,
                )
            image_path, note = await self._run_external_photo_generation_serial(
                prompt_text,
                session_key=session_key,
                reference_image_path=reference_image_path,
                reference_image_paths=reference_image_paths,
                image_size=image_size,
            )
            if waited_ms > 500:
                note = f"{note}；在线图片 API 排队等待 {waited_ms}ms"
            return image_path, note

    def _parse_custom_headers(self, raw: str) -> dict[str, str]:
        """Parse custom HTTP headers from multi-line 'Key: Value' text."""
        result: dict[str, str] = {}
        if not raw or not isinstance(raw, str):
            return result
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            idx = line.find(":")
            if idx <= 0:
                continue
            key = line[:idx].strip()
            value = line[idx + 1:].strip()
            if key:
                result[key] = value
        return result

    def _external_image_custom_headers(self) -> dict[str, str]:
        """Return parsed custom headers for the currently active (primary or backup) image API."""
        return self._parse_custom_headers(getattr(self, "external_image_api_custom_headers", ""))

    def _external_image_api_runtime_keys(self) -> tuple[str, ...]:
        return (
            "external_image_api_platform",
            "external_image_api_base_url",
            "external_image_api_key",
            "external_image_api_model",
            "external_image_api_size",
            "external_image_api_ratio",
            "external_image_api_timeout_seconds",
            "external_image_api_custom_headers",
        )

    def _legacy_external_image_api_endpoint(self, *, backup: bool = False) -> dict[str, Any]:
        prefix = "backup_" if backup else ""
        endpoint = {
            "name": "备选在线 API" if backup else "主在线 API",
            "enabled": bool(getattr(self, "enable_backup_external_image_api", False)) if backup else True,
            "platform": getattr(self, f"{prefix}external_image_api_platform", "auto"),
            "base_url": getattr(self, f"{prefix}external_image_api_base_url", ""),
            "api_key": getattr(self, f"{prefix}external_image_api_key", ""),
            "model": getattr(self, f"{prefix}external_image_api_model", ""),
            "size": getattr(self, f"{prefix}external_image_api_size", "1024x1024"),
            "ratio": getattr(self, f"{prefix}external_image_api_ratio", ""),
            "timeout_seconds": getattr(self, f"{prefix}external_image_api_timeout_seconds", 180),
            "custom_headers": getattr(self, f"{prefix}external_image_api_custom_headers", ""),
        }
        normalizer = getattr(self, "_normalize_external_image_api_endpoint", None)
        return normalizer(endpoint, index=1 if backup else 0) if callable(normalizer) else endpoint

    def _external_image_api_endpoint_label(self, endpoint: dict[str, Any], index: int = 0) -> str:
        name = _single_line(endpoint.get("name"), 60) if isinstance(endpoint, dict) else ""
        if name:
            return name
        model = _single_line(endpoint.get("model"), 60) if isinstance(endpoint, dict) else ""
        return f"在线 API #{index + 1}" + (f"({model})" if model else "")

    def _external_image_api_endpoint_signature(self, endpoint: dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(endpoint.get("platform") or "auto").lower(),
            str(endpoint.get("base_url") or "").strip().rstrip("/"),
            str(endpoint.get("model") or "").strip(),
            str(endpoint.get("api_key") or "").strip()[:12],
        )

    def _external_image_api_endpoint_queue(
        self,
        *,
        include_incomplete: bool = False,
        include_disabled: bool = False,
    ) -> list[dict[str, Any]]:
        normalizer = getattr(self, "_normalize_external_image_api_endpoints", None)
        configured = getattr(self, "external_image_api_endpoints", [])
        configured = normalizer(configured) if callable(normalizer) else configured
        raw_endpoints = list(configured or []) if isinstance(configured, list) else []
        if not raw_endpoints:
            raw_endpoints = [
                self._legacy_external_image_api_endpoint(backup=False),
                self._legacy_external_image_api_endpoint(backup=True),
            ]
        endpoints: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for endpoint in raw_endpoints[:12]:
            if not isinstance(endpoint, dict):
                continue
            normalized = normalizer([endpoint]) if callable(normalizer) else [endpoint]
            endpoint = normalized[0] if normalized else dict(endpoint)
            if not include_disabled and not bool(endpoint.get("enabled", True)):
                continue
            if not include_incomplete and self._external_image_api_endpoint_unavailable_note(endpoint):
                continue
            signature = self._external_image_api_endpoint_signature(endpoint)
            if signature in seen:
                continue
            seen.add(signature)
            endpoints.append(endpoint)
        return endpoints

    def _external_image_api_endpoint_unavailable_note(self, endpoint: dict[str, Any]) -> str:
        if not isinstance(endpoint, dict):
            return "invalid"
        if not bool(endpoint.get("enabled", True)):
            return "disabled"
        missing = []
        if not str(endpoint.get("base_url") or "").strip():
            missing.append("base_url")
        if not str(endpoint.get("api_key") or "").strip():
            missing.append("api_key")
        if not str(endpoint.get("model") or "").strip():
            missing.append("model")
        if missing:
            return "missing_" + ",".join(missing)
        try:
            note = self._external_image_model_misconfiguration_note_for_values(
                model_name=endpoint.get("model"),
                configured_platform=endpoint.get("platform", "auto"),
                base_url=endpoint.get("base_url"),
            )
            return _single_line(note, 160) if note else ""
        except Exception:
            return ""

    def _apply_external_image_api_endpoint_runtime(self, endpoint: dict[str, Any]) -> None:
        normalizer = getattr(self, "_normalize_external_image_api_platform", None)
        platform = endpoint.get("platform", "auto")
        setattr(
            self,
            "external_image_api_platform",
            normalizer(platform) if callable(normalizer) else str(platform or "auto").strip().lower(),
        )
        setattr(self, "external_image_api_base_url", str(endpoint.get("base_url") or "").strip())
        setattr(self, "external_image_api_key", str(endpoint.get("api_key") or "").strip())
        setattr(self, "external_image_api_model", str(endpoint.get("model") or "").strip())
        setattr(self, "external_image_api_size", str(endpoint.get("size") or "1024x1024").strip() or "1024x1024")
        setattr(self, "external_image_api_ratio", _single_line(endpoint.get("ratio"), 20))
        setattr(self, "external_image_api_timeout_seconds", _safe_int(endpoint.get("timeout_seconds"), 180, 20, 600))
        setattr(self, "external_image_api_custom_headers", str(endpoint.get("custom_headers") or "").strip())

    async def _run_external_photo_generation_serial(
        self,
        prompt_text: str,
        *,
        session_key: str,
        reference_image_path: str = "",
        reference_image_paths: Any = (),
        image_size: str = "",
    ) -> tuple[str, str]:
        endpoints = self._external_image_api_endpoint_queue(include_incomplete=True)
        if not endpoints:
            return "", "在线图片 API 未配置完整"
        notes: list[str] = []
        for index, endpoint in enumerate(endpoints):
            label = self._external_image_api_endpoint_label(endpoint, index)
            unavailable = self._external_image_api_endpoint_unavailable_note(endpoint)
            if unavailable:
                note = f"{label}不可用：{unavailable}"
                notes.append(note)
                logger.info("[PrivateCompanion] 在线图片 API 端点跳过: endpoint=%s note=%s", label, _single_line(unavailable, 160))
                continue
            logger.info(
                "[PrivateCompanion] 尝试在线图片 API 端点: index=%s/%s name=%s platform=%s model=%s",
                index + 1,
                len(endpoints),
                _single_line(label, 80),
                _single_line(endpoint.get("platform"), 30) or "auto",
                _single_line(endpoint.get("model"), 80) or "-",
            )
            image_path, note = await self._run_external_photo_generation_with_endpoint(
                endpoint,
                prompt_text,
                session_key=session_key,
                reference_image_path=reference_image_path,
                reference_image_paths=reference_image_paths,
                image_size=image_size,
            )
            if image_path:
                return image_path, f"{note}；已使用{label}"
            notes.append(f"{label}失败：{_single_line(note, 220)}")
        return "", "；".join(notes[-6:]) or "在线图片 API 未返回可用图片"

    async def _run_external_photo_generation_with_backup(
        self,
        prompt_text: str,
        *,
        session_key: str,
        reference_image_path: str = "",
        reference_image_paths: Any = (),
        image_size: str = "",
    ) -> tuple[str, str]:
        return await self._run_external_photo_generation_with_endpoint(
            self._legacy_external_image_api_endpoint(backup=True),
            prompt_text,
            session_key=session_key,
            reference_image_path=reference_image_path,
            reference_image_paths=reference_image_paths,
            image_size=image_size,
        )

    async def _run_external_photo_generation_with_endpoint(
        self,
        endpoint: dict[str, Any],
        prompt_text: str,
        *,
        session_key: str,
        reference_image_path: str = "",
        reference_image_paths: Any = (),
        image_size: str = "",
    ) -> tuple[str, str]:
        raw_paths = reference_image_paths
        if isinstance(raw_paths, str):
            raw_paths = (raw_paths,)
        planned_paths: list[str] = []
        for raw_path in (reference_image_path, *(raw_paths or ())):
            clean_path = _path_text(raw_path, 1000)
            if clean_path and clean_path not in planned_paths:
                planned_paths.append(clean_path)
        endpoint_capacity = self._external_image_endpoint_multi_reference_capacity(
            endpoint,
            len(planned_paths) or 1,
        )
        endpoint_paths = planned_paths[:endpoint_capacity]
        endpoint_prompt_text = self._photo_reference_prompt_for_backend_capacity(
            prompt_text,
            planned_count=len(planned_paths),
            submitted_count=len(endpoint_paths),
        )
        if len(endpoint_paths) < len(planned_paths):
            logger.info(
                "[PrivateCompanion] 在线图片 API 端点参考图投影: model=%s planned=%s submitted=%s prompt_hash=%s",
                _single_line(endpoint.get("model"), 80),
                len(planned_paths),
                len(endpoint_paths),
                hashlib.sha256(endpoint_prompt_text.encode("utf-8", "ignore")).hexdigest()[:16],
            )
        old_values = {key: getattr(self, key, None) for key in self._external_image_api_runtime_keys()}
        try:
            self._apply_external_image_api_endpoint_runtime(endpoint)
            image_path, note = await self._run_external_photo_generation_once(
                endpoint_prompt_text,
                session_key=session_key,
                reference_image_path=endpoint_paths[0] if endpoint_paths else "",
                reference_image_paths=tuple(endpoint_paths),
                image_size=image_size,
            )
            if image_path and planned_paths:
                note = (
                    f"{note}；实际提交 {len(endpoint_paths)}/{len(planned_paths)} 张参考图"
                )
            return image_path, note
        finally:
            for key, value in old_values.items():
                setattr(self, key, value)

    async def _run_agnes_photo_generation(
        self,
        prompt_text: str,
        *,
        session_key: str,
        reference_image_path: str = "",
        image_size: str = "",
    ) -> tuple[str, str]:
        endpoint = self._external_image_endpoint("generations")
        if not endpoint:
            return "", "未配置 Agnes Image API 地址"
        model = _single_line(getattr(self, "external_image_api_model", ""), 120)
        if not model:
            return "", "未配置 Agnes Image 模型"
        size, ratio = self._agnes_image_size_and_ratio(image_size, prompt_text)
        extra_body: dict[str, Any] = {"response_format": "url"}
        reference_image_path = _path_text(reference_image_path, 1000)
        if reference_image_path:
            data_url = await self._reference_image_to_data_url(reference_image_path)
            if not data_url:
                return "", "Agnes 参考图无法转换为 Data URI"
            extra_body["image"] = [data_url]
        payload: dict[str, Any] = {
            "model": model,
            "prompt": str(prompt_text or "").strip(),
            "size": size,
            "extra_body": extra_body,
        }
        if ratio:
            payload["ratio"] = ratio
        try:
            import aiohttp

            headers = {
                "Authorization": f"Bearer {self.external_image_api_key}",
                "Content-Type": "application/json",
            }
            headers.update(self._external_image_custom_headers())
            timeout = aiohttp.ClientTimeout(total=float(self.external_image_api_timeout_seconds))
            endpoints = self._external_image_endpoint_candidates("generations") or [endpoint]
            last_error = ""
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for index, candidate in enumerate(endpoints):
                    logger.info(
                        "[PrivateCompanion] Agnes Image 提交: endpoint=%s model=%s size=%s ratio=%s reference=%s prompt_chars=%s prompt_preview=%s",
                        self._external_image_diagnostic_text(candidate, 160),
                        model,
                        size,
                        ratio or "-",
                        bool(reference_image_path),
                        len(str(prompt_text or "")),
                        _single_line(prompt_text, 180),
                    )
                    async with session.post(candidate, headers=headers, json=payload) as response:
                        text = await response.text()
                        response_preview = re.sub(
                            r'("b64_json"\s*:\s*")[^"]+',
                            r'\1[base64 omitted]',
                            text or "",
                            flags=re.I,
                        )
                        logger.info(
                            "[PrivateCompanion] Agnes Image 响应: endpoint=%s status=%s chars=%s preview=%s",
                            self._external_image_diagnostic_text(candidate, 160),
                            response.status,
                            len(text or ""),
                            self._external_image_diagnostic_text(response_preview, 220),
                        )
                        if response.status >= 400:
                            last_error = self._external_image_api_error_note(
                                response.status,
                                text,
                                reference=bool(reference_image_path),
                                endpoint=candidate,
                            )
                            if response.status == 404 and index + 1 < len(endpoints):
                                continue
                            return "", last_error
                    data = self._extract_json_payload(text) if text else {}
                    if not isinstance(data, dict):
                        return "", "Agnes Image 返回格式无效"
                    items = data.get("data")
                    first = items[0] if isinstance(items, list) and items else None
                    if not isinstance(first, dict):
                        return "", "Agnes Image 未返回图片数据"
                    saved, note = await self._materialize_external_image_value(
                        first,
                        session_key=session_key,
                        success_note="ok",
                    )
                    if saved:
                        detail = f"ok；Agnes Image {size}{('/' + ratio) if ratio else ''}"
                        if reference_image_path:
                            detail += "；已使用参考图"
                        return saved, detail
                    return "", note
            return "", last_error or "Agnes Image 未返回数据"
        except asyncio.TimeoutError:
            return "", self._external_image_timeout_note(reference=bool(reference_image_path))
        except Exception as exc:
            safe_error = self._external_image_diagnostic_text(exc, 220)
            logger.warning("[PrivateCompanion] Agnes Image 生图失败: %s", safe_error)
            return "", safe_error

    @staticmethod
    def _normalize_minimax_image_ratio(value: Any) -> str:
        text = str(value or "").strip().lower().replace("：", ":").replace("/", ":")
        aliases = {
            "square": "1:1",
            "方形": "1:1",
            "portrait": "3:4",
            "竖版": "3:4",
            "landscape": "4:3",
            "横版": "4:3",
            "widescreen": "16:9",
            "宽屏": "16:9",
        }
        text = aliases.get(text, text)
        return text if text in {"1:1", "16:9", "4:3", "3:2", "2:3", "3:4", "9:16", "21:9"} else ""

    def _minimax_image_size_payload(self, image_size: str = "") -> dict[str, Any]:
        raw = str(image_size or getattr(self, "external_image_api_size", "") or "1024x1024").strip()
        live_model = str(getattr(self, "external_image_api_model", "")).strip().lower() == "image-01-live"
        configured_ratio = self._normalize_minimax_image_ratio(
            getattr(self, "external_image_api_ratio", "")
        )
        inline_ratio = ""
        if "@" in raw:
            raw, inline_value = raw.rsplit("@", 1)
            inline_ratio = self._normalize_minimax_image_ratio(inline_value)
        ratio = inline_ratio or configured_ratio
        if ratio and not live_model:
            return {"aspect_ratio": ratio}

        dimensions = re.fullmatch(r"\s*(\d{3,4})\s*[xX*×]\s*(\d{3,4})\s*", raw)
        width = int(dimensions.group(1)) if dimensions else 1024
        height = int(dimensions.group(2)) if dimensions else 1024
        if live_model:
            live_ratios = {
                "1:1": 1.0,
                "16:9": 16 / 9,
                "4:3": 4 / 3,
                "3:2": 3 / 2,
                "2:3": 2 / 3,
                "3:4": 3 / 4,
                "9:16": 9 / 16,
            }
            target_ratio = ratio or f"{width}:{height}"
            target_width, target_height = (int(part) for part in target_ratio.split(":"))
            numeric_ratio = target_width / max(1, target_height)
            closest = min(live_ratios, key=lambda item: abs(live_ratios[item] - numeric_ratio))
            return {"aspect_ratio": closest}

        if dimensions:
            if 512 <= width <= 2048 and 512 <= height <= 2048 and width % 8 == 0 and height % 8 == 0:
                return {"width": width, "height": height}
        return {"width": 1024, "height": 1024}

    def _minimax_api_error_note(self, payload: dict[str, Any]) -> str:
        base_resp = payload.get("base_resp")
        if not isinstance(base_resp, dict):
            return ""
        raw_code = base_resp.get("status_code", 0)
        try:
            code = int(raw_code or 0)
        except (TypeError, ValueError):
            code = -1
        if code == 0:
            return ""
        labels = {
            1002: "请求触发限流，请稍后重试",
            1004: "账号鉴权失败，请检查 API Key",
            1008: "账号余额不足",
            1026: "图片描述触发内容安全检查",
            2013: "请求参数不符合 MiniMax 图片接口要求",
            2049: "API Key 无效",
        }
        message = self._external_image_diagnostic_text(base_resp.get("status_msg"), 160)
        detail = labels.get(code, "MiniMax 图片接口返回业务错误")
        return f"{detail}（status_code={code}）" + (f"：{message}" if message else "")

    async def _run_minimax_photo_generation(
        self,
        prompt_text: str,
        *,
        session_key: str,
        reference_image_path: str = "",
        image_size: str = "",
    ) -> tuple[str, str]:
        endpoint = self._external_image_endpoint("generations")
        if not endpoint:
            return "", "未配置 MiniMax API 地址"
        prompt = str(prompt_text or "").strip()
        if not prompt:
            return "", "MiniMax 生图提示词为空"
        if len(prompt) > 1500:
            logger.info(
                "[PrivateCompanion] MiniMax 生图提示词超过官方 1500 字符限制，按上限提交: original_chars=%s",
                len(prompt),
            )
            prompt = prompt[:1500]

        payload: dict[str, Any] = {
            "model": self.external_image_api_model,
            "prompt": prompt,
            "response_format": "base64",
            "n": 1,
        }
        payload.update(self._minimax_image_size_payload(image_size))
        if reference_image_path:
            path = Path(_path_text(reference_image_path, 1000))
            if not path.exists() or not path.is_file():
                return "", "MiniMax 提交前参考图已不可用"
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                return "", "MiniMax 参考图格式不支持，请使用 PNG 或 JPEG"
            try:
                if path.stat().st_size >= _MINIMAX_REFERENCE_IMAGE_MAX_BYTES:
                    return "", "MiniMax 参考图需小于 10 MB"
            except OSError:
                return "", "MiniMax 参考图读取失败"
            image_value = await self._reference_image_to_data_url(str(path))
            if not image_value:
                return "", "MiniMax 参考图读取失败"
            payload["subject_reference"] = [
                {
                    "type": "character",
                    "image_file": image_value,
                }
            ]

        try:
            import aiohttp

            headers = {
                "Authorization": f"Bearer {self.external_image_api_key}",
                "Content-Type": "application/json",
            }
            headers.update(self._external_image_custom_headers())
            timeout = aiohttp.ClientTimeout(total=float(self.external_image_api_timeout_seconds))
            last_note = ""
            retried = False
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for attempt in range(2):
                    logger.info(
                        "[PrivateCompanion] MiniMax 图片提交: endpoint=%s model=%s reference=%s dimensions=%s attempt=%s/2 prompt_chars=%s prompt_preview=%s",
                        self._external_image_diagnostic_text(endpoint, 160),
                        _single_line(self.external_image_api_model, 80),
                        bool(reference_image_path),
                        _single_line(
                            payload.get("aspect_ratio")
                            or f"{payload.get('width', '-')}x{payload.get('height', '-')}",
                            30,
                        ),
                        attempt + 1,
                        len(prompt),
                        _single_line(prompt, 180),
                    )
                    async with session.post(endpoint, headers=headers, json=payload) as response:
                        text = await response.text()
                        response_preview = re.sub(
                            r'("image_base64"\s*:\s*\[\s*")[^"]+',
                            r'\1[base64 omitted]',
                            text or "",
                            flags=re.I,
                        )
                        logger.info(
                            "[PrivateCompanion] MiniMax 图片响应: endpoint=%s status=%s chars=%s preview=%s",
                            self._external_image_diagnostic_text(endpoint, 160),
                            response.status,
                            len(text or ""),
                            self._external_image_diagnostic_text(response_preview, 220),
                        )
                        if response.status >= 400:
                            last_note = self._external_image_api_error_note(
                                response.status,
                                text,
                                reference=bool(reference_image_path),
                                endpoint=endpoint,
                            )
                            retry_delay = self._external_image_api_transient_retry_delay(response.status)
                            if attempt == 0 and retry_delay > 0:
                                retried = True
                                await asyncio.sleep(retry_delay)
                                continue
                            return "", last_note
                    data = self._extract_json_payload(text) if text else {}
                    if not isinstance(data, dict):
                        return "", "MiniMax 图片接口返回格式无效"
                    last_note = self._minimax_api_error_note(data)
                    if last_note:
                        base_resp = data.get("base_resp")
                        raw_code = base_resp.get("status_code") if isinstance(base_resp, dict) else 0
                        if attempt == 0 and str(raw_code) == "1002":
                            retried = True
                            await asyncio.sleep(1.0)
                            continue
                        return "", last_note

                    result_data = data.get("data")
                    if not isinstance(result_data, dict):
                        return "", "MiniMax 图片接口未返回 data 图片数据"
                    candidates: list[Any] = []
                    for key in ("image_base64", "image_urls"):
                        values = result_data.get(key)
                        if isinstance(values, list):
                            for value in values:
                                if not value:
                                    continue
                                if key == "image_base64" and isinstance(value, str) and not value.lower().startswith("data:image"):
                                    value = f"data:image/png;base64,{value}"
                                candidates.append(value)
                        elif values:
                            if key == "image_base64" and isinstance(values, str) and not values.lower().startswith("data:image"):
                                values = f"data:image/png;base64,{values}"
                            candidates.append(values)
                    if not candidates:
                        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
                        failed = _safe_int(metadata.get("failed_count"), 0, 0)
                        if failed:
                            return "", f"MiniMax 未返回图片，内容安全检查拦截 {failed} 张"
                        return "", "MiniMax 图片接口未返回 image_base64 或 image_urls"

                    success_note = "ok"
                    if reference_image_path:
                        success_note += "；已使用本地人设参考图"
                    for candidate in candidates:
                        saved, note = await self._materialize_external_image_value(
                            candidate,
                            session_key=session_key,
                            success_note=success_note,
                        )
                        if saved:
                            if retried:
                                note += "；上游短暂错误后重试成功"
                            return saved, note
                        last_note = note
                    return "", last_note or "MiniMax 图片结果解析失败"
            return "", last_note or "MiniMax 图片接口未返回数据"
        except asyncio.TimeoutError:
            return "", self._external_image_timeout_note(reference=bool(reference_image_path))
        except Exception as exc:
            safe_error = self._external_image_diagnostic_text(exc, 220)
            logger.warning("[PrivateCompanion] MiniMax 图片生成失败: %s", safe_error)
            return "", safe_error

    async def _run_external_photo_generation_once(
        self,
        prompt_text: str,
        *,
        session_key: str,
        reference_image_path: str = "",
        reference_image_paths: Any = (),
        image_size: str = "",
    ) -> tuple[str, str]:
        platform = self._resolved_external_image_api_platform()
        endpoint = self._external_image_endpoint()
        if platform == "bailian":
            endpoint = self._bailian_multimodal_endpoint() or self._bailian_api_root()
        elif platform == "modelscope":
            endpoint = self._modelscope_generation_endpoint()
        elif platform == "doubao":
            endpoint = self._doubao_generation_endpoint()
        elif platform == "gemini":
            endpoint = self._gemini_generate_content_endpoint()
        if not endpoint:
            return "", f"未配置{self._external_image_platform_label(platform)} API 地址"
        if not self.external_image_api_key:
            return "", f"未配置{self._external_image_platform_label(platform)} API Key"
        if not self.external_image_api_model:
            return "", f"未配置{self._external_image_platform_label(platform)}图片模型"
        model_note = self._external_image_model_misconfiguration_note()
        if model_note:
            return "", model_note
        normalized_reference_paths: list[str] = []
        raw_reference_paths = reference_image_paths
        if isinstance(raw_reference_paths, str):
            raw_reference_paths = (raw_reference_paths,)
        for raw_path in (reference_image_path, *(raw_reference_paths or ())):
            path = _path_text(raw_path, 1000)
            if path and path not in normalized_reference_paths:
                normalized_reference_paths.append(path)
        reference_image_paths = tuple(normalized_reference_paths)
        reference_image_path = normalized_reference_paths[0] if normalized_reference_paths else ""
        if platform == "bailian":
            multimodal_first = bool(reference_image_path) or self._bailian_prefers_multimodal()
            bailian_note = ""
            if multimodal_first:
                image_path, note = await self._run_bailian_multimodal_photo_generation(
                    prompt_text,
                    session_key=session_key,
                    reference_image_path=reference_image_path,
                    image_size=image_size,
                )
                if image_path:
                    return image_path, note
                bailian_note = note
                if reference_image_path:
                    logger.info(
                        "[PrivateCompanion] 百炼参考图多模态生图失败,停止异步纯文回退: %s",
                        _single_line(note, 180),
                    )
                    return "", f"百炼参考图多模态接口失败：{_single_line(note, 180)}"
                logger.info(
                    "[PrivateCompanion] 百炼多模态生图失败,回退异步文生图: %s",
                    _single_line(note, 180),
                )
                if "超时" in str(note or ""):
                    return "", note
            image_path, note = await self._run_bailian_async_photo_generation(
                prompt_text,
                session_key=session_key,
                image_size=image_size,
            )
            if image_path:
                return image_path, note
            if bailian_note:
                return "", f"{note}；多模态回退原因：{_single_line(bailian_note, 120)}"
            return "", note
        if platform == "modelscope":
            if reference_image_path:
                return "", "魔搭社区文生图接口暂不支持参考图输入，请改用 OpenAI 兼容参考图接口或 ComfyUI images=1 工作流"
            return await self._run_modelscope_photo_generation(
                prompt_text,
                session_key=session_key,
                image_size=image_size,
            )
        if platform == "doubao":
            if reference_image_path:
                return "", "豆包/火山方舟文生图接口暂不支持参考图输入，请改用 Gemini、OpenAI 兼容参考图接口或 ComfyUI images=1 工作流"
            return await self._run_doubao_photo_generation(
                prompt_text,
                session_key=session_key,
                image_size=image_size,
            )
        if platform == "gemini":
            return await self._run_gemini_photo_generation(
                prompt_text,
                session_key=session_key,
                reference_image_path=reference_image_path,
                image_size=image_size,
            )
        if platform == "minimax":
            minimax_reference_path = reference_image_path
            if not minimax_reference_path:
                raw_minimax_paths = reference_image_paths
                if isinstance(raw_minimax_paths, str):
                    raw_minimax_paths = (raw_minimax_paths,)
                for raw_path in raw_minimax_paths or ():
                    minimax_reference_path = _path_text(raw_path, 1000)
                    if minimax_reference_path:
                        break
            return await self._run_minimax_photo_generation(
                prompt_text,
                session_key=session_key,
                reference_image_path=minimax_reference_path,
                image_size=image_size,
            )
        if platform == "agnes":
            return await self._run_agnes_photo_generation(
                prompt_text,
                session_key=session_key,
                reference_image_path=reference_image_path,
                image_size=image_size,
            )
        if platform == "sensenova" and reference_image_path:
            return "", "SenseNova U1 Fast 官方接口不支持参考图输入，请使用纯文生图或切换其他参考图后端"
        if reference_image_path and not os.path.isfile(reference_image_path):
            return "", "在线图片 API 提交前参考图已不可用，已停止纯文生图回退"
        if reference_image_path:
            logger.info(
                "[PrivateCompanion] 在线图片 API 尝试参考图接口: endpoint=%s model=%s size=%s reference=%s prompt_preview=%s",
                self._external_image_diagnostic_text(self._external_image_endpoint("edits"), 160),
                _single_line(self.external_image_api_model, 80),
                self._sanitize_external_image_size(image_size),
                _single_line(reference_image_path, 160),
                _single_line(prompt_text, 180),
            )
            image_path, note = await self._run_external_photo_edit_generation(
                prompt_text,
                session_key=session_key,
                reference_image_path=reference_image_path,
                reference_image_paths=reference_image_paths,
                image_size=image_size,
            )
            if image_path:
                return image_path, note
            logger.info(
                "[PrivateCompanion] 在线图片 API 参考图生图失败,停止纯文回退: %s",
                self._external_image_diagnostic_text(note, 180),
            )
            return "", f"参考图接口失败：{self._external_image_diagnostic_text(note, 180)}"
        try:
            import aiohttp

            payload = {
                "model": self._sensenova_image_model() if platform == "sensenova" else self.external_image_api_model,
                "prompt": prompt_text,
                "size": self._sanitize_sensenova_image_size(image_size) if platform == "sensenova" else self._sanitize_external_image_size(image_size),
            }
            if platform == "sensenova":
                payload["n"] = 1
            headers = {
                "Authorization": f"Bearer {self.external_image_api_key}",
                "Content-Type": "application/json",
            }
            headers.update(self._external_image_custom_headers())
            timeout = aiohttp.ClientTimeout(total=float(self.external_image_api_timeout_seconds))
            endpoints = self._external_image_endpoint_candidates("generations") or [endpoint]
            last_error_note = ""
            data: dict[str, Any] | None = None
            retried_after_upstream_error = False
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for index, candidate_endpoint in enumerate(endpoints):
                    text = ""
                    for attempt in range(2):
                        logger.info(
                            "[PrivateCompanion] 在线图片 API 生图提交: endpoint=%s model=%s size=%s prompt_chars=%s attempt=%s/2 prompt_preview_chars=%s prompt_preview=%s",
                            self._external_image_diagnostic_text(candidate_endpoint, 160),
                            _single_line(self.external_image_api_model, 80),
                            payload["size"],
                            len(str(prompt_text or "")),
                            attempt + 1,
                            180,
                            _single_line(prompt_text, 180),
                        )
                        async with session.post(candidate_endpoint, headers=headers, json=payload) as response:
                            text = await response.text()
                            logger.info(
                                "[PrivateCompanion] 在线图片 API 生图响应: endpoint=%s status=%s chars=%s preview=%s",
                                self._external_image_diagnostic_text(candidate_endpoint, 160),
                                response.status,
                                len(text or ""),
                                self._external_image_diagnostic_text(text, 220),
                            )
                            if response.status >= 400:
                                last_error_note = self._external_image_api_error_note(response.status, text, endpoint=candidate_endpoint)
                                retry_delay = self._external_image_api_transient_retry_delay(response.status)
                                if attempt == 0 and retry_delay > 0:
                                    retried_after_upstream_error = True
                                    logger.info(
                                        "[PrivateCompanion] 在线图片 API 上游短暂失败,稍后重试同一端点: status=%s delay=%.1fs endpoint=%s",
                                        response.status,
                                        retry_delay,
                                        self._external_image_diagnostic_text(candidate_endpoint, 160),
                                    )
                                    await asyncio.sleep(retry_delay)
                                    continue
                                if response.status == 404 and index + 1 < len(endpoints):
                                    logger.info(
                                        "[PrivateCompanion] 在线图片 API 生图端点 404,尝试候选端点: failed=%s next=%s",
                                        self._external_image_diagnostic_text(candidate_endpoint, 160),
                                        self._external_image_diagnostic_text(endpoints[index + 1], 160),
                                    )
                                    break
                                return "", last_error_note
                        data = self._extract_json_payload(text) if text else {}
                        break
                    if data is None and last_error_note and index + 1 < len(endpoints):
                        continue
                    break
            if data is None:
                return "", last_error_note or "在线图片 API 未返回数据"
            if not isinstance(data, dict):
                return "", "在线图片 API 返回格式无效"
            first = None
            items = data.get("data")
            if isinstance(items, list) and items:
                first = items[0]
            if not isinstance(first, dict):
                return "", "在线图片 API 未返回图片数据"
            image_value = str(first.get("b64_json") or first.get("url") or "").strip()
            if image_value:
                logger.info(
                    "[PrivateCompanion] 在线图片 API 生图返回: %s",
                    self._external_image_diagnostic_text(image_value, 180),
                )
                saved, note = await self._materialize_external_openai_image_item(
                    first,
                    session_key=session_key,
                    success_note="ok",
                )
                if saved and retried_after_upstream_error:
                    note = f"{note}；上游短暂错误后重试成功"
                return saved, note
            return "", "在线图片 API 未返回 url 或 b64_json"
        except asyncio.TimeoutError:
            note = self._external_image_timeout_note()
            logger.info(
                "[PrivateCompanion] 在线图片 API 生图超时: endpoint=%s model=%s timeout=%ss",
                self._external_image_diagnostic_text(endpoint, 160),
                _single_line(self.external_image_api_model, 80),
                _safe_int(getattr(self, "external_image_api_timeout_seconds", 180), 180, 1),
            )
            return "", note
        except Exception as e:
            safe_error = self._external_image_diagnostic_text(e, 220)
            logger.warning("[PrivateCompanion] 在线图片 API 生图失败: %s", safe_error)
            return "", safe_error

    async def _run_external_photo_edit_generation(
        self,
        prompt_text: str,
        *,
        session_key: str,
        reference_image_path: str,
        reference_image_paths: Any = (),
        image_size: str = "",
    ) -> tuple[str, str]:
        endpoint = self._external_image_endpoint("edits")
        if not endpoint:
            return "", "未配置在线图片 API 地址"
        normalized_paths: list[str] = []
        raw_paths = reference_image_paths
        if isinstance(raw_paths, str):
            raw_paths = (raw_paths,)
        for raw_path in (reference_image_path, *(raw_paths or ())):
            clean_path = _path_text(raw_path, 1000)
            if clean_path and clean_path not in normalized_paths:
                normalized_paths.append(clean_path)
        if not normalized_paths:
            return "", "参考图路径不可用"
        planned_reference_count = len(normalized_paths)
        if planned_reference_count > 1:
            supported_capacity = self._external_image_multi_reference_capacity(
                planned_reference_count,
                inspect_queue=False,
            )
            if supported_capacity <= 1:
                normalized_paths = normalized_paths[:1]
            else:
                normalized_paths = normalized_paths[:supported_capacity]
        submitted_prompt_text = self._photo_reference_prompt_for_backend_capacity(
            prompt_text,
            planned_count=planned_reference_count,
            submitted_count=len(normalized_paths),
        )
        if len(normalized_paths) < planned_reference_count:
            logger.info(
                "[PrivateCompanion] 在线图片 API 按当前端点容量降级参考图: model=%s planned=%s submitted=%s",
                _single_line(self.external_image_api_model, 80),
                planned_reference_count,
                len(normalized_paths),
            )
        paths = [Path(value) for value in normalized_paths]
        for path in paths:
            if not path.exists() or not path.is_file():
                return "", "参考图路径不可用"
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                return "", "参考图格式不支持"
        try:
            import aiohttp

            image_payloads: list[tuple[Path, bytes, str]] = []
            for path in paths:
                suffix = path.suffix.lower()
                content_type = "image/png"
                if suffix in {".jpg", ".jpeg"}:
                    content_type = "image/jpeg"
                elif suffix == ".webp":
                    content_type = "image/webp"
                image_payloads.append(
                    (path, await asyncio.to_thread(path.read_bytes), content_type)
                )

            def build_form() -> Any:
                form = aiohttp.FormData()
                form.add_field("model", self.external_image_api_model)
                form.add_field("prompt", submitted_prompt_text)
                form.add_field("size", self._sanitize_external_image_size(image_size))
                multi = len(image_payloads) > 1
                for index, (path, image_bytes, content_type) in enumerate(image_payloads, start=1):
                    form.add_field(
                        "image[]" if multi else "image",
                        image_bytes,
                        filename=f"reference_{index}{path.suffix.lower()}",
                        content_type=content_type,
                    )
                return form

            headers = {"Authorization": f"Bearer {self.external_image_api_key}"}
            headers.update(self._external_image_custom_headers())
            timeout = aiohttp.ClientTimeout(total=float(self.external_image_api_timeout_seconds))
            endpoints = self._external_image_endpoint_candidates("edits") or [endpoint]
            last_error_note = ""
            data: dict[str, Any] | None = None
            retried_after_upstream_error = False
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for index, candidate_endpoint in enumerate(endpoints):
                    text = ""
                    for attempt in range(2):
                        logger.info(
                            "[PrivateCompanion] 在线图片 API 参考图提交: endpoint=%s model=%s size=%s reference_count=%s bytes=%s attempt=%s/2 prompt_preview=%s",
                            self._external_image_diagnostic_text(candidate_endpoint, 160),
                            _single_line(self.external_image_api_model, 80),
                            self._sanitize_external_image_size(image_size),
                            len(image_payloads),
                            sum(len(item[1]) for item in image_payloads),
                            attempt + 1,
                            _single_line(submitted_prompt_text, 180),
                        )
                        async with session.post(candidate_endpoint, headers=headers, data=build_form()) as response:
                            text = await response.text()
                            logger.info(
                                "[PrivateCompanion] 在线图片 API 参考图响应: endpoint=%s status=%s chars=%s preview=%s",
                                self._external_image_diagnostic_text(candidate_endpoint, 160),
                                response.status,
                                len(text or ""),
                                self._external_image_diagnostic_text(text, 220),
                            )
                            if response.status >= 400:
                                last_error_note = self._external_image_api_error_note(response.status, text, reference=True, endpoint=candidate_endpoint)
                                retry_delay = self._external_image_api_transient_retry_delay(response.status)
                                if attempt == 0 and retry_delay > 0:
                                    retried_after_upstream_error = True
                                    logger.info(
                                        "[PrivateCompanion] 在线图片 API 参考图上游短暂失败,稍后重试同一端点: status=%s delay=%.1fs endpoint=%s",
                                        response.status,
                                        retry_delay,
                                        self._external_image_diagnostic_text(candidate_endpoint, 160),
                                    )
                                    await asyncio.sleep(retry_delay)
                                    continue
                                if response.status == 404 and index + 1 < len(endpoints):
                                    logger.info(
                                        "[PrivateCompanion] 在线图片 API 参考图端点 404,尝试候选端点: failed=%s next=%s",
                                        self._external_image_diagnostic_text(candidate_endpoint, 160),
                                        self._external_image_diagnostic_text(endpoints[index + 1], 160),
                                    )
                                    break
                                return "", last_error_note
                        data = self._extract_json_payload(text) if text else {}
                        break
                    if data is None and last_error_note and index + 1 < len(endpoints):
                        continue
                    break
            if data is None:
                return "", last_error_note or "参考图接口未返回数据"
            if not isinstance(data, dict):
                return "", "参考图接口返回格式无效"
            first = None
            items = data.get("data")
            if isinstance(items, list) and items:
                first = items[0]
            if not isinstance(first, dict):
                return "", "参考图接口未返回图片数据"
            image_value = str(first.get("b64_json") or first.get("url") or "").strip()
            if image_value:
                logger.info(
                    "[PrivateCompanion] 在线图片 API 参考图返回: %s",
                    self._external_image_diagnostic_text(image_value, 180),
                )
                saved, note = await self._materialize_external_openai_image_item(
                    first,
                    session_key=session_key,
                    success_note=(
                        "ok；已使用本地人设参考图"
                        if len(image_payloads) == 1
                        else f"ok；已使用 {len(image_payloads)} 张参考图"
                    ),
                )
                if saved and retried_after_upstream_error:
                    note += "；上游短暂错误后重试成功"
                if saved and len(image_payloads) < planned_reference_count:
                    note += f"；当前端点仅支持 {len(image_payloads)}/{planned_reference_count} 张参考图"
                return saved, note
            return "", "参考图接口未返回 url 或 b64_json"
        except asyncio.TimeoutError:
            note = self._external_image_timeout_note(reference=True)
            logger.info(
                "[PrivateCompanion] 在线图片 API 参考图生图超时: endpoint=%s model=%s timeout=%ss reference=%s",
                self._external_image_diagnostic_text(endpoint, 160),
                _single_line(self.external_image_api_model, 80),
                _safe_int(getattr(self, "external_image_api_timeout_seconds", 180), 180, 1),
                len(image_payloads),
            )
            return "", note
        except Exception as e:
            safe_error = self._external_image_diagnostic_text(e, 220)
            logger.warning("[PrivateCompanion] 在线图片 API 参考图生图失败: %s", safe_error)
            return "", safe_error

    def _find_photo_workflow_with_text_count(
        self,
        module: Any,
        workflow_dir: Any,
        workflow_name: str,
        image_count: int = 0,
    ) -> tuple[str, int, int]:
        if not hasattr(module, "list_workflows_in_dir"):
            return "", 0, 0
        try:
            workflows = module.list_workflows_in_dir(workflow_dir)
        except Exception:
            return "", 0, 0
        candidates = []
        expected_images = max(0, int(image_count or 0))
        for item in workflows or []:
            if not isinstance(item, dict):
                continue
            if item.get("name") != workflow_name:
                continue
            if _safe_int(item.get("images"), 0) != expected_images:
                continue
            if _safe_int(item.get("videos"), 0) != 0:
                continue
            text_count = max(1, _safe_int(item.get("texts"), 1, 1))
            filename = str(item.get("filename") or "").strip()
            if not filename:
                continue
            path = os.path.join(str(workflow_dir), filename)
            if os.path.exists(path):
                candidates.append((text_count, path))
        if not candidates:
            return "", 0, 0
        candidates.sort(key=lambda value: value[0])
        return candidates[0][1], candidates[0][0], expected_images

    def _get_comfyui_module(self) -> Any:
        for module_name in (
            "astrbot_plugin_comfyui.main",
            "data.plugins.astrbot_plugin_comfyui.main",
        ):
            try:
                module = importlib.import_module(module_name)
                if hasattr(module, "ComfyUIWorkflow"):
                    return module
            except Exception:
                continue
        for module in list(sys.modules.values()):
            try:
                if hasattr(module, "ComfyUIWorkflow") and hasattr(module, "_get_result_for_prompt"):
                    return module
            except Exception:
                continue
        return None

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

    async def _trigger_proactive_decorating_hooks(self, umo: str, chain: list[Any]) -> list[Any]:
        if not self.enable_proactive_decorating_hooks or not chain:
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
        sender_name = _single_line(getattr(self, "bot_name", ""), 40) or "PrivateCompanion"
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
        if not getattr(self, "segmented_proactive_send_as_forward", False):
            return False
        target_type = str(target_type or "").strip().lower()
        target_id = _single_line(target_id, 80)
        if target_type not in {"private", "group"} or not target_id:
            return False
        raw_segments = [_redact_outbound_secrets(item, self).strip() for item in segments if str(item or "").strip()]
        if len(raw_segments) <= 1:
            return False
        if getattr(self, "enable_tts_enhancement", False) and any(re.search(r"</?t{2,}s\b", item, flags=re.IGNORECASE) for item in raw_segments):
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
        scope = str(getattr(self, "segmented_proactive_chat_scope", "all") or "all").strip().lower()
        if scope not in {"all", "private", "group"}:
            scope = "all"
        chat_type = str(chat_type or "").strip().lower()
        return scope == "all" or scope == chat_type

    def _segmented_scope_allows_umo(self, umo: str) -> bool:
        session = self._parse_message_session(umo)
        if not session:
            return self._segmented_chat_scope_allows("private")
        chat_type = "group" if self._message_type_for_session(session) == MessageType.GROUP_MESSAGE else "private"
        return self._segmented_chat_scope_allows(chat_type)

    def _segmented_scope_allows_event(self, event: AstrMessageEvent) -> bool:
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return self._segmented_chat_scope_allows("private")
        except Exception:
            pass
        if self._extract_group_id_from_event(event):
            return self._segmented_chat_scope_allows("group")
        return self._segmented_chat_scope_allows("private")

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
        if self.enable_precise_platform_send and session and platform:
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
                    await self._calc_segmented_proactive_interval(primary_texts[-1])
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
            getattr(self, "reaction_expression_delivery_mode", "separate_after")
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
                await asyncio.sleep(await self._calc_segmented_proactive_interval(segment))
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
        parts = [
            str(item.get("time", "")).strip(),
            str(item.get("activity", "")).strip(),
            f"情绪：{item.get('mood', '')}".strip(),
        ]
        seed = _single_line(item.get("message_seed"), 120)
        if seed:
            parts.append(f"可分享碎片：{seed}")
        return "｜".join(part for part in parts if part)

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
        cleaned = self._strip_internal_identity_anchors(cleaned)
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
        return "\n".join(lines[:3])[:260]

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
            return "\n".join(normalized)[:260]
        head = normalized[:2]
        tail = "".join(normalized[2:])
        return "\n".join(head + [tail])[:260]

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
        asks_user = bool(re.search(r"(你那边|你呢|你那儿|你那里|你现在|你今天|你还|你有没有|你要不要|你是不是)", tail))
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
        share_probability = self.proactive_share_probability
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
        if current_item and self.include_schedule_in_messages and random.random() < 0.22:
            reasons.append("background_schedule")
        if not reasons:
            reasons.append("check_in")
        elif _safe_int(user.get("ignored_streak"), 0, 0) <= 0 and random.random() < 0.12:
            reasons.append("check_in")
        reason = planned_reason if planned_reason and self._is_reason_allowed_now(planned_reason) else random.choice(reasons)

        if reason == "insomnia_night":
            return reason, "夜间清醒时的一句短开场；不报时、不追问、不拉长。"

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

        style = _single_line(user.get("style") or self.default_style, 24)
        style_hint = f"；参考语气偏好：{style}" if style else ""
        return "check_in", f"无明确来源时的轻量开场{style_hint}；优先贴近关系事实、当前状态或当前日程，不使用固定模板。"
