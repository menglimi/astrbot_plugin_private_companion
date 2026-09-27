# -*- coding: utf-8 -*-
"""
GroupObservationMixin — 从 main.py 重新拆分出的群聊观察
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
from .helpers import (
    _date_key,
    _group_link_message_context,
    _normalize_outbound_punctuation_flow,
    _now_ts,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_internal_message_blocks,
    _today_key,
)
from .conversation_injection_plan import (
    PLACEMENT_DYNAMIC_SYSTEM,
    PLACEMENT_TURN_TAIL,
    get_conversation_injection_plan,
)
from .conversation_prompt_section import (
    PromptDocument,
    PromptRenderMode,
    PromptSection,
    prompt_document,
    prompt_heading_ref,
    prompt_list,
    prompt_section,
    prompt_text,
    render_prompt_document,
    render_prompt_sections,
)
from .domains.social.group_mood import (
    project_group_mood_prompt_facts,
    settle_group_mood,
)
from .domains.social.roleplay_strength import project_roleplay_strength
from .domains.social.group_moments import (
    extract_group_moment_candidates,
    extract_moment_portrait_candidates,
    select_group_moments_for_prompt,
    settle_group_moments,
)
from .domains.social.joke_boundary import (
    joke_guard_suggestion,
    settle_joke_boundary,
)
from .group_prompt_context import (
    build_group_prompt_context,
)
from .group_addressing_rules import group_message_addresses_bot
from .segmented_message import sanitize_llm_segment_control_tokens
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
from .group_observation_inbound_context import GroupObservationInboundContextMixin
from .group_observation_context_format import GroupObservationContextFormatMixin
from .group_observation_status_interject_share import GroupObservationStatusInterjectShareMixin
from .group_observation_interject_episode import GroupObservationInterjectEpisodeMixin
from .group_observation_share_schedule import GroupObservationShareScheduleMixin
from .group_observation_topic_relationship import GroupObservationTopicRelationshipMixin
from .group_observation_slang_learn_cleanup import GroupObservationSlangLearnCleanupMixin
from .group_observation_slang_bg_task import GroupObservationSlangBgTaskMixin
from .group_observation_social_context import GroupObservationSocialContextMixin
from .group_observation_observation_update import GroupObservationObservationUpdateMixin

from .group_observation_shared import (
    DEFAULT_AI_DAILY_NEWS_SOURCE,
    DEFAULT_NEWS_SOURCES,
    LEGACY_DEFAULT_NEWS_SOURCES,
    PREVIOUS_TECH_DEFAULT_NEWS_SOURCES,
    _ALMANAC_JI,
    _ALMANAC_YI,
    _GROUP_INJECTION_GUARD_THRESHOLD,
    _GROUP_INJECTION_META_MARKERS,
    _GROUP_INJECTION_PERSISTENCE_MARKERS,
    _GROUP_INJECTION_PERSONA_MARKERS,
    _GROUP_INJECTION_QUOTE_DAMPENERS,
    _GROUP_INJECTION_TARGET_MARKERS,
    _LUNAR_DAY_NAMES,
    _LUNAR_MONTH_NAMES,
    _PLATFORM_DISPLAY_NAMES,
    _SOLAR_TERM_DATES,
    _persona_value,
    _render_group_background_block,
    _render_group_background_document,
    build_group_episode_cache_prompt_document,
    build_group_episode_cache_prompts,
    logger,
)
class GroupObservationMixin(GroupObservationObservationUpdateMixin, GroupObservationSocialContextMixin, GroupObservationSlangBgTaskMixin, GroupObservationSlangLearnCleanupMixin, GroupObservationTopicRelationshipMixin, GroupObservationShareScheduleMixin, GroupObservationInterjectEpisodeMixin, GroupObservationStatusInterjectShareMixin, GroupObservationContextFormatMixin, GroupObservationInboundContextMixin):
    _GROUP_ROLE_LABELS = {"owner": "群主", "admin": "管理员", "member": "普通成员", "unknown": "未知"}
