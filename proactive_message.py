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

from .conversation_prompt_section import (
    PhotoPromptContent,
    PromptDocument,
    PromptDocumentPart,
    PromptLabel,
    PromptLabelStyle,
    PromptRenderMode,
    PromptRenderSpec,
    PromptSection,
    exact_text,
    prompt_cdata,
    prompt_document,
    prompt_document_part,
    prompt_section,
    render_prompt_document,
    render_prompt_sections,
)



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
    _split_address_terms,
    _strip_internal_message_blocks,
    _strip_outbound_control_blocks,
    _today_key,
    normalize_bot_relationship_cards,
)
from .memory_context_policy import (
    core_memory_usage_contract_section,
)
from .final_response_persistence import (
    FinalResponsePersistenceMixin,
    collect_proactive_delivery,
)
from .planning import (
    build_daily_plan_prompt,
    build_detail_enhancement_prompt,
    _external_schedule_material_context,
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
    LLM_SEGMENT_MARKER,
    component_kind,
    component_order_from_owner,
    component_strategies_from_owner,
    plan_component_chunks,
    sanitize_llm_segment_control_tokens,
    split_llm_controlled_text,
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
from .wardrobe_photo import resolve_daily_outfit_profile as resolve_wardrobe_daily_outfit_profile
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
from .proactive_message_photo_generation import ProactiveMessagePhotoGenerationMixin
from .proactive_message_photo_generation import (  # noqa: F401  (宿主继续导出)
    PhotoGenerationResult,  # noqa: F401
)
from .proactive_message_outbound_delivery import ProactiveMessageOutboundDeliveryMixin
from .proactive_message_outbound_delivery import (  # noqa: F401  (宿主继续导出)
    _ProactiveSendOutcome,  # noqa: F401
)
from .proactive_message_framework_prompt import ProactiveMessageFrameworkPromptMixin
from .proactive_message_action_execution import ProactiveMessageActionExecutionMixin
from .proactive_message_prompt_context import ProactiveMessagePromptContextMixin
from .proactive_message_external_share import ProactiveMessageExternalShareMixin
from .proactive_message_send_review import ProactiveMessageSendReviewMixin
from .proactive_message_generation import ProactiveMessageGenerationMixin
from .proactive_message_text_finalize import ProactiveMessageTextFinalizeMixin
from .proactive_message_voice_tts import ProactiveMessageVoiceTtsMixin
from .proactive_message_chat_bridge import ProactiveMessageChatBridgeMixin
from .logging_util import get_module_logger
from .proactive_message_shared import (  # noqa: F401  (宿主继续导出，既有调用点零改动)
    _PROACTIVE_DOCUMENT_RENDER,  # noqa: F401
    _persona_provider_id,  # noqa: F401
    _proactive_prompt_part,  # noqa: F401
)

logger = get_module_logger(__name__)


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








class ProactiveMessageMixin(FinalResponsePersistenceMixin, ProactiveMessageChatBridgeMixin, ProactiveMessageVoiceTtsMixin, ProactiveMessageTextFinalizeMixin, ProactiveMessageGenerationMixin, ProactiveMessageSendReviewMixin, ProactiveMessageExternalShareMixin, ProactiveMessagePromptContextMixin, ProactiveMessageActionExecutionMixin, ProactiveMessageFrameworkPromptMixin, ProactiveMessageOutboundDeliveryMixin, ProactiveMessagePhotoGenerationMixin):
    """主动消息生成、动作执行和发送链路"""
