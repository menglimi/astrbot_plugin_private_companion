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
import sqlite3
import time
import unicodedata
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
    DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT,
    DEFAULT_REPLY_STYLE_PROMPT,
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
    _flat_get,
    _missing_optional_model_dependency,
    _normalize_outbound_punctuation_flow,
    _now_ts,
    _safe_float,
    _safe_int,
    _set_today_key_timezone,
    _set_into_config,
    _single_line,
    _strip_internal_message_blocks,
    _strip_outbound_control_blocks,
    _today_key,
)
from .config_migration import migrate_flat_config_into_schema_groups
from .user_rest_gate import UserRestGateMixin
from .memory_companion_adapter import MemoryCompanionAdapterMixin
from .forward_message import ForwardMessageMixin
from .private_image import PrivateImageMixin
from .prompt_surface import PromptSurface
from .qzone_integration import QzoneMixin
from .segmented_message import flatten_component_chunks, split_plain_component_chain_detailed
from .token_budget import TokenBudgetMixin
from .worldbook import WorldbookMixin
from .user_memory import UserMemoryMixin
from .creative import CreativeMixin
from .proactive import ProactiveMixin
from .group_wakeup import GroupWakeupMixin
from .group_observation import GroupObservationMixin
from .event_dispatch import EventDispatchMixin
from .private_reading import PrivateReadingMixin
from .news_exploration import NewsExplorationMixin
try:
    from .self_timeline import SelfTimelineMixin
except ModuleNotFoundError as exc:
    if str(getattr(exc, "name", "") or "").split(".")[-1] != "self_timeline":
        raise

    class SelfTimelineMixin:
        """Fallback used when an old release package missed self_timeline.py."""

        def _format_self_timeline_context_for_reply(self, *args: Any, **kwargs: Any) -> str:
            return ""

    logger.warning("[PrivateCompanion] self_timeline.py 缺失，已跳过 Bot 自身时间线注入能力。请重新安装完整版本。")
from .core_store import CoreStoreMixin
from .integration_status import IntegrationStatusMixin
from .astrbot_knowledge import AstrBotKnowledgeMixin
from .atrelay import AtRelayMixin
from .proactive_engine import ProactiveEngineMixin
from .proactive_message import ProactiveMessageMixin
from .daily_state import DailyStateMixin
from .state_views import StateViewsMixin
from .interaction_utils import InteractionUtilsMixin
from .llm_tool_actions import LlmToolActionsMixin
from .command_handlers import CommandHandlersMixin
from .tts_enhancement import TtsEnhancementMixin
from .tts_tool_sanitizer import TtsToolSanitizerMixin
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

_private_companion_plugin: Any | None = None

DEFAULT_AI_DAILY_MORNING_UID = "3706929260006322"
DEFAULT_AI_DAILY_JUYA_UID = "285286947"
DEFAULT_AI_DAILY_SOURCES = "\n".join(
    [
        f"AI日报|橘鸦Juya|{DEFAULT_AI_DAILY_JUYA_UID}|日报 早报|23:00",
        f"AI早报|黑鸦Heya|{DEFAULT_AI_DAILY_MORNING_UID}|早报 日报|12:00",
    ]
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


def get_private_companion_api() -> Any | None:
    plugin = _private_companion_plugin
    if plugin is None:
        return None
    return getattr(plugin, "extension_api", None)


class PrivateCompanionExtensionAPI:
    """Lightweight integration API for external AstrBot plugins."""

    def __init__(self, plugin: "PrivateCompanionPlugin") -> None:
        self._plugin = plugin

    def register_proactive_ability(self, spec: dict[str, Any]) -> bool:
        return self._plugin.register_external_proactive_ability(spec)

    def unregister_proactive_ability(self, name: str) -> bool:
        return self._plugin.unregister_external_proactive_ability(name)

    def list_proactive_abilities(self) -> list[dict[str, Any]]:
        return self._plugin.external_proactive_abilities()

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

_PROACTIVE_ONLY_TEMP_UNLOCK_ALIASES = {
    "全部": "all",
    "all": "all",
    "被动": "all",
    "被动链路": "all",
    "状态": "inject_passive_states",
    "状态注入": "inject_passive_states",
    "被动状态": "inject_passive_states",
    "图片": "enable_private_image_self_recognition",
    "识图": "enable_private_image_self_recognition",
    "私聊图片": "enable_private_image_self_recognition",
    "合并消息": "enable_forward_message_adaptation",
    "转发": "enable_forward_message_adaptation",
    "转发消息": "enable_forward_message_adaptation",
    "防抖": "enable_message_debounce",
    "智能防抖": "enable_message_debounce",
    "撤回": "enable_recall_enhancement",
    "撤回增强": "enable_recall_enhancement",
    "tts": "enable_tts_enhancement",
    "TTS": "enable_tts_enhancement",
    "语音": "enable_tts_enhancement",
    "分段": "enable_segmented_proactive_reply",
    "回复分段": "enable_segmented_proactive_reply",
    "群聊": "enable_group_companion",
    "群聊观察": "enable_group_companion",
    "技能": "enable_skill_growth_passive_injection",
    "技能注入": "enable_skill_growth_passive_injection",
    "吃什么": "enable_food_menu_recommendation",
    "吃什么候选": "enable_food_menu_recommendation",
    "候选菜单": "enable_food_menu_recommendation",
    "书柜偏好": "enable_private_reading_preference_influence",
    "夹层偏好": "enable_private_reading_preference_influence",
    "关系网": "enable_worldbook_member_recognition",
    "跨用户记忆": "enable_cross_user_memory_bridge",
    "跨用户记忆互通": "enable_cross_user_memory_bridge",
    "互动查询": "enable_cross_user_memory_bridge",
    "跨群转述": "enable_atrelay_tools",
    "转述工具": "enable_atrelay_tools",
    "livingmemory": "enable_livingmemory_integration",
    "lmem": "enable_livingmemory_integration",
}
_PROACTIVE_ONLY_TEMP_UNLOCK_LABELS = {
    "all": "全部被动链路",
    "inject_passive_states": "被动状态注入",
    "enable_intent_emotion_analysis": "意图/情绪分析",
    "enable_llm_timer_scheduling": "对话临时预约",
    "enable_passive_topic_suppression": "重复话题抑制",
    "enable_environment_perception": "环境感知",
    "enable_message_debounce": "防抖",
    "enable_recall_enhancement": "撤回增强",
    "enable_private_image_self_recognition": "私聊图片识别",
    "enable_forward_message_adaptation": "合并/转发消息阅读",
    "enable_group_companion": "群聊观察",
    "enable_skill_growth_passive_injection": "技能被动注入",
    "enable_food_menu_recommendation": "吃什么候选",
    "enable_private_reading_preference_influence": "夹层阅读偏好影响",
    "enable_worldbook_member_recognition": "关系网成员识别",
    "enable_cross_user_memory_bridge": "跨用户记忆互通",
    "enable_atrelay_tools": "跨群转述工具",
    "enable_livingmemory_integration": "LivingMemory 被动引导",
    "enable_tts_enhancement": "TTS 后处理",
    "enable_segmented_proactive_reply": "普通 LLM 分段",
}
_PROACTIVE_ONLY_TEMP_UNLOCK_GROUPS = {
    "private_event_pipeline": {
        "enable_message_debounce",
        "enable_private_image_self_recognition",
        "enable_forward_message_adaptation",
    },
    "group_event_pipeline": {
        "enable_group_companion",
        "enable_message_debounce",
        "enable_forward_message_adaptation",
    },
    "llm_request": {
        "inject_passive_states",
        "enable_intent_emotion_analysis",
        "enable_llm_timer_scheduling",
        "enable_passive_topic_suppression",
        "enable_environment_perception",
        "enable_tts_enhancement",
        "enable_private_image_self_recognition",
        "enable_forward_message_adaptation",
        "enable_group_companion",
        "enable_skill_growth_passive_injection",
        "enable_food_menu_recommendation",
        "enable_private_reading_preference_influence",
        "enable_worldbook_member_recognition",
        "enable_cross_user_memory_bridge",
        "enable_livingmemory_integration",
    },
    "pc_tools": {
        "enable_atrelay_tools",
        "enable_worldbook_member_recognition",
        "enable_cross_user_memory_bridge",
        "enable_qzone_integration",
    },
}
_PROACTIVE_ONLY_TEMP_UNLOCK_RELATED = {
    "enable_atrelay_tools": ["enable_worldbook_member_recognition"],
    "enable_cross_user_memory_bridge": ["enable_worldbook_member_recognition"],
    "enable_group_companion": ["enable_worldbook_member_recognition"],
    "enable_forward_message_adaptation": ["enable_private_image_self_recognition"],
}


@register(
    PLUGIN_NAME,
    "menglimi",
    "我会永远陪着你：为 AstrBot 提供人格连续性、关系识别、主动行为和可视化管理的陪伴编排插件。",
    "5.6.10",
)
class PrivateCompanionPlugin(
    CoreStoreMixin,
    AstrBotKnowledgeMixin,
    IntegrationStatusMixin,
    UserRestGateMixin,
    MemoryCompanionAdapterMixin,
    PrivateImageMixin,
    ForwardMessageMixin,
    QzoneMixin,
    TokenBudgetMixin,
    WorldbookMixin,
    UserMemoryMixin,
    CreativeMixin,
    ProactiveMixin,
    ProactiveEngineMixin,
    ProactiveMessageMixin,
    DailyStateMixin,
    StateViewsMixin,
    InteractionUtilsMixin,
    LlmToolActionsMixin,
    CommandHandlersMixin,
    TtsEnhancementMixin,
    TtsToolSanitizerMixin,
    GroupWakeupMixin,
    GroupObservationMixin,
    EventDispatchMixin,
    PrivateReadingMixin,
    NewsExplorationMixin,
    SelfTimelineMixin,
    AtRelayMixin,
    Star,
):
    @staticmethod
    def _cfg_raw(config: AstrBotConfig, key: str, default: Any = None) -> Any:
        return _flat_get(config, key, default)

    @staticmethod
    def _cfg_bool(config: AstrBotConfig, key: str, default: bool = True) -> bool:
        value = _flat_get(config, key, default)
        if isinstance(value, str):
            text = value.strip().lower()
            parsed: bool | None = None
            if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
                parsed = True
            elif text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
                parsed = False
            if parsed is not None:
                _set_into_config(config, key, parsed)
                return parsed
        return bool(value)

    @staticmethod
    def _cfg_str(config: AstrBotConfig, key: str, default: str = "", fallback: str = "") -> str:
        return str(_flat_get(config, key, default)).strip() or fallback

    @staticmethod
    def _cfg_int(config: AstrBotConfig, key: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
        return _safe_int(_flat_get(config, key, default), default, minimum, maximum)

    @staticmethod
    def _cfg_float(config: AstrBotConfig, key: str, default: float, minimum: float = 0.0) -> float:
        return _safe_float(_flat_get(config, key, default), default, minimum)

    @staticmethod
    def _cfg_unit_interval(config: AstrBotConfig, key: str, default: float, minimum: float = 0.0) -> float:
        original = _safe_float(_flat_get(config, key, default), default, minimum)
        value = original / 100.0 if original > 1.0 else original
        value = max(minimum, min(1.0, value))
        if value != original:
            _set_into_config(config, key, value)
        return value

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        global _private_companion_plugin
        _private_companion_plugin = self
        self.extension_api = PrivateCompanionExtensionAPI(self)
        self._external_proactive_abilities: dict[str, dict[str, Any]] = {}
        self.config = config
        c = config
        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        os.makedirs(self.data_dir, exist_ok=True)
        self.data_file = os.path.join(self.data_dir, "companions.json")
        self.storage_backend = self._cfg_str(c, "storage_backend", "json", "json").strip().lower() or "json"
        if self.storage_backend not in {"json", "sqlite"}:
            self.storage_backend = "json"
        self.storage_sqlite_path = self._cfg_str(c, "storage_sqlite_path", "", "")
        self._rebuild_store_manager()
        config_migration_started = time.perf_counter()
        self._startup_config_migration_changes = migrate_flat_config_into_schema_groups(
            c,
            schema_path=Path(__file__).with_name("_conf_schema.json"),
            logger=logger,
            save=False,
        )
        config_migration_elapsed_ms = int((time.perf_counter() - config_migration_started) * 1000)
        if config_migration_elapsed_ms > 1200:
            logger.warning(
                "[PrivateCompanion] 启动配置迁移耗时较高: elapsed=%sms changes=%s",
                config_migration_elapsed_ms,
                self._startup_config_migration_changes,
            )

        self.enabled = self._cfg_bool(c, "enabled", True)
        self.enable_proactive_only_mode = self._cfg_bool(c, "enable_proactive_only_mode", False)
        self.proactive_intensity_preset = self._normalize_proactive_intensity_preset(
            self._cfg_str(c, "proactive_intensity_preset", "off", "off")
        )
        self.check_interval_seconds = self._cfg_int(c, "check_interval_seconds", 60, 30)
        self.idle_minutes = self._cfg_int(c, "idle_minutes", 60, 5)
        self.min_interval_minutes = self._cfg_int(c, "min_interval_minutes", 120, 10)
        self.proactive_unanswered_slowdown_start = self._cfg_int(c, "proactive_unanswered_slowdown_start", 1, 1, 10)
        self.proactive_unanswered_max_interval_multiplier = min(
            8.0,
            max(1.0, self._cfg_float(c, "proactive_unanswered_max_interval_multiplier", 2.2, 1.0)),
        )
        self.friend_unanswered_max_cooldown_hours = min(
            168.0,
            max(1.0, self._cfg_float(c, "friend_unanswered_max_cooldown_hours", 60.0, 1.0)),
        )
        self.timer_pre_silence_minutes = self._cfg_int(c, "timer_pre_silence_minutes", 20, 0, 240)
        self.max_daily_messages = self._cfg_int(c, "max_daily_messages", 8, 0, 12)
        self.inbound_message_debounce_seconds = self._cfg_float(c, "inbound_message_debounce_seconds", 3.0, 0.0)
        self.enable_recall_enhancement = self._cfg_bool(c, "enable_recall_enhancement", True)
        self.enable_recall_cancel_reply = self._cfg_bool(c, "enable_recall_cancel_reply", self.enable_recall_enhancement)
        self.enable_recall_message_cache = self._cfg_bool(c, "enable_recall_message_cache", True)
        self.enable_recall_transcribe_command = self._cfg_bool(c, "enable_recall_transcribe_command", True)
        self.recall_message_cache_ttl_seconds = self._cfg_float(c, "recall_message_cache_ttl_seconds", 600.0, 60.0)
        self.recall_message_cache_max_items = self._cfg_int(c, "recall_message_cache_max_items", 300, 0, 3000)
        self.recall_message_image_cache_max_mb = self._cfg_float(c, "recall_message_image_cache_max_mb", 256.0, 0.0)
        self.recall_message_cache_text_chars = self._cfg_int(c, "recall_message_cache_text_chars", 500, 80, 2000)
        self.recall_cancel_reply_ttl_seconds = self.recall_message_cache_ttl_seconds
        self.enable_forbidden_word_recall = self._cfg_bool(c, "enable_forbidden_word_recall", False)
        self.recall_forbidden_words = self._parse_text_list_config(self._cfg_raw(c, "recall_forbidden_words", []), limit=300)
        self.recall_forbidden_word_case_sensitive = self._cfg_bool(c, "recall_forbidden_word_case_sensitive", False)
        self.recall_forbidden_scope = self._cfg_str(c, "recall_forbidden_scope", "bot_and_group", "bot_and_group").lower()
        if self.recall_forbidden_scope not in {"bot_only", "group_only", "bot_and_group"}:
            self.recall_forbidden_scope = "bot_and_group"
        self._recalled_message_ids: dict[str, dict[str, Any]] = {}
        self._recall_message_cache: dict[str, dict[str, Any]] = {}
        self.enable_message_debounce = self._cfg_bool(
            c,
            "enable_message_debounce",
            self._cfg_bool(c, "enable_semantic_message_debounce", True),
        )
        self.enable_semantic_message_debounce = self.enable_message_debounce
        self.enable_smart_message_debounce = self._cfg_bool(c, "enable_smart_message_debounce", False)
        self.smart_message_debounce_provider_id = self._cfg_str(c, "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID", "")
        self.smart_message_debounce_wait_seconds = self._cfg_float(c, "smart_message_debounce_wait_seconds", 3.0, 0.0)
        self.smart_message_debounce_model_timeout_seconds = self._cfg_float(c, "smart_message_debounce_model_timeout_seconds", 0.8, 0.2)
        self.smart_message_debounce_learning_window_seconds = self._cfg_float(c, "smart_message_debounce_learning_window_seconds", 8.0, 1.0)
        self.smart_message_debounce_examples_limit = self._cfg_int(c, "smart_message_debounce_examples_limit", 8, 0, 30)
        legacy_semantic_debounce_seconds = self._cfg_float(c, "semantic_message_debounce_seconds", 8.0, 0.0)
        text_debounce_raw = self._cfg_raw(c, "text_message_debounce_seconds", None)
        text_debounce_default = legacy_semantic_debounce_seconds if text_debounce_raw in (None, "") else 0.0
        self.text_message_debounce_seconds = self._cfg_float(c, "text_message_debounce_seconds", text_debounce_default, 0.0)
        self.image_message_debounce_seconds = self._cfg_float(c, "image_message_debounce_seconds", 8.0, 0.0)
        self.forward_message_debounce_seconds = self._cfg_float(c, "forward_message_debounce_seconds", 0.0, 0.0)
        self.text_message_debounce_max_wait_seconds = self._cfg_float(c, "text_message_debounce_max_wait_seconds", 12.0, 0.0)
        self.message_debounce_max_merge_messages = self._cfg_int(c, "message_debounce_max_merge_messages", 8, 0, 30)
        self.semantic_message_debounce_seconds = self.text_message_debounce_seconds
        self.private_image_vision_wait_seconds = self._cfg_float(c, "private_image_vision_wait_seconds", 30.0, 0.0)
        self.private_image_provider_timeout_seconds = self._cfg_float(c, "private_image_provider_timeout_seconds", 12.0, 3.0)
        self.enable_private_image_self_recognition = self._cfg_bool(c, "enable_private_image_self_recognition", True)
        self.enable_private_image_vision_cache = self._cfg_bool(c, "enable_private_image_vision_cache", True)
        self.private_image_vision_cache_max_items = self._cfg_int(c, "private_image_vision_cache_max_items", 300, 0, 3000)
        self.enable_context_image_captioning = self._cfg_bool(c, "enable_context_image_captioning", True)
        self.context_image_caption_max_items = self._cfg_int(c, "context_image_caption_max_items", 12, 0, 50)
        self.context_image_caption_timeout_seconds = self._cfg_float(c, "context_image_caption_timeout_seconds", 8.0, 0.0)
        self.enable_private_image_gif_enhancement = self._cfg_bool(c, "enable_private_image_gif_enhancement", True)
        self.private_image_gif_max_frames = self._cfg_int(c, "private_image_gif_max_frames", 4, 1, 8)
        self.enable_group_conversation_followup = self._cfg_bool(c, "enable_group_conversation_followup", True)
        self.group_conversation_followup_seconds = self._cfg_int(c, "group_conversation_followup_seconds", 120, 0, 600)
        self.group_conversation_followup_max_turns = self._cfg_int(c, "group_conversation_followup_max_turns", 1, 0, 10)
        self.quiet_hours = self._cfg_str(c, "quiet_hours", "23:00-08:30")
        self.default_style = self._cfg_str(c, "default_style", "温柔", "温柔")
        reply_style_raw = _flat_get(c, "reply_style_prompt", None)
        self.reply_style_prompt = DEFAULT_REPLY_STYLE_PROMPT if reply_style_raw is None else str(reply_style_raw).strip()
        self.worldview_adaptation_mode = self._cfg_str(c, "worldview_adaptation_mode", "auto", "auto")
        if self.worldview_adaptation_mode not in {"auto", "modern", "fantasy", "sci_fi", "custom", "off"}:
            self.worldview_adaptation_mode = "auto"
        self.worldview_adaptation_prompt = self._cfg_str(c, "worldview_adaptation_prompt", "")
        self.default_nickname = self._cfg_str(c, "default_nickname", "你", "你")
        self.require_private_opt_in = self._cfg_bool(c, "require_private_opt_in", True)
        self.target_user_ids = self._cfg_raw(c, "target_user_ids", [])
        self.private_user_aliases = self._parse_private_user_aliases(self._cfg_raw(c, "private_user_aliases", ""))
        self.private_user_delivery_aliases = self._parse_private_user_aliases(self._cfg_raw(c, "private_user_delivery_aliases", ""))
        self._load_tts_enhancement_config(c)
        self.target_platform = self._cfg_str(c, "target_platform", "aiocqhttp", "aiocqhttp")
        self.default_enable_configured_targets = self._cfg_bool(c, "default_enable_configured_targets", True)
        self.enable_environment_perception = self._cfg_bool(c, "enable_environment_perception", True)
        timezone_default = self._cfg_str(c, "timezone", "Asia/Shanghai", "Asia/Shanghai")
        self.environment_perception_timezone = self._cfg_str(
            c,
            "environment_perception_timezone",
            timezone_default,
            "Asia/Shanghai",
        )
        _set_today_key_timezone(self.environment_perception_timezone)
        self.enable_holiday_perception = self._cfg_bool(c, "enable_holiday_perception", True)
        self.holiday_country = self._cfg_str(c, "holiday_country", "CN", "CN").upper()
        self.enable_platform_perception = self._cfg_bool(c, "enable_platform_perception", True)
        self.enable_model_perception = self._cfg_bool(c, "enable_model_perception", True)
        self.enable_worldview_perception = self._cfg_bool(c, "enable_worldview_perception", False)
        self.enable_lunar_perception = self._cfg_bool(c, "enable_lunar_perception", True)
        self.enable_solar_term_perception = self._cfg_bool(c, "enable_solar_term_perception", True)
        self.enable_almanac_perception = self._cfg_bool(c, "enable_almanac_perception", False)
        self.provider_config_mode = self._normalize_provider_config_mode(
            self._cfg_raw(c, "provider_config_mode", None),
            c,
        )
        _page_font = str(self._cfg_raw(c, "page_font_family", "original") or "original").strip().lower()
        self.page_font_family = _page_font if _page_font in {"original", "cheng"} else "original"
        _page_theme = str(self._cfg_raw(c, "page_theme", "classic") or "classic").strip().lower()
        self.page_theme = _page_theme if _page_theme in {"classic", "dark", "warm", "forest", "sakura"} else "classic"
        self.fast_response_provider_id = self._cfg_str(c, "FAST_RESPONSE_PROVIDER_ID", "")
        self.complex_reasoning_provider_id = self._cfg_str(c, "COMPLEX_REASONING_PROVIDER_ID", "")
        self.creative_model_provider_id = self._cfg_str(c, "CREATIVE_MODEL_PROVIDER_ID", "")
        self.llm_provider_id = self._cfg_str(c, "LLM_PROVIDER_ID", "")
        self.daily_token_limit = self._cfg_int(c, "daily_token_limit", 1_000_000, 0)
        legacy_soft_enabled = self._cfg_bool(c, "enable_maintenance_token_saver", True)
        legacy_soft_limit = self._cfg_int(c, "maintenance_token_soft_limit", 800_000, 0)
        self.enable_daily_token_soft_limit = self._cfg_bool(c, "enable_daily_token_soft_limit", legacy_soft_enabled)
        self.daily_token_soft_limit = self._cfg_int(c, "daily_token_soft_limit", legacy_soft_limit, 0)
        self.enable_maintenance_token_saver = self.enable_daily_token_soft_limit
        self.maintenance_token_soft_limit = self.daily_token_soft_limit
        self.daily_plan_provider_id = self._cfg_str(c, "DAILY_PLAN_PROVIDER_ID", "")
        self.enable_daily_plan = self._cfg_bool(c, "enable_daily_plan", True)
        self.daily_plan_time = self._cfg_str(c, "daily_plan_time", "07:30")
        self.bot_name = self._cfg_str(c, "bot_name", "小星", "小星")
        self.include_schedule_in_messages = self._cfg_bool(c, "include_schedule_in_messages", True)
        self.daily_plan_prompt = self._cfg_str(c, "daily_plan_prompt", "")
        self.plugin_specific_persona_id = self._cfg_str(c, "plugin_specific_persona_id", "")
        self.schedule_persona_prompt = self._cfg_str(c, "schedule_persona_prompt", "")
        self.schedule_worldview_prompt = self._cfg_str(c, "schedule_worldview_prompt", "")
        self.roleplay_user_profile_prompt = self._cfg_str(c, "roleplay_user_profile_prompt", "")
        self.roleplay_knowledge_source_ids = self._normalize_roleplay_knowledge_source_ids(
            self._cfg_raw(c, "roleplay_knowledge_source_ids", [])
        )
        self.private_image_self_recognition_hint = self._cfg_str(c, "private_image_self_recognition_hint", "")
        self.daily_plan_item_count = self._cfg_int(c, "daily_plan_item_count", 10, 5, 16)
        self.enable_humanized_states = self._cfg_bool(c, "enable_humanized_states", True)
        self.enable_health_state = self._cfg_bool(c, "enable_health_state", True)
        self.enable_hunger_state = self._cfg_bool(c, "enable_hunger_state", True)
        self.enable_cycle_state = self._cfg_bool(c, "enable_cycle_state", True)
        self.humanized_state_intensity = self._cfg_int(c, "humanized_state_intensity", 50, 0, 100)
        self.enable_rest_reply_simulation = self._cfg_bool(c, "enable_rest_reply_simulation", False)
        self.rest_reply_mode = self._cfg_str(c, "rest_reply_mode", "probability", "probability").strip().lower()
        if self.rest_reply_mode in {"model", "模型", "llm_judge", "llm-judge"}:
            self.rest_reply_mode = "llm"
        if self.rest_reply_mode not in {"probability", "llm"}:
            self.rest_reply_mode = "probability"
        self.rest_reply_probability = self._cfg_int(c, "rest_reply_probability", 18, 0, 100) / 100.0
        self.rest_reply_llm_threshold = self._cfg_int(c, "rest_reply_llm_threshold", 65, 0, 100)
        self.rest_reply_active_windows = self._cfg_str(c, "rest_reply_active_windows", "23:00-08:30,12:20-13:40")
        self.rest_reply_awake_grace_minutes = self._cfg_int(c, "rest_reply_awake_grace_minutes", 30, 0, 240)
        self.enable_rest_backlog_reply = self._cfg_bool(c, "enable_rest_backlog_reply", True)
        self.rest_backlog_max_messages = self._cfg_int(c, "rest_backlog_max_messages", 4, 1, 12)
        self.rest_wakeup_provider_id = self._cfg_str(c, "REST_WAKEUP_PROVIDER_ID", "")
        self.enable_enhanced_dreams = self._cfg_bool(c, "enable_enhanced_dreams", False)
        self.dream_diary_provider_id = self._cfg_str(
            c,
            "DREAM_DIARY_PROVIDER_ID",
            self._cfg_str(c, "DREAM_PROVIDER_ID", self._cfg_str(c, "DIARY_PROVIDER_ID", "")),
        )
        self.dream_provider_id = self.dream_diary_provider_id
        self.diary_provider_id = self.dream_diary_provider_id
        self.dream_afterglow_mode = self._cfg_str(c, "dream_afterglow_mode", "auto", "auto")
        if self.dream_afterglow_mode not in {"auto", "轻", "标准", "明显"}:
            self.dream_afterglow_mode = "auto"
        self.enable_mixed_dream_themes = self._cfg_bool(c, "enable_mixed_dream_themes", True)
        self.enable_intimate_dream_theme = self._cfg_bool(c, "enable_intimate_dream_theme", False)
        self.dream_theme_candidates = self._cfg_str(
            c,
            "dream_theme_candidates",
            "温柔日常,奇幻,恐怖,追逐,悬疑,荒诞,怀旧,暧昧春梦",
        )
        self.inject_passive_states = self._cfg_bool(c, "inject_passive_states", True)
        self.enable_passive_state_delta_injection = self._cfg_bool(c, "enable_passive_state_delta_injection", True)
        self.passive_injection_position = self._normalize_passive_injection_position(
            self._cfg_str(c, "passive_injection_position", "prompt")
        )
        self.framework_session_lock_mode = self._normalize_framework_session_lock_mode(
            self._cfg_str(c, "framework_session_lock_mode", "auto", "auto")
        )
        self.proactive_share_probability = self._cfg_int(c, "proactive_share_probability", 45, 0, 100) / 100
        self.enable_daily_greetings = self._cfg_bool(c, "enable_daily_greetings", True)
        self.greeting_idle_minutes = self._cfg_int(c, "greeting_idle_minutes", 30, 0, 240)
        self.allow_insomnia_night_message = self._cfg_bool(c, "allow_insomnia_night_message", True)
        self.proactive_reply_context_hours = self._cfg_int(c, "proactive_reply_context_hours", 12, 1, 72)
        self.enable_creative_writing = self._cfg_bool(c, "enable_creative_writing", True)
        self.creative_inspiration_probability = self._cfg_unit_interval(c, "creative_inspiration_probability", 0.20, 0.0)
        self.creative_share_probability = self._cfg_unit_interval(c, "creative_share_probability", 0.28, 0.0)
        self.creative_chars_per_session = self._cfg_int(
            c,
            "creative_chars_per_session",
            self._cfg_int(c, "creative_base_chars_per_hour", 220, 60, 1200),
            60,
            1200,
        )
        self.creative_base_chars_per_hour = self.creative_chars_per_session
        self.creative_max_active_projects = self._cfg_int(c, "creative_max_active_projects", 2, 1, 5)
        self.creative_hidden_mode = self._cfg_bool(c, "creative_hidden_mode", True)
        self.creative_provider_id = self._cfg_str(c, "CREATIVE_PROVIDER_ID", "")
        self.creative_outline_provider_id = self._cfg_str(c, "CREATIVE_OUTLINE_PROVIDER_ID", "")
        self.creative_review_provider_id = self._cfg_str(c, "CREATIVE_REVIEW_PROVIDER_ID", "")
        self.voice_prompt_provider_id = self._cfg_str(c, "VOICE_PROMPT_PROVIDER_ID", "")
        self.history_summary_provider_id = self._cfg_str(c, "HISTORY_SUMMARY_PROVIDER_ID", "")
        self.enable_llm_proactive_message = self._cfg_bool(c, "enable_llm_proactive_message", True)
        self.enable_llm_proactive_persona_judge = self._cfg_bool(c, "enable_llm_proactive_persona_judge", True)
        self.proactive_persona_judge_provider_id = self._cfg_str(c, "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID", "")
        self.proactive_persona_judge_send_threshold = self._cfg_int(c, "proactive_persona_judge_send_threshold", 62, 0, 100)
        self.proactive_persona_judge_cache_minutes = self._cfg_int(c, "proactive_persona_judge_cache_minutes", 180, 5, 720)
        self.enable_llm_timer_scheduling = self._cfg_bool(c, "enable_llm_timer_scheduling", False)
        self.enable_proactive_decorating_hooks = self._cfg_bool(c, "enable_proactive_decorating_hooks", True)
        self.enable_precise_platform_send = self._cfg_bool(c, "enable_precise_platform_send", True)
        self.enable_proactive_quote_trigger_message = self._cfg_bool(c, "enable_proactive_quote_trigger_message", False)
        self.enable_quote_group_reply = self._cfg_bool(c, "enable_quote_group_reply", True)
        self.enable_quote_group_interjection = self._cfg_bool(c, "enable_quote_group_interjection", True)
        self.enable_quote_private_proactive = self._cfg_bool(c, "enable_quote_private_proactive", True)
        self.quote_skip_short_reply_chars = self._cfg_int(c, "quote_skip_short_reply_chars", 0, 0, 120)
        self.quote_target_strategy = self._cfg_str(c, "quote_target_strategy", "current", "current").lower()
        if self.quote_target_strategy not in {"current", "quoted", "auto"}:
            self.quote_target_strategy = "current"
        self._reply_component_style_cache: dict[str, tuple[str, str]] = {}
        self.enable_segmented_proactive_reply = self._cfg_bool(c, "enable_segmented_proactive_reply", False)
        self.segmented_proactive_scope = self._cfg_str(c, "segmented_proactive_scope", "proactive_only", "proactive_only")
        if self.segmented_proactive_scope not in {"proactive_only", "all_llm"}:
            self.segmented_proactive_scope = "proactive_only"
        self.segmented_proactive_chat_scope = self._cfg_str(c, "segmented_proactive_chat_scope", "all", "all").lower()
        if self.segmented_proactive_chat_scope not in {"all", "private", "group"}:
            self.segmented_proactive_chat_scope = "all"
        self.segmented_proactive_threshold = self._cfg_int(c, "segmented_proactive_threshold", 500, 20, 1024)
        self.segmented_proactive_min_segment_chars = self._cfg_int(c, "segmented_proactive_min_segment_chars", 8, 1, 40)
        self.segmented_proactive_max_segments = self._cfg_int(c, "segmented_proactive_max_segments", 3, 1, 8)
        self.segmented_proactive_split_mode = self._cfg_str(c, "segmented_proactive_split_mode", "regex", "regex")
        if self.segmented_proactive_split_mode not in {"regex", "words"}:
            self.segmented_proactive_split_mode = "regex"
        self.segmented_proactive_regex = str(self._cfg_raw(c, "segmented_proactive_regex", r".*?[。？！~…\n]+|.+$"))
        split_words = self._cfg_raw(c, "segmented_proactive_split_words", ["。", "？", "！", "~", "…", "“"])
        self.segmented_proactive_split_words = [str(item) for item in split_words] if isinstance(split_words, list) else ["。", "？", "！", "~", "…", "“"]
        if "……" in self.segmented_proactive_split_words and "…" not in self.segmented_proactive_split_words:
            self.segmented_proactive_split_words.append("…")
        self.enable_segmented_proactive_content_cleanup = self._cfg_bool(c, "enable_segmented_proactive_content_cleanup", False)
        self.segmented_proactive_content_cleanup_scope = self._cfg_str(c, "segmented_proactive_content_cleanup_scope", "all", "all")
        if self.segmented_proactive_content_cleanup_scope not in {"all", "trailing"}:
            self.segmented_proactive_content_cleanup_scope = "all"
        self.segmented_proactive_content_cleanup_rule = str(self._cfg_raw(c, "segmented_proactive_content_cleanup_rule", r"[\n]"))
        cleanup_words = self._cfg_raw(c, "segmented_proactive_content_cleanup_words", ["\n"])
        self.segmented_proactive_content_cleanup_words = (
            [str(item) for item in cleanup_words if str(item) != ""]
            if isinstance(cleanup_words, list)
            else ["\n"]
        )
        self.segmented_proactive_interval_method = self._cfg_str(c, "segmented_proactive_interval_method", "log", "log")
        if self.segmented_proactive_interval_method not in {"random", "log"}:
            self.segmented_proactive_interval_method = "log"
        self.segmented_proactive_interval_min = self._cfg_float(c, "segmented_proactive_interval_min", 1.5, 0.1)
        self.segmented_proactive_interval_max = self._cfg_float(c, "segmented_proactive_interval_max", 3.5, 0.1)
        self.segmented_proactive_log_base = self._cfg_float(c, "segmented_proactive_log_base", 1.8, 1.1)
        self.segmented_proactive_send_as_forward = self._cfg_bool(c, "segmented_proactive_send_as_forward", False)
        if self.segmented_proactive_interval_max < self.segmented_proactive_interval_min:
            self.segmented_proactive_interval_max = self.segmented_proactive_interval_min
        self.proactive_prompt_template = self._cfg_str(c, "proactive_prompt_template", "")
        self.max_proactive_plan_lag_minutes = self._cfg_int(c, "max_proactive_plan_lag_minutes", 180, 5, 1440)
        self._recent_inbound_message_debounce: dict[str, float] = {}
        self._semantic_message_buffers: dict[str, dict[str, Any]] = {}
        self.enable_detail_enhancement = self._cfg_bool(c, "enable_detail_enhancement", False)
        self.detail_enhancement_provider_id = self._cfg_str(c, "DETAIL_ENHANCEMENT_PROVIDER_ID", "")
        self.narration_provider_id = self._cfg_str(c, "NARRATION_PROVIDER_ID", "")
        self.photo_prompt_provider_id = self._cfg_str(c, "PHOTO_PROMPT_PROVIDER_ID", "")
        self.comfyui_photo_workflow_name = self._cfg_str(c, "COMFYUI_PHOTO_WORKFLOW_NAME", "")
        self.comfyui_text2img_workflow_name = self._cfg_str(c, "COMFYUI_TEXT2IMG_WORKFLOW_NAME", self.comfyui_photo_workflow_name)
        self.comfyui_selfie_workflow_name = self._cfg_str(c, "COMFYUI_SELFIE_WORKFLOW_NAME", self.comfyui_photo_workflow_name)
        self.photo_persona_reference_image_path = self._cfg_str(c, "photo_persona_reference_image_path", "")
        self.comfyui_photo_wait_seconds = self._cfg_int(c, "comfyui_photo_wait_seconds", 90, 5, 600)
        self.photo_generation_backend = self._cfg_str(c, "photo_generation_backend", "auto", "auto").strip().lower()
        if self.photo_generation_backend not in {"auto", "comfyui", "sdgen", "external"}:
            self.photo_generation_backend = "auto"
        self.enable_local_photo_load_guard = self._cfg_bool(c, "enable_local_photo_load_guard", True)
        self.local_photo_cpu_busy_percent = self._cfg_int(c, "local_photo_cpu_busy_percent", 85, 1, 100)
        self.local_photo_memory_busy_percent = self._cfg_int(c, "local_photo_memory_busy_percent", 88, 1, 100)
        self.local_photo_defer_minutes = self._cfg_int(c, "local_photo_defer_minutes", 30, 1, 240)
        self._local_photo_load_cache: dict[str, Any] = {}
        self.external_image_api_platform = self._normalize_external_image_api_platform(
            self._cfg_str(c, "external_image_api_platform", "auto", "auto")
        )
        self.external_image_api_base_url = self._cfg_str(c, "EXTERNAL_IMAGE_API_BASE_URL", "")
        self.external_image_api_key = self._cfg_str(c, "EXTERNAL_IMAGE_API_KEY", "")
        self.external_image_api_model = self._cfg_str(c, "EXTERNAL_IMAGE_API_MODEL", "")
        self.external_image_api_size = self._cfg_str(c, "external_image_api_size", "1024x1024", "1024x1024")
        self.external_image_api_timeout_seconds = self._cfg_int(c, "external_image_api_timeout_seconds", 180, 20, 600)
        self.external_image_api_custom_headers = self._cfg_str(c, "external_image_api_custom_headers", "")
        self.enable_backup_external_image_api = self._cfg_bool(c, "enable_backup_external_image_api", False)
        self.backup_external_image_api_platform = self._normalize_external_image_api_platform(
            self._cfg_str(c, "backup_external_image_api_platform", "auto", "auto")
        )
        self.backup_external_image_api_base_url = self._cfg_str(c, "BACKUP_EXTERNAL_IMAGE_API_BASE_URL", "")
        self.backup_external_image_api_key = self._cfg_str(c, "BACKUP_EXTERNAL_IMAGE_API_KEY", "")
        self.backup_external_image_api_model = self._cfg_str(c, "BACKUP_EXTERNAL_IMAGE_API_MODEL", "")
        self.backup_external_image_api_size = self._cfg_str(c, "backup_external_image_api_size", "1024x1024", "1024x1024")
        self.backup_external_image_api_timeout_seconds = self._cfg_int(c, "backup_external_image_api_timeout_seconds", 180, 20, 600)
        self.backup_external_image_api_custom_headers = self._cfg_str(c, "backup_external_image_api_custom_headers", "")
        self.photo_generation_style = self._cfg_str(c, "photo_generation_style", "真实", "真实")
        self.photo_generation_style_custom_prompt = self._cfg_str(c, "photo_generation_style_custom_prompt", "")
        self.photo_generation_fixed_prompt = self._cfg_str(c, "photo_generation_fixed_prompt", "")
        self.photo_generation_scene_presets = self._cfg_raw(c, "photo_generation_scene_presets", "")
        self.enable_daily_outfit_photo = self._cfg_bool(c, "enable_daily_outfit_photo", False)
        self.daily_outfit_photo_prompt = self._cfg_str(c, "daily_outfit_photo_prompt", "")
        self.enable_natural_language_photo_generation = self._cfg_bool(c, "enable_natural_language_photo_generation", False)
        self.natural_language_photo_generation_max_daily = self._cfg_int(c, "natural_language_photo_generation_max_daily", 2, 0, 100)
        raw_natural_photo_extra = _flat_get(c, "natural_language_photo_extra_prompt", None)
        self.natural_language_photo_extra_prompt = (
            DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT
            if raw_natural_photo_extra is None
            else str(raw_natural_photo_extra).strip()
        )
        self.enable_weather_context = self._cfg_bool(c, "enable_weather_context", True)
        self.weather_api_key = self._cfg_str(c, "weather_api_key", "")
        self.weather_city = self._cfg_str(c, "weather_city", "")
        self.weather_lat = self._cfg_float(c, "weather_lat", 0.0, -90.0)
        self.weather_lon = self._cfg_float(c, "weather_lon", 0.0, -180.0)
        self.weather_refresh_minutes = self._cfg_int(c, "weather_refresh_minutes", 90, 10, 720)
        self.enable_yesterday_screen_diary_context = self._cfg_bool(c, "enable_yesterday_screen_diary_context", True)
        self.screen_diary_context_max_chars = self._cfg_int(c, "screen_diary_context_max_chars", 700, 200, 1600)
        self.detail_enhancement_lead_minutes = self._cfg_int(c, "detail_enhancement_lead_minutes", 3, 0, 180)
        self.enable_daily_diary = self._cfg_bool(c, "enable_daily_diary", True)
        self.daily_diary_time = self._cfg_str(c, "daily_diary_time", "23:10")
        self.max_diary_entries = self._cfg_int(c, "max_diary_entries", 14, 1, 60)
        self.important_date_lookahead_days = self._cfg_int(c, "important_date_lookahead_days", 7, 0, 60)
        legacy_actions = self._parse_action_list(self._cfg_raw(c, "enabled_proactive_actions", None))
        legacy_photo_enabled = "photo_text" in legacy_actions if legacy_actions else True
        legacy_screen_enabled = "screen_peek" in legacy_actions if legacy_actions else False
        legacy_poke_enabled = "poke" in legacy_actions if legacy_actions else False
        legacy_voice_enabled = "voice" in legacy_actions if legacy_actions else False
        self.enable_photo_text_action = self._cfg_bool(
            c, "enable_photo_text_action", bool(self._cfg_raw(c, "allow_photo_text_action", legacy_photo_enabled))
        )
        self.enable_screen_glance_action = self._cfg_bool(
            c, "enable_screen_glance_action", bool(self._cfg_raw(c, "allow_screen_peek_action", legacy_screen_enabled))
        )
        self.enable_poke_action = self._cfg_bool(
            c, "enable_poke_action", bool(self._cfg_raw(c, "allow_poke_action", legacy_poke_enabled))
        )
        self.enable_voice_action = self._cfg_bool(
            c, "enable_voice_action", bool(self._cfg_raw(c, "allow_voice_action", legacy_voice_enabled))
        )
        self.enable_qq_presence_sync = self._cfg_bool(c, "enable_qq_presence_sync", True)
        self.enable_qq_custom_presence_sync = self._cfg_bool(c, "enable_qq_custom_presence_sync", False)
        self.poke_action_max_times = self._cfg_int(c, "poke_action_max_times", 1, 1, 3)
        self.voice_action_max_chars = self._cfg_int(c, "voice_action_max_chars", 30, 6, 80)
        self.photo_action_max_daily = self._cfg_int(c, "photo_action_max_daily", 1, 0, 5)
        self.proactive_photo_text_probability = self._cfg_int(c, "proactive_photo_text_probability", 18, 0, 100) / 100
        self.screen_peek_max_daily = self._cfg_int(c, "screen_peek_max_daily", 1, 0, 5)
        self.screen_peek_cooldown_minutes = self._cfg_int(c, "screen_peek_cooldown_minutes", 240, 0, 1440)
        self.enable_unanswered_screen_peek_followup = self._cfg_bool(c, "enable_unanswered_screen_peek_followup", True)
        self.unanswered_screen_peek_after_minutes = self._cfg_int(c, "unanswered_screen_peek_after_minutes", 45, 10, 240)
        self.unanswered_screen_peek_cooldown_minutes = self._cfg_int(c, "unanswered_screen_peek_cooldown_minutes", 180, 30, 1440)
        self.enable_mai_style_integration = self._cfg_bool(c, "enable_mai_style_integration", True)
        self.enable_companion_memory = self._cfg_bool(c, "enable_companion_memory", True)
        self.enable_expression_learning = self._cfg_bool(c, "enable_expression_learning", True)
        self.expression_learning_mode = self._cfg_str(c, "expression_learning_mode", "balanced", "balanced").lower()
        if self.expression_learning_mode not in {"light", "balanced", "aggressive"}:
            self.expression_learning_mode = "balanced"
        self.enable_expression_manual_review = self._cfg_bool(c, "enable_expression_manual_review", False)
        self.enable_expression_style_review = self._cfg_bool(c, "enable_expression_style_review", True)
        self.enable_intent_emotion_analysis = self._cfg_bool(c, "enable_intent_emotion_analysis", True)
        self.enable_response_self_review = self._cfg_bool(c, "enable_response_self_review", True)
        self.response_review_mode = self._cfg_str(c, "response_review_mode", "severe_only", "severe_only").lower()
        if self.response_review_mode not in {"local_only", "severe_only", "full"}:
            self.response_review_mode = "severe_only"
        self.enable_smart_silence = self._cfg_bool(c, "enable_smart_silence", True)
        self.smart_silence_provider_id = self._cfg_str(c, "SMART_SILENCE_PROVIDER_ID", "")
        self.smart_silence_min_confidence = self._cfg_unit_interval(c, "smart_silence_min_confidence", 0.66, 0.0)
        self.smart_silence_model_timeout_seconds = self._cfg_float(c, "smart_silence_model_timeout_seconds", 1.2, 0.2)
        self._smart_silence_cache: dict[str, dict[str, Any]] = {}
        self.proactive_review_strength = self._cfg_str(c, "proactive_review_strength", "lenient", "lenient").lower()
        if self.proactive_review_strength not in {"lenient", "balanced", "strict"}:
            self.proactive_review_strength = "lenient"
        self.proactive_review_hard_risk_threshold = self._cfg_unit_interval(c, "proactive_review_hard_risk_threshold", 0.70, 0.0)
        self.proactive_review_low_score_threshold = self._cfg_unit_interval(c, "proactive_review_low_score_threshold", 0.34, 0.0)
        self.proactive_review_pressure_threshold = self._cfg_unit_interval(c, "proactive_review_pressure_threshold", 0.55, 0.0)
        self.enable_passive_topic_suppression = self._cfg_bool(c, "enable_passive_topic_suppression", True)
        self.enable_relationship_state_machine = self._cfg_bool(c, "enable_relationship_state_machine", True)
        self.enable_emotion_simulation = self._cfg_bool(c, "enable_emotion_simulation", True)
        self.enable_llm_emotion_judgement = self._cfg_bool(c, "enable_llm_emotion_judgement", False)
        self.emotion_judgement_mode = self._cfg_str(c, "emotion_judgement_mode", "suspicious", "suspicious").lower()
        if self.emotion_judgement_mode not in {"suspicious", "always", "off"}:
            self.emotion_judgement_mode = "suspicious"
        self.emotional_gate_hurt_threshold = self._cfg_int(c, "emotional_gate_hurt_threshold", 70, 10, 100)
        if self.emotional_gate_hurt_threshold == 55:
            self.emotional_gate_hurt_threshold = 70
            _set_into_config(c, "emotional_gate_hurt_threshold", self.emotional_gate_hurt_threshold)
        self.emotional_gate_refuse_threshold = self._cfg_int(c, "emotional_gate_refuse_threshold", 90, 20, 100)
        if self.emotional_gate_refuse_threshold == 80:
            self.emotional_gate_refuse_threshold = 90
            _set_into_config(c, "emotional_gate_refuse_threshold", self.emotional_gate_refuse_threshold)
        if self.emotional_gate_refuse_threshold <= self.emotional_gate_hurt_threshold:
            self.emotional_gate_refuse_threshold = min(100, self.emotional_gate_hurt_threshold + 5)
        self.emotional_gate_recovery_per_hour = self._cfg_int(c, "emotional_gate_recovery_per_hour", 24, 1, 60)
        if self.emotional_gate_recovery_per_hour == 12:
            self.emotional_gate_recovery_per_hour = 24
            _set_into_config(c, "emotional_gate_recovery_per_hour", self.emotional_gate_recovery_per_hour)
        self.emotional_gate_max_hurt_minutes = self._cfg_int(c, "emotional_gate_max_hurt_minutes", 90, 10, 720)
        if self.emotional_gate_max_hurt_minutes == 180:
            self.emotional_gate_max_hurt_minutes = 90
            _set_into_config(c, "emotional_gate_max_hurt_minutes", self.emotional_gate_max_hurt_minutes)
        self.enable_dialogue_episode_memory = self._cfg_bool(c, "enable_dialogue_episode_memory", True)
        self.enable_open_loop_tracking = self._cfg_bool(c, "enable_open_loop_tracking", True)
        self.enable_user_habit_learning = self._cfg_bool(c, "enable_user_habit_learning", True)
        self.enable_food_menu_recommendation = self._cfg_bool(c, "enable_food_menu_recommendation", True)
        self.user_habit_min_count = self._cfg_int(c, "user_habit_min_count", 3, 2, 20)
        self.user_habit_max_items = self._cfg_int(c, "user_habit_max_items", 24, 8, 80)
        self.enable_skill_growth_simulation = self._cfg_bool(c, "enable_skill_growth_simulation", True)
        self.skill_growth_rate = self._cfg_float(c, "skill_growth_rate", 1.0, 0.1)
        self.skill_growth_custom_skills = self._cfg_str(c, "skill_growth_custom_skills", "")
        self.enable_skill_growth_passive_injection = self._cfg_bool(c, "enable_skill_growth_passive_injection", False)
        self.enable_skill_growth_schedule_influence = self._cfg_bool(c, "enable_skill_growth_schedule_influence", True)
        self.skill_growth_schedule_influence_strength = self._cfg_unit_interval(c, "skill_growth_schedule_influence_strength", 0.35, 0.0)
        self.memory_refresh_interval_minutes = self._cfg_int(c, "memory_refresh_interval_minutes", 360, 30, 4320)
        self.max_companion_memory_items = self._cfg_int(c, "max_companion_memory_items", 36, 8, 120)
        self.max_learned_expression_items = self._cfg_int(c, "max_learned_expression_items", 18, 4, 60)
        self.mai_style_provider_id = self._cfg_str(c, "MAI_STYLE_PROVIDER_ID", "")
        self.companion_memory_provider_id = self._cfg_str(c, "COMPANION_MEMORY_PROVIDER_ID", "")
        self.dialogue_episode_provider_id = self._cfg_str(c, "DIALOGUE_EPISODE_PROVIDER_ID", "")
        self.relationship_analysis_provider_id = self._cfg_str(c, "RELATIONSHIP_ANALYSIS_PROVIDER_ID", "")
        self.response_review_provider_id = self._cfg_str(c, "RESPONSE_REVIEW_PROVIDER_ID", "")
        self.troubleshooting_provider_id = self._cfg_str(c, "TROUBLESHOOTING_PROVIDER_ID", "")
        self.emotion_judgement_provider_id = self._cfg_str(c, "EMOTION_JUDGEMENT_PROVIDER_ID", "")
        self.response_review_max_chars = self._cfg_int(c, "response_review_max_chars", 260, 80, 900)
        self.passive_topic_memory_hours = self._cfg_int(c, "passive_topic_memory_hours", 8, 1, 72)
        self.episode_memory_refresh_messages = self._cfg_int(c, "episode_memory_refresh_messages", 8, 3, 40)
        self.episode_memory_refresh_minutes = self._cfg_int(c, "episode_memory_refresh_minutes", 90, 15, 1440)
        self.max_dialogue_episodes = self._cfg_int(c, "max_dialogue_episodes", 12, 3, 40)
        self.enable_group_companion = self._cfg_bool(c, "enable_group_companion", True)
        self.group_access_mode = self._cfg_str(c, "group_access_mode", "whitelist", "whitelist").lower()
        if self.group_access_mode not in {"whitelist", "blacklist"}:
            self.group_access_mode = "whitelist"
        self.target_group_ids = self._cfg_raw(c, "target_group_ids", [])
        self.group_whitelist_ids = self._cfg_raw(c, "group_whitelist_ids", self.target_group_ids)
        self.group_blacklist_ids = self._cfg_raw(c, "group_blacklist_ids", [])
        self.require_target_group = self._cfg_bool(c, "require_target_group", True)
        self.enable_group_slang_learning = self._cfg_bool(c, "enable_group_slang_learning", True)
        self.enable_group_member_profiles = self._cfg_bool(c, "enable_group_member_profiles", True)
        self.enable_group_context_injection = self._cfg_bool(c, "enable_group_context_injection", True)
        self.enable_group_injection_guard = self._cfg_bool(c, "enable_group_injection_guard", True)
        self.enable_group_persona_denoise = self._cfg_bool(c, "enable_group_persona_denoise", True)
        self.enable_forward_message_adaptation = self._cfg_bool(c, "enable_forward_message_adaptation", True)
        self.forward_message_mode = self._cfg_str(c, "forward_message_mode", "inject", "inject").lower()
        if self.forward_message_mode in {"注入", "injection"}:
            self.forward_message_mode = "inject"
        elif self.forward_message_mode in {"转述", "summary", "summarize", "narrate", "relay"}:
            self.forward_message_mode = "transcribe"
        elif self.forward_message_mode not in {"inject", "transcribe"}:
            self.forward_message_mode = "inject"
        self.forward_message_provider_id = self._cfg_str(c, "FORWARD_MESSAGE_PROVIDER_ID", "")
        self.forward_message_max_messages = self._cfg_int(c, "forward_message_max_messages", 80, 5, 300)
        self.forward_message_max_chars = self._cfg_int(c, "forward_message_max_chars", 5000, 800, 20000)
        self.forward_message_parse_nested = self._cfg_bool(c, "forward_message_parse_nested", True)
        self.forward_message_image_vision = self._cfg_bool(c, "forward_message_image_vision", True)
        self.forward_message_image_limit = self._cfg_int(c, "forward_message_image_limit", 4, 0, 12)
        self.forward_message_image_vision_timeout_seconds = self._cfg_float(c, "forward_message_image_vision_timeout_seconds", 6.0, 0.0)
        self.enable_group_scene_awareness = self._cfg_bool(c, "enable_group_scene_awareness", True)
        self.group_scene_recent_limit = self._cfg_int(c, "group_scene_recent_limit", 5, 2, 12)
        self.enable_group_reality_promise_guard = self._cfg_bool(c, "enable_group_reality_promise_guard", True)
        self.enable_group_wakeup_enhancement = self._cfg_bool(c, "enable_group_wakeup_enhancement", True)
        self.group_wakeup_direct_words = self._parse_text_list_config(self._cfg_raw(c, "group_wakeup_direct_words", []))
        self.group_wakeup_context_words = self._parse_text_list_config(
            self._cfg_raw(c, "group_wakeup_context_words", ["机器人", "bot"])
        )
        if self.group_wakeup_context_words == ["有人叫你", "提到你", "说到你", "机器人", "AI"]:
            self.group_wakeup_context_words = ["机器人", "bot"]
        self.group_wakeup_interest_keywords = self._parse_text_list_config(self._cfg_raw(c, "group_wakeup_interest_keywords", []))
        self.group_wakeup_interest_probability = self._cfg_int(c, "group_wakeup_interest_probability", 18, 0, 100) / 100
        self.enable_group_wakeup_question = self._cfg_bool(c, "enable_group_wakeup_question", True)
        self.group_wakeup_question_threshold = self._cfg_int(c, "group_wakeup_question_threshold", 65, 0, 100)
        self.enable_group_wakeup_cold_group = self._cfg_bool(c, "enable_group_wakeup_cold_group", False)
        self.group_wakeup_cold_group_threshold = self._cfg_int(c, "group_wakeup_cold_group_threshold", 65, 0, 100)
        self.group_wakeup_cold_group_idle_minutes = self._cfg_int(c, "group_wakeup_cold_group_idle_minutes", 25, 3, 720)
        self.group_wakeup_cooldown_seconds = self._cfg_int(c, "group_wakeup_cooldown_seconds", 90, 0, 3600)
        self.group_wakeup_generated_keyword_limit = self._cfg_int(c, "group_wakeup_generated_keyword_limit", 24, 4, 80)
        self.group_wakeup_topic_interest_max_boost = self._cfg_int(c, "group_wakeup_topic_interest_max_boost", 45, 0, 150) / 100
        self.group_wakeup_debounce_pending_penalty = self._cfg_int(c, "group_wakeup_debounce_pending_penalty", 65, 0, 100) / 100
        self.group_wakeup_fatigue_limit = self._cfg_int(c, "group_wakeup_fatigue_limit", 5, 1, 20)
        self.group_wakeup_fatigue_decay_minutes = self._cfg_int(c, "group_wakeup_fatigue_decay_minutes", 90, 5, 720)
        self.group_wakeup_log_limit = self._cfg_int(c, "group_wakeup_log_limit", 80, 10, 300)
        self.group_wakeup_short_text_wait_seconds = self._cfg_float(c, "group_wakeup_short_text_wait_seconds", 15.0, 0.0)
        self.enable_group_high_intensity_mode = self._cfg_bool(c, "enable_group_high_intensity_mode", True)
        self.group_high_intensity_wakeup_window_seconds = self._cfg_int(c, "group_high_intensity_wakeup_window_seconds", 60, 15, 600)
        self.group_high_intensity_wakeup_threshold = self._cfg_int(c, "group_high_intensity_wakeup_threshold", 3, 2, 20)
        self.group_high_intensity_cooldown_seconds = self._cfg_int(c, "group_high_intensity_cooldown_seconds", 150, 30, 1800)
        self.group_high_intensity_merge_seconds = self._cfg_int(c, "group_high_intensity_merge_seconds", 8, 1, 30)
        self.group_high_intensity_max_merge_messages = self._cfg_int(c, "group_high_intensity_max_merge_messages", 8, 0, 50)
        self.group_high_intensity_merge_scope = self._cfg_str(c, "group_high_intensity_merge_scope", "group", "group").lower()
        if self.group_high_intensity_merge_scope in {"sender", "same_sender", "same_user", "user"}:
            self.group_high_intensity_merge_scope = "same_user"
        elif self.group_high_intensity_merge_scope not in {"group", "same_user"}:
            self.group_high_intensity_merge_scope = "group"
        self.enable_group_interjection = self._cfg_bool(c, "enable_group_interjection", False)
        self.enable_group_repeat_follow = self._cfg_bool(c, "enable_group_repeat_follow", True)
        self.group_repeat_trigger_threshold = self._cfg_int(c, "group_repeat_trigger_threshold", 4, 3, 20)
        self.group_repeat_count_distinct_users_only = self._cfg_bool(c, "group_repeat_count_distinct_users_only", False)
        self.group_repeat_follow_probability = self._cfg_int(c, "group_repeat_follow_probability", 18, 0, 100) / 100
        self.group_repeat_interrupt_probability = self._cfg_int(c, "group_repeat_interrupt_probability", 10, 0, 100) / 100
        self.group_repeat_interrupt_probability_step = self._cfg_int(c, "group_repeat_interrupt_probability_step", 12, 0, 100) / 100
        self.group_repeat_interrupt_text = self._cfg_str(c, "group_repeat_interrupt_text", "禁止复读", "禁止复读")
        self.group_repeat_interrupt_image_path = self._cfg_str(c, "group_repeat_interrupt_image_path", "")
        self.group_interject_min_interval_minutes = self._cfg_int(c, "group_interject_min_interval_minutes", 180, 10, 1440)
        self.group_interject_max_daily = self._cfg_int(c, "group_interject_max_daily", 2, 0, 12)
        self.max_group_recent_messages = self._cfg_int(c, "max_group_recent_messages", 80, 20, 300)
        self.max_group_slang_terms = self._cfg_int(c, "max_group_slang_terms", 40, 8, 160)
        self.enable_group_topic_threads = self._cfg_bool(c, "enable_group_topic_threads", True)
        self.enable_group_episode_memory = self._cfg_bool(c, "enable_group_episode_memory", True)
        self.enable_group_interjection_feedback = self._cfg_bool(c, "enable_group_interjection_feedback", True)
        self.enable_group_slang_meanings = self._cfg_bool(c, "enable_group_slang_meanings", True)
        self.enable_group_slang_web_search = self._cfg_bool(c, "enable_group_slang_web_search", False)
        self.group_slang_web_search_terms = self._cfg_int(c, "group_slang_web_search_terms", 4, 1, 12)
        self.group_slang_web_search_results = self._cfg_int(c, "group_slang_web_search_results", 2, 1, 5)
        self.enable_group_relationship_graph = self._cfg_bool(c, "enable_group_relationship_graph", True)
        self.enable_group_privacy_guard = self._cfg_bool(c, "enable_group_privacy_guard", True)
        self.enable_worldbook_member_recognition = self._cfg_bool(c, "enable_worldbook_member_recognition", True)
        self.enable_atrelay_tools = self._cfg_bool(c, "enable_atrelay_tools", True)
        self.enable_cross_user_memory_bridge = self._cfg_bool(c, "enable_cross_user_memory_bridge", False)
        self.cross_user_memory_owner_only = self._cfg_bool(c, "cross_user_memory_owner_only", True)
        self.atrelay_require_worldbook_first = self._cfg_bool(c, "atrelay_require_worldbook_first", True)
        self.atrelay_member_cache_minutes = self._cfg_int(c, "atrelay_member_cache_minutes", 60, 1, 1440)
        self.atrelay_sensitive_confirm = self._cfg_bool(c, "atrelay_sensitive_confirm", True)
        self.enable_atrelay_llm_rewrite = self._cfg_bool(c, "enable_atrelay_llm_rewrite", True)
        self.atrelay_default_relay_style = self._cfg_str(c, "atrelay_default_relay_style", "persona", "persona")
        self.atrelay_multi_target_limit = self._cfg_int(c, "atrelay_multi_target_limit", 5, 1, 20)
        self.worldbook_auto_import = self._cfg_bool(c, "worldbook_auto_import", True)
        self.worldbook_member_match_aliases = self._cfg_bool(c, "worldbook_member_match_aliases", True)
        self.worldbook_self_registration = self._cfg_bool(c, "worldbook_self_registration", True)
        self.worldbook_self_registration_block_words = self._parse_text_list_config(
            self._cfg_raw(c, "worldbook_self_registration_block_words", []),
            limit=120,
        )
        self.worldbook_self_registration_block_reply = self._cfg_str(
            c,
            "worldbook_self_registration_block_reply",
            "这个称呼我不记。",
        )
        if self.worldbook_self_registration_block_reply in {"这个称呼我先不记。", "你是小猪"}:
            self.worldbook_self_registration_block_reply = "这个称呼我不记。"
            _set_into_config(c, "worldbook_self_registration_block_reply", self.worldbook_self_registration_block_reply)
        self.worldbook_auto_pending_observations = self._cfg_bool(c, "worldbook_auto_pending_observations", True)
        self.worldbook_member_inject_limit = self._cfg_int(c, "worldbook_member_inject_limit", 6, 1, 20)
        self.worldbook_config_paths = self._cfg_str(c, "worldbook_config_paths", "")
        self.group_interject_provider_id = self._cfg_str(c, "GROUP_INTERJECT_PROVIDER_ID", "")
        self.group_episode_provider_id = self._cfg_str(c, "GROUP_EPISODE_PROVIDER_ID", "")
        self.group_slang_provider_id = self._cfg_str(c, "GROUP_SLANG_PROVIDER_ID", "")
        self.group_followup_judge_provider_id = self._cfg_str(c, "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID", "")
        self.enable_livingmemory_integration = self._cfg_bool(c, "enable_livingmemory_integration", True)
        self.livingmemory_tool_name = self._cfg_str(c, "livingmemory_tool_name", "recall_long_term_memory", "recall_long_term_memory")
        self.enable_bilibili_integration = self._cfg_bool(c, "enable_bilibili_integration", True)
        self.enable_bilibili_boredom_watch = self._cfg_bool(c, "enable_bilibili_boredom_watch", True)
        self.bilibili_boredom_min_interval_hours = self._cfg_int(c, "bilibili_boredom_min_interval_hours", 8, 2, 72)
        self.bilibili_share_probability = self._cfg_unit_interval(c, "bilibili_share_probability", 0.35, 0.0)
        self.bilibili_share_min_score = self._cfg_int(c, "bilibili_share_min_score", 7, 0, 10)
        self.enable_news_integration = self._cfg_bool(c, "enable_news_integration", False)
        self.enable_news_boredom_read = self._cfg_bool(c, "enable_news_boredom_read", True)
        self.enable_news_daily_hot_read = self._cfg_bool(c, "enable_news_daily_hot_read", self._cfg_bool(c, "enable_hot_trend_sources", True))
        self.news_min_interval_hours = self._cfg_int(c, "news_min_interval_hours", 6, 1, 72)
        self.news_share_probability = self._cfg_unit_interval(c, "news_share_probability", 0.22, 0.0)
        self.enable_external_event_self_link = self._cfg_bool(c, "enable_external_event_self_link", True)
        self.external_event_self_link_probability = self._cfg_unit_interval(c, "external_event_self_link_probability", 0.62, 0.0)
        self.external_event_self_link_cooldown_hours = self._cfg_int(c, "external_event_self_link_cooldown_hours", 12, 1, 168)
        self.news_max_items_per_source = self._cfg_int(c, "news_max_items_per_source", 5, 1, 20)
        self.news_hot_sources = self._cfg_str(c, "news_hot_sources", self._cfg_str(c, "hot_trend_sources", "weibo,hackernews"))
        self.news_hot_max_items = self._cfg_int(c, "news_hot_max_items", self._cfg_int(c, "hot_trend_max_items", 12, 3, 30), 3, 30)
        self.enable_ai_daily_watch = self._cfg_bool(c, "enable_ai_daily_watch", True)
        self.ai_daily_sources = self._cfg_str(c, "ai_daily_sources", DEFAULT_AI_DAILY_SOURCES)
        self.ai_daily_source_uid = re.sub(r"\D+", "", self._cfg_str(c, "ai_daily_source_uid", "285286947")) or "285286947"
        self.ai_daily_check_window = self._cfg_str(c, "ai_daily_check_window", "07:30-12:30")
        self.ai_daily_check_interval_minutes = self._cfg_int(c, "ai_daily_check_interval_minutes", 40, 10, 240)
        self.ai_daily_prefer_text_version = self._cfg_bool(c, "ai_daily_prefer_text_version", True)
        self.news_sources = self._cfg_str(
            c,
            "news_sources",
            DEFAULT_NEWS_SOURCES,
        )
        if str(self.news_sources or "").strip() in {LEGACY_DEFAULT_NEWS_SOURCES, PREVIOUS_TECH_DEFAULT_NEWS_SOURCES}:
            self.news_sources = DEFAULT_NEWS_SOURCES
        self.news_provider_id = self._cfg_str(c, "NEWS_PROVIDER_ID", "")
        self.enable_web_exploration = self._cfg_bool(c, "enable_web_exploration", False)
        self.enable_web_exploration_boredom_search = self._cfg_bool(c, "enable_web_exploration_boredom_search", True)
        self.web_exploration_min_interval_hours = self._cfg_int(c, "web_exploration_min_interval_hours", 8, 1, 168)
        self.web_exploration_share_probability = self._cfg_unit_interval(c, "web_exploration_share_probability", 0.18, 0.0)
        self.web_exploration_max_results = self._cfg_int(c, "web_exploration_max_results", 6, 3, 20)
        self.web_exploration_interests = self._cfg_str(
            c,
            "web_exploration_interests",
            "按 Bot 人格自行决定；可偏向最近聊天、日程、人设兴趣、作品、技术、生活小知识、流行梗、时讯、新鲜事物。",
        )
        self.web_exploration_provider_id = self._cfg_str(c, "WEB_EXPLORATION_PROVIDER_ID", "")
        self.web_exploration_api_base_url = self._cfg_str(c, "WEB_EXPLORATION_API_BASE_URL", "")
        self.web_exploration_api_key = self._cfg_str(c, "WEB_EXPLORATION_API_KEY", "")
        self.web_exploration_api_model = self._cfg_str(c, "WEB_EXPLORATION_API_MODEL", "")
        self.enable_qzone_integration = self._cfg_bool(c, "enable_qzone_integration", True)
        self.qzone_cookie = self._cfg_str(c, "QZONE_COOKIE", "")
        self.enable_qzone_life_publish = self._cfg_bool(c, "enable_qzone_life_publish", False)
        self.qzone_life_publish_min_interval_hours = self._cfg_int(c, "qzone_life_publish_min_interval_hours", 24, 4, 168)
        self.qzone_life_publish_probability = self._cfg_unit_interval(c, "qzone_life_publish_probability", 0.18, 0.0)
        self.enable_qzone_generated_image_publish = self._cfg_bool(c, "enable_qzone_generated_image_publish", True)
        self.qzone_generated_image_probability = self._cfg_unit_interval(c, "qzone_generated_image_probability", 0.25, 0.0)
        self.enable_qzone_comment_inbox = self._cfg_bool(c, "enable_qzone_comment_inbox", False)
        self.qzone_comment_inbox_interval_minutes = self._cfg_int(c, "qzone_comment_inbox_interval_minutes", 60, 5, 1440)
        self.qzone_comment_inbox_recent_posts = self._cfg_int(c, "qzone_comment_inbox_recent_posts", 5, 1, 20)
        self.qzone_comment_inbox_max_replies_per_tick = self._cfg_int(c, "qzone_comment_inbox_max_replies_per_tick", 1, 1, 5)
        self.enable_qzone_emotional_vent_publish = self._cfg_bool(c, "enable_qzone_emotional_vent_publish", False)
        self.qzone_emotional_vent_threshold = self._cfg_int(c, "qzone_emotional_vent_threshold", 90, 40, 100)
        self.qzone_emotional_vent_cooldown_hours = self._cfg_int(c, "qzone_emotional_vent_cooldown_hours", 72, 4, 336)
        self.qzone_emotional_vent_probability = self._cfg_unit_interval(c, "qzone_emotional_vent_probability", 0.35, 0.0)
        self.enable_jm_cosmos_integration = self._cfg_bool(
            c,
            "enable_private_reading_integration",
            self._cfg_bool(c, "enable_jm_cosmos_integration", False),
        )
        self.enable_jm_cosmos_boredom_read = self._cfg_bool(
            c,
            "enable_private_reading_boredom_read",
            self._cfg_bool(c, "enable_jm_cosmos_boredom_read", False),
        )
        self.enable_private_reading_ask_recommendation = self._cfg_bool(
            c,
            "enable_private_reading_ask_recommendation",
            False,
        )
        self.jm_cosmos_min_interval_hours = self._cfg_int(
            c,
            "private_reading_min_interval_hours",
            self._cfg_int(c, "jm_cosmos_min_interval_hours", 18, 4, 168),
            4,
            168,
        )
        self.jm_cosmos_max_photo_count = self._cfg_int(
            c,
            "private_reading_max_photo_count",
            self._cfg_int(c, "jm_cosmos_max_photo_count", 60, 8, 120),
            8,
            120,
        )
        self.jm_cosmos_share_probability = self._cfg_unit_interval(
            c,
            "private_reading_share_probability",
            self._cfg_unit_interval(c, "jm_cosmos_share_probability", 0.18, 0.0),
            0.0,
        )
        self.private_reading_ask_probability = self._cfg_unit_interval(c, "private_reading_ask_probability", 0.16, 0.0)
        self.enable_private_reading_preference_influence = self._cfg_bool(
            c,
            "enable_private_reading_preference_influence",
            True,
        )
        self.private_reading_preference_min_ratings = self._cfg_int(
            c,
            "private_reading_preference_min_ratings",
            5,
            1,
            30,
        )
        self.private_reading_preference_max_terms = self._cfg_int(
            c,
            "private_reading_preference_max_terms",
            8,
            2,
            20,
        )
        self.jm_cosmos_default_keywords = self._cfg_str(
            c,
            "private_reading_default_keywords",
            self._cfg_str(c, "jm_cosmos_default_keywords", "纯爱,恋爱,同人"),
        )
        self.private_reading_blocked_tags = self._cfg_str(
            c,
            "private_reading_blocked_tags",
            self._cfg_str(c, "jm_cosmos_blocked_tags", "連載中,長篇,青年漫"),
        )
        self.plugin_vision_provider_id = self._cfg_str(c, "PLUGIN_VISION_PROVIDER_ID", "")
        self.jm_cosmos_vision_provider_id = self._cfg_str(
            c,
            "PRIVATE_READING_VISION_PROVIDER_ID",
            self._cfg_str(c, "JM_COSMOS_VISION_PROVIDER_ID", ""),
        )
        self._apply_quick_provider_defaults()
        if isinstance(c, dict):
            legacy_private_reading_keys = {
                "enable_jm_cosmos_integration": "enable_private_reading_integration",
                "enable_jm_cosmos_boredom_read": "enable_private_reading_boredom_read",
                "jm_cosmos_min_interval_hours": "private_reading_min_interval_hours",
                "jm_cosmos_max_photo_count": "private_reading_max_photo_count",
                "jm_cosmos_share_probability": "private_reading_share_probability",
                "jm_cosmos_default_keywords": "private_reading_default_keywords",
                "jm_cosmos_blocked_tags": "private_reading_blocked_tags",
                "JM_COSMOS_VISION_PROVIDER_ID": "PRIVATE_READING_VISION_PROVIDER_ID",
            }
            for old_key, new_key in legacy_private_reading_keys.items():
                old_value = self._cfg_raw(c, old_key, None)
                if old_value is not None:
                    if self._cfg_raw(c, new_key, None) is None:
                        _set_into_config(c, new_key, old_value)
                    c.pop(old_key, None)
        self.group_episode_refresh_minutes = self._cfg_int(c, "group_episode_refresh_minutes", 180, 30, 1440)
        self.group_slang_summary_minutes = self._cfg_int(c, "group_slang_summary_minutes", 360, 60, 2880)
        self.max_group_topic_threads = self._cfg_int(c, "max_group_topic_threads", 12, 3, 40)
        self.max_group_episodes = self._cfg_int(c, "max_group_episodes", 10, 3, 40)
        self.max_group_relationship_edges = self._cfg_int(c, "max_group_relationship_edges", 80, 10, 300)
        # Backward-compatible aliases for stored daily plans and older code paths.
        self.allow_photo_text_action = self.enable_photo_text_action
        self.allow_screen_peek_action = self.enable_screen_glance_action
        self.allow_poke_action = self.enable_poke_action
        self.allow_voice_action = self.enable_voice_action

        self._patch_livingmemory_processor_compat()
        self._report_integrated_feature_conflicts()
        self._data_lock = asyncio.Lock()
        self._conversation_db_lock = asyncio.Lock()
        self._framework_agent_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._default_persona_prompt_cache = ""
        self._default_persona_prompt_cache_at = 0.0
        self._default_persona_prompt_cache_umo = ""
        self._default_persona_prompt_cache_persona_id = ""
        self._default_persona_prompt_refresh_task: asyncio.Task | None = None
        self._passive_light_injection_cache: dict[str, Any] = {}
        self._passive_state_session_cache: dict[str, dict[str, Any]] = {}
        self._data_save_task: asyncio.Task | None = None
        self._data_save_dirty = False
        self._maintenance_failure_cooldowns: dict[str, dict[str, Any]] = {}
        self._framework_captured_send_cache: dict[str, list[Any]] = {}
        self._framework_session_locks: dict[str, asyncio.Lock] = {}
        self._segmented_reply_remainder_locks: dict[str, asyncio.Lock] = {}
        self._last_input_status_at: dict[str, float] = {}
        self._passive_input_status_tasks: dict[str, asyncio.Task] = {}
        self._recent_inbound_activity_by_scope: dict[str, dict[str, Any]] = {}
        self._startup_maintenance_task: asyncio.Task | None = None
        self._qzone_last_bot = None
        startup_load_started = time.perf_counter()
        self.data = self._load_data_sync()
        self._apply_tts_runtime_overrides()
        load_elapsed_ms = int((time.perf_counter() - startup_load_started) * 1000)
        if load_elapsed_ms > 1200:
            logger.warning("[PrivateCompanion] 启动读取数据耗时较高: elapsed=%sms", load_elapsed_ms)
        self.page_api = None
        self._register_page_api_if_available()

    def _sqlite_wal_candidate_paths(self) -> list[Path]:
        data_root = Path(get_astrbot_data_path())
        candidates = [
            data_root / "data_v4.db",
            data_root / "plugin_data" / "astrbot_plugin_livingmemory" / "conversations.db",
            data_root / "plugin_data" / "astrbot_plugin_livingmemory" / "livingmemory.db",
            data_root / "plugin_data" / "astrbot_plugin_livingmemory" / "livingmemory_graph_documents.db",
            data_root / "knowledge_base" / "kb.db",
        ]
        seen: set[str] = set()
        paths: list[Path] = []
        for path in candidates:
            try:
                resolved = str(path.resolve())
            except Exception:
                resolved = str(path)
            if resolved in seen or not path.exists() or not path.is_file():
                continue
            seen.add(resolved)
            paths.append(path)
        return paths

    def _apply_sqlite_wal_to_file(self, db_path: Path) -> str:
        conn = sqlite3.connect(str(db_path), timeout=15.0)
        try:
            conn.execute("PRAGMA busy_timeout=15000")
            mode_row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA wal_autocheckpoint=1000")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.commit()
            return str(mode_row[0] if mode_row else "")
        finally:
            conn.close()

    def _apply_sqlite_pragmas_to_dbapi_connection(self, dbapi_connection: Any) -> None:
        try:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA busy_timeout=15000")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA wal_autocheckpoint=1000")
            finally:
                cursor.close()
        except Exception:
            try:
                dbapi_connection.execute("PRAGMA busy_timeout=15000")
                dbapi_connection.execute("PRAGMA journal_mode=WAL")
                dbapi_connection.execute("PRAGMA synchronous=NORMAL")
                dbapi_connection.execute("PRAGMA wal_autocheckpoint=1000")
            except Exception:
                pass

    def _iter_possible_sqlalchemy_engines(self) -> list[Any]:
        roots = [
            getattr(self, "context", None),
            getattr(getattr(self, "context", None), "conversation_manager", None),
        ]
        engines: list[Any] = []
        seen_objects: set[int] = set()

        def _visit(obj: Any, depth: int = 0) -> None:
            if obj is None or depth > 3:
                return
            obj_id = id(obj)
            if obj_id in seen_objects:
                return
            seen_objects.add(obj_id)
            cls_name = obj.__class__.__name__.lower()
            module_name = str(getattr(obj.__class__, "__module__", "")).lower()
            if "sqlalchemy" in module_name and "engine" in cls_name:
                engines.append(obj)
            for attr in (
                "engine", "_engine", "async_engine", "_async_engine", "sync_engine",
                "db", "_db", "store", "_store", "session_maker", "_session_maker",
                "conversation_manager",
            ):
                try:
                    child = getattr(obj, attr, None)
                except Exception:
                    continue
                if child is not None and child is not obj:
                    _visit(child, depth + 1)

        for root in roots:
            _visit(root)
        unique: list[Any] = []
        seen_engines: set[int] = set()
        for engine in engines:
            target = getattr(engine, "sync_engine", engine)
            if id(target) in seen_engines:
                continue
            seen_engines.add(id(target))
            unique.append(target)
        return unique

    def _install_sqlite_wal_engine_hooks(self) -> int:
        try:
            from sqlalchemy import event as sqlalchemy_event
        except Exception:
            return 0
        installed = 0
        for engine in self._iter_possible_sqlalchemy_engines():
            if bool(getattr(engine, "_private_companion_sqlite_wal_hooked", False)):
                continue
            try:
                url = str(getattr(engine, "url", "") or "").lower()
                if url and "sqlite" not in url:
                    continue
            except Exception:
                pass

            def _on_connect(dbapi_connection, _connection_record, plugin_self=self):
                plugin_self._apply_sqlite_pragmas_to_dbapi_connection(dbapi_connection)

            try:
                sqlalchemy_event.listen(engine, "connect", _on_connect)
                setattr(engine, "_private_companion_sqlite_wal_hooked", True)
                installed += 1
            except Exception as exc:
                logger.debug("[PrivateCompanion] SQLite WAL engine hook 安装失败: %s", _single_line(exc, 120))
        return installed

    async def _apply_sqlite_wal_optimizations(self) -> None:
        applied: list[str] = []
        failed: list[str] = []
        for path in self._sqlite_wal_candidate_paths():
            try:
                mode = await asyncio.to_thread(self._apply_sqlite_wal_to_file, path)
                applied.append(f"{path.name}:{mode or 'unknown'}")
            except Exception as exc:
                failed.append(f"{path.name}:{_single_line(exc, 80)}")
        hooks = self._install_sqlite_wal_engine_hooks()
        if applied or hooks:
            logger.info(
                "[PrivateCompanion] SQLite WAL 并发优化已应用: files=%s engine_hooks=%s",
                "，".join(applied) or "无",
                hooks,
            )
        if failed:
            logger.warning("[PrivateCompanion] SQLite WAL 并发优化部分失败: %s", "；".join(failed))

    async def initialize(self):
        if not self.enabled:
            logger.info("[PrivateCompanion] 插件总开关已关闭,不启动主动消息循环")
            return
        self._log_registered_command_handlers()
        self._install_send_message_to_user_tool_sanitizer()
        self._schedule_default_persona_prompt_refresh()
        needs_startup_save = False
        async with self._data_lock:
            changed = False
            if self.default_enable_configured_targets:
                self._sync_configured_targets()
                changed = True
            recovered_troubleshooting = self._recover_stale_troubleshooting_proactive_plans()
            if recovered_troubleshooting:
                logger.info("[PrivateCompanion] 已恢复未完成的排障临时主动任务: %s", recovered_troubleshooting)
            if self._prime_enabled_user_schedules():
                changed = True
            if recovered_troubleshooting:
                changed = True
            if changed:
                needs_startup_save = True
        if needs_startup_save:
            self._schedule_data_save(delay=0.5)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._scheduler_loop())
            logger.info("[PrivateCompanion] 主动消息循环已启动")
        if self._startup_maintenance_task is None or self._startup_maintenance_task.done():
            self._startup_maintenance_task = asyncio.create_task(self._run_startup_background_maintenance())
        asyncio.create_task(self._reset_stale_qq_presence_if_needed())
        asyncio.create_task(self._startup_prepare_today())
        asyncio.create_task(self._refresh_passive_injection_cache())

    def _log_registered_command_handlers(self) -> None:
        expected = {
            "companion_command": "/陪伴(alias: /私聊陪伴, /主动陪伴)",
            "group_companion_command": "/陪伴群(alias: /群陪伴, /群聊陪伴)",
        }
        found: set[str] = set()
        try:
            for handler in star_handlers_registry:
                callback = getattr(handler, "handler", None) or getattr(handler, "func", None)
                handler_name = (
                    getattr(handler, "handler_name", "")
                    or getattr(handler, "name", "")
                    or getattr(callback, "__name__", "")
                )
                if handler_name in expected:
                    found.add(handler_name)
        except Exception as exc:
            logger.debug("[PrivateCompanion] 指令注册诊断失败: %s", _single_line(exc, 120))
            return
        registered = [expected[name] for name in expected if name in found]
        missing = [expected[name] for name in expected if name not in found]
        if registered:
            logger.info("[PrivateCompanion] AstrBot 指令已注册: %s", "；".join(registered))
        if missing:
            logger.warning("[PrivateCompanion] AstrBot 指令注册诊断未找到: %s", "；".join(missing))

    def _run_startup_data_maintenance_locked(self) -> bool:
        changed = False

        def run_step(label: str, func: Any) -> None:
            nonlocal changed
            started = time.perf_counter()
            try:
                if callable(func) and func():
                    changed = True
            except Exception as exc:
                logger.warning("[PrivateCompanion] 启动后台维护步骤失败: %s error=%s", label, _single_line(exc, 160))
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if elapsed_ms > 1200:
                logger.warning("[PrivateCompanion] 启动后台维护步骤耗时较高: step=%s elapsed=%sms", label, elapsed_ms)

        run_step("legacy_prompt_trace_cleanup", self._cleanup_legacy_proactive_prompt_traces)
        run_step("framework_meta_leak_cleanup", self._cleanup_framework_meta_leak_records)
        run_step("runtime_social_fact_sanitize", self._sanitize_runtime_social_facts_inplace)
        run_step("private_user_alias_merge", self._merge_private_user_alias_records)
        run_step("group_slang_cleanup", self._cleanup_all_group_slang_terms)
        run_step("recall_image_cache_cleanup", lambda: self._cleanup_recall_message_image_cache(force=True))

        def cleanup_groups() -> bool:
            groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
            if not isinstance(groups, dict):
                return False
            group_changed = False
            cleaner = getattr(self, "_cleanup_group_members", None)
            edge_cleaner = getattr(self, "_cleanup_group_relationship_edges", None)
            for raw_group in groups.values():
                if not isinstance(raw_group, dict):
                    continue
                if callable(cleaner) and cleaner(raw_group):
                    group_changed = True
                if callable(edge_cleaner) and edge_cleaner(raw_group):
                    group_changed = True
            return group_changed

        run_step("group_record_cleanup", cleanup_groups)

        if self.worldbook_auto_import:
            run_step("worldbook_auto_import", self._import_worldbook_entries_from_sources)
        return changed

    async def _run_startup_background_maintenance(self) -> None:
        await asyncio.sleep(0)
        started = time.perf_counter()
        try:
            if _safe_int(getattr(self, "_startup_config_migration_changes", 0), 0, 0) > 0:
                config_started = time.perf_counter()
                await asyncio.to_thread(self._save_config_if_possible)
                elapsed_ms = int((time.perf_counter() - config_started) * 1000)
                if elapsed_ms > 1200:
                    logger.warning("[PrivateCompanion] 启动后台配置保存耗时较高: elapsed=%sms", elapsed_ms)
            try:
                await asyncio.wait_for(self._apply_sqlite_wal_optimizations(), timeout=20)
            except asyncio.TimeoutError:
                logger.warning("[PrivateCompanion] SQLite WAL 后台优化超时,已跳过本轮启动优化")
            async with self._data_lock:
                if self._run_startup_data_maintenance_locked():
                    self._save_data_sync()
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if elapsed_ms > 1200:
                logger.info("[PrivateCompanion] 启动后台维护完成: elapsed=%sms", elapsed_ms)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[PrivateCompanion] 启动后台维护失败: %s", _single_line(exc, 160), exc_info=True)

    async def terminate(self):
        global _private_companion_plugin
        self._stop_event.set()

        async def cancel_task(task: Any, label: str, timeout: float = 3.0) -> None:
            if not isinstance(task, asyncio.Task) or task.done():
                return
            task.cancel()
            done, pending = await asyncio.wait({task}, timeout=timeout)
            if pending:
                logger.warning("[PrivateCompanion] 终止后台任务超时,继续卸载: task=%s", label)
                return
            for finished in done:
                try:
                    await finished
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.debug("[PrivateCompanion] 终止后台任务时收到异常: task=%s error=%s", label, _single_line(exc, 160))

        if self._task:
            await cancel_task(self._task, "proactive_scheduler")
        for task in list(self._passive_input_status_tasks.values()):
            if isinstance(task, asyncio.Task) and not task.done():
                task.cancel()
        self._passive_input_status_tasks.clear()
        startup_task = getattr(self, "_startup_maintenance_task", None)
        await cancel_task(startup_task, "startup_maintenance")
        save_task = getattr(self, "_data_save_task", None)
        await cancel_task(save_task, "delayed_data_save")
        try:
            await asyncio.wait_for(self._save_data_on_terminate(), timeout=3.0)
        except asyncio.TimeoutError:
            logger.warning("[PrivateCompanion] 终止时保存数据超时,已跳过最终保存以避免卡死卸载")
        if _private_companion_plugin is self:
            _private_companion_plugin = None

    async def _save_data_on_terminate(self) -> None:
        async with self._data_lock:
            self._save_data_sync()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10000)
    async def observe_recall_enhancement_events(self, event: AstrMessageEvent):
        """记录普通消息和 QQ/OneBot 撤回事件，用于撤回增强。"""
        self._qzone_note_event_bot(event)
        if not self.enabled:
            return
        self._note_inbound_activity_for_scope(event)
        if not self.enable_recall_enhancement:
            return
        raw = self._event_raw_payload(event)
        if raw.get("post_type") == "notice":
            notice_type = str(raw.get("notice_type") or "").strip()
            if notice_type not in {"friend_recall", "group_recall"}:
                return
            message_id = _single_line(raw.get("message_id") or raw.get("msg_id"), 120)
            if not message_id:
                return
            scope = _single_line(
                (f"group:{raw.get('group_id')}" if raw.get("group_id") else "")
                or (f"private:{raw.get('user_id')}" if raw.get("user_id") else "")
                or getattr(event, "unified_msg_origin", ""),
                160,
            )
            self._record_recalled_message_id(
                message_id,
                scope=scope,
                notice_type=notice_type,
                sender_id=_single_line(raw.get("user_id"), 80),
            )
            if notice_type == "friend_recall":
                recall_user_id = _single_line(raw.get("user_id"), 80)
                if recall_user_id:
                    self._stop_passive_input_status_loop(recall_user_id)
                    logger.info(
                        "[PrivateCompanion] 用户撤回消息，已停止私聊输入状态: user=%s message_id=%s",
                        recall_user_id,
                        message_id,
                    )
            logger.info(
                "[PrivateCompanion] 已记录消息撤回: notice=%s scope=%s message_id=%s",
                notice_type,
                scope or "-",
                message_id,
            )
            return

        await self._cache_message_for_recall(event)
        if not self.enable_forbidden_word_recall or not self._forbidden_recall_words():
            return
        message_id = self._event_message_id(event)
        if not message_id:
            return
        is_group = bool(self._extract_group_id_from_event(event))
        is_self = self._event_sender_id(event) and self._event_sender_id(event) == self._event_self_id(event)
        scope = self.recall_forbidden_scope
        if scope == "bot_only" and not is_self:
            return
        if scope == "group_only" and not is_group:
            return
        if scope == "bot_and_group" and not (is_self or is_group):
            return
        text = self._event_text_for_recall_cache(event, limit=2000)
        hit = self._forbidden_recall_hit(text)
        if not hit:
            return
        ok = await self._try_delete_message(event, message_id, reason=f"forbidden:{hit}")
        logger.info(
            "[PrivateCompanion] 违禁词撤回检查命中: scope=%s self=%s group=%s ok=%s word=%s message_id=%s",
            scope,
            is_self,
            is_group,
            ok,
            _single_line(hit, 40),
            message_id,
        )

    @filter.on_decorating_result()
    async def stop_passive_input_status_before_private_send(self, event: AstrMessageEvent):
        """LLM 回复进入发送前阶段时释放会话锁；私聊额外停止持续输入状态。"""
        if not self.enabled:
            return
        if bool(getattr(event, "is_private_chat", lambda: False)()):
            self._stop_passive_input_status_loop(event)
        self._release_framework_session_lock_for_event(event, label="decorating_result")

    @filter.on_decorating_result()
    async def strip_outbound_control_blocks_before_send(self, event: AstrMessageEvent):
        """发送前兜底清理内部控制块，避免 timer/TTSBLOCK 泄漏到聊天。"""
        if not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "llm_request"):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        changed = False
        protected_tts_tokens = getattr(event, "_private_companion_tts_block_tokens", None)
        preserve_private_tts_tokens = (
            bool(getattr(self, "enable_tts_enhancement", False))
            and isinstance(protected_tts_tokens, dict)
            and bool(protected_tts_tokens)
        )
        for comp in chain:
            if not isinstance(comp, Plain):
                continue
            original = str(getattr(comp, "text", "") or "")
            cleaned = _strip_outbound_control_blocks(
                original,
                preserve_private_tts_tokens=preserve_private_tts_tokens,
                allowed_private_tts_tokens=set(protected_tts_tokens.keys()) if isinstance(protected_tts_tokens, dict) else None,
            )
            if not bool(getattr(self, "enable_tts_enhancement", False)):
                cleaned = re.sub(r"</?t{2,}s\b[^>]*>", "", cleaned, flags=re.IGNORECASE).strip()
            if cleaned != original:
                changed = True
                try:
                    comp.text = cleaned
                except Exception:
                    pass
        if changed:
            logger.warning(
                "[PrivateCompanion] 发送前已清理内部控制标签: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )

    @filter.on_decorating_result()
    async def cancel_reply_if_trigger_recalled_before_send(self, event: AstrMessageEvent):
        """若触发/唤醒消息在回复发出前被撤回，则静默取消本次回复。"""
        if not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_recall_enhancement"):
            return
        recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event)
        if not recalled_message_id:
            return
        logger.info(
            "[PrivateCompanion] 触发消息已撤回或发送前不可见，取消本次发送: session=%s message_id=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            recalled_message_id,
        )
        self._record_passive_no_reply(
            event,
            source="撤回取消",
            reason="触发消息已撤回或发送前不可见",
            detail=str(recalled_message_id),
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    @filter.on_decorating_result()
    async def suppress_forbidden_outbound_before_send(self, event: AstrMessageEvent):
        """自己的待发送消息命中违禁词时，优先在发送前拦截。"""
        if not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_recall_enhancement"):
            return
        if not self.enable_recall_enhancement or not self.enable_forbidden_word_recall:
            return
        if not self._forbidden_recall_words():
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        text = self._chain_text_for_forbidden_recall(chain)
        hit = self._forbidden_recall_hit(text)
        if not hit:
            return
        logger.warning(
            "[PrivateCompanion] 待发送消息命中违禁词，已拦截发送: word=%s session=%s",
            _single_line(hit, 40),
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
        )
        self._record_passive_no_reply(
            event,
            source="发送前拦截",
            reason="待发送消息命中屏蔽词",
            detail=hit,
            reply_preview=text,
            level="warn",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    @filter.on_decorating_result()
    async def suppress_framework_error_leak_before_send(self, event: AstrMessageEvent):
        """避免 AstrBot/Core 的技术错误和工具循环摘要直接发进聊天。"""
        if not self.enabled:
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain or any(not isinstance(comp, Plain) for comp in chain):
            return
        text = "\n".join(str(getattr(comp, "text", "") or "") for comp in chain).strip()
        compact = text.lower()
        receipt_compact = re.sub(r"[\s。.!！?？,，；;:：]+", "", text)
        status_receipt_like = (
            len(receipt_compact) <= 28
            and any(token in receipt_compact for token in ("发送给用户", "发给用户", "发送给对方", "发给对方", "发出去了"))
            and any(token in receipt_compact for token in ("已", "已经", "完成", "成功"))
        )
        if receipt_compact in {
            "已发送",
            "发送成功",
            "发送完成",
            "发送完毕",
            "已成功发送",
            "消息已发送",
            "消息发送成功",
            "消息已发送会等对方回复",
            "messagesent",
            "sent",
        } or status_receipt_like or self._is_proactive_delivery_receipt_text(text) or re.fullmatch(r"(?i)message\s+sent\s+to\s+session\s+\S+", text):
            atrelay_result = getattr(event, "private_companion_atrelay_tool_result", None)
            if isinstance(atrelay_result, dict) and _single_line(atrelay_result.get("status"), 24) in {"success", "scheduled"}:
                final_reply = _single_line(atrelay_result.get("final_reply"), 80) or "说过啦。"
                reference = _single_line(atrelay_result.get("final_reply_reference"), 260)
                rewriter = getattr(self, "_rewrite_reference_reply_with_persona", None)
                if reference and callable(rewriter):
                    sender_id = ""
                    try:
                        sender_id = self._canonical_private_user_id(str(event.get_sender_id()))
                    except Exception:
                        try:
                            sender_id = str(event.get_sender_id())
                        except Exception:
                            sender_id = ""
                    users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
                    user = users.get(sender_id) if sender_id and isinstance(users, dict) and isinstance(users.get(sender_id), dict) else {}
                    rewritten = await rewriter(
                        reference,
                        scene="拦截工具发送状态后改成自然聊天回执",
                        user=user,
                        event=event,
                        fallback_text=final_reply,
                        task="atrelay_receipt_rewrite",
                        max_chars=70,
                        allow_fallback=True,
                        preserve_status=True,
                    )
                    if rewritten:
                        final_reply = rewritten
                logger.info(
                    "[PrivateCompanion] 工具发送回执已改为自然短句: before=%s after=%s",
                    _single_line(text, 120),
                    final_reply,
                )
                event.set_result(self._build_result_from_chain([Plain(final_reply)]))
                return
            companion_receipt = bool(
                getattr(event, "private_companion_proactive_framework", False)
                or getattr(event, "private_companion_force_sanitize_tools", False)
            )
            if not companion_receipt:
                logger.debug(
                    "[PrivateCompanion] 放行非陪伴插件工具回执: session=%s text=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    _single_line(text, 120),
                )
                return
            logger.warning(
                "[PrivateCompanion] 已拦截孤立工具发送回执外发: session=%s text=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(text, 120),
            )
            self._record_passive_no_reply(
                event,
                source="发送前拦截",
                reason="孤立工具发送回执被拦截",
                reply_preview=text,
                level="warn",
            )
            empty_result = self._build_result_from_chain([])
            try:
                empty_result.stop_event()
            except Exception:
                pass
            event.set_result(empty_result)
            event.stop_event()
            return
        error_markers = (
            "error occurred while processing agent request",
            "all chat models failed",
            "sqlite3.operationalerror",
            "database is locked",
            "sqlalche.me/e/20/e3q8",
            "model do not support image input",
            "image_url",
            "invalidparameter",
        )
        tool_loop_markers = (
            "trying to send messages",
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
        )
        marker_kind = "framework_error" if any(marker in compact for marker in error_markers) else ""
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        if not marker_kind and callable(meta_leak_checker) and meta_leak_checker(text):
            marker_kind = "tool_loop_summary"
        if not marker_kind and any(marker in compact for marker in tool_loop_markers):
            marker_kind = "tool_loop_summary"
        if not marker_kind:
            return
        logger.warning(
            "[PrivateCompanion] 已拦截框架异常文本外发: kind=%s session=%s preview=%s",
            marker_kind,
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            _single_line(text, 180),
        )
        self._record_passive_no_reply(
            event,
            source="发送前拦截",
            reason=f"框架异常文本外发被拦截:{marker_kind}",
            reply_preview=text,
            level="warn",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    @filter.on_decorating_result()
    async def suppress_group_question_wakeup_collision_reply(self, event: AstrMessageEvent):
        """答疑唤醒的群聊回复发送前复核，避免 Bot 碰瓷式插话。"""
        if not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_group_companion"):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        if not bool(getattr(self, "enable_response_self_review", True)):
            return
        if str(getattr(self, "response_review_mode", "severe_only") or "severe_only").strip().lower() == "local_only":
            return
        if bool(getattr(event, "_private_companion_group_question_review_done", False)):
            return
        setattr(event, "_private_companion_group_question_review_done", True)
        group_id = self._extract_group_id_from_event(event)
        if not group_id:
            return
        scene = getattr(event, "private_companion_group_scene", None)
        if not isinstance(scene, dict) or str(scene.get("trigger") or "") != "group_wakeup_question":
            return
        result = event.get_result()
        if result is None:
            return
        try:
            if hasattr(result, "is_llm_result") and not result.is_llm_result():
                return
        except Exception:
            pass
        chain = list(getattr(result, "chain", []) or [])
        if not chain:
            return
        reply_text = self._chain_text_for_forbidden_recall(chain, limit=600)
        if not reply_text:
            return
        try:
            review = await self._review_group_question_wakeup_reply_before_send(event, reply_text=reply_text)
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 群聊答疑回复发送前复核失败,默认放行: %s",
                _single_line(exc, 160),
            )
            return
        if str(review.get("decision") or "") != "drop":
            return
        logger.info(
            "[PrivateCompanion] 已拦截群聊答疑碰瓷回复: group=%s reason=%s text=%s",
            group_id,
            _single_line(review.get("reason"), 120),
            _single_line(reply_text, 160),
        )
        self._record_passive_no_reply(
            event,
            source="群聊答疑复核",
            reason=_single_line(review.get("reason"), 120) or "群聊答疑碰瓷回复被拦截",
            reply_preview=reply_text,
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    @filter.on_decorating_result()
    async def suppress_smart_silence_reply_before_send(self, event: AstrMessageEvent):
        """用户明确想停下当前话题时，用小模型决定是否静默取消待发送回复。"""
        if not self.enabled:
            return
        if (
            bool(getattr(self, "enable_response_self_review", True))
            and bool(getattr(event, "_private_companion_response_review_drop", False))
        ):
            logger.info("[PrivateCompanion] 回复复核去重发送前兜底拦截")
            self._record_passive_no_reply(
                event,
                source="回复复核去重",
                reason="最终回复与上一条 Bot 消息重复",
                level="info",
            )
            empty_result = self._build_result_from_chain([])
            try:
                empty_result.stop_event()
            except Exception:
                pass
            event.set_result(empty_result)
            event.stop_event()
            return
        if bool(getattr(event, "_private_companion_smart_silence_drop", False)):
            logger.info(
                "[PrivateCompanion] 智能沉默发送前兜底拦截: reason=%s",
                _single_line(getattr(event, "_private_companion_smart_silence_reason", ""), 120),
            )
            self._record_passive_no_reply(
                event,
                source="智能沉默",
                reason=_single_line(getattr(event, "_private_companion_smart_silence_reason", ""), 120) or "用户边界语义触发静默",
                level="info",
            )
            empty_result = self._build_result_from_chain([])
            try:
                empty_result.stop_event()
            except Exception:
                pass
            event.set_result(empty_result)
            event.stop_event()
            return
        if not bool(getattr(self, "enable_smart_silence", True)):
            return
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return
        except Exception:
            pass
        result = event.get_result()
        if result is None:
            return
        try:
            if hasattr(result, "is_llm_result") and not result.is_llm_result():
                return
        except Exception:
            pass
        chain = list(getattr(result, "chain", []) or [])
        if not chain or any(not isinstance(comp, Plain) for comp in chain):
            return
        reply_text = self._chain_text_for_forbidden_recall(chain, limit=600)
        if not reply_text:
            return
        inbound_text = _single_line(
            getattr(event, "private_companion_group_text", "") or getattr(event, "message_str", ""),
            260,
        )
        if not self._smart_silence_trigger_reason(inbound_text):
            return
        recent_context: list[str] = []
        group_id = self._extract_group_id_from_event(event)
        if group_id:
            group = self._get_group(group_id)
            sender_id = ""
            try:
                sender_id = str(event.get_sender_id())
            except Exception:
                sender_id = ""
            flow_formatter = getattr(self, "_format_group_recent_flow_for_review", None)
            recent_flow = (
                flow_formatter(group, sender_id=sender_id, text=inbound_text, max_lines=8, max_chars=1000)
                if callable(flow_formatter)
                else ""
            )
            for line in recent_flow.splitlines():
                line = _single_line(line, 140)
                if line.startswith("- "):
                    line = line[2:].strip()
                if line:
                    recent_context.append(line)
        try:
            decision = await self._decide_smart_silence(
                inbound_text=inbound_text,
                response_text=reply_text,
                user=None,
                session_kind="group" if group_id else "chat",
                recent_context=recent_context,
            )
        except Exception as exc:
            logger.info("[PrivateCompanion] 智能沉默发送前判定失败,默认放行: %s", _single_line(exc, 120))
            return
        if str(decision.get("decision") or "") != "silent":
            return
        logger.info(
            "[PrivateCompanion] 智能沉默已取消本轮群聊回复: group=%s reason=%s inbound=%s reply=%s",
            group_id or "-",
            _single_line(decision.get("reason"), 120),
            _single_line(inbound_text, 120),
            _single_line(reply_text, 140),
        )
        self._record_passive_no_reply(
            event,
            source="智能沉默",
            reason=_single_line(decision.get("reason"), 120) or "群聊边界语义触发静默",
            reply_preview=reply_text,
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    @filter.on_decorating_result()
    async def record_empty_passive_result_before_send(self, event: AstrMessageEvent):
        """发送前兜底记录空结果，避免被动不回复却没有排障原因。"""
        if not self.enabled:
            return
        if bool(getattr(event, "_private_companion_passive_no_reply_recorded", False)):
            return
        if bool(getattr(event, "private_companion_proactive_framework", False)):
            return
        result = event.get_result()
        if result is None:
            return
        chain = list(getattr(result, "chain", []) or [])
        if chain:
            return
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = False
        is_group = bool(self._extract_group_id_from_event(event))
        if not is_private and not is_group:
            return
        self._record_passive_no_reply(
            event,
            source="发送前检查",
            reason="发送前结果为空",
            level="info",
        )

    @filter.on_decorating_result()
    async def apply_tts_enhancement_before_send_hook(self, event: AstrMessageEvent):
        """发送前处理 TTS强化标签和自动语音转换。"""
        if self._proactive_only_blocks_passive_event(event, "enable_tts_enhancement"):
            return
        await self.apply_tts_enhancement_before_send(event)

    @filter.on_decorating_result()
    async def strip_group_internal_identity_anchors(self, event: AstrMessageEvent):
        """发送前清理群聊内部身份锚点，避免调试标记泄露到回复。"""
        if not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_group_companion"):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        if not self._extract_group_id_from_event(event):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        for comp in chain:
            if not isinstance(comp, Plain):
                continue
            original = str(getattr(comp, "text", "") or "")
            cleaned = self._strip_internal_identity_anchors(original)
            if cleaned != original:
                try:
                    comp.text = cleaned
                except Exception:
                    pass

    @filter.on_decorating_result()
    async def suppress_group_silent_control_reply(self, event: AstrMessageEvent):
        """模型输出“不回复”控制语时静默吞掉，避免把内部判断发到群里。"""
        if not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_group_companion"):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        if not self._extract_group_id_from_event(event):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain or any(not isinstance(comp, Plain) for comp in chain):
            return
        text = "".join(str(getattr(comp, "text", "") or "") for comp in chain).strip()
        if not self._is_silent_control_reply_text(text):
            return
        logger.info("[PrivateCompanion] 已静默吞掉群聊不回复控制语: %s", _single_line(text, 120))
        self._record_passive_no_reply(
            event,
            source="群聊静默",
            reason="模型输出不回复控制语",
            reply_preview=text,
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    async def _review_group_question_wakeup_reply_before_send(
        self,
        event: AstrMessageEvent,
        *,
        reply_text: str,
    ) -> dict[str, str]:
        provider_id = self._task_provider(self.response_review_provider_id, self.group_followup_judge_provider_id, self.mai_style_provider_id)
        if not provider_id:
            return {"decision": "send", "reason": "未配置复核模型"}
        scene = getattr(event, "private_companion_group_scene", None)
        if not isinstance(scene, dict):
            scene = {}
        group_id = self._extract_group_id_from_event(event)
        group = self._get_group(group_id) if group_id else {}
        inbound_text = _single_line(
            getattr(event, "private_companion_group_text", "") or getattr(event, "message_str", "") or "",
            220,
        )
        sender_id = ""
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        flow_formatter = getattr(self, "_format_group_recent_flow_for_review", None)
        recent_flow = (
            flow_formatter(group, sender_id=sender_id, text=inbound_text, max_lines=12, max_chars=1400)
            if callable(flow_formatter)
            else ""
        )
        wakeup = group.get("last_group_wakeup") if isinstance(group.get("last_group_wakeup"), dict) else {}
        prompt = f"""
判断这条群聊回复是否应该在发送前拦截。

只输出 JSON 对象，不要解释。

可选 decision：
- send：确实是在自然回答群里的公共求助/开放问题，可以发送。
- drop：像 Bot 碰瓷插话，或问题明显是在接群友的话、问别人、吐槽/反问，不该发送。

判断标准：
- 没有明确 @ Bot 或引用 Bot 时，要更保守。
- 如果触发句只是“为什么/啥情况/怎么回事/不会吧？”这类接话、吐槽、反问，通常 drop。
- 如果是“有没有人懂/谁会/求问/报错/怎么解决/帮忙”这类公共求助，通常 send。
- 如果待发送内容虽然正确，但当前群聊并不需要 Bot 插入，也应 drop。

【本轮群唤醒】
trigger={_single_line(scene.get('trigger'), 40)} reason={_single_line(scene.get('reason'), 60)}
wakeup_type={_single_line(wakeup.get('type'), 40)} score={_single_line(wakeup.get('score'), 20)}/{_single_line(wakeup.get('threshold'), 20)} detail={_single_line(wakeup.get('reason_detail'), 160)}

【真实最近群聊】
{recent_flow or "（无）"}

【触发消息】
{inbound_text}

【待发送回复】
{_single_line(reply_text, 360)}

请输出：
{{"decision":"send|drop","reason":"一句很短的原因"}}
""".strip()
        started = time.perf_counter()
        raw = await self._llm_call(
            prompt,
            max_tokens=120,
            provider_id=provider_id,
            task="group_question_wakeup_reply_review",
        )
        payload = self._parse_json_object(raw)
        decision = str((payload or {}).get("decision") or "").strip().lower()
        reason = _single_line((payload or {}).get("reason"), 120)
        if decision not in {"send", "drop"}:
            decision = "send"
            reason = reason or "复核输出不可解析，默认放行"
        logger.info(
            "[PrivateCompanion] 群聊答疑回复发送前复核: decision=%s elapsed=%dms reason=%s trigger=%s text=%s",
            decision,
            int((time.perf_counter() - started) * 1000),
            reason,
            _single_line(scene.get("trigger"), 40),
            _single_line(reply_text, 140),
        )
        if recent_flow:
            logger.info(
                "[PrivateCompanion] 群聊答疑复核已附带真实群聊上下文: group=%s lines=%s chars=%s",
                group_id or "-",
                len([line for line in recent_flow.splitlines() if line.strip()]),
                len(recent_flow),
            )
        return {"decision": decision, "reason": reason}

    @filter.on_decorating_result()
    async def apply_segmented_llm_reply_scope(self, event: AstrMessageEvent):
        """按回复范围与分段策略整理 LLM 输出，减少长回复和误引用。"""
        if not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_segmented_proactive_reply"):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_segmented_proactive_reply"):
            return
        if self.segmented_proactive_scope != "all_llm":
            return
        if not self._segmented_scope_allows_event(event):
            return
        result = event.get_result()
        if result is None or not result.chain:
            return
        is_llm_result = False
        try:
            is_llm_result = bool(result.is_llm_result())
        except Exception:
            is_llm_result = False
        chain = list(result.chain or [])
        if not is_llm_result and not self._friend_private_plain_result_allows_segmenting(event, chain):
            return
        if getattr(result, "use_t2i_", None) or getattr(result, "use_markdown_", None):
            return
        if str(event.get_platform_name() or "") in {"qq_official", "weixin_official_account", "dingtalk"}:
            return
        if not chain:
            return
        chunks, changed, text = self._segment_llm_reply_chain(event, chain)
        if not chunks or not text:
            return
        if len(chunks) <= 1:
            if changed:
                event.set_result(self._build_result_from_chain(chunks[0]))
            return
        logger.debug("[PrivateCompanion] 按插件规则分段 LLM 回复: %s -> %s 段", len(text), len(chunks))
        logger.info(
            "[PrivateCompanion] 按插件规则分段 LLM 回复: segments=%s first=%s full=%s",
            len(chunks),
            _single_line(self._segmented_chunk_log_text(chunks[0]), 120),
            _single_line(text, 420),
        )
        plain_segments = self._plain_text_segments_from_chunks(chunks)
        if plain_segments and len(plain_segments) == len(chunks) and await self._send_segmented_event_forward_message(event, plain_segments, source="decorating_result"):
            empty_result = self._build_result_from_chain([])
            try:
                empty_result.stop_event()
            except Exception:
                pass
            event.set_result(empty_result)
            event.stop_event()
            return
        event.set_result(self._build_result_from_chain(chunks[0]))
        activity_baseline = time.time()
        if len(chunks) > 1:
            asyncio.create_task(
                self._send_segmented_llm_chain_remainder(
                    event,
                    chunks[1:],
                    previous_segment=self._segmented_chunk_log_text(chunks[0]),
                    source="decorating_result",
                    started_at=activity_baseline,
                )
            )

    @filter.on_decorating_result()
    async def remember_group_bot_reply_context_before_send(self, event: AstrMessageEvent):
        """记录群聊 Bot 实际候选回复，供下一轮连续对话判断使用。"""
        if not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_group_companion"):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id:
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        reply_text = self._chain_text_for_forbidden_recall(chain, limit=500)
        reply_text = _single_line(_strip_internal_message_blocks(reply_text), 260)
        if not reply_text:
            return
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        scene = getattr(event, "private_companion_group_scene", None)
        talking_to_bot = isinstance(scene, dict) and str(scene.get("talking_to") or "") == "bot"
        async with self._data_lock:
            group = self._get_group(group_id)
            active = self._group_active_conversation(group)
            if not talking_to_bot and str(active.get("sender_id") or "") != str(sender_id or ""):
                return
            active["last_bot_reply"] = reply_text
            active["last_bot_reply_ts"] = _now_ts()
            self._save_data_sync()

    def _friend_private_plain_result_allows_segmenting(self, event: AstrMessageEvent, chain: list[Any]) -> bool:
        try:
            if not bool(getattr(event, "is_private_chat", lambda: False)()):
                return False
        except Exception:
            return False
        if not chain or any(not isinstance(comp, Plain) for comp in chain):
            return False
        text = "".join(str(getattr(comp, "text", "") or "") for comp in chain).strip()
        if not text:
            return False
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            user_id = ""
        user_id = self._canonical_private_user_id(_single_line(user_id, 80))
        if not user_id:
            return False
        raw_users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
        user = raw_users.get(user_id) if isinstance(raw_users, dict) else None
        role = self._private_user_role(user if isinstance(user, dict) else {}, user_id)
        return role == "friend"

    @filter.on_decorating_result()
    async def final_tts_markup_guard_before_send(self, event: AstrMessageEvent):
        """发送前终检 TTS 标签，避免 <tts> 原样泄漏到聊天。"""
        if self._proactive_only_blocks_passive_event(event, "enable_tts_enhancement"):
            return
        guard = getattr(self, "finalize_outbound_tts_markup_guard", None)
        if callable(guard):
            await guard(event)

    def _is_reply_component(self, component: Any) -> bool:
        try:
            if Reply is not None and isinstance(component, Reply):
                return True
        except Exception:
            pass
        return component.__class__.__name__.lower() == "reply"

    def _segmented_chunk_log_text(self, chunk: list[Any]) -> str:
        parts: list[str] = []
        for comp in chunk or []:
            if isinstance(comp, Plain):
                text = str(getattr(comp, "text", "") or "").strip()
                if text:
                    parts.append(text)
                continue
            if self._is_reply_component(comp):
                parts.append("[引用]")
            else:
                parts.append(f"[{comp.__class__.__name__}]")
        return " ".join(parts).strip()

    def _plain_text_segments_from_chunks(self, chunks: list[list[Any]]) -> list[str]:
        segments: list[str] = []
        for chunk in chunks or []:
            if not chunk or any(not isinstance(comp, Plain) for comp in chunk):
                return []
            text = "".join(str(getattr(comp, "text", "") or "") for comp in chunk).strip()
            text = self._strip_leading_sentence_boundary_artifacts(text)
            if not text:
                return []
            segments.append(text)
        return segments

    def _segmented_context_chars(self, text: str) -> set[str]:
        text = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", str(text or ""), flags=re.IGNORECASE)
        stop_chars = set(
            "的一是不了在有和人就都而及与着或个上也很到说要去会这那我你他她它们"
            "吧呢呀啊吗么啦喔哦噢嘛哈嘿诶哎被把给让才还再又没别刚边里外"
        )
        chars = {ch for ch in text if "\u4e00" <= ch <= "\u9fff" and ch not in stop_chars}
        chars.update(re.findall(r"[a-zA-Z][a-zA-Z0-9_]{1,}", text.lower()))
        return chars

    def _segmented_context_overlap_ratio(self, left: str, right: str) -> float:
        left_chars = self._segmented_context_chars(left)
        right_chars = self._segmented_context_chars(right)
        if not left_chars or not right_chars:
            return 1.0
        return len(left_chars & right_chars) / max(1, min(len(left_chars), len(right_chars)))

    def _segmented_remainder_context_drift_reason(
        self,
        event: AstrMessageEvent,
        *,
        previous_text: str,
        next_text: str,
        source: str = "",
    ) -> str:
        """Stop delayed passive chunks when they look like a different reply turn."""
        if source != "decorating_result" or self.segmented_proactive_scope != "all_llm":
            return ""
        prev = _single_line(previous_text, 260)
        nxt = _single_line(next_text, 260)
        if not prev or not nxt:
            return ""
        inbound = ""
        getter = getattr(event, "get_message_str", None)
        if callable(getter):
            try:
                inbound = str(getter() or "")
            except Exception:
                inbound = ""
        if not inbound:
            inbound = str(getattr(event, "message_str", "") or "")
        if any(marker in inbound for marker in ("在干嘛", "干什么", "忙什么", "忙啥", "进度", "代码", "项目", "修到", "跑通", "测试", "校验")):
            return ""

        context = f"{inbound}\n{prev}"
        context_chars = self._segmented_context_chars(context)
        next_chars = self._segmented_context_chars(nxt)
        if len(context_chars) < 8 or len(next_chars) < 6:
            return ""
        overlap = self._segmented_context_overlap_ratio(context, nxt)
        if overlap >= 0.08:
            return ""

        food_markers = ("西瓜", "水果", "吃", "甜", "买", "拎", "饭", "餐", "晚饭", "午饭", "口", "手勒", "奖励")
        work_markers = ("逻辑", "校验", "进度", "跑通", "顺手", "焦躁", "代码", "编译", "测试", "调试", "需求", "项目")
        checkin_markers = ("忙完没", "忙完了吗", "忙完了没", "你那边忙", "歇会", "休息一下", "停下来")
        fresh_turn_pattern = r"^\s*(在呢|我在|我这边|这边|刚把|刚刚把|刚刚|我刚|你那边|你这边)"

        context_has_food = any(marker in context for marker in food_markers)
        next_has_work = any(marker in nxt for marker in work_markers)
        next_is_checkin = any(marker in nxt for marker in checkin_markers)
        if context_has_food and (next_has_work or next_is_checkin):
            return "food_topic_to_work_or_checkin"
        if re.search(fresh_turn_pattern, nxt) and (next_has_work or next_is_checkin):
            return "fresh_turn_without_topic_overlap"
        if re.search(r"^\s*(你那边|你这边)", nxt) and next_is_checkin:
            return "new_checkin_without_topic_overlap"
        if "昨晚" in nxt and "昨晚" not in context and re.search(fresh_turn_pattern, nxt):
            return "unexpected_time_anchor"
        return ""

    def _segmented_previous_text_needs_closure(self, text: str) -> bool:
        text = _single_line(text, 220).strip()
        if not text:
            return False
        if text[-1:] in "。！？!?~～":
            return False
        if text.endswith(("，", ",", "、", "：", ":", "；", ";", "——", "…", "...")):
            return True
        incomplete_suffixes = (
            "因为",
            "所以",
            "但是",
            "不过",
            "只是",
            "而且",
            "然后",
            "如果",
            "虽然",
            "尽管",
            "除非",
            "只要",
            "等到",
            "直到",
            "为了",
            "关于",
            "至于",
            "比如",
            "像是",
            "之前",
            "之后",
            "以前",
            "以后",
            "的时候",
            "那时候",
            "这时候",
            "一边",
            "一面",
        )
        if text.endswith(incomplete_suffixes):
            return True
        return bool(re.search(r"(因为|所以|但是|不过|如果|虽然|尽管|除非|只要|等到|直到|为了|关于|至于|比如|像是)\s*$", text))

    def _segmented_should_finish_after_new_activity(self, previous_text: str, next_text: str) -> bool:
        prev = _single_line(previous_text, 220).strip()
        nxt = _single_line(next_text, 220).strip()
        if not prev or not nxt:
            return False
        if self._segmented_previous_text_needs_closure(prev):
            return True
        if prev[-1:] in "。！？!?~～":
            return False
        continuation_prefixes = (
            "其实",
            "就是",
            "也就是",
            "然后",
            "而且",
            "所以",
            "但是",
            "不过",
            "只是",
            "还",
            "也",
            "就",
            "才",
            "再",
        )
        if nxt.startswith(continuation_prefixes) and re.search(r"(之前|之后|以前|以后|时候|因为|不过|但是|如果|虽然|为了)$", prev):
            return True
        return False

    def _clean_segmented_reply_chunks(self, chunks: list[list[Any]]) -> list[list[Any]]:
        cleaned_chunks: list[list[Any]] = []
        for chunk in chunks or []:
            cleaned_chunk: list[Any] = []
            for comp in chunk or []:
                if isinstance(comp, Plain):
                    text = self._strip_leading_sentence_boundary_artifacts(str(getattr(comp, "text", "") or ""))
                    if text:
                        cleaned_chunk.append(Plain(text))
                    continue
                cleaned_chunk.append(comp)
            if cleaned_chunk:
                cleaned_chunks.append(cleaned_chunk)
        return cleaned_chunks

    def _segment_llm_reply_chain(self, event: AstrMessageEvent, chain: list[Any]) -> tuple[list[list[Any]], bool, str]:
        reply_prefix = [comp for comp in chain if self._is_reply_component(comp)]
        content_chain = [comp for comp in chain if not self._is_reply_component(comp)]
        units, changed, split_changed, full_text = split_plain_component_chain_detailed(
            content_chain,
            plain_type=Plain,
            split_text=self._split_proactive_text,
        )
        if not full_text:
            return [], False, ""

        quote_message_id = ""
        if (
            bool(getattr(self, "enable_proactive_quote_trigger_message", False))
            and bool(getattr(self, "enable_quote_group_reply", True))
            and not reply_prefix
            and not self._chain_has_reply_component(chain)
        ):
            quote_message_id = self._group_current_reply_quote_message_id(event, text_or_chain=content_chain)
            reply = self._make_reply_component(quote_message_id, event=event)
            if reply is not None:
                reply_prefix = [reply]
                changed = True
        if reply_prefix and units:
            units[0] = [*reply_prefix, *units[0]]

        if not changed:
            return [chain], False, full_text
        if not split_changed:
            cleaned = self._clean_segmented_reply_chunks([flatten_component_chunks(units)])
            return cleaned, True, full_text
        return self._clean_segmented_reply_chunks(units), True, full_text

    async def _send_segmented_llm_chain_remainder(
        self,
        event: AstrMessageEvent,
        chunks: list[list[Any]],
        *,
        previous_segment: str = "",
        source: str = "",
        started_at: float | None = None,
    ) -> None:
        """后台补发被动分段的剩余组件片段；只拆文本，媒体组件保持原子发送。"""
        prev = previous_segment
        total = len([item for item in chunks if item])
        sent_index = 0
        scope = self._event_scope_key(event)
        started_at = _safe_float(started_at, 0.0, 0.0) or self._event_inbound_activity_ts(event)
        async with self._segmented_remainder_lock(scope):
            for chunk in chunks:
                if not chunk:
                    continue
                sent_index += 1
                try:
                    preview = self._segmented_chunk_log_text(chunk)
                    outbound_chunk = chunk
                    drift_reason = self._segmented_remainder_context_drift_reason(
                        event,
                        previous_text=prev,
                        next_text=preview,
                        source=source,
                    )
                    if drift_reason:
                        logger.info(
                            "[PrivateCompanion] 分段剩余组件疑似上下文割裂，停止发送: source=%s reason=%s sent=%s/%s prev=%s next=%s",
                            source or "unknown",
                            drift_reason,
                            max(0, sent_index - 1),
                            total,
                            _single_line(prev, 120),
                            _single_line(preview, 120),
                        )
                        return
                    wait_for = prev or preview
                    delay = await self._calc_segmented_proactive_interval(wait_for)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    new_activity = self._scope_has_new_inbound_activity(scope, started_at, ignore_self=True)
                    if new_activity and self._segmented_should_finish_after_new_activity(prev, preview):
                        logger.info(
                            "[PrivateCompanion] 会话已有新消息，但上一段未收口，允许补发一段分段收尾: source=%s scope=%s prev=%s next=%s",
                            source or "unknown",
                            scope or "unknown",
                            _single_line(prev, 120),
                            _single_line(preview, 120),
                        )
                        started_at = time.time()
                    elif new_activity:
                        logger.info(
                            "[PrivateCompanion] 会话已有新消息，停止发送分段剩余组件: source=%s scope=%s sent=%s/%s",
                            source or "unknown",
                            scope or "unknown",
                            max(0, sent_index - 1),
                            total,
                        )
                        return
                    recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event)
                    if recalled_message_id:
                        logger.info(
                            "[PrivateCompanion] 触发消息已撤回或发送前不可见，停止发送分段剩余组件: source=%s message_id=%s sent=%s/%s",
                            source or "unknown",
                            recalled_message_id,
                            max(0, sent_index - 1),
                            total,
                        )
                        return
                    if chunk and all(isinstance(comp, Plain) for comp in chunk):
                        normalized_segment = "".join(str(getattr(comp, "text", "") or "") for comp in chunk).strip()
                        normalizer = getattr(self, "_normalize_tts_tags", None)
                        if callable(normalizer) and re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", normalized_segment, flags=re.IGNORECASE):
                            try:
                                normalized_segment = str(normalizer(normalized_segment) or normalized_segment).strip()
                            except Exception:
                                pass
                        if (
                            bool(getattr(self, "enable_tts_enhancement", False))
                            and re.search(r"<tts\b[^>]*>.*?</tts>", normalized_segment, flags=re.IGNORECASE | re.DOTALL)
                        ):
                            processor = getattr(self, "_process_tts_tags", None)
                            if callable(processor):
                                fallback_plain = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", normalized_segment, flags=re.IGNORECASE).strip()
                                processed_chunk = await processor(normalized_segment, event, fallback_plain=fallback_plain)
                                if processed_chunk:
                                    outbound_chunk = processed_chunk
                        elif re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", normalized_segment, flags=re.IGNORECASE):
                            cleaned = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", normalized_segment, flags=re.IGNORECASE).strip()
                            outbound_chunk = [Plain(cleaned)] if cleaned else []
                    if not outbound_chunk:
                        continue
                    hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(outbound_chunk))
                    if hit:
                        logger.warning("[PrivateCompanion] 分段剩余组件命中违禁词，停止发送: word=%s", _single_line(hit, 40))
                        return
                    await event.send(event.chain_result(outbound_chunk))
                    logger.info(
                        "[PrivateCompanion] 分段 LLM 剩余组件已发送: source=%s index=%s/%s preview=%s",
                        source or "unknown",
                        sent_index,
                        total,
                        _single_line(preview, 120),
                    )
                    prev = preview
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    try:
                        await event.send(self._build_result_from_chain(outbound_chunk))
                        logger.info(
                            "[PrivateCompanion] 分段 LLM 剩余组件已发送: source=%s index=%s/%s preview=%s",
                            source or "unknown",
                            sent_index,
                            total,
                            _single_line(self._segmented_chunk_log_text(chunk), 120),
                        )
                        prev = self._segmented_chunk_log_text(chunk)
                    except Exception:
                        logger.warning(
                            "[PrivateCompanion] 分段 LLM 剩余组件发送失败: source=%s error=%s",
                            source or "unknown",
                            _single_line(exc, 160),
                            exc_info=True,
                        )
                        return

    async def _send_segmented_llm_reply_remainder(
        self,
        event: AstrMessageEvent,
        segments: list[str],
        *,
        previous_segment: str = "",
        source: str = "",
        started_at: float | None = None,
    ) -> None:
        """后台补发被动分段的剩余片段，避免阻塞主链首包。"""
        prev = previous_segment
        total = len([item for item in segments if str(item or "").strip()])
        sent_index = 0
        scope = self._event_scope_key(event)
        started_at = _safe_float(started_at, 0.0, 0.0) or self._event_inbound_activity_ts(event)
        for segment in segments:
            segment = str(segment or "").strip()
            if not segment:
                continue
            sent_index += 1
            try:
                drift_reason = self._segmented_remainder_context_drift_reason(
                    event,
                    previous_text=prev,
                    next_text=segment,
                    source=source,
                )
                if drift_reason:
                    logger.info(
                        "[PrivateCompanion] 分段剩余片段疑似上下文割裂，停止发送: source=%s reason=%s sent=%s/%s prev=%s next=%s",
                        source or "unknown",
                        drift_reason,
                        max(0, sent_index - 1),
                        total,
                        _single_line(prev, 120),
                        _single_line(segment, 120),
                    )
                    return
                wait_for = prev or segment
                delay = await self._calc_segmented_proactive_interval(wait_for)
                if delay > 0:
                    await asyncio.sleep(delay)
                new_activity = self._scope_has_new_inbound_activity(scope, started_at, ignore_self=True)
                if new_activity and self._segmented_should_finish_after_new_activity(prev, segment):
                    logger.info(
                        "[PrivateCompanion] 会话已有新消息，但上一段未收口，允许补发一段分段收尾: source=%s scope=%s prev=%s next=%s",
                        source or "unknown",
                        scope or "unknown",
                        _single_line(prev, 120),
                        _single_line(segment, 120),
                    )
                    started_at = time.time()
                elif new_activity:
                    logger.info(
                        "[PrivateCompanion] 会话已有新消息，停止发送分段剩余片段: source=%s scope=%s sent=%s/%s",
                        source or "unknown",
                        scope or "unknown",
                        max(0, sent_index - 1),
                        total,
                    )
                    return
                recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event)
                if recalled_message_id:
                    logger.info(
                        "[PrivateCompanion] 触发消息已撤回或发送前不可见，停止发送分段剩余片段: source=%s message_id=%s sent=%s/%s",
                        source or "unknown",
                        recalled_message_id,
                        max(0, sent_index - 1),
                        total,
                    )
                    return
                sent_tts_chain = False
                normalized_segment = segment
                normalizer = getattr(self, "_normalize_tts_tags", None)
                if callable(normalizer) and re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", normalized_segment, flags=re.IGNORECASE):
                    try:
                        normalized_segment = str(normalizer(normalized_segment) or normalized_segment).strip()
                    except Exception:
                        pass
                if (
                    bool(getattr(self, "enable_tts_enhancement", False))
                    and re.search(r"<tts\b[^>]*>.*?</tts>", normalized_segment, flags=re.IGNORECASE | re.DOTALL)
                ):
                        processor = getattr(self, "_process_tts_tags", None)
                        if callable(processor):
                            fallback_plain = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", normalized_segment, flags=re.IGNORECASE).strip()
                            chain = await processor(normalized_segment, event, fallback_plain=fallback_plain)
                            if chain:
                                hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(chain))
                                if hit:
                                    logger.warning("[PrivateCompanion] 分段 TTS 剩余片段命中违禁词，停止发送: word=%s", _single_line(hit, 40))
                                    return
                                try:
                                    await event.send(event.chain_result(chain))
                                except Exception:
                                    await event.send(self._build_result_from_chain(chain))
                                sent_tts_chain = True
                if not sent_tts_chain:
                    outbound = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", normalized_segment, flags=re.IGNORECASE).strip() or segment
                    hit = self._forbidden_recall_hit(outbound)
                    if hit:
                        logger.warning("[PrivateCompanion] 分段剩余片段命中违禁词，停止发送: word=%s", _single_line(hit, 40))
                        return
                    await event.send(event.plain_result(outbound))
                logger.info(
                    "[PrivateCompanion] 分段 LLM 剩余片段已发送: source=%s index=%s/%s preview=%s",
                    source or "unknown",
                    sent_index,
                    total,
                    _single_line(segment, 120),
                )
                prev = segment
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "[PrivateCompanion] 分段 LLM 剩余片段发送失败: source=%s error=%s",
                    source or "unknown",
                    _single_line(exc, 160),
                    exc_info=True,
                )
                return

    @filter.on_decorating_result()
    async def attach_group_reply_quote(self, event: AstrMessageEvent):
        """群聊回复发送前自动补引用，保持上下文对齐。"""
        result = None
        chain: list[Any] = []
        if not self.enabled:
            return
        if not bool(getattr(self, "enable_proactive_quote_trigger_message", False)):
            return
        if not bool(getattr(self, "enable_quote_group_reply", True)):
            return
        if self._proactive_only_blocks_passive_event(event, "enable_group_companion"):
            return
        try:
            result = event.get_result()
        except Exception as exc:
            logger.debug("[PrivateCompanion] 群聊补引用读取结果失败: %s", _single_line(exc, 120))
            return
        if result is None:
            return
        try:
            if hasattr(result, "is_llm_result") and not result.is_llm_result():
                return
        except Exception:
            pass
        try:
            chain = list(getattr(result, "chain", []) or [])
        except Exception as exc:
            logger.debug("[PrivateCompanion] 群聊补引用读取消息链失败: %s", _single_line(exc, 120))
            return
        if not chain or self._chain_has_reply_component(chain):
            return
        try:
            quote_message_id = self._group_current_reply_quote_message_id(event, text_or_chain=chain)
        except Exception as exc:
            logger.debug("[PrivateCompanion] 群聊补引用计算引用目标失败: %s", _single_line(exc, 120))
            return
        if not quote_message_id:
            return
        try:
            quoted_chain = self._with_optional_reply(chain, quote_message_id, event=event)
        except Exception as exc:
            logger.debug("[PrivateCompanion] 群聊补引用构建消息链失败: %s", _single_line(exc, 120))
            return
        if quoted_chain == chain:
            return
        try:
            result.chain = quoted_chain
        except Exception:
            event.set_result(self._build_result_from_chain(quoted_chain))


    @filter.llm_tool(name="pc_qzone_view_feed")
    async def pc_qzone_view_feed(
        self,
        event: AstrMessageEvent,
        user_id: str = "",
        pos: int = 0,
        like: bool = False,
        reply: bool = False,
        selector: str = "",
        fid: str = "",
    ) -> str:
        """查看某位用户 QQ 空间说说,可按需点赞或评论。

        Args:
            user_id(string): 可选,要查看的 QQ 号；留空时默认查看当前配置账号。
            pos(number): 可选,说说位置,0 表示最新一条。
            like(boolean): 可选,是否给该条说说点赞。
            reply(boolean): 可选,是否按工具内部规则尝试评论。
            selector(string): 可选,自然语言选择器,如“最新”“第2条”“最后”；也可以填 fid。
            fid(string): 可选,明确指定说说 fid。
        """
        if self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_qzone_view_feed_impl(event, user_id=user_id, pos=pos, like=like, reply=reply, selector=selector, fid=fid)

    @filter.llm_tool(name="pc_qzone_publish_feed")
    async def pc_qzone_publish_feed(
        self,
        event: AstrMessageEvent,
        text: str = "",
        images: list[str] | None = None,
        image: str = "",
        image_path: str = "",
        image_url: str = "",
        use_latest_draft: bool = False,
        **kwargs,
    ) -> str:
        """发布一条 QQ 空间说说。必须通过 text 参数传入最终正文；如需带图,通过 images 或 image 传入图片。

        Args:
            text(string): 要发布到 QQ 空间的说说正文。
            images(list[string]): 可选,要随说说发布的本地图片路径或图片 URL 列表。
            image(string): 可选,单张图片的本地路径或图片 URL。
            image_path(string): 可选,单张本地图片路径。
            image_url(string): 可选,单张图片 URL。
            use_latest_draft(boolean): 可选,是否使用最近生成的生活说说草稿。
        """
        if self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        if images:
            kwargs["images"] = images
        if image:
            kwargs["image"] = image
        if image_path:
            kwargs["image_path"] = image_path
        if image_url:
            kwargs["image_url"] = image_url
        if use_latest_draft:
            kwargs["use_latest_draft"] = use_latest_draft
        return await self._pc_qzone_publish_feed_impl(event, text, **kwargs)

    @filter.llm_tool(name="pc_get_group_id_by_name")
    async def pc_get_group_id_by_name(self, event: AstrMessageEvent, **kwargs) -> str:
        """按群名关键词查询机器人已加入的群号。

        Args:
            group_name(string): 群名关键词或群号。
        """
        if self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_get_group_id_by_name_impl(event, **kwargs)

    @filter.llm_tool(name="pc_get_user_id_by_name")
    async def pc_get_user_id_by_name(self, event: AstrMessageEvent, **kwargs) -> str:
        """按关系网名称、别名、群名片或昵称解析群友 QQ。

        Args:
            group_id(string): 目标群号；私聊中可填写要查询的群号。
            nickname(string): 关系网名称、别名、群名片、昵称或 QQ。
        """
        if self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_get_user_id_by_name_impl(event, **kwargs)

    @filter.llm_tool(name="pc_query_relation_person")
    async def pc_query_relation_person(self, event: AstrMessageEvent, **kwargs) -> str:
        """查询关系网里是否认识某个 QQ、昵称或别名。

        Args:
            keyword(string): QQ 号、昵称、别名，或用户原话里最像名字的部分。
        """
        if self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_query_relation_person_impl(event, **kwargs)

    @filter.llm_tool(name="pc_get_specified_group_members")
    async def pc_get_specified_group_members(self, event: AstrMessageEvent, **kwargs) -> str:
        """查询指定群成员,并标记是否已在关系网中登记。

        Args:
            group_id(string): 目标群号。
            keyword(string): 可选筛选关键词、昵称、群名片或 QQ。
        """
        if self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_get_specified_group_members_impl(event, **kwargs)

    @filter.llm_tool(name="pc_query_interaction")
    async def pc_query_interaction(self, event: AstrMessageEvent, **kwargs) -> str:
        """查询 Bot 与某个私聊对象或群聊的近期互动摘要。

        Args:
            scope(string): private/group/auto。
            user_hint(string): 私聊对象/群成员 QQ、关系网名称、别名或显示名。
            group_hint(string): 群号或群名；和 user_hint 同时提供时查询这个人在该群的近期发言。
            hint(string): 不确定目标类型时的原始称呼。
            hours(number): 查询最近多少小时，默认 72。
            limit(number): 返回多少条候选互动线索，默认 36。
        """
        if self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_query_interaction_impl(event, **kwargs)

    @filter.llm_tool(name="pc_relay_message")
    async def pc_relay_message(self, event: AstrMessageEvent, **kwargs) -> str:
        """统一转述入口：把用户明确要求转发/转述/提醒的话发送到群聊或私聊。

        Args:
            destination(string): group/private/auto。发群填 group, 私聊填 private, 不确定填 auto。
            group_hint(string): 群号或群名。群聊转私聊时可用于按群成员名解析 QQ。
            recipient_hint(string): 收件人 QQ、关系网名称、别名、群名片或昵称。
            message(string): 最终要发送的内容。
            at_recipient(boolean): 发到群时是否 @ recipient_hint。
            relay_mode(string): persona/soft/original。默认 persona。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
            delay_until_recipient_seen(boolean): 是否等目标群友在群里出现后再转述。
            need_receipt(boolean): 私聊询问时是否等待对方回复并带回结果。
            confirm_before_report(boolean): 带回私聊回复前是否先向对方确认。
            expire_hours(number): 延迟转述有效小时数。
        """
        if self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_relay_message_impl(event, **kwargs)

    @filter.llm_tool(name="pc_send_to_group")
    async def pc_send_to_group(self, event: AstrMessageEvent, **kwargs) -> str:
        """向指定群聊发送消息,可按 QQ/关系网名称/别名/群名片 @ 群友。

        Args:
            group_id(string): 目标群号。
            message(string): 最终要发送的转述文本。
            at_user(string): 可选,要 @ 的 QQ、关系网名称、别名、群名片或昵称。
            relay_mode(string): persona/soft/original。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
        """
        if self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_send_to_group_impl(event, **kwargs)

    @filter.llm_tool(name="pc_send_to_private_user")
    async def pc_send_to_private_user(self, event: AstrMessageEvent, **kwargs) -> str:
        """向指定 QQ 用户发送私聊消息。

        Args:
            user_id(string): 目标 QQ。
            message(string): 最终要发送的转述文本。
            relay_mode(string): persona/soft/original。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
            need_receipt(boolean): 是否等待对方回复并带回结果。
            confirm_before_report(boolean): 带回私聊回复前是否先向对方确认。
            receipt_expire_hours(number): 等待回执的有效小时数。
        """
        if self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_send_to_private_user_impl(event, **kwargs)

    @filter.llm_tool(name="pc_send_to_groups")
    async def pc_send_to_groups(self, event: AstrMessageEvent, **kwargs) -> str:
        """向多个群发送同一条通知。

        Args:
            group_ids(string): 目标群号,可用逗号、空格或换行分隔。
            message(string): 最终要发送的转述文本。
            at_user(string): 可选,要 @ 的 QQ、关系网名称、别名、群名片或昵称。
            relay_mode(string): persona/soft/original。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
        """
        if self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_send_to_groups_impl(event, **kwargs)

    @filter.llm_tool(name="pc_send_to_private_users")
    async def pc_send_to_private_users(self, event: AstrMessageEvent, **kwargs) -> str:
        """向多个 QQ 用户发送同一条私聊转述。

        Args:
            user_ids(string): 目标 QQ,可用逗号、空格或换行分隔。
            message(string): 最终要发送的转述文本。
            relay_mode(string): persona/soft/original。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
        """
        if self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_send_to_private_users_impl(event, **kwargs)

    @filter.llm_tool(name="pc_schedule_group_relay")
    async def pc_schedule_group_relay(self, event: AstrMessageEvent, **kwargs) -> str:
        """挂起一条群聊转述,等目标用户在群里发言后自动 @ 并转述。

        Args:
            group_id(string): 目标群号。
            at_user(string): 目标 QQ、关系网名称、别名、群名片或昵称。
            message(string): 最终要发送的转述文本。
            relay_mode(string): persona/soft/original。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
            expire_hours(number): 挂起有效小时数。
        """
        if self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_schedule_group_relay_impl(event, **kwargs)

    async def _append_environment_perception_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        marker = "<!-- private_companion_environment_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        environment_injection = await self._format_environment_perception(event)
        if environment_injection:
            placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                req,
                marker,
                environment_injection,
                priority=30,
                source="environment",
            ) else "system_prompt"
            if placement == "system_prompt":
                req.system_prompt = f"{current_prompt}\n\n{marker}\n{environment_injection}".strip()
            await self._record_request_prompt_fragment(
                event,
                title="请求级环境感知注入",
                key="environment.request",
                text=environment_injection,
                source="environment",
                metadata={"注入位置": placement},
            )

    @staticmethod
    def _normalize_passive_injection_position(value: Any) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "auto": "auto",
            "自动": "auto",
            "cache": "auto",
            "cache_friendly": "auto",
            "缓存友好": "auto",
            "prompt": "prompt",
            "request": "prompt",
            "turn": "prompt",
            "tail": "prompt",
            "user_prompt": "prompt",
            "current_prompt": "prompt",
            "当前请求": "prompt",
            "当前请求末尾": "prompt",
            "请求末尾": "prompt",
            "用户消息末尾": "prompt",
            "system": "system_prompt",
            "system_prompt": "system_prompt",
            "系统提示": "system_prompt",
            "系统提示词": "system_prompt",
            "强约束": "system_prompt",
        }
        return aliases.get(text, text if text in {"auto", "prompt", "system_prompt"} else "prompt")

    def _format_reply_style_prompt(self) -> str:
        text = str(getattr(self, "reply_style_prompt", "") or "").strip()
        if not text:
            return ""
        text = re.sub(r"\r\n?", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not text:
            return ""
        return (
            "【回复风格约束】\n"
            f"{text}\n"
            "这些规则用于普通聊天和主动消息的表达节奏；如果当前问题确实需要排障、教程、代码说明、复杂解释或用户明确要求详细说明，可以优先保证信息完整。"
        )

    async def _append_reply_style_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *,
        mode: str = "passive",
        priority: int = 12,
    ) -> None:
        style_prompt = self._format_reply_style_prompt()
        if not style_prompt:
            return
        marker = "<!-- private_companion_reply_style_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            style_prompt,
            priority=priority,
            source="reply_style",
        ) else "system_prompt"
        if placement == "system_prompt":
            req.system_prompt = f"{current_prompt}\n\n{marker}\n{style_prompt}".strip()
        await self._record_request_prompt_fragment(
            event,
            title="回复风格约束",
            key="reply.style",
            text=style_prompt,
            source="reply_style",
            mode=mode,
            metadata={"注入位置": placement},
        )

    @staticmethod
    def _compact_high_intensity_prompt_lines(text: Any, *, max_chars: int = 900, max_lines: int = 12) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        lines: list[str] = []
        for line in re.split(r"[\r\n]+", raw):
            cleaned = _single_line(line, 180).strip()
            if cleaned:
                lines.append(cleaned)
        if not lines:
            return _single_line(raw, max_chars)
        if len(lines) > max_lines:
            lines = lines[: max(1, max_lines - 1)] + [f"...已省略 {len(lines) - max_lines + 1} 行高强度背景"]
        compact = "\n".join(lines).strip()
        if len(compact) > max_chars:
            compact = compact[:max_chars].rstrip() + "\n...已截断高强度背景"
        return compact

    def _format_group_high_intensity_reply_guard(self, event: AstrMessageEvent | None = None) -> str:
        high_intensity = getattr(event, "private_companion_group_high_intensity", None) if event is not None else None
        if not isinstance(high_intensity, dict) or not high_intensity.get("active"):
            return ""
        return "\n".join(
            [
                "【群聊高强度短回复护栏】",
                "当前群聊处于高强度/合并收口状态，本轮只抓一个重点短句接住即可。",
                "必须优先服从 AstrBot 人格、系统提示和回复风格配置里的字数、句数、口吻、语言和表达节奏要求；用户在这些配置里写了什么，就按对应要求回复。",
                "如果配置要求很短，就不要因为高强度背景而扩写；如果配置要求口语、简体中文、少句数或特定风格，也要继续保持。",
                "不要因为关系网、状态、记忆或合并消息而扩写、复述背景、逐条总结；一般 1 句，能少字就少字。",
            ]
        )

    async def _append_group_high_intensity_reply_guard_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        guard_text = self._format_group_high_intensity_reply_guard(event)
        if not guard_text:
            return
        marker = "<!-- private_companion_group_high_intensity_reply_guard_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            guard_text,
            priority=11,
            source="group_high_intensity",
        ) else "system_prompt"
        if placement == "system_prompt":
            req.system_prompt = f"{current_prompt}\n\n{marker}\n{guard_text}".strip()
        await self._record_request_prompt_fragment(
            event,
            title="群聊高强度短回复护栏",
            key="group.high_intensity.reply_guard",
            text=guard_text,
            source="group_high_intensity",
            mode="group",
            priority=11,
            metadata={"注入位置": placement},
        )

    @staticmethod
    def _normalize_framework_session_lock_mode(value: Any) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "auto": "auto",
            "自动": "auto",
            "compat": "auto",
            "compatibility": "auto",
            "兼容": "auto",
            "legacy": "auto",
            "旧版": "auto",
            "always": "always",
            "on": "always",
            "true": "always",
            "enable": "always",
            "enabled": "always",
            "开启": "always",
            "始终": "always",
            "off": "off",
            "false": "off",
            "disable": "off",
            "disabled": "off",
            "关闭": "off",
        }
        return aliases.get(text, text if text in {"auto", "always", "off"} else "auto")

    @staticmethod
    def _normalize_provider_config_mode(value: Any, config: Any = None) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "quick": "quick",
            "fast": "quick",
            "simple": "quick",
            "快速": "quick",
            "快速配置": "quick",
            "precision": "precision",
            "precise": "precision",
            "advanced": "precision",
            "detail": "precision",
            "detailed": "precision",
            "精准": "precision",
            "精准配置": "precision",
            "分流": "precision",
            "分流模型": "precision",
        }
        if text in aliases:
            return aliases[text]
        if text in {"quick", "precision"}:
            return text

        precision_keys = (
            "MAI_STYLE_PROVIDER_ID",
            "DAILY_PLAN_PROVIDER_ID",
            "DETAIL_ENHANCEMENT_PROVIDER_ID",
            "DREAM_DIARY_PROVIDER_ID",
            "CREATIVE_PROVIDER_ID",
            "CREATIVE_OUTLINE_PROVIDER_ID",
            "CREATIVE_REVIEW_PROVIDER_ID",
            "VOICE_PROMPT_PROVIDER_ID",
            "tts_conversion_provider_id",
            "PHOTO_PROMPT_PROVIDER_ID",
            "NARRATION_PROVIDER_ID",
            "HISTORY_SUMMARY_PROVIDER_ID",
            "RESPONSE_REVIEW_PROVIDER_ID",
            "SMART_SILENCE_PROVIDER_ID",
            "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
            "TROUBLESHOOTING_PROVIDER_ID",
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
            "REST_WAKEUP_PROVIDER_ID",
            "RELATIONSHIP_ANALYSIS_PROVIDER_ID",
            "EMOTION_JUDGEMENT_PROVIDER_ID",
            "COMPANION_MEMORY_PROVIDER_ID",
            "DIALOGUE_EPISODE_PROVIDER_ID",
            "GROUP_INTERJECT_PROVIDER_ID",
            "GROUP_EPISODE_PROVIDER_ID",
            "GROUP_SLANG_PROVIDER_ID",
            "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
            "FORWARD_MESSAGE_PROVIDER_ID",
            "PRIVATE_READING_VISION_PROVIDER_ID",
            "NEWS_PROVIDER_ID",
            "WEB_EXPLORATION_PROVIDER_ID",
        )
        if any(str(_flat_get(config, key, "") or "").strip() for key in precision_keys):
            return "precision"
        return "quick"

    @staticmethod
    def _normalize_external_image_api_platform(value: Any) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "auto": "auto",
            "自动": "auto",
            "openai": "openai",
            "openai-compatible": "openai",
            "openai_compatible": "openai",
            "openai兼容": "openai",
            "兼容": "openai",
            "兼容模式": "openai",
            "external": "openai",
            "bailian": "bailian",
            "dashscope": "bailian",
            "aliyun": "bailian",
            "alibaba": "bailian",
            "modelstudio": "bailian",
            "model_studio": "bailian",
            "百炼": "bailian",
            "阿里云百炼": "bailian",
            "通义万相": "bailian",
        }
        return aliases.get(text, text if text in {"auto", "openai", "bailian"} else "auto")

    def _apply_quick_provider_defaults(self) -> None:
        fast = str(getattr(self, "fast_response_provider_id", "") or "").strip()
        complex_model = str(getattr(self, "complex_reasoning_provider_id", "") or "").strip()
        creative = str(getattr(self, "creative_model_provider_id", "") or "").strip()
        plugin_vision = str(getattr(self, "plugin_vision_provider_id", "") or "").strip()

        attr_config_keys = {
            "llm_provider_id": "LLM_PROVIDER_ID",
            "mai_style_provider_id": "MAI_STYLE_PROVIDER_ID",
            "daily_plan_provider_id": "DAILY_PLAN_PROVIDER_ID",
            "detail_enhancement_provider_id": "DETAIL_ENHANCEMENT_PROVIDER_ID",
            "history_summary_provider_id": "HISTORY_SUMMARY_PROVIDER_ID",
            "relationship_analysis_provider_id": "RELATIONSHIP_ANALYSIS_PROVIDER_ID",
            "companion_memory_provider_id": "COMPANION_MEMORY_PROVIDER_ID",
            "dialogue_episode_provider_id": "DIALOGUE_EPISODE_PROVIDER_ID",
            "group_episode_provider_id": "GROUP_EPISODE_PROVIDER_ID",
            "forward_message_provider_id": "FORWARD_MESSAGE_PROVIDER_ID",
            "proactive_persona_judge_provider_id": "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
            "response_review_provider_id": "RESPONSE_REVIEW_PROVIDER_ID",
            "smart_silence_provider_id": "SMART_SILENCE_PROVIDER_ID",
            "troubleshooting_provider_id": "TROUBLESHOOTING_PROVIDER_ID",
            "emotion_judgement_provider_id": "EMOTION_JUDGEMENT_PROVIDER_ID",
            "smart_message_debounce_provider_id": "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
            "rest_wakeup_provider_id": "REST_WAKEUP_PROVIDER_ID",
            "group_followup_judge_provider_id": "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
            "group_interject_provider_id": "GROUP_INTERJECT_PROVIDER_ID",
            "group_slang_provider_id": "GROUP_SLANG_PROVIDER_ID",
            "voice_prompt_provider_id": "VOICE_PROMPT_PROVIDER_ID",
            "tts_conversion_provider_id": "tts_conversion_provider_id",
            "narration_provider_id": "NARRATION_PROVIDER_ID",
            "news_provider_id": "NEWS_PROVIDER_ID",
            "web_exploration_provider_id": "WEB_EXPLORATION_PROVIDER_ID",
            "creative_provider_id": "CREATIVE_PROVIDER_ID",
            "creative_outline_provider_id": "CREATIVE_OUTLINE_PROVIDER_ID",
            "creative_review_provider_id": "CREATIVE_REVIEW_PROVIDER_ID",
            "dream_diary_provider_id": "DREAM_DIARY_PROVIDER_ID",
            "dream_provider_id": "DREAM_DIARY_PROVIDER_ID",
            "diary_provider_id": "DREAM_DIARY_PROVIDER_ID",
            "photo_prompt_provider_id": "PHOTO_PROMPT_PROVIDER_ID",
            "jm_cosmos_vision_provider_id": "PRIVATE_READING_VISION_PROVIDER_ID",
        }

        if str(getattr(self, "provider_config_mode", "quick") or "quick").strip().lower() != "quick":
            config = getattr(self, "config", None)
            for attr, config_key in attr_config_keys.items():
                setattr(self, attr, self._cfg_str(config, config_key, ""))
            self.plugin_vision_provider_id = ""
            return

        def fill(attr: str, provider_id: str) -> None:
            setattr(self, attr, provider_id)

        fill("llm_provider_id", complex_model)
        fill("mai_style_provider_id", fast or complex_model)

        for attr in (
            "daily_plan_provider_id",
            "detail_enhancement_provider_id",
            "history_summary_provider_id",
            "relationship_analysis_provider_id",
            "companion_memory_provider_id",
            "dialogue_episode_provider_id",
            "group_episode_provider_id",
            "forward_message_provider_id",
            "proactive_persona_judge_provider_id",
        ):
            fill(attr, complex_model)

        for attr in (
            "response_review_provider_id",
            "smart_silence_provider_id",
            "troubleshooting_provider_id",
            "emotion_judgement_provider_id",
            "smart_message_debounce_provider_id",
            "rest_wakeup_provider_id",
            "group_interject_provider_id",
            "group_slang_provider_id",
            "voice_prompt_provider_id",
            "tts_conversion_provider_id",
            "narration_provider_id",
            "news_provider_id",
            "web_exploration_provider_id",
        ):
            fill(attr, fast or complex_model)
        fill("group_followup_judge_provider_id", fast)

        for attr in (
            "creative_provider_id",
            "creative_outline_provider_id",
            "creative_review_provider_id",
            "dream_diary_provider_id",
            "dream_provider_id",
            "diary_provider_id",
            "photo_prompt_provider_id",
        ):
            fill(attr, creative or complex_model)
        self.jm_cosmos_vision_provider_id = plugin_vision

    def _detect_astrbot_version(self) -> str:
        candidates: list[Any] = []
        for obj in (
            getattr(self, "context", None),
            getattr(getattr(self, "context", None), "core_lifecycle", None),
            getattr(getattr(self, "context", None), "metadata", None),
        ):
            if obj is None:
                continue
            for attr in ("version", "astrbot_version", "__version__", "VERSION"):
                try:
                    candidates.append(getattr(obj, attr, ""))
                except Exception:
                    pass
        for module_name in ("astrbot", "astrbot.core", "astrbot.api"):
            try:
                module = importlib.import_module(module_name)
            except Exception:
                continue
            for attr in ("__version__", "VERSION", "version"):
                try:
                    candidates.append(getattr(module, attr, ""))
                except Exception:
                    pass
        for candidate in candidates:
            text = _single_line(candidate, 40)
            if re.search(r"\d+\.\d+(?:\.\d+)?", text):
                return text
        return ""

    @staticmethod
    def _parse_version_tuple(value: Any) -> tuple[int, int, int] | None:
        match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(value or ""))
        if not match:
            return None
        return (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3) or 0),
        )

    def _framework_session_lock_enabled(self) -> bool:
        mode = self._normalize_framework_session_lock_mode(getattr(self, "framework_session_lock_mode", "auto"))
        if mode == "always":
            return True
        if mode == "off":
            return False
        version = self._parse_version_tuple(self._detect_astrbot_version())
        if version is None:
            return False
        # AstrBot 4.25.x 曾出现主链/会话库并发锁问题；新版本默认不再额外串行化。
        return (4, 25, 0) <= version <= (4, 25, 2)

    def _append_turn_prompt_fragment_by_position(
        self,
        req: ProviderRequest,
        marker: str,
        text: str,
        *,
        priority: int = 50,
        source: str = "",
    ) -> bool:
        position = self._normalize_passive_injection_position(getattr(self, "passive_injection_position", "prompt"))
        if position == "system_prompt":
            return False
        content = str(text or "").strip()
        if not content:
            return False
        try:
            marker = _single_line(marker, 120) or "<!-- private_companion_turn_fragment -->"
            current = str(getattr(req, "prompt", "") or "")
            fragments = getattr(req, "_private_companion_turn_prompt_fragments", None)
            if not isinstance(fragments, list):
                fragments = []
                setattr(req, "_private_companion_turn_prompt_fragments", fragments)
            if marker in current or any(isinstance(item, dict) and item.get("marker") == marker for item in fragments):
                return True
            fragments.append(
                {
                    "marker": marker,
                    "content": content,
                    "priority": int(priority),
                    "source": _single_line(source, 80),
                    "index": len(fragments),
                }
            )
            self._render_turn_prompt_fragments(req)
            return True
        except Exception as exc:
            logger.debug("[PrivateCompanion] 指定位置 prompt 注入失败,回退 system_prompt: %s", _single_line(exc, 120))
            return False

    def _render_turn_prompt_fragments(self, req: ProviderRequest) -> None:
        start_marker = "<!-- private_companion_turn_fragments_start -->"
        end_marker = "<!-- private_companion_turn_fragments_end -->"
        current = str(getattr(req, "prompt", "") or "")
        base = re.sub(
            rf"\n*\s*{re.escape(start_marker)}.*?{re.escape(end_marker)}\s*",
            "\n\n",
            current,
            flags=re.DOTALL,
        ).strip()
        fragments = getattr(req, "_private_companion_turn_prompt_fragments", None)
        if not isinstance(fragments, list) or not fragments:
            setattr(req, "prompt", base)
            return
        seen_markers: set[str] = set()
        seen_content: set[str] = set()
        rendered_parts: list[str] = []
        for item in sorted(
            (frag for frag in fragments if isinstance(frag, dict)),
            key=lambda frag: (_safe_int(frag.get("priority"), 50), _safe_int(frag.get("index"), 0)),
        ):
            marker = _single_line(item.get("marker"), 120)
            content = str(item.get("content") or "").strip()
            if not marker or not content:
                continue
            if marker in seen_markers or content in seen_content:
                continue
            seen_markers.add(marker)
            seen_content.add(content)
            rendered_parts.append(f"{marker}\n{content}")
        if not rendered_parts:
            setattr(req, "prompt", base)
            return
        managed = f"{start_marker}\n" + "\n\n".join(rendered_parts) + f"\n{end_marker}"
        setattr(req, "prompt", f"{base}\n\n{managed}".strip() if base else managed)

    async def _record_request_prompt_fragment(
        self,
        event: AstrMessageEvent,
        *,
        title: str,
        key: str,
        text: str,
        source: str = "",
        mode: str = "",
        priority: int = 50,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        recorder = getattr(self, "_record_prompt_injection_snapshot", None)
        content = str(text or "").strip()
        if not callable(recorder) or not content:
            return
        await recorder(
            kind="request",
            session=_single_line(getattr(event, "unified_msg_origin", ""), 160) or self._event_scope_key(event),
            title=title,
            text=content,
            mode=mode,
            trace_id=self._prompt_injection_trace_id_for_event(event),
            message_preview=self._prompt_injection_message_preview_for_event(event),
            sender_label=self._prompt_injection_sender_label_for_event(event),
            modules=[
                {
                    "key": key,
                    "source": source,
                    "priority": priority,
                    "content": content,
                    "chars": len(content),
                }
            ],
            metadata={
                **(metadata or {}),
                "会话": _single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown",
                "发送者": _single_line(self._event_sender_id(event), 80),
            },
        )

    async def _format_passive_environment_fragment(self, event: AstrMessageEvent, *, lightweight: bool = False) -> str:
        if not lightweight:
            return await self._format_environment_perception(event)
        if not self._feature_enabled_or_temp_unlocked("enable_environment_perception"):
            return ""
        current = self._environment_now()
        lines = [
            "【轻量环境感知】",
            "这是当前消息的轻量背景边界，主要影响时间感、平台语境和回复节奏；如果用户刚好在问时间、平台或环境感受，可以按需要自然带出，没问到时就只当背景参考。",
            f"时间：{current.strftime('%Y-%m-%d %H:%M')}",
            "时间锚点必须以这一行真实时间为准；不要把未来日程、睡眠段、旧记忆或上次对话里的时间说成当前时间。",
        ]
        current_minutes = current.hour * 60 + current.minute
        if not (22 * 60 <= current_minutes or current_minutes <= 90):
            lines.append(
                "当前没有进入深夜时段；即使人格、作息或旧上下文提到“可能很晚”“晚上睡觉”，也不能主动说快十一点、困不困、该睡了或晚安。"
            )
        platform = await self._format_platform_perception(event)
        if platform:
            lines.append(f"会话：{platform}")
        return "\n".join(lines)

    async def _append_capability_boundary_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        marker = "<!-- private_companion_capability_boundary_v1 -->"
        current_prompt = req.system_prompt or ""
        if marker in current_prompt:
            return
        boundary = (
            "【能力边界】\n"
            "你不能假装自己能影响现实、网络、游戏房间、他人设备或用户身体动作。"
            "没有可用工具且没有实际执行结果时,不要承诺“我这就拉你/我帮你操作/我已经处理/我去修/我给你弄好”。"
            "遇到拉人、开房间、修网、重启、登录、下载、现实代办等请求,只能自然说明自己做不到实际操作,可以提醒、陪用户确认、建议对方找能操作的人,或在确有工具时调用工具后再描述结果。"
        )
        req.system_prompt = f"{current_prompt}\n\n{marker}\n{boundary}".strip()
        await self._record_request_prompt_fragment(
            event,
            title="能力边界注入",
            key="capability.boundary",
            text=boundary,
            source="guard",
            mode="group",
        )

    async def _append_conditional_tool_instructions_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        message_text = str(getattr(event, "message_str", "") or "")
        current_prompt = req.system_prompt or ""
        atrelay_instruction = self._atrelay_tool_instruction()
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        atrelay_marker = "<!-- private_companion_atrelay_tools_v1 -->"
        if atrelay_instruction and atrelay_marker not in current_prompt and atrelay_marker not in current_turn_prompt:
            if self._message_looks_like_atrelay_request(message_text):
                await self._append_atrelay_target_summary_to_request(event, req)
                current_prompt = req.system_prompt or ""
                current_turn_prompt = str(getattr(req, "prompt", "") or "")
                placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                    req,
                    atrelay_marker,
                    atrelay_instruction,
                    priority=88,
                    source="tools",
                ) else "system_prompt"
                if placement == "system_prompt":
                    current_prompt = f"{current_prompt}\n\n{atrelay_marker}\n{atrelay_instruction}".strip()
                    req.system_prompt = current_prompt
                await self._record_request_prompt_fragment(
                    event,
                    title="跨群转述工具注入",
                    key="tools.atrelay",
                    text=atrelay_instruction,
                    source="tools",
                    mode="conditional",
                    metadata={"注入位置": placement},
                )
        relation_instruction = self._relation_lookup_instruction()
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        relation_marker = "<!-- private_companion_relation_lookup_v1 -->"
        try:
            relation_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            relation_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        relation_query = any(token in message_text for token in ("查关系网", "关系网查", "查一下关系", "查查关系"))
        relation_query = relation_query or (
            any(token in message_text for token in ("查一下", "查查", "帮我查", "查一查"))
            and (
                bool(re.search(r"\d{5,12}", message_text))
                or any(token in message_text for token in ("这个人", "这人", "那个人", "那人", "是谁", "认识"))
            )
        )
        livingmemory_relation_context = (
            relation_private
            and bool(getattr(self, "enable_livingmemory_integration", False))
            and bool(getattr(self, "_livingmemory_available", lambda: False)())
        )
        if relation_private and relation_instruction and relation_marker not in current_prompt and relation_marker not in current_turn_prompt and (relation_query or livingmemory_relation_context):
            placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                req,
                relation_marker,
                relation_instruction,
                priority=87,
                source="tools",
            ) else "system_prompt"
            if placement == "system_prompt":
                current_prompt = f"{current_prompt}\n\n{relation_marker}\n{relation_instruction}".strip()
                req.system_prompt = current_prompt
            await self._record_request_prompt_fragment(
                event,
                title="关系网查询工具注入",
                key="tools.relation_lookup",
                text=relation_instruction,
                source="tools",
                mode="conditional",
                metadata={"注入位置": placement, "触发原因": "livingmemory" if livingmemory_relation_context and not relation_query else "query"},
            )
        qzone_instruction = self._qzone_tool_instruction()
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        qzone_marker = "<!-- private_companion_qzone_tools_v1 -->"
        if qzone_instruction and qzone_marker not in current_prompt and qzone_marker not in current_turn_prompt:
            if any(token in message_text for token in ("说说", "空间", "QQ空间", "动态", "点赞", "评论")):
                placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                    req,
                    qzone_marker,
                    qzone_instruction,
                    priority=88,
                    source="tools",
                ) else "system_prompt"
                if placement == "system_prompt":
                    current_prompt = f"{current_prompt}\n\n{qzone_marker}\n{qzone_instruction}".strip()
                    req.system_prompt = current_prompt
                await self._record_request_prompt_fragment(
                    event,
                    title="QQ 空间工具注入",
                    key="tools.qzone",
                    text=qzone_instruction,
                    source="tools",
                    mode="conditional",
                    metadata={"注入位置": placement},
                )
        cross_user_instruction = self._cross_user_memory_query_instruction()
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        cross_user_marker = "<!-- private_companion_cross_user_memory_v1 -->"
        if cross_user_instruction and cross_user_marker not in current_prompt and cross_user_marker not in current_turn_prompt:
            if any(token in message_text for token in (
                "聊了什么", "说了什么", "发了什么", "讲了什么", "互动", "和谁聊", "跟谁聊", "最近跟", "最近和",
                "你和", "你跟", "在群里", "那个群", "这个群", "私聊过", "聊过",
            )):
                placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                    req,
                    cross_user_marker,
                    cross_user_instruction,
                    priority=88,
                    source="tools",
                ) else "system_prompt"
                if placement == "system_prompt":
                    current_prompt = f"{current_prompt}\n\n{cross_user_marker}\n{cross_user_instruction}".strip()
                    req.system_prompt = current_prompt
                await self._record_request_prompt_fragment(
                    event,
                    title="跨用户记忆互通工具注入",
                    key="tools.cross_user_memory",
                    text=cross_user_instruction,
                    source="tools",
                    mode="conditional",
                    metadata={"注入位置": placement},
                )

    def _is_lightweight_private_passive_inbound(self, text: str) -> bool:
        cleaned = _single_line(text, 80)
        if not cleaned:
            return False
        if len(cleaned) > 18:
            return False
        heavy_tokens = (
            "图片", "看图", "照片", "语音", "引用", "转发", "聊天记录",
            "帮我", "怎么", "为什么", "是什么", "怎么办", "分析", "解释", "总结",
            "日程", "状态", "近况", "在干嘛", "做什么", "忙什么",
            "书柜", "夹层", "抽屉", "阅读", "读过", "看过", "素材", "本子", "漫画", "藏本",
            "新闻", "说说", "空间", "发给", "转告", "@",
        )
        if any(token in cleaned for token in heavy_tokens):
            return False
        bookshelf_checker = getattr(self, "_user_asks_bookshelf_reading_memory", None)
        if callable(bookshelf_checker) and bookshelf_checker(cleaned):
            return False
        return True

    def _private_passive_state_fingerprint(self, state: dict[str, Any], current_user: dict[str, Any] | None = None) -> dict[str, Any]:
        now = self._environment_now()
        time_label, _ = self._current_time_period_label(now)
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        activity = _single_line(current_item.get("activity"), 50) if isinstance(current_item, dict) else ""
        detail = self._current_detail_segment_for_update()
        detail_key = _single_line(detail.get("key"), 80) if isinstance(detail, dict) else ""
        detail_summary = _single_line(detail.get("summary"), 80) if isinstance(detail, dict) else ""
        weather = _single_line(state.get("weather"), 60)
        conditions: list[str] = []
        raw_conditions = state.get("conditions")
        if isinstance(raw_conditions, list):
            for cond in raw_conditions[:3]:
                if not isinstance(cond, dict) or not self._should_show_condition(cond):
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 18)
                if label and label not in conditions:
                    conditions.append(label)
        return {
            "date": _today_key(),
            "time_label": time_label,
            "energy_bracket": (energy // 10) * 10,
            "mood": _single_line(state.get("mood_bias"), 18) or "平稳",
            "activity": self._sanitize_schedule_context_for_private_user(activity, current_user or {}) if activity else "",
            "detail": detail_key or detail_summary,
            "weather": weather if weather and weather != "暂无天气信息" else "",
            "conditions": conditions[:2],
        }

    def _format_private_passive_state_snapshot(
        self,
        state: dict[str, Any],
        current_user: dict[str, Any] | None,
        *,
        direct: bool = False,
    ) -> str:
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias"), 18) or "平稳"
        now = self._environment_now()
        time_label, _ = self._current_time_period_label(now)
        pieces = [f"时间节奏：{time_label}", f"精神约 {energy}/100", f"情绪底色偏{mood}"]
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        schedule = self._sanitize_schedule_context_for_private_user(
            self._format_plan_item_for_prompt(current_item),
            current_user or {},
        )
        if schedule:
            pieces.append(f"拟人化日程素材：{schedule}")
        detail = self._current_detail_segment_for_update()
        if isinstance(detail, dict):
            summary = _single_line(detail.get("summary"), 90)
            if summary:
                pieces.append(f"模拟氛围：{summary}")
        weather = _single_line(state.get("weather"), 60)
        if weather and weather != "暂无天气信息":
            pieces.append(f"天气素材：{weather}")
        conditions: list[str] = []
        raw_conditions = state.get("conditions")
        if isinstance(raw_conditions, list):
            for cond in raw_conditions[:3]:
                if not isinstance(cond, dict) or not self._should_show_condition(cond):
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 18)
                if label and label not in conditions:
                    conditions.append(label)
        if conditions:
            pieces.append("身体感素材：" + "、".join(conditions[:2]))
        usage = (
            "用户这轮在问 Bot 近况或状态，可以把下面内容当作 Bot 自身近况简短承接。"
            if direct
            else "Bot 自身模拟状态刚有变化；用户没问近况时不要主动展开。"
        )
        return "\n".join(
            [
                "【Bot 自身模拟状态更新】",
                "以下只描述 Bot 的拟人化内部状态/场景素材，不是用户事实、不是现实证据，也不要写入长期记忆。",
                "只用于语气、长短、节奏和轻微接话；不要把它改写成用户做过的事或现实已经发生的事件。",
                usage + " " + "；".join(pieces) + "。",
            ]
        )

    def _private_passive_state_update_for_prompt(
        self,
        *,
        session: str,
        state: dict[str, Any],
        current_user: dict[str, Any] | None,
        inbound_text: str,
        lightweight: bool,
    ) -> tuple[str, bool, str]:
        session_key = _single_line(session, 160) or "unknown"
        cache = getattr(self, "_passive_state_session_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._passive_state_session_cache = cache
        fingerprint = self._private_passive_state_fingerprint(state, current_user)
        previous = cache.get(session_key) if isinstance(cache.get(session_key), dict) else {}
        changed = previous.get("fingerprint") != fingerprint
        direct_state_request = self._user_asks_recent_bot_activity(inbound_text) or bool(
            re.search(r"(状态|日程|精力|心情|情绪|在干嘛|做什么|忙什么|近况)", str(inbound_text or ""))
        )
        now_ts = _now_ts()
        cache[session_key] = {
            "fingerprint": fingerprint,
            "ts": now_ts,
            "last_changed_ts": now_ts if changed else _safe_float(previous.get("last_changed_ts"), now_ts),
        }
        if len(cache) > 240:
            stale = sorted(
                ((key, _safe_float(value.get("ts"), 0)) for key, value in cache.items() if isinstance(value, dict)),
                key=lambda item: item[1],
            )
            for key, _ in stale[: max(0, len(cache) - 200)]:
                cache.pop(key, None)
        if direct_state_request:
            return self._format_private_passive_state_snapshot(state, current_user, direct=True), changed, "direct"
        if changed:
            return self._format_private_passive_state_snapshot(state, current_user, direct=False), True, "changed"
        return "", False, "unchanged_light" if lightweight else "unchanged"

    def _format_group_persona_denoise_prompt(self, event: AstrMessageEvent | None = None) -> str:
        if not bool(getattr(self, "enable_group_persona_denoise", True)):
            return ""
        scene = getattr(event, "private_companion_group_scene", None) if event is not None else None
        trigger = _single_line(scene.get("trigger"), 40) if isinstance(scene, dict) else ""
        high_intensity = getattr(event, "private_companion_group_high_intensity", None) if event is not None else None
        high_active = isinstance(high_intensity, dict) and bool(high_intensity.get("active"))
        sender_id = ""
        sender_display_name = ""
        sender_is_target = False
        if event is not None:
            try:
                sender_id = _single_line(str(event.get_sender_id()), 40)
            except Exception:
                sender_id = ""
            try:
                sender_display_name = _single_line(self._sender_display_name(event), 40)
            except Exception:
                sender_display_name = ""
            if sender_id:
                users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
                current_user = users.get(sender_id) if isinstance(users, dict) else None
                sender_is_target = self._is_target_private_user(
                    sender_id,
                    current_user if isinstance(current_user, dict) else None,
                )
        lines = [
            "【群聊人格降噪】",
            "这是群聊场景，更适合先接住当前被问到的事或眼前话题，语气也尽量比私聊更轻一点。",
            "群聊里的身份优先按平台稳定 ID 理解；昵称、群名片、角色名、别名和“通常是谁”这类设定，更适合作为称呼线索，不直接当成身份结论。",
            "提到群聊旧消息、群梗、记忆召回或最近群聊时，尽量保留具体成员名或 QQ 标签，例如“A[QQ:...] 说过/起哄过”；只有确实缺少成员线索时，再概括成“群里有人”。除非当前消息或引用明确就是这位发言者，尽量不要顺手改写成“你说过”“主人说过”这类直接归到当前对象身上的表达。",
            "状态、日程、情绪和私聊关系更适合只留在语气底色里；如果没有人明确问到，就不必主动展开能量、天气、日程、心情或插件状态。",
            "表达上尽量自然一点，不需要刻意堆动作描写、撒娇、长解释或关系总结；一句能说清，就简单说一句。",
            "如果只是被轻轻提到，或者话题本身并不需要你展开，宁可短一点、轻一点、贴着当前梗回应，也不用顺势写成主动陪伴式长回复。",
        ]
        if sender_id:
            identity_line = f"当前群聊发言者稳定 ID：{sender_id}"
            if sender_display_name and sender_display_name != sender_id:
                identity_line += f"；显示名：{sender_display_name}"
            lines.append(identity_line)
            if sender_is_target:
                lines.append("当前发言者 ID 与目标陪伴用户匹配；相关关系可以保留，但在群聊这种公共场合里，亲密度和表达还是稍微收一点更自然。")
            else:
                lines.append("当前发言者不是已配置的目标陪伴用户；更适合把 TA 当成普通群成员来接话，别把专属称呼或私聊关系直接套到 TA 身上。若要提到主人或目标用户，也更适合作为第三方提及。")
        else:
            lines.append("本轮还不能确认当前发言者的稳定 ID，所以先别只凭昵称、群名片或角色设定就把对方认成主人或目标用户。")
        if trigger:
            lines.append(f"本轮触发：{trigger}。按这个触发强度自然回应就够了，不用顺手把亲密度或话题范围再往上抬。")
        if high_active:
            lines.append("群里刚才比较密集，这轮回复更适合收一点：抓住一个重点回应就好，不必逐条点名展开。")
        return "\n".join(lines)

    async def _append_group_persona_denoise_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        if not bool(getattr(self, "enable_group_companion", True)):
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id or not self._group_enabled_for_event(group_id):
            return
        denoise_text = self._format_group_persona_denoise_prompt(event)
        if not denoise_text:
            return
        marker = "<!-- private_companion_group_persona_denoise_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            denoise_text,
            priority=32,
            source="group",
        ) else "system_prompt"
        if placement == "system_prompt":
            req.system_prompt = f"{current_prompt}\n\n{marker}\n{denoise_text}".strip()
        await self._record_request_prompt_fragment(
            event,
            title="群聊人格降噪注入",
            key="group.persona_denoise",
            text=denoise_text,
            source="group",
            mode="group",
            metadata={"注入位置": placement},
        )

    async def _append_non_target_private_identity_guard_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        marker = "<!-- private_companion_non_target_private_guard_v1 -->"
        current_prompt = req.system_prompt or ""
        if marker in current_prompt:
            return
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            user_id = ""
        user_id = _single_line(user_id, 40)
        canonical_user_id = self._canonical_private_user_id(user_id) if user_id else ""
        if canonical_user_id:
            user_id = canonical_user_id
        if not user_id or self._is_bot_self_user_id(user_id):
            return
        raw_users = self.data.get("users", {})
        current_user = raw_users.get(user_id) if isinstance(raw_users, dict) else None
        if (
            isinstance(current_user, dict)
            and self._is_target_private_user(user_id, current_user)
            and bool(current_user.get("enabled", True))
        ):
            return
        display_name = ""
        try:
            display_name = _single_line(self._sender_display_name(event), 40)
        except Exception:
            display_name = ""
        lines = [
            "【私聊身份防串】",
            f"当前私聊对象稳定 ID：{user_id}",
            "这个用户不是插件当前启用的目标陪伴用户/主用户。",
            "如果基础人格里包含“主人”“恋人”“专属称呼”或只属于主用户的关系设定,不要套用到当前私聊对象身上。",
            "可以保留人格的通用说话风格,但关系身份、亲密度、记忆和承诺必须按当前用户重新判断。",
            "除非当前用户明确提出角色扮演或临时设定,否则不要把对方当成主人、恋人或目标陪伴对象。",
        ]
        if display_name and display_name != user_id:
            lines.append(f"平台当前显示名：{display_name}。显示名只作称呼线索,不能覆盖稳定 ID。")
        profile = None
        try:
            profile = self._worldbook_profile_by_user_id(user_id)
        except Exception:
            profile = None
        if isinstance(profile, dict) and profile.get("enabled", True):
            name = _single_line(profile.get("name"), 40)
            gender = _single_line(profile.get("gender"), 40)
            identity = _single_line(profile.get("identity_note") or profile.get("note") or profile.get("content"), 220)
            boundary = _single_line(profile.get("boundary_note"), 140)
            aliases = []
            for item in profile.get("aliases") if isinstance(profile.get("aliases"), list) else []:
                alias = _single_line(item, 24)
                if alias and alias != user_id and alias not in aliases:
                    aliases.append(alias)
            lines.append("【当前用户关系网资料】")
            lines.append("以下资料来自当前私聊 QQ 号的精确匹配,只用于识别当前用户,不能外推到主用户。")
            if name and name != user_id:
                lines.append(f"登记名：{name}")
            if gender:
                lines.append(f"性别：{gender}")
            if aliases:
                lines.append(f"可用称呼线索：{'、'.join(aliases[:6])}")
            if identity:
                lines.append(f"身份备注：{identity}")
            if boundary:
                lines.append(f"互动边界：{boundary}")
            lines.append("即使此用户资料中有亲昵称呼,也必须服从上面的防串规则：不要把目标陪伴用户的专属关系套给 TA。")
        guard_text = chr(10).join(lines)
        req.system_prompt = f"{current_prompt}\n\n{marker}\n{guard_text}".strip()
        await self._record_request_prompt_fragment(
            event,
            title="非目标私聊防串注入",
            key="identity.non_target",
            text=guard_text,
            source="identity",
            mode="private",
        )

    def _message_looks_like_atrelay_request(self, text: str) -> bool:
        text = str(text or "")
        return any(token in text for token in (
            "发到", "发给", "告诉", "转告", "转达", "带话", "捎话", "通知", "私聊",
            "帮我", "替我", "你去", "跟他说", "和他说", "跟她说", "和她说", "说一声",
            "@", "艾特", "群友", "群里", "群聊", "出现", "冒泡", "上线",
        ))

    def _format_atrelay_target_summary_for_prompt(self, text: str) -> str:
        if not (self.enabled and self.enable_atrelay_tools):
            return ""
        text = str(text or "")
        if not self._message_looks_like_atrelay_request(text):
            return ""
        lines = ["【本轮转述目标摘要】"]
        has_signal = False
        group_expected = any(token in text for token in ("群里", "群聊", "发到", "发群", "群"))
        member_expected = any(token in text for token in ("找", "告诉", "转告", "转达", "跟", "和", "给", "@", "艾特", "私聊", "说一句", "说一声"))

        group_matches = self._atrelay_cached_group_matches(text)
        if group_matches:
            has_signal = True
            if len(group_matches) == 1:
                group = group_matches[0]
                lines.append(
                    "目标群候选：确定｜"
                    f"{_single_line(group.get('group_name'), 60) or group.get('group_id')}（群号:{_single_line(group.get('group_id'), 40)}）"
                    f"｜来源:{_single_line(group.get('source'), 30) or 'local'}"
                )
            else:
                parts = [
                    f"{_single_line(item.get('group_name'), 40) or item.get('group_id')}（{_single_line(item.get('group_id'), 40)}）"
                    for item in group_matches[:5]
                ]
                lines.append("目标群候选：多个｜" + "；".join(parts))
        elif group_expected:
            has_signal = True
            lines.append("目标群候选：未命中｜用户可能还需要补充群名或群号。")

        member_profiles = self._select_worldbook_member_profiles_for_private_text(text, limit=5)
        if member_profiles:
            has_signal = True
            if len(member_profiles) == 1:
                profile = member_profiles[0]
                uid = _single_line(profile.get("user_id"), 40)
                name = _single_line(profile.get("name"), 40) or uid
                identity = _single_line(profile.get("identity_note") or profile.get("note") or profile.get("content"), 100)
                parts = [f"{name}（QQ:{uid or '-'}）"]
                if identity:
                    parts.append(f"身份:{identity}")
                lines.append("目标成员候选：确定｜" + "｜".join(parts))
            else:
                parts = [
                    f"{_single_line(profile.get('name'), 32) or _single_line(profile.get('user_id'), 40)}"
                    f"（{_single_line(profile.get('user_id'), 40) or '-'}）"
                    for profile in member_profiles[:5]
                ]
                lines.append("目标成员候选：多个｜" + "；".join(parts))
        elif member_expected:
            has_signal = True
            lines.append("目标成员候选：未命中｜没有从关系网里确定收话人。")

        if not has_signal:
            return ""
        lines.append("这些只是本轮目标解析线索；真正发送仍以用户明确要求和工具执行结果为准。")
        return "\n".join(lines)

    def _parse_direct_atrelay_request(self, text: str) -> dict[str, Any]:
        cleaned = _single_line(text, 260)
        if not cleaned or not self._message_looks_like_atrelay_request(cleaned):
            return {}
        destination = ""
        if "私聊" in cleaned or "私信" in cleaned:
            destination = "private"
        elif any(token in cleaned for token in ("群里", "群聊", "发到群", "发群", "到群里", "去群里")):
            destination = "group"
        if not destination:
            return {}

        profiles = self._select_worldbook_member_profiles_for_private_text(cleaned, limit=3)
        if len(profiles) != 1:
            return {}
        profile = profiles[0]
        recipient_id = _single_line(profile.get("user_id"), 40)
        recipient_name = _single_line(profile.get("name"), 40) or recipient_id
        tokens = sorted(
            [token for token in self._worldbook_profile_tokens(profile) if token and token in cleaned],
            key=len,
            reverse=True,
        )
        target_token = tokens[0] if tokens else recipient_name
        if not target_token or target_token not in cleaned:
            return {}

        _, after = cleaned.split(target_token, 1)
        after = re.sub(r"^(?:说一句|说一声|说下|说|告诉|转告|带话|发|：|:|，|,|\s)+", "", after).strip()
        if not after:
            # “告诉 A B”这类没有“说一句”的短命令，目标后面的内容就是正文。
            after = cleaned[cleaned.find(target_token) + len(target_token):].strip()
        message = _single_line(after, 300).strip(" ：:，,。")
        if not message:
            return {}

        group_hint = ""
        if destination == "group":
            group_matches = self._atrelay_cached_group_matches(cleaned)
            if len(group_matches) == 1:
                group_hint = _single_line(group_matches[0].get("group_id") or group_matches[0].get("group_name"), 80)
        return {
            "destination": destination,
            "recipient_hint": recipient_id or recipient_name or target_token,
            "group_hint": group_hint,
            "message": message,
            "target_token": target_token,
        }

    def _pending_atrelay_requests(self) -> dict[str, Any]:
        pending = self.data.setdefault("pending_atrelay_requests", {})
        if not isinstance(pending, dict):
            pending = {}
            self.data["pending_atrelay_requests"] = pending
        now = _now_ts()
        expired = [
            key for key, item in pending.items()
            if not isinstance(item, dict) or now - _safe_float(item.get("ts"), 0) > 10 * 60
        ]
        for key in expired:
            pending.pop(key, None)
        return pending

    def _store_pending_atrelay_request(self, user_id: str, payload: dict[str, Any], reason: str = "") -> None:
        uid = _single_line(user_id, 40)
        if not uid or not isinstance(payload, dict):
            return
        pending = self._pending_atrelay_requests()
        pending[uid] = {
            "ts": _now_ts(),
            "payload": {
                "destination": _single_line(payload.get("destination"), 20),
                "recipient_hint": _single_line(payload.get("recipient_hint"), 80),
                "group_hint": _single_line(payload.get("group_hint"), 80),
                "message": _single_line(payload.get("message"), 300),
                "target_token": _single_line(payload.get("target_token"), 80),
            },
            "reason": _single_line(reason, 120),
        }
        self._save_data_sync()
        logger.info(
            "[PrivateCompanion] 转述请求等待补群: user=%s target=%s text=%s reason=%s",
            uid,
            _single_line(payload.get("recipient_hint"), 80),
            _single_line(payload.get("message"), 80),
            _single_line(reason, 120),
        )

    async def _format_direct_atrelay_final_reply(
        self,
        event: AstrMessageEvent,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        status = _single_line(result.get("status"), 40)
        fallback = _single_line(result.get("final_reply") or result.get("message"), 240)
        sender_id = ""
        try:
            sender_id = self._canonical_private_user_id(str(event.get_sender_id()))
        except Exception:
            try:
                sender_id = str(event.get_sender_id())
            except Exception:
                sender_id = ""
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        user = users.get(sender_id) if sender_id and isinstance(users, dict) and isinstance(users.get(sender_id), dict) else {}
        rewriter = getattr(self, "_rewrite_reference_reply_with_persona", None)
        if status not in {"success", "scheduled"}:
            if callable(rewriter):
                rewritten = await rewriter(
                    f"参考意图：转述没有成功；原因是「{fallback or '未知'}」。用当前人格简短告诉用户失败，不要说成已经发出。",
                    scene="跨群/私聊转述失败回执",
                    user=user,
                    event=event,
                    fallback_text=fallback or "转述没成功。",
                    task="atrelay_receipt_rewrite",
                    max_chars=80,
                    allow_fallback=True,
                    preserve_status=True,
                )
                if rewritten:
                    return rewritten
            return fallback or "转述没成功。"
        recipient = _single_line(payload.get("target_token") or payload.get("recipient_hint"), 60) or "对方"
        if status == "scheduled":
            reference = f"参考意图：转述已挂起，等{recipient}下次在群里出现或冒泡时再转达；简短告诉用户会稍后带到。"
            fallback_ok = f"等{recipient}出现我再说。"
        else:
            reference = (
                f"参考意图：转述已经成功发给{recipient}；只给用户一个很短的成功回执，"
                "不要复述转述正文，也不要写工具执行状态。"
            )
            fallback_ok = f"给{recipient}带到了。" if recipient and recipient not in {"对方", "群里"} else "带到了。"
        if callable(rewriter):
            rewritten = await rewriter(
                reference,
                scene="跨群/私聊转述成功回执",
                user=user,
                event=event,
                fallback_text=fallback_ok,
                task="atrelay_receipt_rewrite",
                max_chars=70,
                allow_fallback=True,
                preserve_status=True,
            )
            if rewritten:
                return rewritten
        return fallback_ok

    async def _send_direct_atrelay_result_reply(
        self,
        event: AstrMessageEvent,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        reply = await self._format_direct_atrelay_final_reply(event, payload, result)
        await event.send(event.plain_result(reply))

    async def _maybe_resume_pending_atrelay_request(self, event: AstrMessageEvent, user_id: str, text: str) -> bool:
        uid = _single_line(user_id, 40)
        pending = self._pending_atrelay_requests()
        item = pending.get(uid)
        if not isinstance(item, dict):
            return False
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if not payload or _single_line(payload.get("destination"), 20) != "group":
            pending.pop(uid, None)
            return False
        hint = _single_line(text, 100)
        if not hint:
            return False
        group_result = await self._resolve_atrelay_target_group(event, hint)
        if group_result.get("status") != "success":
            return False
        payload = dict(payload)
        payload["group_hint"] = _single_line(group_result.get("group_id") or hint, 80)
        result_raw = await self._pc_relay_message_impl(event, **payload)
        try:
            result = json.loads(result_raw)
        except Exception:
            result = {"status": "error", "message": _single_line(result_raw, 240)}
        pending.pop(uid, None)
        self._save_data_sync()
        logger.info(
            "[PrivateCompanion] 已用补充群名续发转述: user=%s group=%s status=%s target=%s",
            uid,
            _single_line(group_result.get("group_id"), 40),
            _single_line(result.get("status"), 40),
            _single_line(payload.get("recipient_hint"), 80),
        )
        await self._send_direct_atrelay_result_reply(event, payload, result)
        event.stop_event()
        return True

    async def _maybe_handle_direct_atrelay_request(self, event: AstrMessageEvent, text: str) -> bool:
        payload = self._parse_direct_atrelay_request(text)
        if not payload:
            return False
        result_raw = await self._pc_relay_message_impl(event, **payload)
        try:
            result = json.loads(result_raw)
        except Exception:
            result = {"status": "error", "message": _single_line(result_raw, 240)}
        status = _single_line(result.get("status"), 40)
        if status in {"need_group", "not_found"} and _single_line(payload.get("destination"), 20) == "group":
            self._store_pending_atrelay_request(str(event.get_sender_id()), payload, _single_line(result.get("message"), 120))
        logger.info(
            "[PrivateCompanion] 明确转述请求已本地直通: status=%s destination=%s target=%s text=%s",
            status or "-",
            _single_line(payload.get("destination"), 20),
            _single_line(payload.get("recipient_hint"), 40),
            _single_line(payload.get("message"), 80),
        )
        await self._send_direct_atrelay_result_reply(event, payload, result)
        event.stop_event()
        return True

    def _text_looks_like_relation_lookup_question(self, text: str) -> bool:
        cleaned = _single_line(text, 180)
        if not cleaned:
            return False
        compact = re.sub(r"\s+", "", cleaned)
        has_query_word = any(
            token in compact
            for token in (
                "认识吗",
                "认得吗",
                "知道吗",
                "是谁",
                "哪位",
                "什么人",
                "这个人",
                "这人",
                "那个人",
                "那人",
                "qq号",
                "QQ号",
                "QQ",
                "qq",
            )
        )
        if re.search(r"\d{5,12}", compact):
            return has_query_word
        if has_query_word:
            try:
                return bool(self._select_worldbook_member_profiles_for_private_text(compact, limit=1))
            except Exception:
                return True
        return False

    async def _private_reply_only_relation_lookup_text(self, event: AstrMessageEvent) -> str:
        try:
            message_id, raw_message = await self._reply_raw_message_for_event(event)
        except Exception as exc:
            logger.info("[PrivateCompanion] 私聊引用关系网问题预读取失败: %s", _single_line(exc, 120))
            return ""
        if raw_message is None:
            return ""
        try:
            info = self._extract_reply_rich_card_info(raw_message)
        except Exception as exc:
            logger.info("[PrivateCompanion] 私聊引用关系网问题解析失败: message_id=%s error=%s", message_id or "-", _single_line(exc, 120))
            return ""
        texts = [_single_line(item, 120) for item in info.get("texts", []) if _single_line(item, 120)]
        if not texts:
            return ""
        quoted_text = _single_line("；".join(texts[:3]), 180)
        if not self._text_looks_like_relation_lookup_question(quoted_text):
            return ""
        logger.info(
            "[PrivateCompanion] 私聊纯引用关系网问题已补触发文本: message_id=%s text=%s",
            message_id or "-",
            _single_line(quoted_text, 120),
        )
        return quoted_text

    async def _append_atrelay_target_summary_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> bool:
        text = str(
            getattr(event, "private_companion_group_text", "")
            or getattr(event, "message_str", "")
            or ""
        )
        summary = self._format_atrelay_target_summary_for_prompt(text)
        if not summary:
            return False
        marker = "<!-- private_companion_atrelay_target_summary_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return True
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            summary,
            priority=86,
            source="tools",
        ) else "system_prompt"
        if placement == "system_prompt":
            req.system_prompt = f"{current_prompt}\n\n{marker}\n{summary}".strip()
        await self._record_request_prompt_fragment(
            event,
            title="本轮转述目标摘要",
            key="tools.atrelay.targets",
            text=summary,
            source="tools",
            mode="conditional",
            metadata={"注入位置": placement},
        )
        return True

    async def _append_worldbook_mentions_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *,
        mode: str = "conditional",
    ) -> None:
        if not bool(getattr(self, "enable_worldbook_member_recognition", False)):
            return
        text = str(
            getattr(event, "private_companion_group_text", "")
            or getattr(event, "message_str", "")
            or ""
        )
        if self._format_atrelay_target_summary_for_prompt(text):
            return
        mention_text = self._format_worldbook_private_mentions_for_prompt(text, limit=4)
        if not mention_text:
            return
        marker = "<!-- private_companion_worldbook_mentions_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            mention_text,
            priority=58,
            source="worldbook",
        ) else "system_prompt"
        if placement == "system_prompt":
            req.system_prompt = f"{current_prompt}\n\n{marker}\n{mention_text}".strip()
        await self._record_request_prompt_fragment(
            event,
            title="本轮关系网提及注入",
            key="worldbook.mentions",
            text=mention_text,
            source="worldbook",
            mode=mode,
            metadata={"注入位置": placement},
        )

    def _request_context_text_size(self, value: Any, *, depth: int = 0) -> int:
        if depth > 8 or value is None:
            return 0
        if isinstance(value, str):
            return len(value)
        if isinstance(value, (int, float, bool)):
            return len(str(value))
        if isinstance(value, dict):
            total = 0
            for key, item in value.items():
                if str(key) in {"tool_calls", "extra_content", "metadata"}:
                    continue
                total += self._request_context_text_size(item, depth=depth + 1)
            return total
        if isinstance(value, (list, tuple)):
            return sum(self._request_context_text_size(item, depth=depth + 1) for item in value)
        return len(str(value))

    def _plain_context_content_for_fast_reply(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    if item.strip():
                        parts.append(item.strip())
                    continue
                if not isinstance(item, dict):
                    text = str(item or "").strip()
                    if text:
                        parts.append(text)
                    continue
                item_type = str(item.get("type") or "").lower()
                if item_type in {"text", "input_text"}:
                    text = str(item.get("text") or "").strip()
                    if text:
                        parts.append(text)
                elif "image" in item_type:
                    parts.append("[图片]")
                elif "audio" in item_type or "voice" in item_type:
                    parts.append("[语音]")
            return "\n".join(parts).strip()
        if isinstance(content, dict):
            for key in ("text", "content", "value"):
                if key in content:
                    return self._plain_context_content_for_fast_reply(content.get(key))
        return str(content or "").strip()

    def _trim_passive_request_context_if_needed(self, event: AstrMessageEvent, req: ProviderRequest, *, is_private_chat: bool) -> None:
        if not is_private_chat:
            return
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list) or len(contexts) <= 24:
            return
        approx_tokens = max(0, self._request_context_text_size(contexts) // 4)
        if approx_tokens < 50000 and len(contexts) < 120:
            return
        trimmed: list[Any] = []
        for item in contexts[-36:]:
            if not isinstance(item, dict):
                text = self._plain_context_content_for_fast_reply(item)
                if text:
                    trimmed.append({"role": "user", "content": _single_line(text, 1200)})
                continue
            role = str(item.get("role") or "").strip().lower()
            if role not in {"system", "user", "assistant"}:
                continue
            text = self._plain_context_content_for_fast_reply(item.get("content"))
            if not text:
                continue
            trimmed.append({"role": role, "content": _single_line(text, 1200)})
        trimmed = trimmed[-24:]
        if not trimmed:
            return
        try:
            req.contexts = trimmed
        except Exception:
            return
        logger.info(
            "[PrivateCompanion] 私聊超长上下文已启用轻量护栏: session=%s contexts=%s->%s approx_tokens=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            len(contexts),
            len(trimmed),
            approx_tokens,
        )

    def _context_text_is_new_conversation_boundary(self, text: Any) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        compact = re.sub(r"\s+", "", raw).lower()
        if compact in {"/new", "／new"}:
            return True
        if "switchedtonewconversation" in compact:
            return True
        if re.search(r"(已|成功)?(切换|开启|创建|新建).{0,8}(新)?会话", raw, flags=re.IGNORECASE):
            return True
        return False

    def _sanitize_request_context_new_conversation_boundary(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list) or not contexts:
            return
        boundary_index = -1
        for index, item in enumerate(contexts):
            text = self._plain_context_content_for_fast_reply(item.get("content") if isinstance(item, dict) else item)
            if self._context_text_is_new_conversation_boundary(text):
                boundary_index = index
        if boundary_index < 0:
            return
        trimmed: list[Any] = []
        for item in contexts[boundary_index + 1:]:
            text = self._plain_context_content_for_fast_reply(item.get("content") if isinstance(item, dict) else item)
            if self._context_text_is_new_conversation_boundary(text):
                continue
            trimmed.append(item)
        if len(trimmed) == len(contexts):
            return
        try:
            req.contexts = trimmed
        except Exception:
            return
        logger.info(
            "[PrivateCompanion] 已按新会话边界裁剪 AstrBot 上下文: session=%s contexts=%s->%s boundary_index=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            len(contexts),
            len(trimmed),
            boundary_index,
        )

    def _tool_name_for_private_passive_guard(self, tool: Any) -> str:
        if tool is None:
            return ""
        if isinstance(tool, dict):
            for key in ("name", "tool_name"):
                value = tool.get(key)
                if value:
                    return str(value).strip()
            func = tool.get("function")
            if isinstance(func, dict) and func.get("name"):
                return str(func.get("name")).strip()
        for attr in ("name", "tool_name", "__name__"):
            value = getattr(tool, attr, None)
            if value:
                return str(value).strip()
        return ""

    @staticmethod
    def _private_passive_tool_blocklist() -> set[str]:
        return {
            "send_message_to_user",
            "future_task",
            "astrbot_file_read_tool",
            "astrbot_file_write_tool",
            "astrbot_file_edit_tool",
            "astrbot_grep_tool",
            "astrbot_execute_shell",
            "astrbot_execute_python",
            "launch_application",
            "screenshot",
            "move_mouse",
            "click_mouse",
            "type_text",
            "press_key",
        }

    @staticmethod
    def _private_passive_tool_leak_text(text: str) -> bool:
        if not text:
            return False
        lowered = text.lower()
        hard_markers = (
            "permission denied. send message to another session",
            "platform_id:message_type:session_id",
            "you must use the `send_message_to_user` tool",
            "use `send_message_to_user` to deliver",
            "image_generation_task_result",
            "agent 使用工具",
            "tool `send_message_to_user` result",
            "all chat models failed",
            "badrequesterror",
            "provider api error",
            "functiondeclaration",
            "schema didn't specify",
        )
        if any(marker in lowered for marker in hard_markers):
            return True
        tool_markers = (
            "send_message_to_user",
            "future_task",
            "web_search_tavily",
            "astrbot_file_read_tool",
            "astrbot_file_write_tool",
        )
        if sum(1 for marker in tool_markers if marker in lowered) >= 2:
            return True
        return False

    def _sanitize_passive_private_agent_context(self, event: AstrMessageEvent, req: ProviderRequest) -> int:
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list) or not contexts:
            return 0
        cleaned: list[Any] = []
        removed = 0
        reasoning_cleaned = 0
        for item in contexts:
            role = ""
            content: Any = item
            if isinstance(item, dict):
                role = str(item.get("role") or "").strip().lower()
                if role in {"assistant", "ai", "bot"}:
                    item, stripped_reasoning, drop_item = self._strip_assistant_reasoning_context(item)
                    if drop_item:
                        removed += 1
                        continue
                    if stripped_reasoning:
                        reasoning_cleaned += 1
                content = item.get("content")
            text = self._plain_context_content_for_fast_reply(content)
            proactive_archive_checker = getattr(self, "_proactive_archive_context_text", None)
            if callable(proactive_archive_checker):
                try:
                    if proactive_archive_checker(text):
                        removed += 1
                        continue
                except Exception:
                    pass
            if role in {"assistant", "system", "tool"} and self._private_passive_tool_leak_text(text):
                removed += 1
                continue
            cleaned.append(item)
        if removed or reasoning_cleaned:
            try:
                req.contexts = cleaned
            except Exception:
                return 0
            logger.info(
                "[PrivateCompanion] 私聊被动主链已清理工具代理/主动占位/推理片段污染历史: session=%s removed=%s reasoning_cleaned=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                removed,
                reasoning_cleaned,
            )
        return removed + reasoning_cleaned

    @staticmethod
    def _strip_assistant_reasoning_context(item: dict[str, Any]) -> tuple[dict[str, Any], bool, bool]:
        content = item.get("content")
        if isinstance(content, list):
            kept: list[Any] = []
            changed = False
            for part in content:
                if isinstance(part, dict):
                    part_type = str(part.get("type") or part.get("kind") or "").strip().lower()
                    if part_type in {"think", "thinking", "reasoning", "reasoning_content"}:
                        changed = True
                        continue
                kept.append(part)
            if not changed:
                return item, False, False
            if not kept:
                return item, True, True
            updated = dict(item)
            updated["content"] = kept
            return updated, True, False
        if isinstance(content, dict):
            part_type = str(content.get("type") or content.get("kind") or "").strip().lower()
            if part_type in {"think", "thinking", "reasoning", "reasoning_content"}:
                return item, True, True
        return item, False, False

    def _sanitize_passive_private_agent_tools(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        force_sanitize = bool(getattr(event, "private_companion_force_sanitize_tools", False))
        if bool(getattr(event, "is_wake", False)) and not force_sanitize:
            return
        if "cron" in event.__class__.__name__.lower():
            return
        prompt_surface = "\n".join(
            [
                str(getattr(req, "system_prompt", "") or ""),
                str(getattr(req, "prompt", "") or ""),
            ]
        )
        if "image_generation_task_result" in prompt_surface or "send_message_to_user` to deliver" in prompt_surface:
            return
        blocklist = self._private_passive_tool_blocklist()
        toolset = getattr(req, "func_tool", None)
        removed: list[str] = []
        if toolset is not None:
            tools = getattr(toolset, "tools", None)
            if isinstance(tools, list):
                kept = []
                for tool in tools:
                    name = self._tool_name_for_private_passive_guard(tool)
                    if name in blocklist:
                        removed.append(name)
                        continue
                    kept.append(tool)
                if len(kept) != len(tools):
                    try:
                        toolset.tools = kept
                    except Exception:
                        pass
            elif isinstance(tools, dict):
                for name in list(tools.keys()):
                    clean_name = str(name or "").strip()
                    if clean_name in blocklist:
                        removed.append(clean_name)
                        tools.pop(name, None)
            elif isinstance(toolset, list):
                kept = []
                for tool in toolset:
                    name = self._tool_name_for_private_passive_guard(tool)
                    if name in blocklist:
                        removed.append(name)
                        continue
                    kept.append(tool)
                if len(kept) != len(toolset):
                    try:
                        req.func_tool = kept
                    except Exception:
                        pass
        context_removed = self._sanitize_passive_private_agent_context(event, req)
        if removed:
            logger.info(
                "[PrivateCompanion] 私聊被动主链已裁剪高风险工具: session=%s tools=%s context_removed=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                ",".join(sorted(set(removed))),
                context_removed,
            )

    def _rest_reply_window_active(self) -> bool:
        raw = str(getattr(self, "rest_reply_active_windows", "") or "").strip()
        if not raw:
            return True
        raw = re.sub(r"\s+", "", raw)
        now_minutes = self._environment_now_minutes()
        for part in re.split(r"[,;；，、]+", raw):
            window = part.strip()
            if not window:
                continue
            start, end = self._parse_window_minutes(window)
            if start is None or end is None:
                continue
            for candidate in (now_minutes, now_minutes + 24 * 60):
                if start <= candidate < end:
                    return True
        return False

    def _rest_reply_sleep_context(self) -> tuple[bool, dict[str, Any], dict[str, Any] | None, str]:
        try:
            current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
            runtime = self._refresh_sleep_runtime_state(current_item)
        except Exception:
            current_item = None
            runtime = self._sleep_runtime_state()
        phase = str((runtime or {}).get("phase") or "")
        window_active = self._rest_reply_window_active()
        sleepy_item = window_active and self._is_sleepy_plan_item(current_item) if isinstance(current_item, dict) else False
        sleeping = window_active and (phase in {"falling_asleep", "light_sleep", "sleeping_again"} or sleepy_item)
        if phase == "woken":
            sleeping = False
        if phase in {"natural_wake", "awake"} and not sleepy_item:
            sleeping = False
        schedule_text = self._format_plan_item_for_prompt(current_item) if isinstance(current_item, dict) else ""
        return sleeping, runtime if isinstance(runtime, dict) else {}, current_item, _single_line(schedule_text, 220)

    @staticmethod
    def _rest_reply_boundary_score(text: str) -> tuple[int, str]:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return 0, "empty"
        no_reply_boundary = r"(?:了|啦|吧|我|这(?:个|条|句|段)(?:消息|话|话题|内容|问题)?|这(?:条)?消息|本条消息|消息|哈|噢|哦|$|[，。！？,.!?])"
        if re.search(
            r"(?:不用|不必|无需|别|不要|先别|暂时别|今晚别|今天别)(?:再)?(?:回(?:复)?|理我|搭理我|接话|说话|出声)"
            + no_reply_boundary,
            compact,
        ):
            return -100, "user_asks_no_reply"
        proactive_only_quiet = bool(
            re.search(r"(?:别|不要|先别|暂时别|今晚别|今天别).{0,10}主动.{0,8}(?:打扰|吵|发消息|找我|回(?:复)?|理我|搭理我|接话|说话)", compact)
        )
        if re.search(r"你.{0,6}(没睡|没在睡|不是在睡|不是睡|不在睡|还没睡|醒着|清醒|没休息|没在休息|不是在休息|不是休息|不在休息)|别装睡|别装休息|装睡|装睡觉|明明醒着|明明没睡|明明没休息|又没睡|又没休息", compact):
            return 100, "user_corrects_not_resting"
        if re.search(r"快醒|醒醒|醒一醒|醒来|醒过来|别睡了?|别睡啦|别睡嘛|先别睡|别睡别睡|起床|起来|快起|回我一下|快回|马上回", compact):
            return 100, "explicit_wakeup_request"
        quiet_pattern = (
            r"(?:不用|不必|无需|别|不要|先别|暂时别|今晚别|今天别)(?:再)?(?:回(?:复)?|理我|搭理我|接话|说话|出声)"
            + no_reply_boundary
            + r"|(?:别|不要|先别|暂时别|今晚别|今天别).{0,10}(?:打扰|吵我|叫我|主动|发消息|找我)"
            r"|(?:安静点|闭嘴|别说话|不要说话|别醒|继续睡)"
        )
        if not proactive_only_quiet and re.search(quiet_pattern, compact):
            return -100, "user_asks_quiet"
        if re.search(r"(?:晚安|好梦|早点睡|早点休息|睡个好觉)", compact):
            return 100, "goodnight_ack"
        if proactive_only_quiet:
            return 0, "user_asks_no_proactive"
        if re.search(r"救命|出事|急|紧急|重要|不舒服|难受|害怕|崩溃|报警|医院|摔|痛", compact):
            return 100, "urgent_or_explicit_wakeup"
        if re.search(r"醒了吗|睡了吗|在吗|能不能回|可以回吗|想你|陪我|听我说|还睡吗|还在睡吗", compact):
            return 72, "soft_wakeup_request"
        return 0, "normal"

    async def _rest_reply_llm_score(
        self,
        *,
        text: str,
        schedule_text: str,
        runtime: dict[str, Any],
        is_private_chat: bool,
    ) -> tuple[int, str]:
        prompt = f"""
你是一个睡眠/休息中是否需要醒来回复的判定器。请只输出 JSON。

背景：
- Bot 当前日程处于睡眠、午休或休息段。
- 当前睡眠阶段：{_single_line(runtime.get("label") or runtime.get("phase"), 40) or "未知"}。
- 当前日程：{schedule_text or "未知"}。
- 会话类型：{"私聊" if is_private_chat else "群聊"}。

判断原则：
- 只有用户明显需要回应、明确叫醒、情绪/安全/紧急需要支持，或继续不回复会显得很不合适时，才建议醒来。
- 普通闲聊、表情、无明确对象的群聊、轻微玩笑、可等到醒来再说的内容，应保持睡眠不回复。
- 如果用户明确说不要打扰、别回、继续睡，必须不回复。

用户消息：
{_single_line(text, 800)}

只输出 JSON：
{{"score": 0-100, "should_reply": true/false, "reason": "一句话原因"}}
""".strip()
        raw = await self._llm_call(
            prompt,
            max_tokens=180,
            provider_id=self._task_provider(self.rest_wakeup_provider_id, self.response_review_provider_id, self.llm_provider_id),
            task="rest_wakeup_judge",
        )
        payload = self._extract_json_payload(raw or "")
        if not isinstance(payload, dict):
            return 0, "llm_invalid"
        try:
            score = max(0, min(100, int(float(payload.get("score", 0)))))
        except (TypeError, ValueError):
            score = 0
        should_reply = bool(payload.get("should_reply"))
        reason = _single_line(payload.get("reason"), 80) or "llm"
        if should_reply and score < self.rest_reply_llm_threshold:
            score = self.rest_reply_llm_threshold
        return score, reason

    async def _should_reply_during_rest(self, event: AstrMessageEvent, *, is_private_chat: bool) -> tuple[bool, str]:
        if not self.enable_rest_reply_simulation:
            return True, "disabled"
        sleeping, runtime, _current_item, schedule_text = self._rest_reply_sleep_context()
        if not sleeping:
            return True, "not_sleeping"
        text = _single_line(getattr(event, "message_str", ""), 800)
        boundary_score, boundary_reason = self._rest_reply_boundary_score(text)
        if boundary_score < 0:
            return False, boundary_reason
        if boundary_score >= max(1, self.rest_reply_llm_threshold):
            try:
                self._mark_sleep_woken_by_user(text)
            except Exception:
                pass
            return True, boundary_reason
        mode = getattr(self, "rest_reply_mode", "probability")
        if mode == "llm":
            score, reason = await self._rest_reply_llm_score(
                text=text,
                schedule_text=schedule_text,
                runtime=runtime,
                is_private_chat=is_private_chat,
            )
            allowed = score >= self.rest_reply_llm_threshold
            if allowed:
                try:
                    self._mark_sleep_woken_by_user(text)
                except Exception:
                    pass
            return allowed, f"llm:{score}/{self.rest_reply_llm_threshold}:{reason}"
        probability = max(0.0, min(1.0, float(getattr(self, "rest_reply_probability", 0.0) or 0.0)))
        hit = random.random() <= probability
        if hit:
            try:
                self._mark_sleep_woken_by_user(text)
            except Exception:
                pass
        return hit, f"probability:{probability:.2f}"

    def _rest_backlog_user_for_event(self, event: AstrMessageEvent) -> tuple[str, dict[str, Any] | None]:
        try:
            if not bool(getattr(event, "is_private_chat", lambda: False)()):
                return "", None
        except Exception:
            return "", None
        try:
            user_id = self._canonical_private_user_id(str(event.get_sender_id()))
        except Exception:
            return "", None
        users = self.data.get("users", {})
        user = users.get(user_id) if isinstance(users, dict) else None
        if not isinstance(user, dict):
            return user_id, None
        if not self._is_target_private_user(user_id, user) or not bool(user.get("enabled", True)):
            return user_id, None
        return user_id, user

    def _record_rest_reply_backlog(self, event: AstrMessageEvent, reason: str) -> None:
        if not bool(getattr(self, "enable_rest_backlog_reply", True)):
            return
        user_id, user = self._rest_backlog_user_for_event(event)
        if not isinstance(user, dict):
            return
        text = _single_line(getattr(event, "message_str", ""), 240)
        if not text:
            text = "发来了一条非文本消息"
        backlog = user.get("rest_reply_backlog")
        if not isinstance(backlog, list):
            backlog = []
        now = time.time()
        backlog.append(
            {
                "ts": now,
                "text": text,
                "reason": _single_line(reason, 80),
            }
        )
        max_items = max(1, _safe_int(getattr(self, "rest_backlog_max_messages", 4), 4, 1))
        user["rest_reply_backlog"] = backlog[-max_items:]
        user["rest_reply_backlog_updated_at"] = now
        self._schedule_data_save()
        logger.info(
            "[PrivateCompanion] 已记录休息中未回复私聊: user=%s count=%s reason=%s text=%s",
            user_id,
            len(user["rest_reply_backlog"]),
            _single_line(reason, 80),
            _single_line(text, 80),
        )

    def _take_rest_reply_backlog_prompt(self, user: dict[str, Any]) -> str:
        if not bool(getattr(self, "enable_rest_backlog_reply", True)):
            return ""
        backlog = user.get("rest_reply_backlog")
        if not isinstance(backlog, list) or not backlog:
            return ""
        max_items = max(1, _safe_int(getattr(self, "rest_backlog_max_messages", 4), 4, 1))
        items = [item for item in backlog[-max_items:] if isinstance(item, dict)]
        if not items:
            user["rest_reply_backlog"] = []
            user["rest_reply_backlog_updated_at"] = 0
            self._schedule_data_save()
            return ""
        lines: list[str] = []
        for idx, item in enumerate(items, 1):
            ts = _safe_float(item.get("ts"), 0)
            if ts > 0:
                try:
                    when = self._environment_fromtimestamp(ts).strftime("%H:%M")
                except Exception:
                    when = datetime.fromtimestamp(ts).strftime("%H:%M")
            else:
                when = "刚才"
            text = _single_line(item.get("text"), 180) or "发来了一条消息"
            lines.append(f"{idx}. {when}｜{text}")
        user["rest_reply_backlog"] = []
        user["rest_reply_backlog_updated_at"] = 0
        self._schedule_data_save()
        if not lines:
            return ""
        return "休息时有几条私聊没来得及回，醒来后补看到：\n" + "\n".join(lines)

    async def _append_rest_reply_backlog_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        user: dict[str, Any],
    ) -> str:
        backlog_prompt = self._take_rest_reply_backlog_prompt(user)
        if not backlog_prompt:
            return ""
        marker = "<!-- private_companion_rest_backlog_v1 -->"
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            backlog_prompt,
            priority=25,
            source="daily_state",
        ) else "system_prompt"
        if placement == "system_prompt":
            req.system_prompt = f"{req.system_prompt or ''}\n\n{marker}\n{backlog_prompt}".strip()
        await self._record_request_prompt_fragment(
            event,
            title="醒后补看私聊",
            key="rest.backlog",
            text=backlog_prompt,
            source="daily_state",
            mode="private",
            metadata={"注入位置": placement},
        )
        return backlog_prompt

    def _stop_reply_for_rest_gate(self, event: AstrMessageEvent, reason: str) -> None:
        self._record_rest_reply_backlog(event, reason)
        logger.info(
            "[PrivateCompanion] 睡眠/休息回复闸门拦截本轮被动回复: session=%s reason=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            _single_line(reason, 120),
        )
        self._record_passive_no_reply(
            event,
            source="休息闸门",
            reason=reason or "睡眠/休息回复闸门拦截",
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    def _stop_private_reply_after_user_rest_signal(self, event: AstrMessageEvent, user_id: str, text: str) -> None:
        logger.info(
            "[PrivateCompanion] 用户明确勿扰/不用回复,已前置拦截本轮私聊回复: user=%s text=%s",
            _single_line(user_id, 80),
            _single_line(text, 120),
        )
        self._record_passive_no_reply(
            event,
            source="休息静默",
            reason="用户明确要求勿扰或不用回复",
            detail=text,
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    def _passive_no_reply_event_text(self, event: AstrMessageEvent | None, *, limit: int = 180) -> str:
        if event is None:
            return ""
        candidates = [
            getattr(event, "private_companion_group_text", ""),
            getattr(event, "message_str", ""),
        ]
        message_obj = getattr(event, "message_obj", None)
        if message_obj is not None:
            candidates.append(getattr(message_obj, "message_str", ""))
        for value in candidates:
            text = _single_line(value, limit)
            if text:
                return text
        component_types: list[str] = []
        try:
            for item in self._event_components(event):
                name = _single_line(self._component_type_name(item), 32)
                if name and name not in component_types:
                    component_types.append(name)
        except Exception:
            component_types = []
        return ",".join(component_types[:6])

    def _record_passive_no_reply(
        self,
        event: AstrMessageEvent | None,
        *,
        source: str,
        reason: str,
        detail: str = "",
        level: str = "info",
        action: str = "",
        reply_preview: str = "",
    ) -> None:
        if bool(getattr(event, "_private_companion_passive_no_reply_recorded", False)):
            return
        if bool(getattr(event, "private_companion_proactive_framework", False)):
            return
        source_text = _single_line(source, 40) or "被动未回复"
        reason_text = _single_line(reason, 120) or "未说明原因"
        level_text = _single_line(level, 12)
        if level_text not in {"error", "warn", "info"}:
            level_text = "info"
        now = _now_ts()
        session = _single_line(getattr(event, "unified_msg_origin", ""), 160) if event is not None else ""
        try:
            sender_id = _single_line(event.get_sender_id(), 80) if event is not None else ""
        except Exception:
            sender_id = ""
        inbound = self._passive_no_reply_event_text(event)
        detail_text = _single_line(detail, 220)
        reply_text = _single_line(reply_preview, 180)
        key = hashlib.sha1(f"{source_text}|{reason_text}".encode("utf-8", errors="ignore")).hexdigest()[:16]
        root = self.data.setdefault("passive_no_reply_records", {})
        if not isinstance(root, dict):
            root = {}
            self.data["passive_no_reply_records"] = root
        items = root.setdefault("items", [])
        if not isinstance(items, list):
            items = []
            root["items"] = items
        target: dict[str, Any] | None = None
        for item in items:
            if isinstance(item, dict) and item.get("key") == key:
                target = item
                break
        if target is None:
            target = {
                "key": key,
                "source": source_text,
                "reason": reason_text,
                "level": level_text,
                "count": 0,
                "first_ts": now,
                "last_ts": 0,
                "samples": [],
            }
            items.append(target)
        target["source"] = source_text
        target["reason"] = reason_text
        target["level"] = level_text
        target["count"] = _safe_int(target.get("count"), 0, 0) + 1
        target["last_ts"] = now
        target["last_session"] = session
        target["last_sender_id"] = sender_id
        target["last_inbound"] = inbound
        target["last_detail"] = detail_text
        target["last_action"] = _single_line(action, 120)
        target["last_reply_preview"] = reply_text
        sample = {
            "ts": now,
            "time": self._format_timestamp_elapsed(now),
            "session": session,
            "sender_id": sender_id,
            "inbound": inbound,
            "detail": detail_text,
            "reply_preview": reply_text,
        }
        samples = target.setdefault("samples", [])
        if not isinstance(samples, list):
            samples = []
            target["samples"] = samples
        samples.insert(0, sample)
        del samples[5:]
        root["total"] = _safe_int(root.get("total"), 0, 0) + 1
        root["last_ts"] = now
        items.sort(key=lambda item: _safe_float(item.get("last_ts"), 0) if isinstance(item, dict) else 0, reverse=True)
        del items[80:]
        if event is not None:
            try:
                setattr(event, "_private_companion_passive_no_reply_recorded", True)
            except Exception:
                pass
        logger.info(
            "[PrivateCompanion] 已记录被动未回复: source=%s reason=%s count=%s session=%s inbound=%s",
            source_text,
            reason_text,
            target.get("count"),
            session or "-",
            _single_line(inbound, 120),
        )
        try:
            self._schedule_data_save()
        except Exception:
            pass

    def _proactive_only_unlock_store(self) -> set[str]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return set()
        raw = data.get("proactive_only_temp_unlocks", [])
        if isinstance(raw, dict):
            items = raw.keys()
        elif isinstance(raw, (list, tuple, set)):
            items = raw
        else:
            items = []
        return {str(item).strip() for item in items if str(item or "").strip()}

    def _set_proactive_only_unlock_store(self, keys: set[str]) -> None:
        self.data["proactive_only_temp_unlocks"] = sorted(keys)

    def _normalize_proactive_only_unlock_key(self, value: Any) -> str:
        text = _single_line(value, 80).strip()
        if not text:
            return ""
        return _PROACTIVE_ONLY_TEMP_UNLOCK_ALIASES.get(text, text)

    def _proactive_only_unlock_label(self, key: str) -> str:
        return _PROACTIVE_ONLY_TEMP_UNLOCK_LABELS.get(key, key)

    def _proactive_only_temp_unlock_allows(self, feature: str = "") -> bool:
        unlocks = self._proactive_only_unlock_store()
        if not unlocks:
            return False
        if "all" in unlocks:
            return True
        feature = str(feature or "").strip()
        if not feature:
            return False
        if feature in unlocks:
            return True
        group = _PROACTIVE_ONLY_TEMP_UNLOCK_GROUPS.get(feature, set())
        return bool(group and (group & unlocks))

    def _feature_enabled_or_temp_unlocked(self, feature: str, default: bool = False) -> bool:
        if bool(getattr(self, feature, default)):
            return True
        return bool(
            getattr(self, "enable_proactive_only_mode", False)
            and self._proactive_only_temp_unlock_allows(feature)
        )

    def _proactive_only_limited_passive_event(self, event: AstrMessageEvent | None) -> bool:
        return bool(
            getattr(self, "enable_proactive_only_mode", False)
            and not bool(getattr(event, "private_companion_proactive_framework", False))
        )

    def _proactive_only_llm_request_needs_full_path(self) -> bool:
        unlocks = self._proactive_only_unlock_store()
        if "all" in unlocks or "llm_request" in unlocks:
            return True
        full_path_keys = {
            "inject_passive_states",
            "enable_intent_emotion_analysis",
            "enable_llm_timer_scheduling",
            "enable_passive_topic_suppression",
            "enable_private_image_self_recognition",
            "enable_group_companion",
            "enable_skill_growth_passive_injection",
            "enable_private_reading_preference_influence",
            "enable_worldbook_member_recognition",
            "enable_livingmemory_integration",
        }
        return bool(full_path_keys & unlocks)

    async def _append_proactive_only_unlocked_llm_request_fragments(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        if self._proactive_only_temp_unlock_allows("enable_tts_enhancement"):
            await self.apply_tts_enhancement_request(event, req)
        if self._proactive_only_temp_unlock_allows("enable_forward_message_adaptation"):
            await self._append_forward_message_context_to_request(event, req)
        if self._proactive_only_temp_unlock_allows("enable_environment_perception"):
            await self._append_environment_perception_to_request(event, req)

    def _clear_proactive_only_temp_unlocks_if_mode_off(self) -> None:
        if getattr(self, "enable_proactive_only_mode", False):
            return
        if not self._proactive_only_unlock_store():
            return
        self.data["proactive_only_temp_unlocks"] = []
        self._schedule_data_save()

    def _format_proactive_only_temp_unlocks(self) -> str:
        unlocks = self._proactive_only_unlock_store()
        if not unlocks:
            return "当前没有临时放行项。"
        labels = [self._proactive_only_unlock_label(key) for key in sorted(unlocks)]
        return "当前主动专用模式临时放行：\n" + "\n".join(f"- {label}" for label in labels)

    def _related_proactive_only_unlock_keys(self, key: str) -> list[str]:
        related = list(_PROACTIVE_ONLY_TEMP_UNLOCK_RELATED.get(key, []) or [])
        return [item for item in related if item and item != key]

    def _apply_proactive_only_temp_unlock(self, key: str, *, sync_related: bool = False, clear: bool = False) -> str:
        normalized = self._normalize_proactive_only_unlock_key(key)
        if not normalized:
            return "没有识别到要临时放行的功能。"
        keys = self._proactive_only_unlock_store()
        target_keys = {normalized}
        if sync_related:
            target_keys.update(self._related_proactive_only_unlock_keys(normalized))
        if clear:
            removed = keys & target_keys
            keys.difference_update(target_keys)
            self._set_proactive_only_unlock_store(keys)
            self._save_data_sync()
            if not removed:
                return "对应临时放行项本来就没有开启。"
            return "已取消临时放行：\n" + "\n".join(f"- {self._proactive_only_unlock_label(item)}" for item in sorted(removed))
        keys.update(target_keys)
        self._set_proactive_only_unlock_store(keys)
        self._save_data_sync()
        return "已临时放行：\n" + "\n".join(f"- {self._proactive_only_unlock_label(item)}" for item in sorted(target_keys))

    def _proactive_only_blocks_passive_event(self, event: AstrMessageEvent | None, feature: str = "") -> bool:
        if not bool(getattr(self, "enable_proactive_only_mode", False)):
            self._clear_proactive_only_temp_unlocks_if_mode_off()
            return False
        if bool(getattr(event, "private_companion_proactive_framework", False)):
            return False
        return not self._proactive_only_temp_unlock_allows(feature)

    async def _record_proactive_only_private_feedback(
        self,
        event: AstrMessageEvent,
        *,
        user_id: str,
        sender_display_name: str,
        text: str,
        received_ts: float,
    ) -> None:
        """主动专用模式下只记录用户回应,不接管被动回复链路。"""
        async with self._data_lock:
            users = self.data.get("users", {})
            canonical_user_id = self._canonical_private_user_id(user_id)
            user = users.get(canonical_user_id) if isinstance(users, dict) else None
            if not isinstance(user, dict):
                return
            user_id = canonical_user_id
            if not self._is_target_private_user(user_id, user) or not bool(user.get("enabled", True)):
                return
            if self._is_recent_poke_echo(user, text):
                logger.info("[PrivateCompanion] 主动专用模式忽略 poke 回流事件: user=%s", user_id)
                return
            if self._is_duplicate_inbound_message(event, scope=f"private:{user_id}", sender_id=user_id, text=text):
                self._schedule_data_save()
                return
            self._note_private_user_umo(user_id, user, event.unified_msg_origin)
            self._note_private_display_name_observation(user, user_id, sender_display_name, now=received_ts)
            user["last_seen"] = received_ts
            user["last_activity_at"] = received_ts
            self._note_private_inbound_activity(user, received_ts, text=text)
            self._mark_greetings_satisfied_by_recent_activity(user, activity_ts=received_ts)
            if text:
                safe_text = self._sanitize_orphan_tts_placeholders(text)
                user["last_user_message"] = safe_text or text
                user["last_user_message_at"] = received_ts
                user["inbound_count"] = _safe_int(user.get("inbound_count"), 0) + 1
                user["episode_message_count"] = _safe_int(user.get("episode_message_count"), 0, 0) + 1
                self._apply_user_rest_silence_from_message(user, safe_text or text, now=received_ts)
            if _safe_float(user.get("awaiting_reply_since"), 0) > 0:
                user["reply_count"] = _safe_int(user.get("reply_count"), 0) + 1
                self._note_action_reply_feedback(
                    user,
                    str(user.get("last_proactive_action") or "message"),
                    text,
                )
                user["relationship_score"] = _safe_int(user.get("relationship_score"), 0) + 2
                user["awaiting_reply_since"] = 0
                user["last_reply_at"] = received_ts
                user["last_private_reply_at"] = received_ts
                user["pending_followup_event"] = {}
                user["planned_proactive_quota_exempt"] = False
            user["ignored_streak"] = 0
            self._schedule_data_save()
        logger.info(
            "[PrivateCompanion] 主动消息专用模式已跳过私聊被动增强: user=%s text=%s",
            user_id,
            _single_line(text, 80) or "非文本消息",
        )

    @filter.on_llm_request()
    async def inject_tts_enhancement_request_fallback(self, event: AstrMessageEvent, req: ProviderRequest):
        """TTS 请求规则独立兜底，避免被状态注入链路早退顺手跳过。"""
        if not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_tts_enhancement"):
            return
        await self.apply_tts_enhancement_request(event, req)

    @filter.on_llm_request()
    async def inject_humanized_state(self, event: AstrMessageEvent, req: ProviderRequest):
        """LLM 请求前注入陪伴状态、群聊上下文、工具边界和合并消息阅读上下文。"""
        def log_bookshelf_secret_skip(reason: str, user: dict[str, Any] | None = None, text: str = "") -> None:
            logger_func = getattr(self, "_log_bookshelf_secret_skip", None)
            if not callable(logger_func):
                return
            source_text = text
            if not source_text:
                source_text = (
                    getattr(event, "private_companion_group_text", "")
                    or getattr(event, "message_str", "")
                    or ""
                )
            logger_func(reason, source_text, user if isinstance(user, dict) else None)

        if not self.enabled:
            return
        if not hasattr(req, "system_prompt"):
            log_bookshelf_secret_skip("llm_request_no_system_prompt")
            self._release_framework_session_lock_for_event(event, label="llm_request_no_system_prompt")
            return
        self._sanitize_request_context_new_conversation_boundary(event, req)
        self._remember_external_llm_request_for_token_stats(event, req)
        proactive_only_limited = self._proactive_only_limited_passive_event(event)
        if self._proactive_only_blocks_passive_event(event, "llm_request"):
            log_bookshelf_secret_skip("proactive_only_mode")
            self._release_framework_session_lock_for_event(event, label="proactive_only_mode")
            return
        if proactive_only_limited and not self._proactive_only_llm_request_needs_full_path():
            await self._append_proactive_only_unlocked_llm_request_fragments(event, req)
            log_bookshelf_secret_skip("proactive_only_limited_light_path")
            return
        is_private_chat = bool(getattr(event, "is_private_chat", lambda: False)())
        private_user_active = False
        if is_private_chat:
            try:
                private_user_id = self._canonical_private_user_id(str(event.get_sender_id()))
            except Exception:
                private_user_id = ""
            raw_users = self.data.get("users", {})
            private_user = raw_users.get(private_user_id) if private_user_id and isinstance(raw_users, dict) else None
            private_user_active = (
                isinstance(private_user, dict)
                and self._is_target_private_user(private_user_id, private_user)
                and bool(private_user.get("enabled", True))
            )
            if not private_user_active:
                reason = "private_user_missing" if not isinstance(private_user, dict) else "private_user_disabled"
                log_bookshelf_secret_skip(reason, private_user if isinstance(private_user, dict) else None)
                logger.info(
                    "[PrivateCompanion] 非目标/未启用私聊跳过陪伴被动增强: user=%s reason=%s",
                    _single_line(private_user_id, 40) or "unknown",
                    reason,
                )
        rest_allowed, rest_reason = await self._should_reply_during_rest(event, is_private_chat=is_private_chat)
        if not rest_allowed:
            log_bookshelf_secret_skip(f"rest_reply_gate:{_single_line(rest_reason, 60)}")
            self._release_framework_session_lock_for_event(event, label="rest_reply_gate")
            self._stop_reply_for_rest_gate(event, rest_reason)
            return
        if rest_reason not in {"disabled", "not_sleeping"}:
            try:
                setattr(event, "private_companion_rest_reply_gate_reason", rest_reason)
            except Exception:
                pass
            logger.info(
                "[PrivateCompanion] 睡眠/休息回复闸门放行本轮被动回复: session=%s reason=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(rest_reason, 120),
            )
        self._trim_passive_request_context_if_needed(event, req, is_private_chat=is_private_chat)
        if is_private_chat:
            self._sanitize_passive_private_agent_tools(event, req)
        await self._enrich_request_context_image_placeholders(event, req)
        group_id_for_lock = ""
        if not is_private_chat and self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            group_id_for_lock = self._extract_group_id_from_event(event)
            if group_id_for_lock and self._group_enabled_for_event(group_id_for_lock):
                await self._acquire_framework_session_lock_for_event(
                    event,
                    label="group_llm_request",
                    private_only=False,
                )
        if (
            bool(getattr(event, "private_companion_deferred_private_image_only", False))
            and not bool(getattr(event, "private_companion_deferred_private_image_only_ready", False))
        ):
            for attr in ("image_urls", "images"):
                existing = getattr(req, attr, None)
                if existing:
                    try:
                        setattr(req, attr, [])
                    except Exception:
                        pass
        await self.apply_tts_enhancement_request(event, req)
        await self._append_forward_message_context_to_request(event, req)
        if not is_private_chat and self.enable_group_reality_promise_guard:
            await self._append_capability_boundary_to_request(event, req)
        if not is_private_chat:
            await self._mark_group_conversation_from_llm_request(event)
            await self._append_group_injection_guard_to_request(event, req)
            await self._append_group_persona_denoise_to_request(event, req)
            await self._append_group_high_intensity_reply_guard_to_request(event, req)
        else:
            await self._append_non_target_private_identity_guard_to_request(event, req)
        if not self._feature_enabled_or_temp_unlocked("inject_passive_states"):
            if is_private_chat and private_user_active:
                await self._append_reply_style_to_request(event, req, mode="private")
            elif not is_private_chat:
                group_id_for_style = self._extract_group_id_from_event(event)
                if (
                    group_id_for_style
                    and self._feature_enabled_or_temp_unlocked("enable_group_companion")
                    and self._group_enabled_for_event(group_id_for_style)
                ):
                    await self._append_reply_style_to_request(event, req, mode="group")
            if is_private_chat:
                try:
                    backlog_user_id = self._canonical_private_user_id(str(event.get_sender_id()))
                except Exception:
                    backlog_user_id = ""
                backlog_user = self.data.get("users", {}).get(backlog_user_id) if backlog_user_id else None
                if isinstance(backlog_user, dict):
                    await self._append_rest_reply_backlog_to_request(event, req, backlog_user)
            await self._append_worldbook_mentions_to_request(event, req, mode="light")
            await self._append_conditional_tool_instructions_to_request(event, req)
            await self._append_environment_perception_to_request(event, req)
            log_bookshelf_secret_skip("passive_injection_disabled", backlog_user if is_private_chat and isinstance(backlog_user, dict) else None)
            return

        if not is_private_chat:
            group_id = self._extract_group_id_from_event(event) if self._feature_enabled_or_temp_unlocked("enable_group_companion") else ""
            group: dict[str, Any] | None = None
            sender_id = ""
            if group_id and self._group_enabled_for_event(group_id):
                try:
                    sender_id = str(event.get_sender_id())
                except Exception:
                    sender_id = ""
                group = self._get_group(group_id)
            if self.enable_group_context_injection and self._feature_enabled_or_temp_unlocked("enable_group_companion"):
                if group_id and self._group_enabled_for_event(group_id):
                    if not isinstance(group, dict):
                        group = self._get_group(group_id)
                    text_for_mark = _single_line(
                        getattr(event, "private_companion_group_text", "") or getattr(event, "message_str", ""),
                        260,
                    )
                    marker = "<!-- private_companion_group_context_v1 -->"
                    current_prompt = req.system_prompt or ""
                    current_turn_prompt = str(getattr(req, "prompt", "") or "")
                    if marker not in current_prompt and marker not in current_turn_prompt:
                        combined_text = await self._consume_semantic_message_buffer_for_event(event, private_chat=False)
                        extra = ""
                        if combined_text:
                            high_intensity = getattr(event, "private_companion_group_high_intensity", None)
                            if isinstance(high_intensity, dict) and high_intensity.get("active"):
                                combined_text = self._compact_high_intensity_prompt_lines(
                                    combined_text,
                                    max_chars=700,
                                    max_lines=8,
                                )
                                extra = (
                                    "\n\n【本轮高强度合并消息】\n"
                                    "群里刚刚短时间内多次叫到你，下面这些消息已压缩为同一轮理解背景；只挑最相关的一点短答，不要逐条回应：\n"
                                    f"{combined_text}"
                                )
                            else:
                                extra = (
                                    "\n\n【本轮用户连续补充】\n"
                                    "用户刚刚在短时间内连续补充了几句,请把它们当作同一轮完整发言理解,不要逐条回复：\n"
                                    f"{combined_text}"
                                )
                        wakeup_effect = getattr(event, "private_companion_group_wakeup_state_effect", None)
                        wakeup_state_text = ""
                        if isinstance(wakeup_effect, dict) and wakeup_effect:
                            try:
                                state = await self._ensure_daily_state()
                            except Exception:
                                state = self.data.get("daily_state", {})
                            wakeup_state_text = "\n\n" + self._format_group_wakeup_humanized_prompt(wakeup_effect, state)
                        if self._user_asks_recalled_messages(text_for_mark):
                            recall_context = self._format_recalled_messages_for_natural_query(event, limit=5)
                            if recall_context:
                                extra = f"{extra}\n\n{recall_context}" if extra else f"\n\n{recall_context}"
                        passive_group_formatter = getattr(self, "_format_group_passive_reply_context_for_prompt", None)
                        if callable(passive_group_formatter):
                            group_context_text = passive_group_formatter(group, sender_id, str(event.message_str or ""))
                        else:
                            group_context_text = self._format_group_context_for_prompt(group, sender_id, str(event.message_str or ""))
                        high_intensity_for_context = getattr(event, "private_companion_group_high_intensity", None)
                        if isinstance(high_intensity_for_context, dict) and high_intensity_for_context.get("active"):
                            group_context_text = self._compact_high_intensity_prompt_lines(
                                group_context_text,
                                max_chars=900,
                                max_lines=14,
                            )
                        recent_atrelay_context = self._format_recent_atrelay_context_for_prompt(
                            kind="group",
                            target=group_id,
                            sender_id=sender_id,
                            current_text=str(event.message_str or ""),
                            limit=2,
                        )
                        if recent_atrelay_context:
                            group_context_text = f"{group_context_text}\n\n{recent_atrelay_context}".strip()
                        group_context_text = f"{group_context_text}{wakeup_state_text}{extra}"
                        if isinstance(high_intensity_for_context, dict) and high_intensity_for_context.get("active"):
                            before_compact_chars = len(group_context_text)
                            group_context_text = self._compact_high_intensity_prompt_lines(
                                group_context_text,
                                max_chars=1600,
                                max_lines=24,
                            )
                            if len(group_context_text) < before_compact_chars:
                                logger.info(
                                    "[PrivateCompanion] 群聊高强度上下文已压缩: group=%s chars=%s->%s",
                                    _single_line(group_id, 40) or "-",
                                    before_compact_chars,
                                    len(group_context_text),
                                )
                        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                            req,
                            marker,
                            group_context_text,
                            priority=60,
                            source="group",
                        ) else "system_prompt"
                        if placement == "system_prompt":
                            req.system_prompt = (
                                f"{current_prompt}\n\n{marker}\n{group_context_text}"
                            ).strip()
                        await self._record_request_prompt_fragment(
                            event,
                            title="群聊上下文注入",
                            key="group.context",
                            text=group_context_text,
                            source="group",
                            mode="group",
                            metadata={"注入位置": placement},
                        )
            group_recall_text = _single_line(
                getattr(event, "private_companion_group_text", "") or getattr(event, "message_str", ""),
                260,
            )
            recall_marker = "<!-- private_companion_recall_query_v1 -->"
            if (
                self._user_asks_recalled_messages(group_recall_text)
                and recall_marker not in (req.system_prompt or "")
                and recall_marker not in str(getattr(req, "prompt", "") or "")
            ):
                recall_context = self._format_recalled_messages_for_natural_query(event, limit=5)
                if recall_context:
                    placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                        req,
                        recall_marker,
                        recall_context,
                        priority=66,
                        source="recall",
                    ) else "system_prompt"
                    if placement == "system_prompt":
                        req.system_prompt = f"{req.system_prompt or ''}\n\n{recall_marker}\n{recall_context}".strip()
                    await self._record_request_prompt_fragment(
                        event,
                        title="历史召回查询注入",
                        key="recall.query",
                        text=recall_context,
                        source="recall",
                        mode="group",
                        metadata={"注入位置": placement},
                    )
            timeline_marker = "<!-- private_companion_self_timeline_v1 -->"
            if timeline_marker not in (req.system_prompt or "") and timeline_marker not in str(getattr(req, "prompt", "") or ""):
                high_intensity_for_timeline = getattr(event, "private_companion_group_high_intensity", None)
                timeline_limit = 3 if isinstance(high_intensity_for_timeline, dict) and high_intensity_for_timeline.get("active") else 8
                self_timeline_context = (
                    ""
                    if self._memory_companion_should_defer_prompt_section("self_timeline", event, req)
                    else self._format_self_timeline_context_for_reply(group_recall_text, limit=timeline_limit)
                )
                if self_timeline_context:
                    placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                        req,
                        timeline_marker,
                        self_timeline_context,
                        priority=67,
                        source="self_timeline",
                    ) else "system_prompt"
                    if placement == "system_prompt":
                        req.system_prompt = f"{req.system_prompt or ''}\n\n{timeline_marker}\n{self_timeline_context}".strip()
                    await self._record_request_prompt_fragment(
                        event,
                        title="自我时间线检索",
                        key="self.timeline",
                        text=self_timeline_context,
                        source="self_timeline",
                        mode="group",
                        metadata={"注入位置": placement},
                    )
            await self._append_reply_style_to_request(event, req, mode="group")
            await self._append_conditional_tool_instructions_to_request(event, req)
            await self._append_environment_perception_to_request(event, req)
            log_bookshelf_secret_skip("group_chat")
            return
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            log_bookshelf_secret_skip("private_sender_missing")
            self._release_framework_session_lock_for_event(event, label="private_sender_missing")
            return
        raw_users = self.data.get("users", {})
        current_user = raw_users.get(user_id) if isinstance(raw_users, dict) else None
        if not isinstance(current_user, dict):
            log_bookshelf_secret_skip("private_user_missing")
            self._release_framework_session_lock_for_event(event, label="private_user_missing")
            return
        if not self._is_target_private_user(user_id, current_user) or not current_user.get("enabled", True):
            log_bookshelf_secret_skip("private_user_disabled", current_user)
            self._release_framework_session_lock_for_event(event, label="private_user_disabled")
            return
        await self._acquire_framework_session_lock_for_event(event, label="private_llm_request")
        if not bool(getattr(event, "private_companion_skip_passive_input_status", False)):
            self._start_passive_input_status_loop(event, user_id)

        state = await self._ensure_daily_state(skip_conversation_summary=True, passive_fast=True)
        inbound_text = _single_line(getattr(event, "message_str", "") or current_user.get("last_user_message"), 260)
        lightweight_passive = self._is_lightweight_private_passive_inbound(inbound_text)
        bookshelf_signal_getter = getattr(self, "_bookshelf_secret_signal_info", None)
        bookshelf_signal = bookshelf_signal_getter(inbound_text) if callable(bookshelf_signal_getter) else {}
        if lightweight_passive and isinstance(bookshelf_signal, dict) and bookshelf_signal.get("likely"):
            lightweight_passive = False
            logger.info(
                "[PrivateCompanion] 夹层密码请求退出轻量被动链路: user=%s direct=%s context=%s access=%s text=%s",
                user_id,
                ",".join(bookshelf_signal.get("direct_matches") or []) or "-",
                ",".join(bookshelf_signal.get("context_matches") or []) or "-",
                ",".join(bookshelf_signal.get("access_matches") or []) or "-",
                inbound_text,
            )
        prompt_surface = PromptSurface()
        reply_style_prompt = self._format_reply_style_prompt()
        if reply_style_prompt:
            prompt_surface.add("reply.style", reply_style_prompt, priority=12, source="reply_style")
        state_changed = False
        state_update_reason = "legacy"
        if bool(getattr(self, "enable_passive_state_delta_injection", True)):
            session_key = _single_line(getattr(event, "unified_msg_origin", ""), 160) or f"private:{user_id}"
            state_update_text, state_changed, state_update_reason = self._private_passive_state_update_for_prompt(
                session=session_key,
                state=state,
                current_user=current_user,
                inbound_text=inbound_text,
                lightweight=lightweight_passive,
            )
            if state_update_text:
                prompt_surface.add("state.session_update", state_update_text, priority=30, source="daily_state")
        elif lightweight_passive:
            lightweight_injection = self._prepared_lightweight_state_injection(state)
            lightweight_injection = self._sanitize_schedule_context_for_private_user(lightweight_injection, current_user)
            prompt_surface.add("state.lightweight", lightweight_injection, priority=30, source="daily_state")
        else:
            state_injection = self._format_state_injection(state)
            state_injection = self._sanitize_schedule_context_for_private_user(state_injection, current_user)
            prompt_surface.add("state.full", state_injection, priority=30, source="daily_state")
            life_context = self._format_life_context_injection()
            life_context = self._sanitize_schedule_context_for_private_user(life_context, current_user)
            if life_context:
                prompt_surface.add("life.context", life_context, priority=35, source="daily_state")
            important_dates = self._format_important_dates_injection()
            if important_dates:
                prompt_surface.add("important.dates", important_dates, priority=36, source="daily_state")
            worldview_context = (
                self._format_worldview_adaptation_prompt()
                if self._feature_enabled_or_temp_unlocked("enable_environment_perception") and self.enable_worldview_perception
                else ""
            )
            if worldview_context:
                prompt_surface.add("worldview.adaptation", worldview_context, priority=37, source="worldview")
        identity_anchor = self._format_private_identity_anchor_for_prompt(user_id, current_user, event)
        if identity_anchor:
            prompt_surface.add("identity.anchor", identity_anchor, priority=10, source="identity")
        recent_atrelay_context = self._format_recent_atrelay_context_for_prompt(
            kind="private",
            target=user_id,
            sender_id=user_id,
            current_text=inbound_text,
            limit=2,
        )
        if recent_atrelay_context:
            prompt_surface.add("atrelay.recent", recent_atrelay_context, priority=26, source="tools")
        if not self._format_atrelay_target_summary_for_prompt(inbound_text):
            mentioned_worldbook = self._format_worldbook_private_mentions_for_prompt(inbound_text, limit=4)
            if mentioned_worldbook:
                prompt_surface.add("worldbook.mentions", mentioned_worldbook, priority=55, source="worldbook")
        environment_fragment = await self._format_passive_environment_fragment(event, lightweight=lightweight_passive)
        if environment_fragment:
            prompt_surface.add(
                "environment.lightweight" if lightweight_passive else "environment.perception",
                environment_fragment,
                priority=20,
                source="environment",
            )
        rest_backlog_prompt = self._take_rest_reply_backlog_prompt(current_user)
        if rest_backlog_prompt:
            prompt_surface.add(
                "rest.backlog",
                rest_backlog_prompt,
                priority=25,
                source="daily_state",
            )
        is_wake_event = bool(getattr(event, "is_wake", False)) or bool(
            getattr(event, "is_at_or_wake_command", False)
        )
        if is_private_chat and not is_wake_event:
            proactive_context = await self._format_proactive_reply_context(event)
            if proactive_context:
                prompt_surface.add("proactive.reply_context", proactive_context, priority=45, source="proactive")
        short_reaction_context = self._format_short_reaction_context_for_prompt(current_user, inbound_text)
        if short_reaction_context:
            prompt_surface.add("turn.short_reaction", short_reaction_context, priority=48, source="conversation")
        reply_chain_context_getter = getattr(self, "_format_reply_chain_context_for_prompt", None)
        if callable(reply_chain_context_getter) and not bool(getattr(event, "private_companion_reply_chain_context_injected", False)):
            try:
                reply_chain_context = await reply_chain_context_getter(event)
            except Exception as exc:
                logger.debug("[PrivateCompanion] 引用链上下文读取失败: %s", _single_line(exc, 120))
                reply_chain_context = ""
            if reply_chain_context:
                prompt_surface.add("reply.chain", reply_chain_context, priority=54, source="forward_message")
        private_image_enhancement_enabled_for_request = self._feature_enabled_or_temp_unlocked(
            "enable_private_image_self_recognition"
        )
        buffered_image_context = (
            self._take_buffered_private_image_context_for_event(event)
            if private_image_enhancement_enabled_for_request
            else {}
        )
        buffered_images = (
            [str(item) for item in buffered_image_context.get("images", []) if str(item or "").strip()]
            if isinstance(buffered_image_context, dict)
            else []
        )
        buffered_image_vision = ""
        delayed_image_sources = getattr(event, "private_companion_delayed_image_sources", [])
        if not buffered_images and isinstance(delayed_image_sources, list):
            buffered_images = [str(item) for item in delayed_image_sources[:5] if str(item or "").strip()]
        buffered_image_vision_limit = self._private_image_vision_text_limit(len(buffered_images))
        if isinstance(buffered_image_context, dict):
            buffered_image_vision = _single_line(buffered_image_context.get("vision_text"), buffered_image_vision_limit)
        delayed_image_vision = _single_line(
            getattr(event, "private_companion_delayed_image_vision_text", ""),
            buffered_image_vision_limit,
        )
        if delayed_image_vision and not buffered_image_vision:
            buffered_image_vision = delayed_image_vision
        buffered_image_mode = _single_line(buffered_image_context.get("image_mode"), 20) if isinstance(buffered_image_context, dict) else ""
        delayed_image_mode = _single_line(getattr(event, "private_companion_delayed_image_mode", ""), 20)
        if not buffered_image_mode and delayed_image_mode:
            buffered_image_mode = delayed_image_mode
        vision_task = buffered_image_context.get("vision_task") if isinstance(buffered_image_context, dict) else None
        if not buffered_image_vision and isinstance(vision_task, asyncio.Task):
            vision_wait_timeout = max(
                2.5,
                min(
                    12.0,
                    _safe_float(getattr(self, "private_image_vision_wait_seconds", 30.0), 30.0, 0.0),
                ),
            )
            try:
                buffered_image_vision = _single_line(await asyncio.wait_for(asyncio.shield(vision_task), timeout=vision_wait_timeout), buffered_image_vision_limit)
            except asyncio.TimeoutError:
                logger.info("[PrivateCompanion] 私聊图片视觉转述仍在进行,本轮先注入路径兜底: timeout=%.1fs", vision_wait_timeout)
            except Exception as exc:
                logger.info("[PrivateCompanion] 私聊图片视觉转述获取失败: %s", _single_line(exc, 120))
        buffered_images_include_gif = (
            bool(getattr(self, "enable_private_image_gif_enhancement", True))
            and self._private_image_sources_include_gif(buffered_images)
            if buffered_images
            else False
        )
        if (
            buffered_images
            and not buffered_image_vision
            and buffered_image_mode != "no_vision"
            and (
                buffered_images_include_gif
                or (buffered_image_mode == "direct" and not self._event_main_provider_supports_image(event))
            )
        ):
            buffered_image_vision = _single_line(
                await self._transcribe_private_inbound_images(
                    buffered_images,
                    umo=str(getattr(event, "unified_msg_origin", "") or ""),
                ),
                buffered_image_vision_limit,
            )
        combined_text = ""
        private_buffer_key = self._semantic_buffer_key(f"private:{user_id}", user_id)
        private_buffer_snapshot = self._semantic_buffer_active_snapshot(private_buffer_key, force=True)
        if (
            not private_image_enhancement_enabled_for_request
            and isinstance(private_buffer_snapshot, dict)
            and _single_line(private_buffer_snapshot.get("kind"), 40) == "image"
        ):
            buffers = getattr(self, "_semantic_message_buffers", None)
            if isinstance(buffers, dict):
                buffers.pop(private_buffer_key, None)
            private_buffer_snapshot = {}
        private_buffer_active = bool(private_buffer_snapshot)
        if not lightweight_passive or buffered_images or private_buffer_active:
            combined_text = await self._consume_semantic_message_buffer_for_event(event, private_chat=True)
        if combined_text:
            prompt_surface.add(
                "turn.continuation",
                "【本轮用户连续补充】\n"
                "用户刚刚在短时间内连续补充了几句,请把它们当作同一轮完整发言理解,不要逐条回复,也不要表现得像用户重复催促：\n"
                f"{combined_text}",
                priority=50,
                source="message_debounce",
            )
            inbound_text = _single_line(combined_text.replace("\n", " "), 260)
        if self._user_asks_recalled_messages(inbound_text):
            prompt_surface.add("recall.query", self._format_recalled_messages_for_natural_query(event, limit=5), priority=52, source="recall")
        food_menu_context = (
            self._format_food_menu_for_reply(inbound_text, limit=3)
            if self._feature_enabled_or_temp_unlocked("enable_food_menu_recommendation")
            else ""
        )
        if food_menu_context:
            prompt_surface.add("food.menu", food_menu_context, priority=53, source="food")
        if re.search(
            r"(你|星缘|bot|机器人).{0,8}(在干嘛|在做什么|做什么|穿什么|穿的?什么|衣服|衣服颜色|什么颜色|吃了什么|吃的?什么|几点吃|什么时候吃|吃饭|进食|在哪里|在哪儿|当前位置|今天状态|现在状态)",
            inbound_text,
        ) or re.search(r"(穿搭|自拍|衣服.{0,8}(颜色|什么色)|穿.{0,6}什么|今天.*衣服|今天.*颜色|刚才.*做|几点.*做了什么)", inbound_text):
            composer = getattr(self, "_memory_companion_compose_feature_context", None)
            if callable(composer):
                try:
                    current_state_memory = await composer(
                        kind="current_state_reply",
                        query=(
                            f"当前状态问答：{inbound_text}；"
                            "今日穿搭、衣服颜色、当前日程、当前位置、刚才做了什么、进食时间、吃了什么、最近自拍、用户常问状态习惯"
                        ),
                        user=current_user,
                        user_id=user_id,
                        event=event,
                        top_k=6,
                        max_chars=950,
                        timeout_seconds=1.6,
                    )
                except Exception as exc:
                    current_state_memory = ""
                    logger.debug("[PrivateCompanion] 当前状态 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))
                if current_state_memory:
                    prompt_surface.add(
                        "memory.current_state",
                        "【我会牢牢记住你 当前状态参考】\n"
                        f"{current_state_memory}\n"
                        "使用方式：只把它当作回答当前状态、穿搭、吃饭、日程连续性的辅助证据；"
                        "优先服从本轮状态注入和明确时间线。不要说“我查到/记忆里”。",
                        priority=54,
                        source="memory_companion",
                    )
        if (
            buffered_images
            and buffered_image_vision
            and buffered_image_mode != "no_vision"
            and self._private_image_user_has_specific_vision_request(inbound_text)
        ):
            contextual_vision = _single_line(
                await self._transcribe_private_inbound_images(
                    buffered_images,
                    umo=str(getattr(event, "unified_msg_origin", "") or ""),
                    user_text=inbound_text,
                    force_contextual=True,
                ),
                buffered_image_vision_limit,
            )
            if contextual_vision:
                buffered_image_vision = contextual_vision
        if buffered_images:
            direct_image_mounted = False
            if buffered_image_mode == "direct" and self._event_main_provider_supports_image(event) and not buffered_images_include_gif:
                image_refs: list[str] = []
                for image_ref in buffered_images[:5]:
                    for request_ref in self._private_image_sources_for_astrbot_request([image_ref]):
                        if request_ref not in image_refs:
                            image_refs.append(request_ref)
                if not image_refs:
                    logger.info(
                        "[PrivateCompanion] 私聊延迟图片无模型可读源,跳过直接挂图: user=%s images=%s",
                        user_id,
                        len(buffered_images),
                    )
                    buffered_image_mode = (
                        "caption"
                        if self._has_private_image_visual_provider(str(getattr(event, "unified_msg_origin", "") or ""))
                        else "no_vision"
                    )
                else:
                    existing = getattr(req, "image_urls", None)
                    if not isinstance(existing, list):
                        existing = []
                    for image_ref in image_refs:
                        if image_ref not in existing:
                            existing.append(image_ref)
                    req.image_urls = existing
                    logger.info(
                        "[PrivateCompanion] 私聊延迟图片已挂回视觉主模型: user=%s images=%s mounted=%s",
                        user_id,
                        len(buffered_images),
                        len(image_refs),
                    )
                    try:
                        await self._refresh_default_persona_prompt(str(getattr(event, "unified_msg_origin", "") or ""))
                    except Exception as exc:
                        logger.debug("[PrivateCompanion] 图片直挂刷新人格缓存失败: %s", exc)
                    direct_role_hint = self._private_image_direct_role_appearance_prompt()
                    if direct_role_hint:
                        prompt_surface.add(
                            "image.direct",
                            direct_role_hint,
                            priority=55,
                            source="private_image",
                        )
                    direct_image_mounted = True
            if not direct_image_mounted and buffered_image_vision:
                intent_line = self._private_image_intent_line(buffered_image_vision)
                ownership_line = self._private_image_ownership_line(buffered_image_vision)
                reply_objective = self._private_image_reply_objective(ownership_line, vision_text=buffered_image_vision, user_text=inbound_text)
                logger.info(
                    "[PrivateCompanion] 私聊延迟图片已注入视觉摘要: user=%s chars=%s intent=%s ownership=%s objective=%s preview=%s",
                    user_id,
                    len(buffered_image_vision),
                    intent_line or "无",
                    ownership_line or "无",
                    _single_line(reply_objective, 120),
                    _single_line(buffered_image_vision, 220),
                )
                image_context_intro = (
                    "用户刚刚只发了一张图片,没有继续补充文字。"
                    if bool(getattr(event, "private_companion_deferred_private_image_only_ready", False))
                    else "用户刚刚先单独发了一张图片,随后补充了文字。"
                )
                prompt_surface.add(
                    "image.vision",
                    "【本轮延迟图片】\n"
                    f"{image_context_intro}下面是这张图的视觉摘要；请按摘要理解当前图片，不要说没看到图。"
                    "只回应本轮图片和用户文字，不要提模型、插件或路径。\n"
                    f"{self._private_image_identity_disambiguation_instruction()}\n"
                    f"{reply_objective}\n"
                    f"{buffered_image_vision}",
                    priority=55,
                    source="private_image",
                )
                await self._memory_companion_record_image_observation(
                    event,
                    content=buffered_image_vision,
                    image_count=len(buffered_images),
                    source="current_private_image",
                    user_id=user_id,
                    user_name=_single_line(current_user.get("nickname") or current_user.get("display_name") or user_id, 80),
                )
            else:
                image_context_intro = (
                    "用户刚刚只发了一张图片,没有继续补充文字。"
                    if bool(getattr(event, "private_companion_deferred_private_image_only_ready", False))
                    else "用户刚刚先单独发了一张图片,随后补充了文字。"
                )
                prompt_surface.add(
                    "image.fallback",
                    "【本轮延迟图片】\n"
                    f"{image_context_intro}图片已暂存，但暂无可靠视觉摘要；"
                    "如果用户问图片内容，请自然说暂时没看清，不要编造画面。\n"
                    + "\n".join(f"- {path}" for path in buffered_images),
                    priority=55,
                    source="private_image",
                )
        elif bool(getattr(event, "private_companion_deferred_private_image_only_ready", False)):
            if buffered_image_vision:
                prompt_surface.add(
                    "image.only.vision",
                    "【本轮延迟图片】\n"
                    "用户只发了一张图片。下面是这张图的视觉摘要；请自然接住图片内容或表达意图，不要提处理过程。\n"
                    f"{buffered_image_vision}",
                    priority=55,
                    source="private_image",
                )
                await self._memory_companion_record_image_observation(
                    event,
                    content=buffered_image_vision,
                    image_count=max(1, len(buffered_images)),
                    source="current_private_image",
                    user_id=user_id,
                    user_name=_single_line(current_user.get("nickname") or current_user.get("display_name") or user_id, 80),
                )
            else:
                prompt_surface.add(
                    "image.only.fallback",
                    "【本轮延迟图片】\n"
                    "用户只发了一张图片，但当前没有可靠图片内容；请自然表示暂时没看清，可以请用户补一句，不要编造画面。",
                    priority=55,
                    source="private_image",
                )
        reply_image_sources: list[str] = []
        reply_image_prompt_anchor = ""
        skip_reply_image_for_forward_context = bool(getattr(event, "private_companion_forward_context_injected", False))
        if skip_reply_image_for_forward_context:
            logger.info("[PrivateCompanion] 本轮已注入合并消息上下文,跳过引用图片重复视觉: user=%s", user_id)
        if (
            not skip_reply_image_for_forward_context
            and not buffered_images
            and not bool(getattr(event, "private_companion_deferred_private_image_only_ready", False))
        ):
            reply_image_sources = await self._find_reply_image_sources_for_event(event)
            if reply_image_sources:
                reply_image_limit = self._private_image_vision_text_limit(len(reply_image_sources))
                reply_image_vision = _single_line(
                    await self._transcribe_private_inbound_images(
                        reply_image_sources,
                        umo=str(getattr(event, "unified_msg_origin", "") or ""),
                        user_text=inbound_text,
                        force_contextual=self._private_image_user_has_specific_vision_request(inbound_text),
                    ),
                    reply_image_limit,
                )
                if reply_image_vision:
                    intent_line = self._private_image_intent_line(reply_image_vision)
                    ownership_line = self._private_image_ownership_line(reply_image_vision)
                    reply_objective = self._private_image_reply_objective(ownership_line, vision_text=reply_image_vision, user_text=inbound_text)
                    logger.info(
                        "[PrivateCompanion] 私聊引用图片已注入视觉摘要: user=%s images=%s intent=%s ownership=%s objective=%s preview=%s",
                        user_id,
                        len(reply_image_sources),
                        intent_line or "无",
                        ownership_line or "无",
                        _single_line(reply_objective, 120),
                        _single_line(reply_image_vision, 220),
                    )
                    reply_image_prompt_anchor = (
                        "【当前引用图片锚点】\n"
                        f"用户本轮是在问被引用图片：{inbound_text or '（空）'}。\n"
                        "下面摘要只属于这一次被引用的图片；请把它作为当前问题的主要依据。\n"
                        f"{reply_objective}\n"
                        f"{reply_image_vision}"
                    )
                    prompt_surface.add(
                        "image.reply.vision",
                        "【本轮引用图片】\n"
                        f"用户这轮引用/回复了一张图片,并发送文字：{inbound_text or '（空）'}。\n"
                        "下面是被引用图片的视觉摘要；请优先回答用户当前文字针对这张图提出的问题。\n"
                        f"{self._private_image_identity_disambiguation_instruction()}\n"
                        f"{reply_objective}\n"
                        f"{reply_image_vision}",
                        priority=55,
                        source="private_image",
                    )
                    await self._memory_companion_record_image_observation(
                        event,
                        content=reply_image_vision,
                        image_count=len(reply_image_sources),
                        source="reply_image",
                        user_id=user_id,
                        user_name=_single_line(current_user.get("nickname") or current_user.get("display_name") or user_id, 80),
                    )
                    image_keys = self._private_image_cache_image_keys(reply_image_sources)
                    if image_keys:
                        try:
                            async with self._data_lock:
                                user = self._get_user(user_id)
                                user["last_private_image_vision_feedback_target"] = {
                                    "ts": _now_ts(),
                                    "image_keys": image_keys,
                                    "vision_text": _single_line(reply_image_vision, reply_image_limit),
                                    "reply": "",
                                    "ownership": ownership_line,
                                    "intent": intent_line,
                                    "source": "reply_image",
                                }
                                self._save_data_sync()
                        except Exception as exc:
                            logger.debug("[PrivateCompanion] 私聊引用图片视觉反馈目标记录失败: %s", exc)
                    try:
                        setattr(event, "private_companion_reply_image_vision_text", _single_line(reply_image_vision, reply_image_limit))
                        setattr(event, "private_companion_reply_image_count", len(reply_image_sources))
                        setattr(event, "private_companion_reply_image_user_text", inbound_text)
                        setattr(
                            event,
                            "private_companion_reply_image_content_question",
                            self._private_image_user_asks_content(inbound_text),
                        )
                    except Exception:
                        pass
                else:
                    prompt_surface.add(
                        "image.reply.fallback",
                        "【本轮引用图片】\n"
                        f"用户这轮引用/回复了一张图片,并发送文字：{inbound_text or '（空）'}。"
                        "当前未能拿到可用视觉摘要；如果用户问图片内容，请自然说明暂时没看清，不要编造。",
                        priority=55,
                        source="private_image",
                    )
        if reply_image_prompt_anchor:
            try:
                current_req_prompt = str(getattr(req, "prompt", "") or "")
                if "<!-- private_companion_reply_image_anchor_v1 -->" not in current_req_prompt:
                    req.prompt = (
                        f"{current_req_prompt}\n\n"
                        "<!-- private_companion_reply_image_anchor_v1 -->\n"
                        f"{reply_image_prompt_anchor}"
                    ).strip()
            except Exception as exc:
                logger.debug("[PrivateCompanion] 私聊引用图片 prompt 锚点写入失败: %s", exc)
        if lightweight_passive:
            log_bookshelf_secret_skip("lightweight_passive", current_user, inbound_text)
        if not lightweight_passive:
            hidden_creative_context = self._format_hidden_creative_context_for_reply(inbound_text)
            if hidden_creative_context:
                prompt_surface.add("creative.hidden", hidden_creative_context, priority=60, source="creative")
            bookshelf_secret_context = await self._format_bookshelf_secret_for_prompt(inbound_text, current_user)
            if bookshelf_secret_context:
                prompt_surface.add("bookshelf.secret", bookshelf_secret_context, priority=61, source="bookshelf")
            bookshelf_reading_context = self._format_bookshelf_reading_context_for_reply(inbound_text, current_user)
            if bookshelf_reading_context:
                prompt_surface.add("bookshelf.reading", bookshelf_reading_context, priority=62, source="bookshelf")
            private_preference_context = self._format_private_reading_preference_influence_for_reply(inbound_text, current_user)
            if private_preference_context:
                prompt_surface.add("private_reading.preference", private_preference_context, priority=63, source="private_reading")
            news_context = self._format_recent_news_context_for_reply(inbound_text)
            if news_context:
                prompt_surface.add("news.recent", news_context, priority=64, source="news")
            web_exploration_context = self._format_recent_web_exploration_context_for_reply(inbound_text)
            if web_exploration_context:
                prompt_surface.add("web_exploration.recent", web_exploration_context, priority=65, source="web_exploration")
            self_timeline_context = self._format_self_timeline_context_for_reply(inbound_text, current_user, limit=8)
            if self._memory_companion_should_defer_prompt_section("self_timeline", event, req):
                self_timeline_context = ""
            if self_timeline_context:
                prompt_surface.add("self.timeline", self_timeline_context, priority=67, source="self_timeline")
            if self._feature_enabled_or_temp_unlocked("enable_skill_growth_passive_injection"):
                skill_context = self._format_skill_growth_for_prompt()
                if skill_context:
                    prompt_surface.add("skill.growth", skill_context, priority=66, source="skill")
            else:
                skill_context = self._format_skill_growth_for_user_text(inbound_text)
                if skill_context:
                    prompt_surface.add("skill.growth.match", skill_context, priority=66, source="skill")
            private_chat_context = self._format_private_chat_context_injection(current_user)
            if self._memory_companion_should_defer_prompt_section("private_context", event, req):
                private_chat_context = ""
            if private_chat_context:
                prompt_surface.add("private.context", private_chat_context, priority=70, source="companion")
            companion_injection = self._format_companion_planner_injection(current_user)
            if companion_injection:
                prompt_surface.add("companion.planner", companion_injection, priority=80, source="companion")
            livingmemory_guidance = self._format_livingmemory_guidance(scope="private" if is_private_chat else "group")
            if self._memory_companion_should_defer_prompt_section("livingmemory_guidance", event, req):
                livingmemory_guidance = ""
            if livingmemory_guidance:
                prompt_surface.add("livingmemory.guidance", livingmemory_guidance, priority=90, source="livingmemory")
            detail_injection = self._format_detail_injection()
            if detail_injection:
                prompt_surface.add("detail.injection", detail_injection, priority=40, source="daily_detail")
            if self.enable_llm_timer_scheduling and is_private_chat:
                try:
                    user_id = str(event.get_sender_id())
                except Exception:
                    user_id = ""
                if user_id:
                    async with self._data_lock:
                        enabled = bool(self._get_user(user_id).get("enabled"))
                    if enabled:
                        prompt_surface.add("timer.scheduling", self._format_timer_scheduling_instruction(), priority=95, source="timer")
        injection = prompt_surface.render()
        marker = "<!-- private_companion_state_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            log_bookshelf_secret_skip("state_marker_already_present", current_user, inbound_text)
            await self._append_conditional_tool_instructions_to_request(event, req)
            return
        if not injection:
            logger.debug("[PrivateCompanion] 被动状态提示词片段为空,跳过状态 marker 注入")
            log_bookshelf_secret_skip("empty_passive_injection", current_user, inbound_text)
            await self._append_conditional_tool_instructions_to_request(event, req)
            return
        injection_placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            injection,
            priority=40,
            source="passive_state",
        ) else "system_prompt"
        if injection_placement == "system_prompt":
            req.system_prompt = f"{current_prompt}\n\n{marker}\n{injection}".strip()
        await self._append_conditional_tool_instructions_to_request(event, req)
        state_log_parts = [
            f"心理能量={state.get('energy', 70)}/100",
            f"情绪底色={state.get('mood_bias', '平稳')}",
        ]
        weather = _single_line(state.get("weather"), 80)
        if weather and weather != "暂无天气信息":
            state_log_parts.append(f"天气={weather}")
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        current_schedule = self._sanitize_schedule_context_for_private_user(
            self._format_plan_item_for_prompt(current_item),
            current_user,
        ) or "无当前日程"
        recorder = getattr(self, "_record_prompt_injection_snapshot", None)
        if callable(recorder):
            await recorder(
                kind="passive",
                session=_single_line(getattr(event, "unified_msg_origin", ""), 160) or self._event_scope_key(event),
                title="被动回复注入",
                text=injection,
                mode="light" if lightweight_passive else "full",
                trace_id=self._prompt_injection_trace_id_for_event(event),
                message_preview=self._prompt_injection_message_preview_for_event(event),
                sender_label=self._prompt_injection_sender_label_for_event(event),
                modules=prompt_surface.rendered_fragments(),
                metadata={
                    "状态": "｜".join(state_log_parts),
                    "当前日程": current_schedule,
                    "注入位置": injection_placement,
                    "状态注入模式": "增量" if bool(getattr(self, "enable_passive_state_delta_injection", True)) else "完整",
                    "状态变化": "是" if state_changed else "否",
                    "状态触发": state_update_reason,
                    "会话": _single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown",
                    "发送者": _single_line(self._event_sender_id(event), 80),
                },
            )
        logger.info(
            "[PrivateCompanion] 已注入被动状态提示词到 %s: mode=%s state_mode=%s reason=%s placement=%s chars=%s 状态=%s；当前日程=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 80) or "unknown_session",
            "light" if lightweight_passive else "full",
            "delta" if bool(getattr(self, "enable_passive_state_delta_injection", True)) else "legacy",
            state_update_reason,
            injection_placement,
            len(injection),
            "｜".join(state_log_parts),
            current_schedule,
        )

    @filter.on_llm_response()
    async def normalize_tts_enhancement_response(self, event: AstrMessageEvent, resp: LLMResponse):
        """规范化 TTS 标签错拼，避免 <ttts> 等内容漏到发送链路。"""
        if self._proactive_only_blocks_passive_event(event, "enable_tts_enhancement"):
            return
        original_text = str(getattr(resp, "completion_text", "") or "")
        normalized_text = _normalize_outbound_punctuation_flow(original_text)
        if normalized_text and normalized_text != original_text:
            resp.completion_text = normalized_text
        await self.protect_tts_enhancement_response_blocks(event, resp)

    @filter.on_llm_response()
    async def record_external_llm_token_usage(self, event: AstrMessageEvent, resp: LLMResponse):
        """统计非插件内部调用的 AstrBot 主回复 Token，单独展示且不计入插件限额。"""
        if not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "llm_request"):
            return
        if bool(getattr(event, "private_companion_skip_external_token_stats", False)):
            return
        prompt = str(getattr(event, "private_companion_external_token_prompt", "") or "")
        started = _safe_float(getattr(event, "private_companion_external_token_start", 0), 0)
        completion = self._completion_text_for_token_stats(resp)
        if not prompt and not completion and resp is None:
            return
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        resp_id = _single_line(getattr(resp, "id", ""), 120) if resp is not None else ""
        usage = getattr(resp, "usage", None) if resp is not None else None
        usage_total = _safe_int(getattr(usage, "total", 0), 0)
        trigger_message_id = self._event_message_id(event)
        completion_sig = hashlib.sha1(
            completion[:4000].encode("utf-8", errors="ignore")
        ).hexdigest()[:16] if completion else ""
        record_key = "|".join(
            (
                resp_id,
                umo,
                sender_id,
                trigger_message_id,
                str(usage_total),
                str(len(prompt)),
                str(len(completion)),
                completion_sig,
            )
        )
        try:
            recorded_keys = getattr(event, "private_companion_external_token_recorded_keys", None)
            if not isinstance(recorded_keys, set):
                recorded_keys = set()
                setattr(event, "private_companion_external_token_recorded_keys", recorded_keys)
            if record_key in recorded_keys:
                return
            recorded_keys.add(record_key)
        except Exception:
            pass
        try:
            is_private_chat = bool(getattr(event, "is_private_chat", lambda: False)())
            task = "astrbot_private_reply" if is_private_chat else "astrbot_group_reply"
        except Exception:
            is_private_chat = False
            task = "astrbot_reply"
        provider_id = self._provider_id_from_llm_response(resp) or self._default_chat_provider_id(umo)
        self._record_external_llm_usage(
            provider_id=provider_id,
            task=task,
            prompt=prompt,
            completion=completion,
            elapsed_ms=int(max(0.0, time.time() - started) * 1000) if started > 0 else 0,
            success=bool(completion),
            error="" if completion else "empty_response",
            resp=resp,
            session_id=umo,
            sender_id=sender_id,
            message_type="private" if is_private_chat else "group",
        )

    @filter.on_llm_response()
    async def capture_llm_timer_directive(self, event: AstrMessageEvent, resp: LLMResponse):
        """LLM 回复后捕获定时/状态指令，并做私聊回复审校。"""
        release_now = False
        try:
            if not self.enabled:
                release_now = True
                return
            if self._proactive_only_blocks_passive_event(event, "enable_llm_timer_scheduling"):
                release_now = True
                return
            if not bool(getattr(event, "is_private_chat", lambda: False)()):
                return
            original_text = str(resp.completion_text or "").strip()
            if not original_text:
                self._stop_passive_input_status_loop(event)
                self._record_passive_no_reply(
                    event,
                    source="主链回复",
                    reason="LLM 返回空回复",
                    level="warn",
                )
                release_now = True
                return
            try:
                user_id = str(event.get_sender_id())
            except Exception:
                self._stop_passive_input_status_loop(event)
                release_now = True
                return
            raw_users = self.data.get("users", {})
            current_user = raw_users.get(user_id) if isinstance(raw_users, dict) else None
            if not isinstance(current_user, dict):
                self._stop_passive_input_status_loop(event)
                release_now = True
                return
            working_text = original_text
            reply_image_count = _safe_int(getattr(event, "private_companion_reply_image_count", 1), 1, 1, 5)
            reply_image_vision = _single_line(
                getattr(event, "private_companion_reply_image_vision_text", ""),
                self._private_image_vision_text_limit(reply_image_count),
            )
            reply_image_user_text = _single_line(
                getattr(event, "private_companion_reply_image_user_text", "") or current_user.get("last_user_message"),
                260,
            )
            if (
                reply_image_vision
                and bool(getattr(event, "private_companion_reply_image_content_question", False))
                and self._private_image_reply_misses_content_question(working_text)
            ):
                corrected = self._private_image_content_answer_from_vision(
                    reply_image_vision,
                    user_text=reply_image_user_text,
                )
                if corrected:
                    logger.info(
                        "[PrivateCompanion] 私聊引用图片回复疑似被历史话题污染,已按视觉摘要纠偏: user=%s before=%s after=%s",
                        user_id,
                        _single_line(working_text, 120),
                        _single_line(corrected, 160),
                    )
                    working_text = corrected
                    resp.completion_text = corrected
            if self.enable_llm_timer_scheduling and "<timer" in original_text.lower():
                cleaned_text, payloads = self._extract_timer_directives(original_text)
                if cleaned_text != original_text:
                    working_text = cleaned_text
                    resp.completion_text = working_text
                if payloads:
                    if self._should_skip_timer_capture_for_official_task(resp, working_text):
                        logger.info(
                            "[PrivateCompanion] 跳过对话临时预约转写: 本轮疑似已由 AstrBot 官方定时计划处理 session=%s",
                            _single_line(getattr(event, "unified_msg_origin", ""), 120),
                        )
                    else:
                        await self._schedule_llm_timer(
                            user_id,
                            payloads[-1],
                            source_text=working_text,
                            source_origin="llm_response",
                            trigger_message_id=self._event_message_id(event),
                            trigger_umo=str(getattr(event, "unified_msg_origin", "") or ""),
                        )

            inbound_text = _single_line(current_user.get("last_user_message"), 260)
            music_album_context = getattr(event, "private_companion_reply_music_album_context", None)
            silence_decision = await self._decide_smart_silence(
                inbound_text=inbound_text,
                response_text=working_text,
                user=current_user,
                session_kind="private",
            )
            if str(silence_decision.get("decision") or "") == "silent":
                setattr(event, "_private_companion_smart_silence_drop", True)
                setattr(event, "_private_companion_smart_silence_reason", _single_line(silence_decision.get("reason"), 120))
                resp.completion_text = ""
                async with self._data_lock:
                    current = self._get_user(user_id)
                    stats = current.setdefault("postprocess_stats", {})
                    if not isinstance(stats, dict):
                        stats = {}
                        current["postprocess_stats"] = stats
                    stats["smart_silence"] = _safe_int(stats.get("smart_silence"), 0, 0) + 1
                    stats["last_smart_silence_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M")
                    self._save_data_sync()
                logger.info(
                    "[PrivateCompanion] 智能沉默已取消本轮私聊回复: user=%s reason=%s inbound=%s reply=%s",
                    user_id,
                    _single_line(silence_decision.get("reason"), 120),
                    _single_line(inbound_text, 120),
                    _single_line(working_text, 140),
                )
                self._record_passive_no_reply(
                    event,
                    source="智能沉默",
                    reason=_single_line(silence_decision.get("reason"), 120) or "用户边界语义触发静默",
                    detail=inbound_text,
                    reply_preview=working_text,
                    level="info",
                )
                release_now = True
                return
            reviewed_text = await self._review_and_rewrite_response(
                current_user,
                inbound_text,
                working_text,
                music_album_context=music_album_context if isinstance(music_album_context, dict) else None,
            )
            if bool(getattr(self, "enable_response_self_review", True)) and self._is_response_review_drop_marker(reviewed_text):
                setattr(event, "_private_companion_response_review_drop", True)
                resp.completion_text = ""
                async with self._data_lock:
                    current = self._get_user(user_id)
                    stats = current.setdefault("postprocess_stats", {})
                    if not isinstance(stats, dict):
                        stats = {}
                        current["postprocess_stats"] = stats
                    stats["duplicate_dropped"] = _safe_int(stats.get("duplicate_dropped"), 0, 0) + 1
                    stats["last_duplicate_dropped_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M")
                    self._save_data_sync()
                logger.info(
                    "[PrivateCompanion] 回复复核已取消重复私聊回复: user=%s inbound=%s reply=%s",
                    user_id,
                    _single_line(inbound_text, 120),
                    _single_line(working_text, 160),
                )
                self._record_passive_no_reply(
                    event,
                    source="回复复核去重",
                    reason="最终回复与上一条 Bot 消息重复",
                    detail=inbound_text,
                    reply_preview=working_text,
                    level="info",
                )
                release_now = True
                return
            if reviewed_text != working_text:
                resp.completion_text = reviewed_text
                working_text = reviewed_text
                async with self._data_lock:
                    current = self._get_user(user_id)
                    stats = current.setdefault("postprocess_stats", {})
                    if not isinstance(stats, dict):
                        stats = {}
                        current["postprocess_stats"] = stats
                    stats["rewritten"] = _safe_int(stats.get("rewritten"), 0, 0) + 1
                    stats["last_rewritten_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M")
                    self._save_data_sync()

            async with self._data_lock:
                live_user_for_duplicate = self._get_user(user_id)
            if bool(getattr(self, "enable_response_self_review", True)):
                should_drop_duplicate, duplicate_reason = self._should_drop_duplicate_reply_text(live_user_for_duplicate, inbound_text, working_text)
            else:
                should_drop_duplicate, duplicate_reason = False, ""
            if should_drop_duplicate:
                setattr(event, "_private_companion_response_review_drop", True)
                resp.completion_text = ""
                async with self._data_lock:
                    current = self._get_user(user_id)
                    stats = current.setdefault("postprocess_stats", {})
                    if not isinstance(stats, dict):
                        stats = {}
                        current["postprocess_stats"] = stats
                    stats["duplicate_dropped"] = _safe_int(stats.get("duplicate_dropped"), 0, 0) + 1
                    stats["last_duplicate_dropped_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M")
                    self._save_data_sync()
                logger.info(
                    "[PrivateCompanion] 发送前去重已取消重复私聊回复: user=%s reason=%s inbound=%s reply=%s",
                    user_id,
                    _single_line(duplicate_reason, 120),
                    _single_line(inbound_text, 120),
                    _single_line(working_text, 160),
                )
                self._record_passive_no_reply(
                    event,
                    source="回复复核去重",
                    reason=duplicate_reason or "最终回复与上一条 Bot 消息重复",
                    detail=inbound_text,
                    reply_preview=working_text,
                    level="info",
                )
                release_now = True
                return

            async with self._data_lock:
                current = self._get_user(user_id)
                visible_reply_text = _single_line(_strip_internal_message_blocks(working_text), 500)
                current["last_companion_message"] = visible_reply_text
                current["last_companion_message_at"] = _now_ts()
                self._remember_passive_reply_topic(current, working_text, inbound_text)
                self._save_data_sync()
        except Exception:
            release_now = True
            raise
        finally:
            if release_now:
                self._release_framework_session_lock_for_event(event, label="llm_response_finally")

    async def _debug_prompt_text(self, kind: str, user: dict[str, Any], event: AstrMessageEvent | None = None) -> str:
        normalized = str(kind or "").strip().lower()
        await self._ensure_weather_context()
        if normalized in {"日程", "plan", "daily_plan"}:
            memory_companion_context = ""
            memory_companion_context_getter = getattr(self, "_memory_companion_compose_schedule_context", None)
            if callable(memory_companion_context_getter):
                memory_companion_context = await memory_companion_context_getter(kind="daily_plan", max_chars=1300)
            return self._build_daily_plan_prompt(
                self._environment_now().strftime("%Y-%m-%d %H:%M"),
                memory_companion_context=memory_companion_context,
            )
        if normalized in {"细化", "detail", "enhancement"}:
            plan = dict(self.data.get("daily_plan", {}))
            state = dict(self.data.get("daily_state", {}))
            enhanced = self.data.get("detail_enhanced_segments", {})
            if not isinstance(enhanced, dict):
                enhanced = {}
            segment = self._current_detail_segment_for_update() or self._pick_detail_segment(plan, enhanced)
            if not segment:
                current_item = self._get_current_plan_item(plan)
                if not isinstance(current_item, dict):
                    return "当前没有可用于细化的日程段。先生成日程,并等到某个时间段临近,或让当天有当前日程项。"
                start = self._parse_hhmm_to_minutes(current_item.get("time")) or self._environment_now_minutes()
                segment = {
                    "start": start,
                    "end": min(24 * 60, start + 120),
                    "item": current_item,
                }
            memory_companion_context = ""
            memory_companion_context_getter = getattr(self, "_memory_companion_compose_schedule_context", None)
            if callable(memory_companion_context_getter):
                memory_companion_context = await memory_companion_context_getter(
                    kind="detail",
                    segment=segment,
                    plan=plan,
                    state=state,
                    max_chars=1100,
                )
            return self._build_detail_enhancement_prompt(
                segment,
                plan,
                state,
                memory_companion_context=memory_companion_context,
            )
        if normalized in {"主动", "proactive"}:
            name = str(user.get("nickname") or self.default_nickname)
            planned_reason = str(user.get("planned_proactive_reason") or "")
            planned_action = str(user.get("planned_proactive_action") or "message")
            planned_motive = _single_line(user.get("planned_proactive_motive"), 140)
            reason = planned_reason if planned_reason and self._is_reason_allowed_now(planned_reason) else ""
            if not reason:
                reason, _ = self._choose_proactive_message(user, name, planned_reason)
                planned_motive = self._choose_proactive_motive(reason, user, action=planned_action)
            planned_topic = _single_line(user.get("planned_proactive_topic"), 48)
            framework_prompt = await self._build_framework_proactive_prompt(
                user=user,
                name=name,
                reason=reason,
                action=planned_action,
                action_context="（调试预览：这里会放工具结果或观察结果）",
                motive=planned_motive,
            )
            return (
                "【说明】\n"
                "当前主动消息已改为走 AstrBot 框架唤醒链。\n"
                "人格、历史对话和会话上下文不再在这里手工重复拼接,而是由框架根据当前 conversation 自动注入。\n\n"
                + (f"【内部话题钩子】\n{planned_topic}\n\n" if planned_topic else "")
                + "【送入框架的任务提示】\n"
                f"{framework_prompt}"
            )
        if normalized in {"回复注入", "reply", "injection"}:
            await self._refresh_default_persona_prompt(getattr(event, "unified_msg_origin", "") if event is not None else "")
            state = await self._ensure_daily_state()
            parts = [self._format_state_injection(state)]
            life_context = self._format_life_context_injection()
            if life_context:
                parts.append(life_context)
            important_dates = self._format_important_dates_injection()
            if important_dates:
                parts.append(important_dates)
            detail_injection = self._format_detail_injection()
            if detail_injection:
                parts.append(detail_injection)
            return "\n\n".join(parts)
        return "可查看的提示词类型：日程 / 细化 / 主动 / 回复注入"

    @filter.command("陪伴", alias={"私聊陪伴", "主动陪伴"})
    async def companion_command(self, event: AstrMessageEvent):
        """管理私聊陪伴状态、日程、记忆、风格、重要日期和可选外部动作。"""
        self._qzone_note_event_bot(event)
        raw_text = str(event.message_str or "")
        args = raw_text.replace("\u3000", " ").split(maxsplit=2)
        action = args[1].strip() if len(args) >= 2 else "帮助"
        value = args[2].strip() if len(args) >= 3 else ""
        companion_manual_query_actions = {"答疑", "排障", "诊断", "说明"}
        companion_manual_confirm_actions = {"答疑确认", "排障确认", "诊断确认", "应用答疑建议", "应用建议"}
        companion_manual_cancel_actions = {"答疑取消", "排障取消", "诊断取消", "取消答疑建议", "取消建议"}
        companion_manual_setting_actions = {"答疑设置", "排障设置", "诊断设置", "答疑修改", "排障修改", "诊断修改"}
        daily_outfit_view_actions = {"今日穿搭图", "今日穿搭", "查看穿搭图", "查看穿搭", "穿搭图", "每日穿搭图", "每日穿搭", "当前穿搭图", "当前穿搭", "展示穿搭图"}
        photo_command_actions = {"生图", "画图", "绘图", "生成图片", "出图", "自拍", "拍照", "拍一张", "改图", "修图", "重绘", "P图", "p图"}
        if action in companion_manual_query_actions:
            inline_value = value.strip()
            if inline_value in {"确认", "应用", "执行", "确认执行", "应用建议"}:
                action = "答疑确认"
                value = ""
            elif inline_value in {"取消", "取消建议", "放弃"}:
                action = "答疑取消"
                value = ""
            else:
                inline_parts = inline_value.split(maxsplit=1)
                if len(inline_parts) >= 2 and inline_parts[0] in {"设置", "修改", "set", "Set", "SET"}:
                    action = "答疑设置"
                    value = inline_parts[1].strip()
                elif re.search(r"^[A-Za-z_][A-Za-z0-9_]*\s*(?:=|:|：|设为|设置为|改成|调到)\s*\S+", inline_value):
                    action = "答疑设置"
                    value = inline_value
                else:
                    maybe_key, maybe_value = self._companion_manual_parse_setting_text(inline_value)
                    if maybe_key and maybe_value:
                        ok, _, _ = self._companion_manual_normalize_config_value(maybe_key, maybe_value)
                        if ok:
                            action = "答疑设置"
                            value = inline_value
        bookshelf_password_reset_actions = {
            "重置夹层密码", "重设夹层密码", "重新生成夹层密码", "刷新夹层密码", "生成夹层密码",
            "重置书柜密码", "重设书柜密码", "重新生成书柜密码", "刷新书柜密码", "生成书柜密码",
        }
        bookshelf_password_output_actions = {
            "输出夹层密码", "强制输出夹层密码", "查看夹层密码", "显示夹层密码",
            "输出书柜密码", "强制输出书柜密码", "查看书柜密码", "显示书柜密码",
            "输出抽屉密码", "查看抽屉密码", "显示抽屉密码",
        }
        bookshelf_password_value_actions = {"强制输出", "输出", "查看密码", "查看", "显示"}
        bookshelf_password_value_targets = {"夹层密码", "书柜密码", "抽屉密码", "书柜暗格", "夹层", "书柜"}
        bookshelf_password_output_requested = (
            action in bookshelf_password_output_actions
            or (
                action in bookshelf_password_value_actions
                and _single_line(value, 24) in bookshelf_password_value_targets
            )
        )
        response_image_path = ""
        response_extra_components: list[Any] = []
        deferred_actions = {
            "重置插件", "重置", "全部重置",
            "查看提示词", "提示词", "prompt",
            "重置细化",
            "重置日程", "生成日程", "刷新日程",
            "生成穿搭", "刷新穿搭", "重置穿搭",
            "生成状态", "刷新状态", "重生状态",
            "增添状态", "添加状态",
            "生成日记", "刷新日记",
            "梦境", "做了什么梦", "今日梦境",
            *bookshelf_password_reset_actions,
            "发说说", "发QQ空间", "发布说说", "空间发布", "发布空间",
            "测试说说链路", "测试空间发布", "测试QQ空间发布", "测试qzone发布",
            "测试说说配图", "测试空间配图", "测试QQ空间配图", "测试qzone配图",
            "新闻", "今日新闻", "AI新闻", "ai新闻", "AI日报", "ai日报", "日报", "AI早报", "ai早报", "早报",
            "TTS语种", "tts语种", "语音语种", "TTS", "tts",
            *companion_manual_query_actions,
            *photo_command_actions,
        }

        is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        public_safe_actions = {
            *companion_manual_query_actions,
            *companion_manual_confirm_actions,
            *companion_manual_cancel_actions,
            *companion_manual_setting_actions,
            *daily_outfit_view_actions,
        }
        if self.require_private_opt_in and not is_private and action not in public_safe_actions:
            await self._reply(event, self._private_only_text())
            event.stop_event()
            return

        management_actions = {
            "重置插件", "重置", "全部重置",
            "查看提示词", "提示词", "prompt",
            "重置细化", "重置日程", "生成日程", "刷新日程",
            "生成穿搭", "刷新穿搭", "重置穿搭",
            "生成状态", "刷新状态", "重生状态",
            "增添状态", "添加状态",
            "生成日记", "刷新日记",
            *bookshelf_password_reset_actions,
            *bookshelf_password_output_actions,
            "发说说", "发QQ空间", "发布说说", "空间发布", "发布空间",
            "测试说说链路", "测试空间发布", "测试QQ空间发布", "测试qzone发布",
            "测试说说配图", "测试空间配图", "测试QQ空间配图", "测试qzone配图",
            "新闻", "今日新闻", "AI新闻", "ai新闻", "AI日报", "ai日报", "日报", "AI早报", "ai早报", "早报",
            "TTS语种", "tts语种", "语音语种", "TTS", "tts",
            "撤回消息", "防撤回", "转述撤回", "撤回转述",
            "日期添加", "添加日期", "重要日期添加",
            "日期删除", "删除日期", "重要日期删除",
            "话头删除", "删除话头", "未完话头删除", "删除未完话头",
            "清空记忆", "忘记我",
            "参考图", "人设参考图", "自拍参考图",
        }
        if (action in management_actions or bookshelf_password_output_requested) and not self._can_manage_private_companion(event):
            await self._reply(event, self._management_denied_text())
            event.stop_event()
            return

        user_id = str(event.get_sender_id())
        async with self._data_lock:
            user = self._get_user(user_id)
            self._note_private_user_umo(user_id, user, event.unified_msg_origin)

            if action in {"状态", "status"}:
                self._reset_daily_counter_if_needed(user)
                last_seen = self._format_timestamp_elapsed(self._latest_user_activity_ts(user))
                last_sent = self._format_timestamp_elapsed(user.get("last_sent"))
                plan = self.data.get("daily_plan", {})
                plan_text = self._format_plan_status_summary(plan if isinstance(plan, dict) else {})
                state = self.data.get("daily_state", {})
                state_text = (
                    f"{state.get('date')}｜能量 {state.get('energy', 70)}/100｜情绪偏{state.get('mood_bias', '平稳')}"
                    if state else "未生成"
                )
                simulation_text = self._format_simulation_summary(user)
                response = "".join(
                    [
                        "运行模式：默认开启\n",
                        f"称呼：{user.get('nickname') or self.default_nickname}\n",
                        f"语气：{user.get('style') or self.default_style}\n",
                        f"日程：{plan_text}\n",
                        f"拟人状态：{state_text}\n",
                        f"关系角色：{self._private_user_role_label(self._private_user_role(user, user_id))}\n",
                        f"今日主动消息：{user.get('sent_today', 0)}/{self._effective_user_daily_limit(user)}\n",
                        f"今日软目标：约 {self._soft_daily_target(user):.1f} 条\n",
                        f"免打扰：{self.quiet_hours}\n",
                        f"上次活跃：{last_seen}\n",
                        f"上次主动：{last_sent}\n",
                        f"下次候选：{self._format_next_proactive(user)}\n",
                        f"{simulation_text}\n" if simulation_text else "",
                        f"{self._format_suspended_summary(user)}\n",
                        f"主动方式承接：{self._format_action_affinity_summary(user)}\n",
                        f"关系：{self._format_relationship_summary(user)}",
                    ]
                )
            elif action in {"撤回消息", "防撤回", "转述撤回", "撤回转述"}:
                if not self.enable_recall_enhancement or not self.enable_recall_transcribe_command:
                    response = "撤回消息转述没有开启。"
                else:
                    response = self._format_recalled_messages_for_event(event, limit=5)
                    response_extra_components = self._recalled_message_media_components_for_event(event, limit=5)
            elif action in {"TTS语种", "tts语种", "语音语种", "TTS", "tts"}:
                tts_value = value
                if action in {"TTS", "tts"}:
                    tts_parts = value.split(maxsplit=1)
                    if tts_parts and tts_parts[0].strip().lower() in {"语种", "语言", "language", "lang"}:
                        tts_value = tts_parts[1].strip() if len(tts_parts) >= 2 else ""
                response = self._set_tts_voice_language_from_command(tts_value)
            elif action in companion_manual_confirm_actions:
                response = self._companion_manual_apply_pending_config(event)
            elif action in companion_manual_cancel_actions:
                response = self._companion_manual_cancel_pending_config(event)
            elif action in companion_manual_setting_actions:
                response = self._companion_manual_apply_setting_command(event, value)
            elif action in companion_manual_query_actions:
                response = "正在结合说明书和当前运行状态做诊断。"
            elif action in {"参考图", "人设参考图", "自拍参考图"}:
                response = await self._photo_reference_command_text(event, user_id, value)
            elif action in daily_outfit_view_actions:
                response, response_image_path = self._daily_outfit_command_payload()
            elif action in photo_command_actions:
                response = "正在准备图片。"
            elif action in {"查看主动判定", "主动判定", "判定"}:
                response = self._explain_proactive_decision(user)
            elif action in {"能力列表", "主动能力", "工具列表"}:
                response = self._format_proactive_ability_list_for_user(user)
            elif action in {"重置插件", "重置", "全部重置"}:
                response = "正在清空插件状态,并重新生成今天的状态和日程。"
            elif action in {"查看提示词", "提示词", "prompt"}:
                response = "正在整理当前这层提示词。"
            elif action in {"重置细化"}:
                response = "正在生成当前时间段细化。"
            elif action in {"增添状态", "添加状态"}:
                response = "正在把这个状态加进去。"
            elif action in {"当前细化", "查看当前细化"}:
                response = self._format_current_detail_view()
            elif action in {"查看今日日程", "查看日程", "今日日程", "日程"}:
                plan = self.data.get("daily_plan", {})
                response = self._format_daily_plan(plan)
            elif action in {"重置日程", "生成日程", "刷新日程"}:
                response = "正在生成今天的日程,我先把今天怎么过想清楚。"
            elif action in {"生成穿搭", "刷新穿搭", "重置穿搭"}:
                response = "正在按今日日程生成每日穿搭照片。"
            elif action in {"生成状态", "刷新状态", "重生状态"}:
                response = "正在刷新今天的拟人状态。"
            elif action in {"梦境", "做了什么梦", "今日梦境"}:
                state = self.data.get("daily_state", {})
                response = self._format_dream_view(state if isinstance(state, dict) else {})
            elif action in {"梦境碎片", "梦碎片", "碎片梦境"}:
                response = self._format_dream_fragment_pool_view()
            elif action in {"画像", "关系", "回复率"}:
                response = self._format_user_profile(user)
            elif action in {"记忆", "陪伴记忆"}:
                response = "当前陪伴记忆：\n" + self._format_companion_memory_for_prompt(user)
            elif action in {"表达学习", "说话风格", "口癖"}:
                response = "当前表达节奏学习：\n" + self._format_expression_profile_for_prompt(user)
            elif action in {"气氛", "意图", "关系状态"}:
                response = "当前气氛判断：\n" + (self._format_intent_relationship_injection(user) or "暂无样本。")
            elif action in {"片段", "对话片段", "共同经历", "未完成"}:
                episode_text = self._format_dialogue_episodes_for_prompt(user) or "暂无对话片段记忆。"
                loop_text = self._format_open_loops_for_prompt(user) or "暂无未完成约定。"
                response = f"当前对话片段：\n{episode_text}\n\n未完话头：\n{loop_text}"
            elif action in {"话头删除", "删除话头", "未完话头删除", "删除未完话头"}:
                response = self._remove_open_loop_entry(user, value)
                self._save_data_sync()
            elif action in {"长期记忆", "livingmemory", "lmem", "向量记忆"}:
                response = self._format_livingmemory_status()
            elif action in {"日记", "bot日记", "小记"}:
                response = self._format_diaries()
            elif action in {"书柜密码", "夹层密码", "抽屉密码", "书柜暗格"}:
                response = "这个要直接问我本人。她会不会说、怎么说,要看当时的人格和心情。"
            elif bookshelf_password_output_requested:
                password = await self._ensure_bookshelf_password_async()
                password_reason = await self._ensure_bookshelf_password_reason_async(password)
                secret = self.data.get("bookshelf_secret", {}) if isinstance(self.data.get("bookshelf_secret"), dict) else {}
                response = (
                    "当前书柜夹层密码：\n"
                    f"{password}\n"
                    f"生成方式：{_single_line(secret.get('basis'), 40) or '未知'}\n"
                    f"理由：{password_reason or '这是一枚书柜夹层里的私密暗号。'}"
                )
            elif action in bookshelf_password_reset_actions:
                secret = self.data.setdefault("bookshelf_secret", {})
                if not isinstance(secret, dict):
                    secret = {}
                    self.data["bookshelf_secret"] = secret
                secret.pop("password", None)
                secret["reset_at"] = _now_ts()
                await self._ensure_bookshelf_password_async()
                self._save_data_sync()
                response = "已重新设置书柜夹层密码。需要查看真实密码可用：陪伴 输出夹层密码"
            elif action in {"发说说", "发QQ空间", "发布说说", "空间发布", "发布空间"}:
                response = "正在发布 QQ 空间说说。"
            elif action in {"测试说说链路", "测试空间发布", "测试QQ空间发布", "测试qzone发布"}:
                response = "正在模拟 QQ 空间发布链路。"
            elif action in {"测试说说配图", "测试空间配图", "测试QQ空间配图", "测试qzone配图"}:
                response = "正在测试 QQ 空间配图生成链路。"
            elif action in {"AI日报", "ai日报", "日报", "AI早报", "ai早报", "早报"}:
                response = "我先看看最近的 AI 日报记录。"
            elif action in {"新闻", "今日新闻", "AI新闻", "ai新闻"}:
                response = "正在读今天的新闻源。"
            elif action in {"生成日记", "刷新日记"}:
                response = "正在写今天的日记。"
            elif action in {"日期列表", "重要日期", "日期"}:
                response = self._format_important_dates()
            elif action in {"日期添加", "添加日期", "重要日期添加"}:
                ok, response = self._add_important_date_entry(value)
                if ok:
                    self._save_data_sync()
            elif action in {"日期删除", "删除日期", "重要日期删除"}:
                response = self._remove_important_date_entry(value)
                self._save_data_sync()
            elif action in {"可做事项", "能做什么"}:
                items = self.data.get("can_do", [])
                if items:
                    response = "我现在可以安排进日程的事：\n" + "\n".join(f"- {_single_line(item, 80)}" for item in items)
                else:
                    response = "还没有可做事项。"
            elif action in {"昵称", "称呼"}:
                if not value:
                    response = "请这样设置：陪伴 昵称 <你喜欢的称呼>"
                else:
                    user["nickname"] = _single_line(value, 24)
                    self._save_data_sync()
                    response = f"记住了,以后我会叫你：{user['nickname']}"
            elif action in {"语气", "风格"}:
                style_value = _single_line(value, 24)
                if not style_value:
                    response = "请这样设置：陪伴 语气 <简短语气描述>"
                else:
                    user["style"] = style_value
                    self._save_data_sync()
                    response = f"语气偏好已记录：{style_value}"
            elif action in {"清空记忆", "忘记我"}:
                self.data.setdefault("users", {}).pop(user_id, None)
                self._save_data_sync()
                response = "已清空你的陪伴设置和轻量记忆。"
            else:
                response = self._help_text()

        if action not in deferred_actions:
            await self._reply_with_optional_media(
                event,
                response,
                response_image_path,
                extra_components=response_extra_components,
            )
        if action in companion_manual_query_actions:
            await self._reply(event, await self._companion_manual_answer(event, value))
            event.stop_event()
            return
        if action in photo_command_actions:
            await self._handle_companion_photo_command(event, user_id, action, value)
            return
        if action in {"发说说", "发QQ空间", "发布说说", "空间发布", "发布空间"}:
            image_sources = await self._qzone_image_sources_from_event(event)
            image_sources, image_select_message = self._qzone_select_image_sources(value, image_sources)
            if image_select_message:
                await self._reply(event, image_select_message)
                event.stop_event()
                return
            publish_text = self._qzone_clean_publish_text(value)
            if image_sources and publish_text in {"[图片]", "【图片】", "图片"}:
                publish_text = ""
            if not publish_text and not image_sources:
                await self._reply(event, "请这样使用：陪伴 发说说 <正文>，也可以随消息附带图片。\n这是公开发布动作，正文或图片不能为空。")
                event.stop_event()
                return
            await self._reply(event, response)
            result = await self._publish_qzone_text(publish_text, event, images=image_sources, auto_generate_image=True)
            if result.get("success"):
                await self._reply(
                    event,
                    "QQ 空间说说已发布。\n"
                    f"QQ：{result.get('uin') or '未知'}\n"
                    f"tid：{result.get('tid') or '未知'}\n"
                    f"正文：{_single_line(result.get('text'), 160) or '无'}\n"
                    f"图片：{len(result.get('images') or [])} 张\n"
                    f"校验：{_single_line(result.get('verify_message'), 120) or ('通过' if result.get('verified') else '未校验')}",
                )
            else:
                await self._reply(event, f"发布失败：{_single_line(result.get('message'), 180)}")
            event.stop_event()
            return
        if action in {"测试说说链路", "测试空间发布", "测试QQ空间发布", "测试qzone发布"}:
            await self._reply(event, response)
            await self._reply(event, await self._test_qzone_publish_tool_chain(event))
            event.stop_event()
            return
        if action in {"测试说说配图", "测试空间配图", "测试QQ空间配图", "测试qzone配图"}:
            await self._reply(event, response)
            await self._reply(event, await self._test_qzone_publish_image_chain(event))
            event.stop_event()
            return
        if action in {"AI日报", "ai日报", "日报", "AI早报", "ai早报", "早报"}:
            await self._reply(event, response)
            await self._maybe_track_ai_daily(force=True)
            await self._reply(event, self._format_ai_daily_digest_for_command())
            event.stop_event()
            return
        if action in {"新闻", "今日新闻", "AI新闻", "ai新闻"}:
            await self._reply(event, response)
            await self._perform_news_reading(reason="user_query", allow_share=False, force=True)
            await self._reply(event, self._format_news_digest_for_command())
            event.stop_event()
            return
        if action in bookshelf_password_reset_actions:
            await self._reply(event, response)
        if action in {"重置插件", "重置", "全部重置"}:
            await self._reset_plugin_store()
            state, plan, _ = await self._rebuild_today_after_reset()
            await self._reply(
                event,
                "插件状态已清空并重建。\n"
                + self._format_state_detail(state)
                + "\n\n"
                + self._format_daily_plan(plan or {}),
            )
        if action in {"重置日程", "生成日程", "刷新日程"}:
            plan = await self._ensure_daily_plan(force=True)
            async with self._data_lock:
                self.data["detail_enhanced_day"] = str((plan or {}).get("date") or _today_key())
                self.data["detail_enhanced_segments"] = {}
                self.data["daily_story_plan"] = {}
                self._save_data_sync()
            await self._reply(event, self._format_daily_plan(plan or {}))
        if action in {"生成穿搭", "刷新穿搭", "重置穿搭"}:
            await self._reply(event, "等我换身衣服哦")
            async with self._data_lock:
                self.data.pop("daily_outfit_photo", None)
                self._save_data_sync()
            plan = await self._ensure_daily_plan(force=False)
            if not plan:
                plan = await self._ensure_daily_plan(force=True)
            outfit_generator = getattr(self, "_ensure_daily_outfit_photo", None)
            outfit = await outfit_generator(force=True) if callable(outfit_generator) else None
            if isinstance(outfit, dict) and outfit.get("path"):
                image_path = _single_line(outfit.get("path"), 300)
                if not os.path.exists(image_path):
                    await self._reply(event, f"每日穿搭照片未生成：图片文件不存在 {image_path}")
                    event.stop_event()
                    return
                try:
                    await event.send(self._build_outbound_result("换好啦，你看", image_path))
                except Exception:
                    chain = self._build_outbound_chain("换好啦，你看", image_path)
                    await event.send(event.chain_result(chain))
            else:
                error = _single_line((outfit or {}).get("error") if isinstance(outfit, dict) else "", 180)
                note = _single_line((outfit or {}).get("note") if isinstance(outfit, dict) else "", 180)
                await self._reply(event, f"每日穿搭照片未生成：{error or note or '没有可用结果'}")
        if action in {"生成状态", "刷新状态", "重生状态"}:
            state = await self._ensure_daily_state(force=True)
            async with self._data_lock:
                self.data["daily_plan"] = {}
                self._save_data_sync()
            await self._reply(
                event,
                self._format_state_detail(state)
                + "\n今天的日程已清空,下次生成日程会按这个状态重新安排。",
            )
        if action in {"增添状态", "添加状态"}:
            ok, message = await self._add_manual_state(value)
            if ok:
                async with self._data_lock:
                    self.data["daily_plan"] = {}
                    state = dict(self.data.get("daily_state", {}))
                    self._save_data_sync()
                await self._reply(
                    event,
                    message
                    + "\n"
                    + self._format_state_detail(state)
                    + "\n今天的日程已清空,下次生成日程会按这个状态重新安排。",
                )
            else:
                await self._reply(event, message)
        if action in {"查看提示词", "提示词", "prompt"}:
            prompt_text = await self._debug_prompt_text(value or "主动", user, event)
            await self._reply(event, prompt_text)
        if action in {"重置细化"}:
            plan = await self._ensure_daily_plan(force=False)
            if not plan:
                plan = await self._ensure_daily_plan(force=True)
            await self._ensure_detail_enhancement(force=False)
            detail_text = self._format_current_detail_view()
            if any(
                marker in detail_text
                for marker in (
                    "还没有",
                    "没有落地",
                    "没有生成出可展示",
                    "当前时间段还没有",
                )
            ):
                await self._ensure_detail_enhancement(force=True)
                detail_text = self._format_current_detail_view()
            await self._reply(event, detail_text)
        if action in {"生成日记", "刷新日记"}:
            diary = await self._ensure_daily_diary(force=True)
            await self._reply(event, self._format_single_diary(diary or {}))
        if action in {"梦境", "做了什么梦", "今日梦境"}:
            state = await self._ensure_daily_state(force=False)
            if not state:
                state = await self._ensure_daily_state(force=True)
                await self._reply(event, self._format_dream_view(state or {}))
        event.stop_event()

    @filter.command("陪伴群", alias={"群陪伴", "群聊陪伴"})
    async def group_companion_command(self, event: AstrMessageEvent):
        """管理群聊陪伴状态、群友画像、群内常见词、话题线程和关系网。"""
        self._qzone_note_event_bot(event)
        async for result in self._group_companion_command_impl(event):
            yield result

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def on_private_message(self, event: AstrMessageEvent):
        """记录私聊互动、图片防抖、用户画像和主动陪伴反馈。"""
        self._qzone_note_event_bot(event)
        received_ts = _now_ts()
        user_id = str(event.get_sender_id())
        self_id = self._event_self_id(event)
        if user_id and self_id and user_id == self_id:
            logger.info("[PrivateCompanion] 忽略 Bot 自己的私聊回流事件: user=%s", user_id)
            return
        sender_display_name = _single_line(self._sender_display_name(event), 40)
        text = _single_line(event.message_str, 120)
        if text.startswith(("陪伴", "/陪伴", "私聊陪伴", "主动陪伴")):
            return
        if self._message_debounce_command_text(event, text):
            return
        existing_reply_preview = self._event_existing_reply_result_preview(event)
        if existing_reply_preview:
            logger.info(
                "[PrivateCompanion] 已有其他链路回复,跳过私聊被动接管: user=%s text=%s result=%s",
                user_id,
                _single_line(text, 80),
                _single_line(existing_reply_preview, 120),
            )
            return
        canonical_user_id = self._canonical_private_user_id(user_id)
        raw_users = self.data.get("users", {})
        existing_user = raw_users.get(canonical_user_id) if isinstance(raw_users, dict) else None
        is_configured_or_manual_target = self._is_target_private_user(
            canonical_user_id,
            existing_user if isinstance(existing_user, dict) else None,
        )
        existing_user_disabled = isinstance(existing_user, dict) and not bool(existing_user.get("enabled", True))
        if not is_configured_or_manual_target or existing_user_disabled:
            logger.info(
                "[PrivateCompanion] 非目标/未启用私聊放行默认主链: user=%s text=%s reason=%s",
                _single_line(canonical_user_id or user_id, 80),
                _single_line(text, 120),
                "disabled" if existing_user_disabled else "not_target",
            )
            return
        natural_photo_text = _single_line(event.message_str, 800)
        if natural_photo_text:
            try:
                if await self._maybe_handle_natural_language_photo_request(event, user_id, natural_photo_text):
                    return
            except Exception as exc:
                missing = _missing_optional_model_dependency(exc)
                if not missing:
                    raise
                logger.warning(
                    "[PrivateCompanion] 私聊自然语言生图前置处理缺少可选模型依赖，已降级放行普通私聊: user=%s module=%s err=%s",
                    user_id,
                    missing,
                    _single_line(exc, 160),
                )
        if self._proactive_only_blocks_passive_event(event, "private_event_pipeline"):
            await self._record_proactive_only_private_feedback(
                event,
                user_id=user_id,
                sender_display_name=sender_display_name,
                text=text,
                received_ts=received_ts,
            )
            return
        receipt_text = _single_line(event.message_str, 800)
        if receipt_text and await self._maybe_handle_atrelay_private_receipt_reply(event, user_id, sender_display_name, receipt_text):
            return
        forward_only_prompt = ""
        if self._feature_enabled_or_temp_unlocked("enable_forward_message_adaptation") and not text:
            try:
                forward_id, forward_payload = await self._find_forward_descriptor_for_event(event)
            except Exception as exc:
                forward_id, forward_payload = "", {}
                logger.info("[PrivateCompanion] 私聊合并消息预解析失败: user=%s error=%s", user_id, _single_line(exc, 120))
            if forward_id or forward_payload:
                forward_only_prompt = "我转发了一段聊天记录,你看看里面在说什么。"
                text = forward_only_prompt
                try:
                    event.message_str = forward_only_prompt
                    message_obj = getattr(event, "message_obj", None)
                    if message_obj is not None:
                        setattr(message_obj, "message_str", forward_only_prompt)
                except Exception:
                    pass
                logger.info(
                    "[PrivateCompanion] 私聊纯合并消息已补触发文本: user=%s id=%s inline=%s",
                    user_id,
                    _single_line(forward_id, 40) or "inline",
                    bool(forward_payload),
                )
        if not text and not forward_only_prompt:
            quoted_relation_text = await self._private_reply_only_relation_lookup_text(event)
            if quoted_relation_text:
                text = quoted_relation_text
                try:
                    event.message_str = quoted_relation_text
                    message_obj = getattr(event, "message_obj", None)
                    if message_obj is not None:
                        setattr(message_obj, "message_str", quoted_relation_text)
                except Exception:
                    pass
        if not text and not forward_only_prompt and not self._private_event_has_image_safe(event, label="private_empty_guard"):
            component_types: list[str] = []
            try:
                for item in self._event_components(event):
                    component_types.append(_single_line(self._component_type_name(item), 40))
            except Exception:
                component_types = []
            logger.info(
                "[PrivateCompanion] 忽略空私聊事件,避免占用主链会话锁并阻止默认 LLM 空跑: user=%s components=%s",
                user_id,
                ",".join([item for item in component_types if item]) or "-",
            )
            self._record_passive_no_reply(
                event,
                source="私聊事件",
                reason="空私聊事件被忽略",
                detail=",".join([item for item in component_types if item]) or "-",
                level="info",
            )
            empty_result = self._build_result_from_chain([])
            try:
                empty_result.stop_event()
            except Exception:
                pass
            event.set_result(empty_result)
            event.stop_event()
            return
        reference_media_with_text = False
        if text and not forward_only_prompt:
            try:
                reference_media_with_text = await self._event_references_media_or_forward_with_text(event, text)
            except Exception as exc:
                missing = _missing_optional_model_dependency(exc)
                if not missing:
                    raise
                logger.warning(
                    "[PrivateCompanion] 私聊引用媒体检测缺少可选模型依赖，已按普通文本继续: user=%s module=%s err=%s",
                    user_id,
                    missing,
                    _single_line(exc, 160),
                )
                reference_media_with_text = False
            if reference_media_with_text:
                logger.info(
                    "[PrivateCompanion] 私聊引用媒体/合并消息附带文字,跳过文本收口等待: user=%s text=%s",
                    user_id,
                    _single_line(text, 80),
                )

        raw_users = self.data.get("users", {})
        fast_user = raw_users.get(user_id) if isinstance(raw_users, dict) else None
        fast_target_user = isinstance(fast_user, dict) and self._is_target_private_user(user_id, fast_user) and bool(fast_user.get("enabled", True))
        if (
            fast_target_user
            and text
            and not forward_only_prompt
            and not reference_media_with_text
            and await self._maybe_resume_pending_atrelay_request(event, user_id, text)
        ):
            return
        if (
            fast_target_user
            and text
            and not forward_only_prompt
            and not reference_media_with_text
            and await self._maybe_handle_direct_atrelay_request(event, text)
        ):
            return
        if (
            fast_target_user
            and text
            and not forward_only_prompt
            and not bool(getattr(self, "enable_smart_message_debounce", False))
            and self._message_debounce_seconds("text") <= 0
            and self._is_lightweight_private_passive_inbound(text)
            and not self._is_private_image_only_message(event, text)
        ):
            if self._is_recent_poke_echo(fast_user, text):
                logger.info("[PrivateCompanion] 忽略 poke 回流事件,不计入用户新消息: %s", user_id)
                return
            if self._is_duplicate_inbound_message(event, scope=f"private:{user_id}", sender_id=user_id, text=text):
                self._record_passive_no_reply(
                    event,
                    source="私聊去重",
                    reason="重复私聊事件被忽略",
                    detail=text,
                    level="info",
                )
                event.stop_event()
                return
            self._note_private_user_umo(user_id, fast_user, event.unified_msg_origin)
            self._note_private_display_name_observation(fast_user, user_id, sender_display_name, now=received_ts)
            fast_user["last_seen"] = received_ts
            fast_user["last_activity_at"] = received_ts
            self._note_private_inbound_activity(fast_user, received_ts, text=text)
            self._mark_greetings_satisfied_by_recent_activity(fast_user, activity_ts=received_ts)
            safe_text = self._sanitize_orphan_tts_placeholders(text)
            fast_user["last_user_message"] = safe_text or text
            fast_user["last_user_message_at"] = received_ts
            try:
                self._memory_companion_apply_emotional_drift(session_id=event.unified_msg_origin or "")
                self._memory_companion_attach_private_context(
                    event,
                    user_id=user_id,
                    user=fast_user,
                    text=safe_text or text,
                )
            except Exception as exc:
                missing = _missing_optional_model_dependency(exc)
                if not missing:
                    raise
                logger.warning(
                    "[PrivateCompanion] 私聊轻量链路记忆桥缺少可选模型依赖，已跳过记忆增强: user=%s module=%s err=%s",
                    user_id,
                    missing,
                    _single_line(exc, 160),
                )
            rest_silence_applied = self._apply_user_rest_silence_from_message(fast_user, safe_text or text, now=received_ts)
            fast_user["inbound_count"] = _safe_int(fast_user.get("inbound_count"), 0) + 1
            fast_user["relationship_score"] = _safe_int(fast_user.get("relationship_score"), 0) + 1
            fast_user["episode_message_count"] = _safe_int(fast_user.get("episode_message_count"), 0, 0) + 1
            if _safe_float(fast_user.get("awaiting_reply_since"), 0) > 0:
                fast_user["reply_count"] = _safe_int(fast_user.get("reply_count"), 0) + 1
                self._note_action_reply_feedback(
                    fast_user,
                    str(fast_user.get("last_proactive_action") or "message"),
                    text,
                )
                fast_user["relationship_score"] = _safe_int(fast_user.get("relationship_score"), 0) + 2
                fast_user["awaiting_reply_since"] = 0
                fast_user["last_reply_at"] = received_ts
                fast_user["last_private_reply_at"] = received_ts
                fast_user["pending_followup_event"] = {}
                fast_user["planned_proactive_quota_exempt"] = False
            fast_user["ignored_streak"] = 0
            fast_user_is_owner = self._private_user_role(fast_user, user_id) == "owner"
            if fast_user_is_owner and self._apply_interaction_warmth_to_state(text, fast_user):
                fast_user["relationship_score"] = _safe_int(fast_user.get("relationship_score"), 0) + 1
            self._schedule_data_save()
            if (
                rest_silence_applied
                and _safe_float(fast_user.get("user_rest_until"), 0) > received_ts
                and self._user_rest_signal_should_block_current_reply(safe_text or text)
            ):
                self._stop_private_reply_after_user_rest_signal(event, user_id, safe_text or text)
                return
            await self._acquire_framework_session_lock_for_event(event, label="private_event_pipeline")
            return

        rest_silence_early_block = False
        rest_silence_early_text = ""
        async with self._data_lock:
            user = self._get_user(user_id)
            is_target_user = self._is_target_private_user(user_id, user) and bool(user.get("enabled", True))
            if self._is_recent_poke_echo(user, text):
                logger.info("[PrivateCompanion] 忽略 poke 回流事件,不计入用户新消息: %s", user_id)
                return
            if self._is_duplicate_inbound_message(event, scope=f"private:{user_id}", sender_id=user_id, text=text):
                self._schedule_data_save()
                self._record_passive_no_reply(
                    event,
                    source="私聊去重",
                    reason="重复私聊事件被忽略",
                    detail=text,
                    level="info",
                )
                event.stop_event()
                return
            if is_target_user and text and not forward_only_prompt and not reference_media_with_text:
                self._maybe_record_smart_message_debounce_followup(
                    scope=f"private:{user_id}",
                    sender_id=user_id,
                    text=text,
                    now=received_ts,
                )
            private_image_enhancement_enabled = (
                self._feature_enabled_or_temp_unlocked("enable_private_image_self_recognition")
                and bool(getattr(self, "enable_message_debounce", getattr(self, "enable_semantic_message_debounce", True)))
                and self._message_debounce_seconds("image") > 0
            )
            private_image_only = (
                is_target_user
                and private_image_enhancement_enabled
                and self._is_private_image_only_message(event, text)
            )
            if (
                is_target_user
                and text
                and not forward_only_prompt
                and private_image_enhancement_enabled
                and not private_image_only
                and self._private_event_has_image_safe(event, label="private_text_image")
            ):
                try:
                    persisted_images = await self._persist_private_inbound_images(event, user_id)
                    usable_images = [source for source in persisted_images if self._private_image_source_to_model_url(source)]
                except Exception as exc:
                    missing = _missing_optional_model_dependency(exc)
                    if not missing:
                        raise
                    logger.warning(
                        "[PrivateCompanion] 私聊图文图片预处理缺少可选模型依赖，已按纯文本继续: user=%s module=%s err=%s",
                        user_id,
                        missing,
                        _single_line(exc, 160),
                    )
                    persisted_images = []
                    usable_images = []
                if usable_images:
                    umo = str(getattr(event, "unified_msg_origin", "") or "")
                    try:
                        has_visual_provider = self._has_private_image_visual_provider(umo)
                    except Exception as exc:
                        missing = _missing_optional_model_dependency(exc)
                        if not missing:
                            raise
                        logger.warning(
                            "[PrivateCompanion] 私聊图文视觉 provider 检测缺少可选模型依赖，已关闭本轮识图: user=%s module=%s err=%s",
                            user_id,
                            missing,
                            _single_line(exc, 160),
                        )
                        has_visual_provider = False
                    setattr(event, "private_companion_delayed_image_sources", usable_images[:5])
                    has_dynamic_gif_sources = (
                        bool(getattr(self, "enable_private_image_gif_enhancement", True))
                        and self._private_image_sources_include_gif(usable_images)
                    )
                    direct_image_mode = (
                        not has_dynamic_gif_sources
                        and self._event_main_provider_supports_image(event)
                    )
                    image_mode = "direct" if direct_image_mode else "caption" if has_visual_provider else "no_vision"
                    setattr(event, "private_companion_delayed_image_mode", image_mode)
                    if image_mode == "caption":
                        try:
                            vision_text = _single_line(
                                await self._transcribe_private_inbound_images(
                                    usable_images[:5],
                                    umo=umo,
                                    user_text=text,
                                    force_contextual=self._private_image_user_mentions_combo_result(text) or self._private_image_user_has_specific_vision_request(text),
                                ),
                                self._private_image_vision_text_limit(len(usable_images)),
                            )
                        except Exception as exc:
                            missing = _missing_optional_model_dependency(exc)
                            if not missing:
                                raise
                            logger.warning(
                                "[PrivateCompanion] 私聊图文视觉摘要缺少可选模型依赖，已按无视觉摘要继续: user=%s module=%s err=%s",
                                user_id,
                                missing,
                                _single_line(exc, 160),
                            )
                            vision_text = ""
                        if vision_text:
                            setattr(event, "private_companion_delayed_image_vision_text", vision_text)
                    logger.info(
                        "[PrivateCompanion] 私聊文本图片混合消息已接入图片上下文: user=%s images=%s mode=%s gif=%s combo=%s vision=%s text=%s",
                        user_id,
                        len(usable_images),
                        image_mode,
                        has_dynamic_gif_sources,
                        self._private_image_user_mentions_combo_result(text),
                        bool(_single_line(getattr(event, "private_companion_delayed_image_vision_text", ""), 80)),
                        _single_line(text, 80),
                    )
                else:
                    logger.info(
                        "[PrivateCompanion] 私聊文本图片混合消息未解析到可用图片源: user=%s sources=%s text=%s",
                        user_id,
                        len(persisted_images),
                        _single_line(text, 80),
                    )
            if is_target_user and forward_only_prompt:
                key = self._semantic_buffer_key(f"private:{user_id}", user_id)
                if self._note_semantic_message_buffer(
                    key,
                    text,
                    now=received_ts,
                    wait_seconds=self._message_debounce_seconds("forward"),
                    kind="forward",
                ):
                    self._schedule_data_save()
                    event.stop_event()
                    return
            if private_image_only:
                setattr(event, "private_companion_deferred_private_image_only", True)
                key = self._semantic_buffer_key(f"private:{user_id}", user_id)
                self._note_semantic_message_buffer(
                    key,
                    "用户刚刚先单独发送了一张图片,可能马上会补充说明。",
                    now=received_ts,
                    wait_seconds=self._message_debounce_seconds("image"),
                    kind="image",
                )
                buffers = getattr(self, "_semantic_message_buffers", None)
                if isinstance(buffers, dict) and isinstance(buffers.get(key), dict):
                    persisted_images = await self._persist_private_inbound_images(event, user_id)
                    has_model_usable_image = any(self._private_image_source_to_model_url(source) for source in persisted_images)
                    if not persisted_images:
                        buffers.pop(key, None)
                        setattr(event, "private_companion_deferred_private_image_only", False)
                        logger.info(
                            "[PrivateCompanion] 私聊单图未解析到可用图片源,放行原始事件: user=%s sources=%s",
                            user_id,
                            len(persisted_images),
                        )
                        self._schedule_data_save()
                        return
                    if not has_model_usable_image:
                        logger.info(
                            "[PrivateCompanion] 私聊单图已保存但不可直供模型,仍进入防抖等待补充: user=%s sources=%s",
                            user_id,
                            len(persisted_images),
                        )
                    buffers[key]["images"] = persisted_images
                    buffers[key]["original_event"] = event
                    has_dynamic_gif_sources = (
                        bool(getattr(self, "enable_private_image_gif_enhancement", True))
                        and self._private_image_sources_include_gif(persisted_images)
                    )
                    umo = str(getattr(event, "unified_msg_origin", "") or "")
                    has_visual_provider = self._has_private_image_visual_provider(umo)
                    direct_image_mode = (
                        bool(persisted_images)
                        and self._event_main_provider_supports_image(event)
                        and not has_dynamic_gif_sources
                    )
                    image_mode = "direct" if direct_image_mode else "caption" if has_visual_provider else "no_vision"
                    buffers[key]["image_mode"] = image_mode
                    if persisted_images and image_mode == "caption":
                        buffers[key]["vision_task"] = asyncio.create_task(
                            self._transcribe_private_inbound_images(
                                persisted_images,
                                umo=umo,
                            )
                        )
                    logger.info(
                        "[PrivateCompanion] 私聊单图已进入防抖缓冲: user=%s images=%s mode=%s vision=%s",
                        user_id,
                        len(persisted_images),
                        image_mode,
                        bool(persisted_images) and image_mode == "caption",
                    )
                    asyncio.create_task(self._finalize_private_image_buffer_after_wait(key, user_id, received_ts))
                self._schedule_data_save()
                event.stop_event()
                return
            elif is_target_user and not forward_only_prompt and not reference_media_with_text:
                key = self._semantic_buffer_key(f"private:{user_id}", user_id)
                buffers = getattr(self, "_semantic_message_buffers", None)
                existing_buffer = buffers.get(key) if isinstance(buffers, dict) else None
                buffered_images = (
                    isinstance(existing_buffer, dict)
                    and isinstance(existing_buffer.get("images"), list)
                    and bool(existing_buffer.get("images"))
                    and _now_ts() - _safe_float(existing_buffer.get("first_ts"), 0) <= max(
                        45.0,
                        self._message_debounce_seconds("image") + 30.0,
                    )
                )
                if buffered_images:
                    messages = existing_buffer.setdefault("messages", [])
                    if not isinstance(messages, list):
                        messages = []
                        existing_buffer["messages"] = messages
                    cleaned_text = _single_line(text, 260)
                    if cleaned_text and cleaned_text not in [_single_line(item.get("text"), 260) for item in messages if isinstance(item, dict)]:
                        messages.append({"ts": _now_ts(), "text": cleaned_text, "sender_name": ""})
                    existing_buffer["updated_ts"] = _now_ts()
                    if _safe_float(existing_buffer.get("deadline_ts"), 0.0) <= 0:
                        first_ts = _safe_float(existing_buffer.get("first_ts"), received_ts, received_ts)
                        existing_buffer["deadline_ts"] = first_ts + self._message_debounce_seconds("image")
                    logger.info(
                        "[PrivateCompanion] 消息收口合并补话: kind=image mode=fixed scope=private:%s sender=%s wait=%.1fs count=%s text=%s",
                        user_id,
                        user_id,
                        self._message_debounce_seconds("image"),
                        len(messages),
                        _single_line(cleaned_text, 80),
                    )
                else:
                    try:
                        smart_wait = await self._smart_message_debounce_wait_seconds_for_event(
                            event,
                            key=key,
                            text=text,
                            sender_id=user_id,
                            sender_name=sender_display_name,
                            private_chat=True,
                        )
                    except Exception as exc:
                        missing = _missing_optional_model_dependency(exc)
                        if not missing:
                            raise
                        logger.warning(
                            "[PrivateCompanion] 私聊智能收口模型缺少可选依赖，已回退固定等待: user=%s module=%s err=%s",
                            user_id,
                            missing,
                            _single_line(exc, 160),
                        )
                        smart_wait = self._message_debounce_seconds("text")
                        try:
                            setattr(event, "private_companion_smart_message_debounce_result", {"decision": "fixed", "confidence": 0.0, "reason": f"missing {missing}"})
                        except Exception:
                            pass
                    smart_result = getattr(event, "private_companion_smart_message_debounce_result", None)
                    smart_decision = str(smart_result.get("decision") or "") if isinstance(smart_result, dict) else ""
                    smart_handled = smart_decision in {"complete", "incomplete"}
                    wait_seconds = smart_wait if smart_handled else self._message_debounce_seconds("text")
                    if self._note_semantic_message_buffer(
                        key,
                        text,
                        wait_seconds=wait_seconds,
                        smart_debounce={"enabled": smart_handled, "decision": smart_decision or "fixed"},
                        kind="text",
                    ):
                        self._schedule_data_save()
                        event.stop_event()
                        return
                    if smart_handled:
                        self._schedule_data_save()
            user["umo"] = event.unified_msg_origin
            self._note_private_display_name_observation(user, user_id, sender_display_name, now=received_ts)
            if not is_target_user:
                user["enabled"] = False
                self._clear_pending_proactive_plan(user)
            user["last_seen"] = _now_ts()
            user["last_activity_at"] = received_ts or _now_ts()
            self._note_private_inbound_activity(user, received_ts or _now_ts(), text=text)
            self._mark_greetings_satisfied_by_recent_activity(user, activity_ts=received_ts or _now_ts())
            if text:
                user["inbound_count"] = _safe_int(user.get("inbound_count"), 0) + 1
            user["relationship_score"] = _safe_int(user.get("relationship_score"), 0) + 1
            suspended = user.get("suspended_proactive")
            if (
                isinstance(suspended, dict)
                and suspended.get("active")
                and _now_ts() - _safe_float(suspended.get("created_at"), 0) <= self.proactive_reply_context_hours * 3600
            ):
                suspended["resume_ready"] = True
                suspended["complaint_enabled"] = False
                suspended["complaint_sent"] = True
                suspended["second_followup"] = {}
                user["pending_followup_event"] = {}
                user["planned_proactive_quota_exempt"] = False
            if _safe_float(user.get("awaiting_reply_since"), 0) > 0:
                user["reply_count"] = _safe_int(user.get("reply_count"), 0) + 1
                self._note_action_reply_feedback(
                    user,
                    str(user.get("last_proactive_action") or "message"),
                    text,
                )
                user["relationship_score"] = _safe_int(user.get("relationship_score"), 0) + 2
                user["awaiting_reply_since"] = 0
                user["last_reply_at"] = _now_ts()
                user["last_private_reply_at"] = user["last_reply_at"]
                user["pending_followup_event"] = {}
                user["planned_proactive_quota_exempt"] = False
            user["ignored_streak"] = 0
            if text:
                safe_text = self._sanitize_orphan_tts_placeholders(text)
                user["last_user_message"] = safe_text or text
                user["last_user_message_at"] = received_ts
                rest_silence_applied = self._apply_user_rest_silence_from_message(user, safe_text or text, now=received_ts)
                if rest_silence_applied and _safe_float(user.get("user_rest_until"), 0) > received_ts:
                    if self._user_rest_signal_should_block_current_reply(safe_text or text):
                        rest_silence_early_block = bool(is_target_user)
                        rest_silence_early_text = safe_text or text
                self._apply_private_image_vision_negative_feedback(user, safe_text or text)
                user["episode_message_count"] = _safe_int(user.get("episode_message_count"), 0, 0) + 1
                self._update_expression_profile_from_message(user, safe_text or text)
                self._update_companion_memory_from_message(user, safe_text or text)
                self._update_open_loops_from_message(user, safe_text or text)
                self._update_action_preferences_from_message(user, safe_text or text)
                self._update_user_behavior_habits_from_message(user, safe_text or text)
                if (
                    not rest_silence_early_block
                    and (
                        self.enable_intent_emotion_analysis
                        or self.enable_relationship_state_machine
                        or self.enable_emotion_simulation
                    )
                ):
                    intent_profile = self._analyze_inbound_intent(text)
                    if self.enable_intent_emotion_analysis:
                        user["intent_profile"] = intent_profile
                    if self._should_use_llm_emotion_judgement(text, intent_profile):
                        # 模型复核不阻塞本轮被动回复；本轮继续使用进入请求前缓存的情绪状态。
                        user["pending_emotion_judgement"] = {
                            "text": _single_line(text, 240),
                            "created_at": _now_ts(),
                            "local": deepcopy(intent_profile),
                        }
                        asyncio.create_task(self._refine_inbound_emotion_with_model(user_id, text, deepcopy(intent_profile)))
                    else:
                        self._update_relationship_state_from_intent(user, intent_profile)
                if is_target_user and self._cancel_inbound_conflicting_greeting(user, now=_now_ts()):
                    logger.info("[PrivateCompanion] 用户已在当前问候时段自然来聊,已取消冲突问候候选: %s", user_id)
                    if not self._simulation_active(user) and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                        self._schedule_next_proactive(user, now=_now_ts())
            user_is_owner = self._private_user_role(user, user_id) == "owner"
            food_feedback = self._detect_food_feedback(text) if text else {"is_food": False}
            food_feedback_applied = bool(text) and user_is_owner and self._apply_food_feedback_to_state(text)
            food_feedback_actionable = bool(
                food_feedback.get("actionable")
                or food_feedback.get("feeding")
                or food_feedback.get("bot_directed")
            )
            if user_is_owner and food_feedback_actionable:
                user["last_food_feedback_at"] = _now_ts()
                user["last_food_feedback_text"] = _single_line(text, 120)
            if food_feedback.get("is_food"):
                used_food_items = self._mark_food_menu_item_used_from_text(text) if user_is_owner else []
                if used_food_items:
                    user["last_food_menu_choice"] = {
                        "ts": _now_ts(),
                        "items": used_food_items,
                        "text": _single_line(text, 120),
                    }
            care_feedback_applied = bool(text) and user_is_owner and self._apply_care_feedback_to_state(text)
            if care_feedback_applied:
                user["relationship_score"] = _safe_int(user.get("relationship_score"), 0) + 2
            interaction_warmth_applied = bool(text) and is_target_user and user_is_owner and self._apply_interaction_warmth_to_state(text, user)
            if interaction_warmth_applied:
                user["relationship_score"] = _safe_int(user.get("relationship_score"), 0) + 1
            schedule_adjustment_applied = (
                bool(text)
                and is_target_user
                and user_is_owner
                and self._record_schedule_adjustment_from_interaction(text, user)
            )
            if schedule_adjustment_applied:
                user["relationship_score"] = _safe_int(user.get("relationship_score"), 0) + 1
            if food_feedback_applied:
                user["relationship_score"] = _safe_int(user.get("relationship_score"), 0) + 1

            response = ""
            if is_target_user:
                try:
                    self._memory_companion_attach_private_context(
                        event,
                        user_id=user_id,
                        user=user,
                        text=(safe_text if text else "") or text,
                    )
                except Exception as exc:
                    missing = _missing_optional_model_dependency(exc)
                    if not missing:
                        raise
                    logger.warning(
                        "[PrivateCompanion] 私聊记忆上下文挂载缺少可选模型依赖，已跳过记忆增强: user=%s module=%s err=%s",
                        user_id,
                        missing,
                        _single_line(exc, 160),
                    )
            self._schedule_data_save()
            user_snapshot = dict(user)

        if not is_target_user:
            logger.info(
                "[PrivateCompanion] 非目标/未启用私聊放行默认主链: user=%s text=%s",
                _single_line(user_id, 80),
                _single_line(text, 120),
            )
            return
        if is_target_user and rest_silence_early_block:
            self._stop_private_reply_after_user_rest_signal(event, user_id, rest_silence_early_text or text)
            return
        if is_target_user and schedule_adjustment_applied:
            asyncio.create_task(self._kick_proactive_loop_once())
        if response:
            await self._reply(event, response)
            event.stop_event()
        elif is_target_user:
            await self._acquire_framework_session_lock_for_event(event, label="private_event_pipeline")
        if is_target_user:
            asyncio.create_task(self._refresh_persona_relationship(user_id, user_snapshot))
            asyncio.create_task(self._maybe_refresh_companion_memory(user_id, user_snapshot))
            asyncio.create_task(self._maybe_refresh_dialogue_episode(user_id, user_snapshot))

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """观察群聊消息，维护群上下文并判断是否自然唤醒 Bot。"""
        self._qzone_note_event_bot(event)
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        received_ts = _now_ts()
        text = _single_line(event.message_str, 260)
        if not text:
            return
        if text.startswith(("陪伴群", "/陪伴群", "群陪伴", "群聊陪伴")):
            return
        if self._message_debounce_command_text(event, text):
            return
        existing_reply_preview = self._event_existing_reply_result_preview(event)
        if self._proactive_only_blocks_passive_event(event, "group_event_pipeline"):
            logger.debug("[PrivateCompanion] 主动消息专用模式已跳过群聊被动观察")
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id or not self._group_enabled_for_event(group_id):
            return
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        sender_name = self._sender_display_name(event)
        if existing_reply_preview:
            async with self._data_lock:
                if self._is_duplicate_inbound_message(event, scope=f"group:{group_id}", sender_id=sender_id, text=text):
                    self._save_data_sync()
                    return
                group = self._get_group(group_id)
                group["umo"] = _single_line(getattr(event, "unified_msg_origin", ""), 160)
                scene = self._infer_group_scene(event, group, sender_id=sender_id, sender_name=sender_name, text=text)
                self._update_group_observation(
                    group,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    group_id=group_id,
                    scene=scene,
                    message_id=self._event_message_id(event),
                    event=event,
                )
                self._save_data_sync()
                group_snapshot = deepcopy(group)
            logger.info(
                "[PrivateCompanion] 已有其他链路回复,仅记录群聊观察: group=%s sender=%s text=%s result=%s",
                group_id,
                sender_id,
                _single_line(text, 80),
                _single_line(existing_reply_preview, 120),
            )
            asyncio.create_task(self._maybe_refresh_group_episode(group_id, group_snapshot))
            asyncio.create_task(self._maybe_refresh_group_slang_meanings(group_id, group_snapshot))
            return
        try:
            signals = self._event_scene_signals(event)
        except Exception:
            signals = {}
        at_bot = any(
            isinstance(item, dict) and bool(item.get("is_bot"))
            for item in (signals.get("at_targets") if isinstance(signals, dict) else []) or []
        )
        reply_to_bot = bool(
            isinstance(signals, dict)
            and _single_line(signals.get("self_id"), 80)
            and _single_line(signals.get("reply_to_id"), 80) == _single_line(signals.get("self_id"), 80)
        )
        if (at_bot or reply_to_bot) and await self._maybe_handle_natural_language_photo_request(event, sender_id, text, directed=True):
            return
        registration_payload = None
        continuation: bool | None = False
        resting_mention_notice = ""
        scene: dict[str, Any] = {}
        wakeup_state_effect: dict[str, Any] = {}
        group_for_judge: dict[str, Any] = {}
        active_for_judge: dict[str, Any] = {}
        high_intensity_state: dict[str, Any] = {}
        group_snapshot_high_intensity: dict[str, Any] = {}
        async with self._data_lock:
            if self._is_duplicate_inbound_message(event, scope=f"group:{group_id}", sender_id=sender_id, text=text):
                self._save_data_sync()
                event.stop_event()
                return
            group = self._get_group(group_id)
            if sender_id:
                users = self.data.get("users", {})
                current_sender = users.get(sender_id) if isinstance(users, dict) else None
                if sender_id in set(self._configured_target_ids()) or (
                    isinstance(current_sender, dict) and bool(current_sender.get("manual_enabled"))
                ):
                    target_user = self._get_user(sender_id)
                    target_user["last_activity_at"] = received_ts
                    self._mark_greetings_satisfied_by_recent_activity(target_user, activity_ts=received_ts)
                    self._maybe_schedule_group_ignore_complaint(
                        group_id,
                        group,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        text=text,
                        now=received_ts,
                    )
            group["umo"] = _single_line(getattr(event, "unified_msg_origin", ""), 160)
            _, resting_mention_notice = self._group_resting_mention_notice(
                event,
                group,
                sender_id=sender_id,
                now=received_ts,
            )
            if resting_mention_notice:
                self._save_data_sync()
            scene = self._infer_group_scene(event, group, sender_id=sender_id, sender_name=sender_name, text=text)
            if resting_mention_notice:
                continuation = True
                scene.update(
                    {
                        "trigger": "group_wakeup_resting_mention",
                        "talking_to": "bot",
                        "talking_to_name": "你",
                        "reason": "mentioned_resting_user",
                        "wakeup_word": "@休息用户",
                        "wakeup_strength": "strong",
                        "wakeup_strength_label": "明确需要你接话",
                        "wakeup_instruction": (
                            f"群友刚刚 @ 了一个已明确在休息的用户（内部提示：{resting_mention_notice}）。请用当前人格自然提醒发起 @ 的群友晚点再叫他；"
                            "语气柔和、像群友接话，不要像系统通知；不要私聊或 @ 休息用户，不要泄露具体休息截止时间或私聊原因。"
                        ),
                    }
                )
            high_intensity_state = self._group_high_intensity_state(group)
            if not resting_mention_notice:
                continuation = await self._group_message_is_bot_continuation(
                    group,
                    sender_id,
                    sender_name,
                    scene,
                    text,
                    allow_llm=False,
                )
            if continuation is None:
                if high_intensity_state.get("active"):
                    continuation = False
                else:
                    group_for_judge = deepcopy(group)
                active_for_judge = deepcopy(self._group_active_conversation(group))

        if continuation is None:
            judged = await self._group_followup_llm_judge(
                group_for_judge,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                active=active_for_judge,
                scene=scene,
            )
            continuation = bool(judged) if judged is not None else False

        async with self._data_lock:
            group = self._get_group(group_id)
            scene = self._infer_group_scene(event, group, sender_id=sender_id, sender_name=sender_name, text=text)
            if resting_mention_notice:
                setattr(event, "is_at_or_wake_command", True)
                setattr(event, "is_wake", True)
                scene.update(
                    {
                        "trigger": "group_wakeup_resting_mention",
                        "talking_to": "bot",
                        "talking_to_name": "你",
                        "reason": "mentioned_resting_user",
                        "wakeup_word": "@休息用户",
                        "wakeup_strength": "strong",
                        "wakeup_strength_label": "明确需要你接话",
                        "wakeup_fatigue": {},
                        "wakeup_instruction": (
                            f"群友刚刚 @ 了一个已明确在休息的用户（内部提示：{resting_mention_notice}）。请用当前人格自然提醒发起 @ 的群友晚点再叫他；"
                            "语气柔和、像群友接话，不要像系统通知；不要私聊或 @ 休息用户，不要泄露具体休息截止时间或私聊原因。"
                        ),
                    }
                )
            elif continuation:
                setattr(event, "is_at_or_wake_command", True)
                setattr(event, "is_wake", True)
                scene.update({"trigger": "bot_conversation_followup", "talking_to": "bot", "talking_to_name": "你", "reason": "contextual_followup_after_bot_wake"})
            elif self.enable_group_wakeup_enhancement and str(scene.get("trigger") or "") == "mention_bot_name":
                setattr(event, "is_at_or_wake_command", True)
                setattr(event, "is_wake", True)
                strength = self._group_wakeup_strength("direct_word", group, scene)
                fatigue = self._bump_group_wakeup_fatigue(group, "direct_word")
                scene.update(
                    {
                        "trigger": "group_wakeup_direct_word",
                        "talking_to": "bot",
                        "talking_to_name": "你",
                        "reason": "direct_wakeup_word",
                        "wakeup_word": _single_line(self.bot_name, 60),
                        "wakeup_strength": strength,
                        "wakeup_strength_label": self._group_wakeup_strength_label(strength),
                        "wakeup_fatigue": dict(fatigue),
                        "wakeup_note": "群友提到了 Bot 名字。",
                    }
                )
                group["last_group_wakeup_at"] = _now_ts()
                group["last_group_wakeup"] = {
                    "ts": _now_ts(),
                    "type": "direct_word",
                    "word": _single_line(self.bot_name, 60),
                    "strength": strength,
                    "strength_label": self._group_wakeup_strength_label(strength),
                    "reason": "direct_wakeup_word",
                    "reason_label": self._group_wakeup_reason_label("direct_word", "direct_wakeup_word"),
                    "reason_detail": "提到 Bot 名字或强唤醒词",
                    "fatigue": dict(fatigue),
                    "sender_id": sender_id,
                    "sender_name": _single_line(sender_name, 40),
                    "text": _single_line(text, 120),
                }
                wakeup_state_effect = self._apply_group_wakeup_to_humanized_state(scene, text)
                self._record_group_wakeup_log(
                    group,
                    scene=scene,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    wakeup=group["last_group_wakeup"],
                    result="woke",
                    strength=strength,
                    fatigue=fatigue,
                    note=_single_line(scene.get("wakeup_note"), 180),
                )
            else:
                wakeup = self._evaluate_group_wakeup(
                    group,
                    scene=scene,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    group_id=group_id,
                )
                if wakeup:
                    setattr(event, "is_at_or_wake_command", True)
                    setattr(event, "is_wake", True)
                    strength = _single_line(wakeup.get("strength"), 24) or self._group_wakeup_strength(str(wakeup.get("type") or ""), group, scene)
                    fatigue = self._bump_group_wakeup_fatigue(group, str(wakeup.get("type") or ""))
                    scene.update(
                        {
                            "trigger": f"group_wakeup_{wakeup.get('type')}",
                            "talking_to": "bot",
                            "talking_to_name": "你",
                            "reason": _single_line(wakeup.get("reason"), 60),
                            "wakeup_word": _single_line(wakeup.get("word"), 60),
                            "wakeup_strength": strength,
                            "wakeup_strength_label": self._group_wakeup_strength_label(strength),
                            "wakeup_fatigue": dict(fatigue),
                            "wakeup_note": _single_line(wakeup.get("note"), 180),
                            "wakeup_topic_weight": wakeup.get("topic_weight") if isinstance(wakeup.get("topic_weight"), dict) else {},
                        }
                    )
                    group["last_group_wakeup_at"] = _now_ts()
                    group["last_group_wakeup"] = {
                        "ts": _now_ts(),
                        "type": _single_line(wakeup.get("type"), 40),
                        "word": _single_line(wakeup.get("word"), 60),
                        "strength": strength,
                        "strength_label": self._group_wakeup_strength_label(strength),
                        "fatigue": dict(fatigue),
                        "probability": wakeup.get("probability"),
                        "score": wakeup.get("score"),
                        "threshold": wakeup.get("threshold"),
                        "intensity": wakeup.get("intensity"),
                        "help_type": wakeup.get("help_type"),
                        "reason": _single_line(wakeup.get("reason"), 80),
                        "reason_label": _single_line(wakeup.get("reason_label"), 80) or self._group_wakeup_reason_label(str(wakeup.get("type") or ""), str(wakeup.get("reason") or "")),
                        "reason_detail": _single_line(wakeup.get("reason_detail"), 180) or self._group_wakeup_reason_detail(wakeup),
                        "topic_weight": wakeup.get("topic_weight") if isinstance(wakeup.get("topic_weight"), dict) else {},
                        "sender_id": sender_id,
                        "sender_name": _single_line(sender_name, 40),
                        "text": _single_line(text, 120),
                    }
                    wakeup_state_effect = self._apply_group_wakeup_to_humanized_state(scene, text)
                    self._record_group_wakeup_log(
                        group,
                        scene=scene,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        text=text,
                        wakeup=group["last_group_wakeup"],
                        result="woke",
                        strength=strength,
                        fatigue=fatigue,
                        note=_single_line(wakeup.get("note"), 180),
                    )
                    logger.info(
                        "[PrivateCompanion] 群聊增强唤醒命中: group=%s sender=%s type=%s word=%s strength=%s fatigue=%s reason=%s detail=%s",
                        group_id,
                        sender_id,
                        wakeup.get("type"),
                        wakeup.get("word"),
                        strength,
                        fatigue.get("label"),
                        group["last_group_wakeup"].get("reason_label"),
                        group["last_group_wakeup"].get("reason_detail"),
                    )
            talking_to_bot = str(scene.get("talking_to") or "") == "bot"
            if (
                not talking_to_bot
                and
                str(scene.get("talking_to") or "") not in {"group", ""}
                and str(self._group_active_conversation(group).get("sender_id") or "") != str(sender_id or "")
            ):
                self._mark_group_bot_conversation(group, sender_id, sender_name, active=False)
            scene_trigger = str(scene.get("trigger") or "")
            if talking_to_bot and scene_trigger in {"at_bot", "reply_bot"}:
                strength = self._group_wakeup_strength("direct_word", group, scene)
                fatigue = self._bump_group_wakeup_fatigue(group, "direct_word")
                scene.setdefault("wakeup_strength", strength)
                scene.setdefault("wakeup_strength_label", self._group_wakeup_strength_label(strength))
                scene["wakeup_fatigue"] = dict(fatigue)
                group["last_group_wakeup_at"] = _now_ts()
                group["last_group_wakeup"] = {
                    "ts": _now_ts(),
                    "type": "direct_word",
                    "word": "@" if scene_trigger == "at_bot" else "reply",
                    "strength": strength,
                    "strength_label": self._group_wakeup_strength_label(strength),
                    "reason": "explicit_at_or_reply",
                    "reason_label": "明确 @ 或引用 Bot",
                    "reason_detail": "群友明确 @ 或引用了 Bot",
                    "fatigue": dict(fatigue),
                    "sender_id": sender_id,
                    "sender_name": _single_line(sender_name, 40),
                    "text": _single_line(text, 120),
                }
                self._record_group_wakeup_log(
                    group,
                    scene=scene,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    wakeup=group["last_group_wakeup"],
                    result="woke",
                    strength=strength,
                    fatigue=fatigue,
                    note="群友明确 @ 或引用了 Bot。",
                )
            high_intensity_state = self._group_high_intensity_state(group)
            if high_intensity_state.get("active"):
                setattr(event, "private_companion_group_high_intensity", dict(high_intensity_state))
            high_intensity_merge_active = bool(high_intensity_state.get("merge_active"))
            setattr(event, "private_companion_group_scene", dict(scene))
            setattr(event, "private_companion_group_sender_name", sender_name)
            setattr(event, "private_companion_group_text", text)
            setattr(event, "private_companion_group_contextual_followup", bool(continuation))
            self._memory_companion_attach_group_context(
                event,
                group_id=group_id,
                group=group,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
            )
            if wakeup_state_effect:
                setattr(event, "private_companion_group_wakeup_state_effect", dict(wakeup_state_effect))
            group_reference_media_with_text = False
            if talking_to_bot and text:
                group_reference_media_with_text = await self._event_references_media_or_forward_with_text(event, text)
                if group_reference_media_with_text:
                    logger.info(
                        "[PrivateCompanion] 群聊引用媒体/合并消息附带文字,跳过群聊收口等待: group=%s sender=%s text=%s",
                        group_id,
                        sender_id,
                        _single_line(text, 80),
                    )
            if high_intensity_merge_active and talking_to_bot and not group_reference_media_with_text:
                high_key = self._group_high_intensity_buffer_key(group_id, sender_id)
                if self._note_semantic_message_buffer(
                    high_key,
                    text,
                    sender_name=sender_name,
                    wait_seconds=self._group_high_intensity_merge_wait_seconds(),
                    force=True,
                    kind="group_high_intensity",
                ):
                    self._update_group_observation(
                        group,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        text=text,
                        group_id=group_id,
                        scene=scene,
                        message_id=self._event_message_id(event),
                        event=event,
                    )
                    self._save_data_sync()
                    logger.info(
                        "[PrivateCompanion] 群聊高强度消息已合并等待: group=%s sender=%s scope=%s recent_wakeups=%s floor=%s reason=%s wait=%ss text=%s",
                        group_id,
                        sender_id,
                        getattr(self, "group_high_intensity_merge_scope", "group"),
                        high_intensity_state.get("recent_wakeups"),
                        high_intensity_state.get("merge_recent_floor"),
                        high_intensity_state.get("reason"),
                        self._group_high_intensity_merge_wait_seconds(),
                        _single_line(text, 80),
                    )
                    event.stop_event()
                    return
            group_smart_wait = 0.0
            group_buffer_key = self._semantic_buffer_key(f"group:{group_id}", sender_id)
            if talking_to_bot and not high_intensity_merge_active and not group_reference_media_with_text:
                self._maybe_record_smart_message_debounce_followup(
                    scope=f"group:{group_id}",
                    sender_id=sender_id,
                    text=text,
                    now=_now_ts(),
                )
                group_smart_wait = await self._smart_message_debounce_wait_seconds_for_event(
                    event,
                    key=group_buffer_key,
                    text=text,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    private_chat=False,
                )
            group_smart_result = getattr(event, "private_companion_smart_message_debounce_result", None)
            group_smart_decision = str(group_smart_result.get("decision") or "") if isinstance(group_smart_result, dict) else ""
            group_smart_handled = group_smart_decision in {"complete", "incomplete"}
            short_wait = 0.0
            if not high_intensity_merge_active:
                short_wait = self._group_short_wakeup_wait_seconds(event, text, smart_result=group_smart_result)
            if short_wait > 0:
                group_smart_wait = max(group_smart_wait, short_wait)
                group_smart_decision = "incomplete"
                group_smart_handled = True
            if (
                talking_to_bot
                and not high_intensity_merge_active
                and not group_reference_media_with_text
                and group_smart_decision == "incomplete"
                and self._note_semantic_message_buffer(
                    group_buffer_key,
                    text,
                    sender_name=sender_name,
                    wait_seconds=group_smart_wait,
                    smart_debounce={"enabled": group_smart_handled, "decision": group_smart_decision or "fixed"},
                    kind="group_short_wakeup" if short_wait > 0 else "group_text",
                )
            ):
                self._save_data_sync()
                event.stop_event()
                return
            self._update_group_observation(
                group,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                group_id=group_id,
                scene=scene,
                message_id=self._event_message_id(event),
                event=event,
            )
            registration_payload = self._maybe_worldbook_self_register_from_group_message(
                event,
                group_id=group_id,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                group=group,
            )
            share_scheduled = self._maybe_schedule_group_private_share(group_id, group, trigger_sender_id=sender_id)
            self._save_data_sync()
            group_snapshot = deepcopy(group)
            group_snapshot_high_intensity = dict(high_intensity_state)
        await self._dispatch_due_atrelay_tasks(event, group_id, sender_id)
        if isinstance(registration_payload, dict) and registration_payload.get("blocked_reply"):
            await self._reply(event, str(registration_payload.get("blocked_reply") or "这个称呼我不记。"))
            event.stop_event()
            return
        if isinstance(registration_payload, dict) and registration_payload.get("confirm_reply"):
            await self._reply(event, str(registration_payload.get("confirm_reply") or ""))
            if not registration_payload.get("user_id"):
                event.stop_event()
                return
        if registration_payload and registration_payload.get("user_id"):
            asyncio.create_task(self._refresh_worldbook_self_registration_impression(registration_payload))
        if share_scheduled:
            asyncio.create_task(self._kick_proactive_loop_once())
        if not group_snapshot_high_intensity.get("active"):
            asyncio.create_task(self._maybe_refresh_group_episode(group_id, group_snapshot))
            asyncio.create_task(self._maybe_refresh_group_slang_meanings(group_id, group_snapshot))
            await self._maybe_group_interject(event, group_snapshot, text)
        else:
            logger.info(
                "[PrivateCompanion] 群聊高强度收口生效: group=%s recent_wakeups=%s threshold=%s merge_active=%s floor=%s reason=%s merge_scope=%s merge_wait=%ss skip=followup-refresh/interject",
                group_id,
                group_snapshot_high_intensity.get("recent_wakeups"),
                group_snapshot_high_intensity.get("threshold"),
                group_snapshot_high_intensity.get("merge_active"),
                group_snapshot_high_intensity.get("merge_recent_floor"),
                group_snapshot_high_intensity.get("reason"),
                getattr(self, "group_high_intensity_merge_scope", "group"),
                self._group_high_intensity_merge_wait_seconds(),
            )
        original_interject_at = _safe_float(group.get("last_interject_at"), 0) if isinstance(group, dict) else 0
        repeat_state_changed = group_snapshot.get("repeat_follow_state") != (
            group.get("repeat_follow_state") if isinstance(group, dict) else {}
        )
        if _safe_float(group_snapshot.get("last_interject_at"), 0) > original_interject_at or repeat_state_changed:
            async with self._data_lock:
                current = self._get_group(group_id)
                current["last_interject_at"] = group_snapshot.get("last_interject_at", current.get("last_interject_at", 0))
                current["interject_day"] = group_snapshot.get("interject_day", current.get("interject_day", ""))
                current["interject_today"] = group_snapshot.get("interject_today", current.get("interject_today", 0))
                current["last_bot_interjection"] = group_snapshot.get("last_bot_interjection", current.get("last_bot_interjection", {}))
                current["repeat_follow_state"] = group_snapshot.get("repeat_follow_state", current.get("repeat_follow_state", {}))
                self._save_data_sync()
        if talking_to_bot:
            await self._acquire_framework_session_lock_for_event(
                event,
                label="group_event_pipeline",
                private_only=False,
            )

    def _format_timestamp_elapsed(self, timestamp: Any) -> str:
        ts = _safe_float(timestamp, 0)
        if ts <= 0:
            return "从未"
        seconds = max(0, _now_ts() - ts)
        return self._format_elapsed(seconds)

    def _format_elapsed(self, seconds: float) -> str:
        if seconds < 5:
            return "刚刚"
        if seconds < 60:
            return f"{int(seconds)} 秒前"
        if seconds < 3600:
            return f"{int(seconds // 60)} 分钟前"
        if seconds < 86400:
            return f"{int(seconds // 3600)} 小时前"
        return f"{int(seconds // 86400)} 天前"
