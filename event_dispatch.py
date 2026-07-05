# -*- coding: utf-8 -*-
"""
EventDispatchMixin — 从 main.py 重新拆分出的事件分发
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

_PROMPT_MODULE_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "state.session_update": ("模拟状态更新", "增量注入 Bot 自身模拟状态变化，并标明不是用户事实或长期记忆。"),
    "state.lightweight": ("轻量模拟状态", "短句/轻量被动回复复用 Bot 自身模拟状态，只提供身体状态和表达节奏。"),
    "state.full": ("完整模拟状态", "注入 Bot 自身模拟状态，只提供身体状态、情绪和表达节奏，不混入用户事实。"),
    "life.context": ("模拟生活背景", "单独注入 Bot 拟人化日程和生活线索，便于排查日程污染。"),
    "important.dates": ("重要日期", "单独注入近期重要日期，只在用户提到相关计划或纪念时自然承接。"),
    "worldview.adaptation": ("世界观适配", "补充当前人格/世界观的表达边界，避免回复和设定脱节。"),
    "identity.anchor": ("身份锚点", "固定私聊对象身份和称呼，降低昵称变化、群名片或历史记忆导致的认错。"),
    "turn.continuation": ("连续补话", "把用户短时间内连续补充的内容视作同一轮输入，避免逐条误回。"),
    "recall.query": ("历史召回查询", "当用户询问此前聊过什么时，提供近期可自然引用的消息。"),
    "image.direct": ("图片直挂", "说明图片已交给视觉主模型，要求同时理解画面和用户借图表达。"),
    "image.vision": ("图片视觉摘要", "当前主模型不能可靠直接看图时，注入视觉模型摘要和回复目标。"),
    "image.fallback": ("图片兜底", "图片存在但没有可用视觉摘要时，防止模型编造画面内容。"),
    "image.only.vision": ("单图视觉摘要", "用户只发图没有文字时，注入该图摘要并要求自然接住图片表达。"),
    "image.only.fallback": ("单图兜底", "用户只发图但识图失败时，要求不要沉默也不要编造。"),
    "image.reply.vision": ("引用图片摘要", "用户引用/回复某张图时，把被引用图片作为本轮主要依据。"),
    "image.reply.fallback": ("引用图片兜底", "引用图片无法识别时，避免把旧图或历史内容当成当前引用目标。"),
    "creative.hidden": ("创作上下文", "在用户触发创作相关话题时，补充必要的创作状态和边界。"),
    "bookshelf.secret": ("书柜隐藏线索", "在合适场景提供书柜相关的隐藏上下文，不主动暴露机制。"),
    "bookshelf.reading": ("阅读上下文", "补充近期阅读/书柜内容对本轮回复的影响。"),
    "private_reading.preference": ("阅读偏好", "让私聊回复更贴近用户已形成的阅读偏好。"),
    "news.recent": ("近期新闻", "用户聊到新闻/时事时，提供近期阅读过的新闻上下文。"),
    "web_exploration.recent": ("主动搜索近况", "用户询问最近搜索/网页探索时，提供真实搜索词、动机、笔记和来源。"),
    "skill.growth": ("能力成长", "注入角色近期能力变化，帮助回复体现可成长性。"),
    "skill.growth.match": ("本轮相关技能", "用户提到已追踪技能时，只注入命中的能力边界。"),
    "companion.planner": ("陪伴规划", "整合关系画像、互动节奏和回复策略，控制陪伴感与边界。"),
    "proactive.reply_context": ("悬着话头", "只用于明确被挂起的半句主动，不再按旧主动消息纠偏普通被动回复。"),
    "detail.injection": ("日程细节", "补充当前生活片段和可用碎片，让回复有具体落点。"),
    "timer.scheduling": ("主动预约", "允许模型在合适时隐藏预约下一次主动开口。"),
    "environment.lightweight": ("轻量环境", "短句被动回复使用的时间和平台边界，避免完全丢失当前语境。"),
    "environment.perception": ("环境感知", "完整被动回复使用的时间、日期、平台和模型环境边界。"),
    "environment.request": ("请求级环境感知", "状态总注入关闭时仍可单独补充的时间、日期、平台和消息媒介边界。"),
    "tts.rule": ("TTS 基础规则", "告诉模型本轮是否可以使用插件私有语音标签、目标语种、双语展示和格式示例。"),
    "tts.frequency": ("TTS 频率控制", "自动语音概率未命中时的软约束；没有用户明确请求时要求本轮必须纯文字，不主动使用任何语音标签。"),
    "tts.block": ("TTS 强约束禁用", "强约束模式下概率未命中或会话冷却时的反向规则，要求本轮禁止任何语音内容。"),
    "tts.force": ("TTS 主用户倾向", "主用户或明确 @ 主用户时的语音倾向提示，仍由模型按语境判断。"),
    "tts.user_request": ("用户语音请求", "用户明确想听语音/声音时的顺应规则，不受自动语音概率限制。"),
    "capability.boundary": ("能力边界", "群聊中防止模型承诺现实操作、网络操作或无法执行的代办。"),
    "tools.atrelay": ("跨群转述工具", "用户可能要转告、私聊、@ 群友时注入的工具使用说明。"),
    "tools.qzone": ("QQ 空间工具", "用户提到空间、说说、动态等场景时注入的工具使用说明。"),
    "group.persona_denoise": ("群聊人格降噪", "群聊回复时降低私聊腔、过度亲密和状态外露。"),
    "group.context": ("群聊上下文", "群聊回复时补充群氛围、当前发言者、最近话题和连续补充内容。"),
    "identity.non_target": ("非目标私聊防串", "私聊对象不是主陪伴用户时防止套用专属关系和记忆。"),
    "forward.message": ("合并转发上下文", "合并转发、聊天记录或引用卡片进入回复时注入的阅读内容或转述。"),
    "reply.chain": ("引用链上下文", "用户引用的消息本身继续引用更早消息时，按层级提供原始被引用内容。"),
}

_PROMPT_MODULE_PREFIX_DESCRIPTIONS: tuple[tuple[str, tuple[str, str]], ...] = (
    ("state.", ("状态片段", "提供当前拟人身体状态、情绪底色和表达节奏。")),
    ("image.", ("图片片段", "帮助模型理解当前图片、引用图片或识图失败时的回复边界。")),
    ("bookshelf.", ("书柜片段", "提供书柜/阅读相关上下文。")),
    ("private_reading.", ("阅读偏好片段", "提供私聊阅读偏好和阅读状态。")),
    ("proactive.", ("主动相关片段", "处理主动消息节奏、边界和明确挂起的话头。")),
    ("timer.", ("预约片段", "处理模型可见的主动预约规则。")),
    ("creative.", ("创作片段", "提供创作状态或创作相关边界。")),
    ("environment.", ("环境片段", "提供当前时间、日期、平台、模型或消息媒介边界。")),
)

_PROMPT_SECTION_DESCRIPTIONS: dict[str, str] = {
    "内容选择菜单": "限制本次主动消息可选内容类型，避免把多个动机拼成一条。",
    "主动能力检索": "提示模型可使用的主动能力和素材来源。",
    "禁止事项": "列出主动消息不能触碰的回复式承接、幻觉和污染项。",
    "最近主动行为闭环": "提供最近主动行为后的反馈，用于降低打扰和重复。",
    "主动意图具体化": "要求主动消息围绕一个具体由头，减少泛泛关心。",
    "语言风格疲劳": "提示模型避开最近重复的开头、口癖和句式。",
    "主动承接边界": "说明本轮是独立主动还是续接来源，防止把历史写成当前对话。",
    "媒体真实性边界": "当本轮不会发图/媒体时，禁止正文假装发了照片或图片。",
    "新闻阅读上下文": "提供新闻阅读或分享相关背景。",
    "人格": "当前会话人格和表达站位。",
    "对象": "收信人身份、称呼或关系背景。",
    "收信人": "主动消息目标用户和关系上下文。",
    "主动原因": "本次主动触发原因和动机。",
    "当前状态": "Bot 自身模拟状态和情绪底色。",
    "当前拟人状态": "Bot 自身身体状态和表达节奏。",
    "当前日程背景": "当前日程素材和时段背景。",
    "当前会话 TTS 规则": "当前会话语音/TTS 的格式和使用规则。",
    "必须满足的格式重点": "语音或特殊输出格式的硬性要求。",
    "当前版本": "当前输出格式或功能版本说明。",
    "这次想分享的画面钩子": "图片/画面类主动消息的素材落点。",
    "生图风格": "图片生成或图片分享相关风格要求。",
}

class EventDispatchMixin:
    """事件分发"""

    def _event_raw_payload(self, event: AstrMessageEvent) -> dict[str, Any]:
        message_obj = getattr(event, "message_obj", None)
        raw = getattr(message_obj, "raw_message", None) if message_obj is not None else None
        return raw if isinstance(raw, dict) else {}

    def _event_message_id(self, event: AstrMessageEvent) -> str:
        message_obj = getattr(event, "message_obj", None)
        for attr in ("message_id", "id", "seq", "message_seq", "real_id"):
            value = getattr(message_obj, attr, None) if message_obj is not None else None
            if value is not None and str(value).strip():
                return _single_line(value, 120)
        raw = self._event_raw_payload(event)
        for key in ("message_id", "id", "msg_id", "seq", "message_seq", "real_id"):
            value = raw.get(key)
            if value is not None and str(value).strip():
                return _single_line(value, 120)
        return ""

    def _event_message_id_candidates(self, event: AstrMessageEvent) -> list[str]:
        ids: list[str] = []
        raw = self._event_raw_payload(event)
        for key in ("message_id", "msg_id", "id", "seq", "message_seq", "real_id"):
            value = raw.get(key)
            if value is not None and str(value).strip():
                ids.append(_single_line(value, 120))
        message_obj = getattr(event, "message_obj", None)
        for attr in ("message_id", "id", "seq", "message_seq", "real_id"):
            value = getattr(message_obj, attr, None) if message_obj is not None else None
            if value is not None and str(value).strip():
                ids.append(_single_line(value, 120))
        seen: set[str] = set()
        unique: list[str] = []
        for message_id in ids:
            if not message_id or message_id in seen:
                continue
            seen.add(message_id)
            unique.append(message_id)
        return unique

    def _event_is_platform_message_event(self, event: AstrMessageEvent) -> bool:
        """Return True only for events whose current id can be queried by get_msg."""
        raw = self._event_raw_payload(event)
        post_type = str(raw.get("post_type") or "").strip().lower()
        if post_type:
            return post_type in {"message", "message_sent"}
        if raw:
            message_type = str(raw.get("message_type") or "").strip().lower()
            if message_type in {"private", "group"}:
                return True
            if any(key in raw for key in ("raw_message", "message", "message_id", "msg_id")):
                return True
            return False
        components = self._event_components(event)
        if components:
            labels: list[str] = []
            for item in components:
                label = item.__class__.__name__.lower()
                if isinstance(item, dict):
                    label = str(item.get("type") or item.get("post_type") or "").strip().lower()
                labels.append(label)
            if labels and all(("poke" in label or "notice" in label) for label in labels):
                return False
            return True
        return bool(str(getattr(event, "message_str", "") or "").strip())

    def _event_scope_key(self, event: AstrMessageEvent) -> str:
        raw = self._event_raw_payload(event)
        group_id = _single_line(raw.get("group_id"), 80)
        user_id = _single_line(raw.get("user_id"), 80)
        if group_id:
            return f"group:{group_id}"
        if user_id:
            return f"private:{user_id}"
        group_id = self._extract_group_id_from_event(event)
        if group_id:
            return f"group:{group_id}"
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                sender_id = _single_line(event.get_sender_id(), 80)
                if sender_id:
                    return f"private:{sender_id}"
        except Exception:
            pass
        return _single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown"

    def _event_sender_id(self, event: AstrMessageEvent) -> str:
        raw = self._event_raw_payload(event)
        sender_id = _single_line(raw.get("user_id"), 80)
        if sender_id:
            return sender_id
        try:
            return _single_line(event.get_sender_id(), 80)
        except Exception:
            return ""

    def _event_existing_reply_result_preview(self, event: AstrMessageEvent) -> str:
        getter = getattr(event, "get_result", None)
        if not callable(getter):
            return ""
        try:
            result = getter()
        except Exception:
            return ""
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return ""
        parts: list[str] = []
        for comp in chain[:4]:
            text = _single_line(
                getattr(comp, "text", "")
                or getattr(comp, "message", "")
                or getattr(comp, "content", ""),
                80,
            )
            if text:
                parts.append(text)
                continue
            if comp.__class__.__name__.lower() != "plain":
                parts.append(comp.__class__.__name__)
        return " / ".join(part for part in parts if part)

    def _event_has_existing_reply_result(self, event: AstrMessageEvent) -> bool:
        return bool(self._event_existing_reply_result_preview(event))

    def _event_self_id(self, event: AstrMessageEvent) -> str:
        raw = self._event_raw_payload(event)
        self_id = _single_line(raw.get("self_id"), 80)
        if self_id:
            return self_id
        try:
            return _single_line(event.get_self_id(), 80)
        except Exception:
            message_obj = getattr(event, "message_obj", None)
            return _single_line(getattr(message_obj, "self_id", ""), 80)

    def _note_inbound_activity_for_scope(self, event: AstrMessageEvent) -> None:
        raw = self._event_raw_payload(event)
        if raw.get("post_type") == "notice":
            return
        scope = self._event_scope_key(event)
        if not scope or scope == "unknown":
            return
        sender_id = self._event_sender_id(event)
        self_id = self._event_self_id(event)
        noted_at = _now_ts()
        try:
            setattr(event, "_private_companion_inbound_ts", noted_at)
        except Exception:
            pass
        activity = getattr(self, "_recent_inbound_activity_by_scope", None)
        if not isinstance(activity, dict):
            activity = {}
            self._recent_inbound_activity_by_scope = activity
        activity[scope] = {
            "ts": noted_at,
            "message_id": self._event_message_id(event),
            "sender_id": sender_id,
            "from_self": bool(sender_id and self_id and sender_id == self_id),
        }
        if len(activity) > 500:
            stale = sorted(
                activity.items(),
                key=lambda kv: _safe_float(kv[1].get("ts") if isinstance(kv[1], dict) else 0, 0),
            )
            for key, _ in stale[: len(activity) - 500]:
                activity.pop(key, None)

    def _scope_has_new_inbound_activity(self, scope: str, since_ts: float, *, ignore_self: bool = True) -> bool:
        activity = getattr(self, "_recent_inbound_activity_by_scope", None)
        if not isinstance(activity, dict):
            return False
        item = activity.get(_single_line(scope, 160))
        if not isinstance(item, dict):
            return False
        if _safe_float(item.get("ts"), 0.0, 0.0) <= since_ts:
            return False
        if ignore_self and bool(item.get("from_self")):
            return False
        return True

    def _event_inbound_activity_ts(self, event: AstrMessageEvent) -> float:
        ts = _safe_float(getattr(event, "_private_companion_inbound_ts", 0), 0.0, 0.0)
        if ts > 0:
            return ts
        raw = self._event_raw_payload(event)
        raw_ts = _safe_float(raw.get("time") or raw.get("timestamp"), 0.0, 0.0)
        if raw_ts > 0:
            return raw_ts
        return _now_ts()

    def _prompt_module_info(self, key: str, fallback_title: str = "") -> tuple[str, str]:
        normalized_key = _single_line(key, 80)
        if normalized_key in _PROMPT_MODULE_DESCRIPTIONS:
            return _PROMPT_MODULE_DESCRIPTIONS[normalized_key]
        for prefix, info in _PROMPT_MODULE_PREFIX_DESCRIPTIONS:
            if normalized_key.startswith(prefix):
                return info
        title = _single_line(fallback_title, 60)
        if title in _PROMPT_SECTION_DESCRIPTIONS:
            return title, _PROMPT_SECTION_DESCRIPTIONS[title]
        if normalized_key.startswith("section."):
            return title or "提示词段落", _PROMPT_SECTION_DESCRIPTIONS.get(title, "按标题从完整 prompt 中拆出的段落，用于定位主动主链提示词来源。")
        return title or normalized_key or "提示词片段", "提示词组装中的一个片段；用于排查它对本轮模型输入的影响。"

    def _split_prompt_modules_by_heading(self, content: str) -> list[dict[str, Any]]:
        text = str(content or "").strip()
        if not text:
            return []
        matches = list(re.finditer(r"(?m)^【([^】\n]{1,40})】\s*$", text))
        modules: list[dict[str, Any]] = []
        if not matches:
            title, description = self._prompt_module_info("prompt.full", "完整提示词")
            return [
                {
                    "key": "prompt.full",
                    "source": "merged_prompt",
                    "priority": 100,
                    "title": title,
                    "description": description,
                    "content": text,
                    "chars": len(text),
                }
            ]
        if matches[0].start() > 0:
            intro = text[: matches[0].start()].strip()
            if intro:
                title, description = self._prompt_module_info("section.0.intro", "开场说明")
                modules.append(
                    {
                        "key": "section.0.intro",
                        "source": "prompt_heading_split",
                        "priority": 0,
                        "title": title,
                        "description": description,
                        "content": intro,
                        "chars": len(intro),
                    }
                )
        for index, match in enumerate(matches):
            heading = _single_line(match.group(1), 60) or f"段落 {index + 1}"
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            part = text[start:end].strip()
            title, description = self._prompt_module_info(f"section.{index + 1}.{heading}", heading)
            modules.append(
                {
                    "key": f"section.{index + 1}.{heading}",
                    "source": "prompt_heading_split",
                    "priority": index + 1,
                    "title": title,
                    "description": description,
                    "content": part,
                    "chars": len(part),
                }
            )
        return modules

    def _normalize_prompt_injection_modules(self, content: str, modules: Any = None) -> list[dict[str, Any]]:
        raw_modules = modules if isinstance(modules, list) else self._split_prompt_modules_by_heading(content)
        result: list[dict[str, Any]] = []
        max_modules = 28
        max_content = 6000
        for index, raw in enumerate(raw_modules[:max_modules]):
            if not isinstance(raw, dict):
                continue
            module_content = str(raw.get("content") or "").strip()
            if not module_content:
                continue
            key = _single_line(raw.get("key"), 100) or f"module.{index + 1}"
            source = _single_line(raw.get("source"), 80)
            raw_title = _single_line(raw.get("title"), 80)
            title, description = self._prompt_module_info(key, raw_title)
            if raw.get("description"):
                description = _single_line(raw.get("description"), 220) or description
            chars = _safe_int(raw.get("chars"), len(module_content), 0)
            truncated = len(module_content) > max_content
            if truncated:
                module_content = module_content[:max_content] + "\n...[模块内容已截断]"
            result.append(
                {
                    "key": key,
                    "source": source,
                    "priority": _safe_int(raw.get("priority"), index, 0),
                    "title": title,
                    "description": description,
                    "chars": chars,
                    "truncated": truncated,
                    "preview": _single_line(module_content, 180),
                    "content": module_content,
                }
            )
        return result

    def _legacy_proactive_prompt_trace_text(self, item: Any) -> str:
        if not isinstance(item, dict):
            return ""
        parts = [
            str(item.get("content") or ""),
            str(item.get("preview") or ""),
            str(item.get("title") or ""),
        ]
        modules = item.get("modules")
        if isinstance(modules, list):
            for module in modules:
                if isinstance(module, dict):
                    parts.extend(
                        [
                            str(module.get("key") or ""),
                            str(module.get("title") or ""),
                            str(module.get("content") or ""),
                            str(module.get("preview") or ""),
                        ]
                    )
        return "\n".join(part for part in parts if part)

    def _is_legacy_proactive_prompt_trace(self, item: Any) -> bool:
        text = self._legacy_proactive_prompt_trace_text(item)
        if not text:
            return False
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        if callable(meta_leak_checker) and meta_leak_checker(text):
            return True
        markers = (
            "【怎么写这条消息】",
            "【禁止事项】",
            "【状态表现层】",
            "【主动意图具体化】",
            "【语言风格疲劳】",
            "你正在为 Private Companion 生成一条主动私聊消息。下面这段规则是稳定规则前缀",
            "日程主语归属必须稳定",
        )
        return any(marker in text for marker in markers)

    def _cleanup_legacy_proactive_prompt_traces(self) -> bool:
        root = self.data.get("recent_prompt_injections") if isinstance(getattr(self, "data", None), dict) else None
        if not isinstance(root, dict):
            return False
        changed = False
        for key in ("proactive",):
            items = root.get(key)
            if not isinstance(items, list):
                continue
            kept = [item for item in items if not self._is_legacy_proactive_prompt_trace(item)]
            if len(kept) != len(items):
                root[key] = kept[:5]
                changed = True
        return changed

    def _prompt_injection_trace_id_for_event(self, event: AstrMessageEvent) -> str:
        cached = _single_line(getattr(event, "private_companion_prompt_trace_id", ""), 80)
        if cached:
            return cached
        session = _single_line(getattr(event, "unified_msg_origin", ""), 160) or self._event_scope_key(event)
        sender_id = self._event_sender_id(event)
        message_id = self._event_message_id(event)
        inbound_ts = self._event_inbound_activity_ts(event)
        text = self._event_text_for_recall_cache(event, limit=280)
        seed = "|".join(
            part
            for part in (
                session,
                sender_id,
                message_id,
                f"{inbound_ts:.3f}" if inbound_ts > 0 else "",
                text,
            )
            if part
        )
        trace_id = f"evt-{hashlib.sha1(seed.encode('utf-8', errors='ignore')).hexdigest()[:16]}" if seed else f"evt-{uuid.uuid4().hex[:16]}"
        try:
            setattr(event, "private_companion_prompt_trace_id", trace_id)
        except Exception:
            pass
        return trace_id

    def _prompt_injection_message_preview_for_event(self, event: AstrMessageEvent) -> str:
        cached = _single_line(getattr(event, "private_companion_prompt_trace_preview", ""), 220)
        if cached:
            return cached
        text = self._event_text_for_recall_cache(event, limit=280)
        if not text:
            text = _single_line(getattr(event, "message_str", ""), 280)
        if not text:
            text = self._event_existing_reply_result_preview(event)
        preview = _single_line(text, 220)
        try:
            setattr(event, "private_companion_prompt_trace_preview", preview)
        except Exception:
            pass
        return preview

    def _prompt_injection_sender_label_for_event(self, event: AstrMessageEvent) -> str:
        sender_id = _single_line(self._event_sender_id(event), 80)
        display_name = _single_line(self._sender_display_name(event), 40)
        if display_name and sender_id and display_name != sender_id:
            return f"{display_name}/{sender_id}"
        return display_name or sender_id

    @staticmethod
    def _prompt_injection_preview_is_internal_prompt(text: str) -> bool:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        internal_markers = (
            "【语音消息规则】",
            "<pc_tts>",
            "</pc_tts>",
            "语音消息规则",
            "提示词片段",
            "请求级环境感知注入",
            "被动回复注入",
            "当前语音正文目标语种",
            "自然聊天时用中文文字推进对话",
            "不要写“中文含义”",
        )
        if any(marker in cleaned for marker in internal_markers):
            return True
        if cleaned.startswith("【") and "规则" in cleaned[:40]:
            return True
        return False

    def _safe_prompt_injection_message_preview(self, value: Any, *, limit: int = 220) -> str:
        preview = _single_line(value, limit)
        if self._prompt_injection_preview_is_internal_prompt(preview):
            return ""
        return preview

    def _upsert_recent_prompt_injection_event(
        self,
        *,
        trace_id: str,
        item: dict[str, Any],
        message_preview: str = "",
        sender_label: str = "",
    ) -> None:
        trace = _single_line(trace_id, 80) or f"trace-{uuid.uuid4().hex[:16]}"
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        preview = (
            self._safe_prompt_injection_message_preview(message_preview)
            or self._safe_prompt_injection_message_preview(metadata.get("触发消息"))
        )
        sender = _single_line(sender_label, 80)
        events = self.data.setdefault("recent_prompt_injection_events", [])
        if not isinstance(events, list):
            events = []
            self.data["recent_prompt_injection_events"] = events
        target: dict[str, Any] | None = None
        for entry in events:
            if isinstance(entry, dict) and _single_line(entry.get("trace_id"), 80) == trace:
                target = entry
                break
        ts = _safe_float(item.get("ts"), _now_ts(), 0.0)
        copied_item = deepcopy(item)
        if target is None:
            target = {
                "trace_id": trace,
                "session": _single_line(item.get("session"), 160) or "unknown",
                "sender_label": sender,
                "message_preview": preview,
                "first_ts": ts,
                "last_ts": ts,
                "items": [],
            }
            events.append(target)
        else:
            if not _single_line(target.get("session"), 160):
                target["session"] = _single_line(item.get("session"), 160) or "unknown"
            if preview:
                target["message_preview"] = preview
            if sender:
                target["sender_label"] = sender
            target["first_ts"] = min(_safe_float(target.get("first_ts"), ts, 0.0), ts)
            target["last_ts"] = max(_safe_float(target.get("last_ts"), ts, 0.0), ts)
        event_items = target.get("items")
        if not isinstance(event_items, list):
            event_items = []
            target["items"] = event_items
        copied_item["trace_seq"] = len(event_items)
        event_items.append(copied_item)
        del event_items[32:]
        events.sort(key=lambda entry: _safe_float(entry.get("last_ts"), 0.0, 0.0), reverse=True)
        del events[10:]

    async def _record_prompt_injection_snapshot(
        self,
        *,
        kind: str,
        session: str,
        title: str,
        text: str,
        mode: str = "",
        metadata: dict[str, Any] | None = None,
        modules: list[dict[str, Any]] | None = None,
        trace_id: str = "",
        message_preview: str = "",
        sender_label: str = "",
    ) -> None:
        content = str(text or "").strip()
        kind = _single_line(kind, 20) or "unknown"
        if kind not in {"passive", "proactive", "request"} or not content:
            return
        now = _now_ts()
        max_content = 12000
        truncated = len(content) > max_content
        if truncated:
            content = content[:max_content] + "\n...[已截断]"
        item = {
            "ts": now,
            "time": self._format_timestamp_elapsed(now) if hasattr(self, "_format_timestamp_elapsed") else "",
            "kind": kind,
            "session": _single_line(session, 160) or "unknown",
            "title": _single_line(title, 80),
            "mode": _single_line(mode, 40),
            "chars": len(str(text or "")),
            "truncated": truncated,
            "preview": _single_line(content, 220),
            "content": content,
            "modules": self._normalize_prompt_injection_modules(str(text or ""), modules),
            "metadata": {
                _single_line(key, 40): _single_line(value, 220)
                for key, value in (metadata or {}).items()
                if _single_line(key, 40) and _single_line(value, 220)
            },
        }
        if kind == "proactive" and self._is_legacy_proactive_prompt_trace(item):
            return
        async with self._data_lock:
            root = self.data.setdefault("recent_prompt_injections", {})
            if not isinstance(root, dict):
                root = {}
                self.data["recent_prompt_injections"] = root
            items = root.setdefault(kind, [])
            if not isinstance(items, list):
                items = []
                root[kind] = items
            items.insert(0, item)
            del items[5:]
            if kind == "request" and any(
                isinstance(module, dict) and _single_line(module.get("key"), 100).startswith("tts.")
                for module in item.get("modules", [])
            ):
                tts_items = root.setdefault("tts", [])
                if not isinstance(tts_items, list):
                    tts_items = []
                    root["tts"] = tts_items
                tts_items.insert(0, item)
                del tts_items[8:]
            self._upsert_recent_prompt_injection_event(
                trace_id=trace_id,
                item=item,
                message_preview=message_preview,
                sender_label=sender_label,
            )
        try:
            self._schedule_data_save(delay=2.0)
        except Exception:
            pass

    def _segmented_remainder_lock(self, scope: str) -> asyncio.Lock:
        key = _single_line(scope, 160) or "unknown"
        locks = getattr(self, "_segmented_reply_remainder_locks", None)
        if not isinstance(locks, dict):
            locks = {}
            self._segmented_reply_remainder_locks = locks
        lock = locks.get(key)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            locks[key] = lock
        if len(locks) > 500:
            for stale_key, stale_lock in list(locks.items()):
                if stale_key != key and isinstance(stale_lock, asyncio.Lock) and not stale_lock.locked():
                    locks.pop(stale_key, None)
                    if len(locks) <= 500:
                        break
        return lock

    def _recall_component_text(self, component: Any) -> str:
        for attr in ("text", "content", "message"):
            value = getattr(component, attr, None)
            if isinstance(value, str):
                return value
        if isinstance(component, dict):
            data = component.get("data") if isinstance(component.get("data"), dict) else component
            text = data.get("text") or data.get("content") or data.get("message")
            if text is not None:
                return str(text)
        return ""

    def _event_text_for_recall_cache(self, event: AstrMessageEvent, *, limit: int = 500) -> str:
        parts: list[str] = []
        try:
            chain = list(event.get_messages() or [])
        except Exception:
            chain = []
        for comp in chain:
            text = self._recall_component_text(comp)
            if text:
                parts.append(text)
                continue
            name = comp.__class__.__name__.lower()
            if "image" in name:
                parts.append("[图片]")
            elif "record" in name or "voice" in name:
                parts.append("[语音]")
            elif "video" in name:
                parts.append("[视频]")
            elif "at" == name or name.endswith(".at"):
                parts.append("[@]")
            elif "reply" in name:
                parts.append("[引用]")
        text = " ".join(item.strip() for item in parts if str(item or "").strip()).strip()
        if not text:
            raw = self._event_raw_payload(event)
            raw_msg = raw.get("raw_message") or raw.get("message")
            if isinstance(raw_msg, str):
                text = raw_msg
            else:
                text = str(getattr(event, "message_str", "") or "")
        return _single_line(text, limit)

    async def _event_image_sources_for_recall_cache(self, event: AstrMessageEvent, *, limit: int = 5) -> list[dict[str, str]]:
        scope = re.sub(r"[^0-9A-Za-z_.-]+", "_", self._event_scope_key(event) or "unknown")
        target_dir = Path(self.data_dir) / "recall_message_images" / scope
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return []
        raw_sources: list[str] = []

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if text and text not in raw_sources:
                raw_sources.append(text)

        async def resolve_component_source(comp: Any) -> str:
            source = ""
            extractor = getattr(self, "_image_component_source", None)
            if callable(extractor):
                try:
                    source = _single_line(extractor(comp), 1000)
                except Exception:
                    source = ""
            if source:
                return source
            converter = getattr(comp, "convert_to_file_path", None)
            if callable(converter):
                try:
                    maybe = converter()
                    return str(await maybe if hasattr(maybe, "__await__") else maybe or "").strip()
                except Exception as exc:
                    logger.debug("[PrivateCompanion] 撤回图片组件转换失败: %s", exc)
            return ""

        for comp in self._event_components(event):
            class_name = comp.__class__.__name__.lower()
            if isinstance(comp, dict):
                class_name = str(comp.get("type") or "").lower()
            if class_name != "image":
                continue
            add(await resolve_component_source(comp))

        message_obj = getattr(event, "message_obj", None)
        extractor = getattr(self, "_extract_image_sources_from_message_obj", None)
        if callable(extractor):
            for source in extractor(message_obj):
                add(source)
        raw_extractor = getattr(self, "_raw_private_image_sources", None)
        if callable(raw_extractor):
            for source in raw_extractor(event):
                add(source)

        persisted: list[dict[str, str]] = []
        now_ms = int(_now_ts() * 1000)

        def add_persisted(source: str, tier: str) -> None:
            text = str(source or "").strip()
            tier = _single_line(tier, 40)
            if not text and tier != "placeholder":
                return
            if any(item.get("source") == text and item.get("tier") == tier for item in persisted):
                return
            persisted.append({"source": text, "tier": tier})

        def persist_data_url(source: str, stem: str) -> str:
            text = str(source or "").strip()
            try:
                if text.startswith("base64://"):
                    raw = base64.b64decode(text[len("base64://"):], validate=False)
                    suffix = ".jpg"
                elif text.startswith("data:") and "," in text:
                    meta, payload = text.split(",", 1)
                    if ";base64" not in meta.lower():
                        return ""
                    raw = base64.b64decode(payload, validate=False)
                    lowered = meta.lower()
                    suffix = ".png" if "png" in lowered else ".webp" if "webp" in lowered else ".gif" if "gif" in lowered else ".jpg"
                else:
                    return ""
                if not raw:
                    return ""
                target = target_dir / f"{stem}{suffix}"
                target.write_bytes(raw)
                return str(target)
            except Exception as exc:
                logger.debug("[PrivateCompanion] 撤回图片 data url 暂存失败: %s", exc)
                return ""

        for index, source in enumerate(raw_sources[: max(1, limit)], 1):
            text = str(source or "").strip()
            if not text:
                continue
            data_path = persist_data_url(text, f"{now_ms}_{index}")
            if data_path:
                add_persisted(data_path, "local")
                continue
            if re.match(r"^https?://", text, flags=re.I):
                downloader = getattr(self, "_persist_private_remote_image_source", None)
                if callable(downloader):
                    try:
                        downloaded = await asyncio.wait_for(downloader(text, target_dir, f"{now_ms}_{index}"), timeout=8.0)
                    except Exception as exc:
                        logger.debug("[PrivateCompanion] 撤回图片远程暂存失败: %s", exc)
                        downloaded = ""
                    if downloaded:
                        add_persisted(downloaded, "local")
                        continue
                add_persisted(text, "url")
                continue
            local_text = text[len("file://"):] if text.startswith("file://") else text
            try:
                source_path = Path(local_text)
                exists = source_path.exists() and source_path.is_file()
            except (OSError, ValueError):
                exists = False
            if exists:
                suffix = source_path.suffix.lower() if source_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"
                target = target_dir / f"{now_ms}_{index}{suffix}"
                try:
                    shutil.copy2(source_path, target)
                    add_persisted(str(target), "local")
                    continue
                except Exception as exc:
                    logger.debug("[PrivateCompanion] 撤回图片本地暂存失败: %s", exc)
            if not re.match(r"^(?:[A-Za-z]:[\\/]|/|\\\\|file://)", text):
                # OneBot/NapCat may expose only a platform-side image file id.
                # Keep it as a best-effort sendable Image(file=...) reference.
                add_persisted(text, "platform_file")
        return persisted[: max(1, limit)]

    def _recall_image_items_from_snapshot(self, row: dict[str, Any]) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        raw_items = row.get("image_items") if isinstance(row.get("image_items"), list) else []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "").strip()
            tier = _single_line(item.get("tier"), 40) or ("local" if source else "placeholder")
            items.append({"source": source, "tier": tier})
        if items:
            return items
        for source in row.get("images") if isinstance(row.get("images"), list) else []:
            text = str(source or "").strip()
            if not text:
                continue
            tier = "url" if re.match(r"^https?://", text, flags=re.I) else "local"
            if not re.match(r"^https?://", text, flags=re.I) and not text.startswith(("data:", "base64://", "file://")):
                try:
                    path = Path(text)
                    if not (path.exists() and path.is_file()):
                        tier = "platform_file"
                except (OSError, ValueError):
                    tier = "platform_file"
            items.append({"source": text, "tier": tier})
        return items

    def _recall_image_status_summary(self, row: dict[str, Any]) -> str:
        items = self._recall_image_items_from_snapshot(row)
        if not items:
            if "[图片]" in str(row.get("text") or ""):
                return "只有图片占位"
            return ""
        counts = {"local": 0, "platform_file": 0, "url": 0, "placeholder": 0}
        for item in items:
            tier = item.get("tier") or "placeholder"
            counts[tier if tier in counts else "placeholder"] += 1
        parts: list[str] = []
        if counts["local"]:
            parts.append(f"原图已缓存 {counts['local']} 张")
        if counts["platform_file"]:
            parts.append(f"仅有平台 file id {counts['platform_file']} 张")
        if counts["url"]:
            parts.append(f"仅有 URL {counts['url']} 张")
        if counts["placeholder"]:
            parts.append(f"只有图片占位 {counts['placeholder']} 张")
        return "；".join(parts)

    def _cleanup_recall_message_cache(self) -> None:
        cache = getattr(self, "_recall_message_cache", None)
        if not isinstance(cache, dict):
            self._recall_message_cache = {}
            self._cleanup_recall_message_image_cache()
            return
        now = _now_ts()
        ttl = max(60.0, _safe_float(getattr(self, "recall_message_cache_ttl_seconds", 600), 600))
        stale = [key for key, item in cache.items() if now - _safe_float(item.get("ts") if isinstance(item, dict) else 0, 0) > ttl]
        for key in stale:
            cache.pop(key, None)
        max_items = max(0, _safe_int(getattr(self, "recall_message_cache_max_items", 300), 300, 0))
        if max_items and len(cache) > max_items:
            ordered = sorted(cache.items(), key=lambda kv: _safe_float(kv[1].get("ts") if isinstance(kv[1], dict) else 0, 0))
            for key, _ in ordered[: len(cache) - max_items]:
                cache.pop(key, None)
        self._cleanup_recall_message_image_cache()

    def _cleanup_recall_message_image_cache(self, *, force: bool = False) -> bool:
        root = Path(getattr(self, "data_dir", "") or "") / "recall_message_images"
        try:
            root_resolved = root.resolve()
            data_resolved = Path(getattr(self, "data_dir", "") or "").resolve()
        except Exception:
            return False
        if not root_resolved.exists() or not root_resolved.is_dir():
            return False
        try:
            root_resolved.relative_to(data_resolved)
        except ValueError:
            return False
        now = _now_ts()
        if not force:
            last_cleanup = _safe_float(getattr(self, "_last_recall_image_cache_cleanup_ts", 0), 0)
            if now - last_cleanup < 300:
                return False
        self._last_recall_image_cache_cleanup_ts = now
        ttl = max(60.0, _safe_float(getattr(self, "recall_message_cache_ttl_seconds", 600), 600))
        max_mb = max(0.0, _safe_float(getattr(self, "recall_message_image_cache_max_mb", 256.0), 256.0, 0.0))
        max_bytes = int(max_mb * 1024 * 1024) if max_mb > 0 else 0
        files: list[tuple[float, int, Path]] = []
        removed_count = 0
        removed_bytes = 0

        try:
            iterator = list(root_resolved.rglob("*"))
        except Exception as exc:
            logger.debug("[PrivateCompanion] 撤回图片缓存扫描失败: %s", exc)
            return False

        for path in iterator:
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
                mtime = float(stat.st_mtime or 0)
                size = int(stat.st_size or 0)
                if now - mtime > ttl:
                    try:
                        path.unlink()
                        removed_count += 1
                        removed_bytes += size
                    except Exception as exc:
                        logger.debug("[PrivateCompanion] 撤回图片过期缓存删除失败: path=%s error=%s", path, exc)
                    continue
                files.append((mtime, size, path))
            except Exception as exc:
                logger.debug("[PrivateCompanion] 撤回图片缓存条目读取失败: path=%s error=%s", path, exc)

        total_bytes = sum(size for _, size, _ in files)
        if max_bytes and total_bytes > max_bytes:
            for _, size, path in sorted(files, key=lambda item: item[0]):
                if total_bytes <= max_bytes:
                    break
                try:
                    path.unlink()
                    total_bytes -= size
                    removed_count += 1
                    removed_bytes += size
                except Exception as exc:
                    logger.debug("[PrivateCompanion] 撤回图片容量缓存删除失败: path=%s error=%s", path, exc)

        for directory in sorted((p for p in iterator if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
            except Exception as exc:
                logger.debug("[PrivateCompanion] 撤回图片空目录清理失败: path=%s error=%s", directory, exc)

        if removed_count:
            logger.info(
                "[PrivateCompanion] 已清理撤回图片缓存: files=%s size=%.1fMB ttl=%.0fs max=%.1fMB",
                removed_count,
                removed_bytes / 1024 / 1024,
                ttl,
                max_mb,
            )
            return True
        return False

    async def _cache_message_for_recall(self, event: AstrMessageEvent) -> None:
        if not getattr(self, "enable_recall_enhancement", True):
            return
        if not getattr(self, "enable_recall_message_cache", True):
            return
        if not self._event_is_platform_message_event(event):
            return
        raw = self._event_raw_payload(event)
        message_ids = self._event_message_id_candidates(event)
        if not message_ids:
            return
        text = self._event_text_for_recall_cache(event, limit=max(80, _safe_int(getattr(self, "recall_message_cache_text_chars", 500), 500, 80)))
        if not text:
            return
        raw_message = str(raw.get("raw_message") or raw.get("message") or "")
        reply_message_ids = self._event_reply_message_ids(event)
        has_image = "[图片]" in text or "[CQ:image" in raw_message
        if not has_image:
            for comp in self._event_components(event):
                class_name = comp.__class__.__name__.lower()
                if isinstance(comp, dict):
                    class_name = str(comp.get("type") or "").lower()
                if class_name == "image":
                    has_image = True
                    break
        image_items = await self._event_image_sources_for_recall_cache(event, limit=5) if has_image else []
        if has_image and not image_items:
            image_items = [{"source": "", "tier": "placeholder"}]
        image_sources = [item.get("source", "") for item in image_items if isinstance(item, dict) and item.get("source")]
        cache = getattr(self, "_recall_message_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._recall_message_cache = cache
        self._cleanup_recall_message_cache()
        message_id = message_ids[0]
        snapshot = {
            "message_id": message_id,
            "message_id_aliases": message_ids,
            "ts": _now_ts(),
            "scope": self._event_scope_key(event),
            "sender_id": self._event_sender_id(event),
            "sender_name": _single_line(self._sender_display_name(event), 60),
            "text": text,
            "raw_message": raw.get("message") if raw.get("message") is not None else raw.get("raw_message"),
            "reply_message_ids": reply_message_ids,
            "images": image_sources,
            "image_items": image_items,
            "image_count": len(image_items),
        }
        for candidate in message_ids:
            cache[candidate] = snapshot

    def _cleanup_recalled_message_ids(self) -> None:
        recalled = getattr(self, "_recalled_message_ids", None)
        if not isinstance(recalled, dict):
            self._recalled_message_ids = {}
            return
        now = _now_ts()
        ttl = max(60.0, _safe_float(getattr(self, "recall_cancel_reply_ttl_seconds", 600), 600))
        stale = [key for key, item in recalled.items() if now - _safe_float(item.get("ts") if isinstance(item, dict) else 0, 0) > ttl]
        for key in stale:
            recalled.pop(key, None)

    def _record_recalled_message_id(
        self,
        message_id: str,
        *,
        scope: str = "",
        notice_type: str = "",
        sender_id: str = "",
    ) -> None:
        message_id = _single_line(message_id, 120)
        if not message_id:
            return
        recalled = getattr(self, "_recalled_message_ids", None)
        if not isinstance(recalled, dict):
            recalled = {}
            self._recalled_message_ids = recalled
        self._cleanup_recalled_message_ids()
        cache = getattr(self, "_recall_message_cache", None)
        snapshot = dict(cache.get(message_id) or {}) if isinstance(cache, dict) and isinstance(cache.get(message_id), dict) else {}
        if not snapshot:
            snapshot = {
                "message_id": message_id,
                "message_id_aliases": [message_id],
                "ts": _now_ts(),
                "scope": _single_line(scope, 160),
                "sender_id": _single_line(sender_id, 80),
                "sender_name": "",
                "text": "[内容未进入短期缓存]",
                "images": [],
                "image_items": [],
                "image_count": 0,
                "cache_miss": True,
            }
            logger.info(
                "[PrivateCompanion] 撤回消息快照未命中: scope=%s message_id=%s notice=%s",
                _single_line(scope, 160) or "-",
                message_id,
                _single_line(notice_type, 40) or "-",
            )
        aliases = [message_id]
        if isinstance(snapshot.get("message_id_aliases"), list):
            aliases.extend(_single_line(item, 120) for item in snapshot.get("message_id_aliases") or [])
        record = {
            "ts": _now_ts(),
            "scope": _single_line(scope, 160) or _single_line(snapshot.get("scope"), 160),
            "notice_type": _single_line(notice_type, 40),
            "message": snapshot,
        }
        seen: set[str] = set()
        for candidate in aliases:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            recalled[candidate] = record

    def _is_message_id_recalled(self, message_id: str) -> bool:
        message_id = _single_line(message_id, 120)
        if not message_id:
            return False
        self._cleanup_recalled_message_ids()
        recalled = getattr(self, "_recalled_message_ids", None)
        return isinstance(recalled, dict) and message_id in recalled

    def _reply_cancel_trigger_message_ids(self, event: AstrMessageEvent, *extra_message_ids: str) -> list[str]:
        ids: list[str] = []
        if self._event_is_platform_message_event(event):
            current_id = self._event_message_id(event)
            if current_id:
                ids.append(current_id)
            quote_id = self._group_current_reply_quote_message_id(event)
            if quote_id:
                ids.append(quote_id)
            for message_id in self._event_reply_message_ids(event):
                if message_id:
                    ids.append(message_id)
        for message_id in extra_message_ids:
            message_id = _single_line(message_id, 120)
            if message_id:
                ids.append(message_id)
        seen: set[str] = set()
        unique: list[str] = []
        for message_id in ids:
            if message_id in seen:
                continue
            seen.add(message_id)
            unique.append(message_id)
        cache = getattr(self, "_recall_message_cache", None)
        if isinstance(cache, dict):
            for message_id in list(unique):
                snapshot = cache.get(message_id)
                nested_ids = snapshot.get("reply_message_ids") if isinstance(snapshot, dict) else None
                if not isinstance(nested_ids, list):
                    continue
                for nested_id in nested_ids:
                    nested = _single_line(nested_id, 120)
                    if nested and nested not in seen:
                        seen.add(nested)
                        unique.append(nested)
        return unique

    def _event_reply_message_ids(self, event: AstrMessageEvent) -> list[str]:
        ids: list[str] = []
        extractor = getattr(self, "_extract_reply_message_id", None)
        for item in self._event_components(event):
            type_name = self._component_type_name(item)
            if type_name != "reply" and "reply" not in type_name:
                continue
            message_id = ""
            if callable(extractor):
                try:
                    message_id = _single_line(extractor(item), 120)
                except Exception:
                    message_id = ""
            if not message_id:
                data = self._component_data(item)
                for key in ("id", "message_id", "msg_id", "seq", "message_seq", "real_id"):
                    value = data.get(key) if isinstance(data, dict) else None
                    if value is not None and str(value).strip():
                        message_id = _single_line(value, 120)
                        break
            if message_id:
                ids.append(message_id)
        seen: set[str] = set()
        unique: list[str] = []
        for message_id in ids:
            if message_id in seen:
                continue
            seen.add(message_id)
            unique.append(message_id)
        return unique

    def _should_cancel_reply_for_recalled_trigger(self, event: AstrMessageEvent, *extra_message_ids: str) -> str:
        if not getattr(self, "enable_recall_enhancement", True):
            return ""
        if not getattr(self, "enable_recall_cancel_reply", True):
            return ""
        for message_id in self._reply_cancel_trigger_message_ids(event, *extra_message_ids):
            if self._is_message_id_recalled(message_id):
                return message_id
        return ""

    async def _platform_message_exists_for_cancel_check(self, event: AstrMessageEvent, message_id: str) -> bool | None:
        message_id = _single_line(message_id, 120)
        if not message_id:
            return None
        bot = getattr(event, "bot", None)
        api = getattr(bot, "api", None)
        call_action = getattr(api, "call_action", None)
        if not callable(call_action):
            return None
        attempts: list[Any] = [message_id]
        try:
            attempts.insert(0, int(message_id))
        except (TypeError, ValueError):
            pass
        missing_error = False
        for value in attempts:
            try:
                raw = await call_action("get_msg", message_id=value)
            except Exception as exc:
                error_text = str(exc or "").lower()
                if any(marker in error_text for marker in ("not found", "message not", "消息不存在", "找不到", "不存在", "已撤回", "recalled")):
                    missing_error = True
                logger.debug(
                    "[PrivateCompanion] 触发消息存在性检查失败: message_id=%s error=%s",
                    message_id,
                    _single_line(exc, 120),
                )
                continue
            if raw:
                return True
        return False if missing_error else None

    async def _should_cancel_reply_for_missing_or_recalled_trigger(self, event: AstrMessageEvent, *extra_message_ids: str) -> str:
        recalled_message_id = self._should_cancel_reply_for_recalled_trigger(event, *extra_message_ids)
        if recalled_message_id:
            return recalled_message_id
        if not getattr(self, "enable_recall_enhancement", True):
            return ""
        if not getattr(self, "enable_recall_cancel_reply", True):
            return ""
        for message_id in self._reply_cancel_trigger_message_ids(event, *extra_message_ids):
            exists = await self._platform_message_exists_for_cancel_check(event, message_id)
            if exists is False:
                self._record_recalled_message_id(message_id, scope=self._event_scope_key(event), notice_type="missing_before_send")
                return message_id
        return ""

    def _should_cancel_reply_for_recalled_message_ids(self, *message_ids: str) -> str:
        if not getattr(self, "enable_recall_enhancement", True):
            return ""
        if not getattr(self, "enable_recall_cancel_reply", True):
            return ""
        for message_id in message_ids:
            message_id = _single_line(message_id, 120)
            if message_id and self._is_message_id_recalled(message_id):
                return message_id
        return ""

    def _recent_recalled_messages_for_scope(self, scope: str, *, limit: int = 5) -> list[dict[str, Any]]:
        if not getattr(self, "enable_recall_enhancement", True):
            return []
        if not getattr(self, "enable_recall_message_cache", True):
            return []
        self._cleanup_recalled_message_ids()
        recalled = getattr(self, "_recalled_message_ids", None)
        if not isinstance(recalled, dict):
            return []
        scope = _single_line(scope, 160)
        rows: list[dict[str, Any]] = []
        seen_messages: set[str] = set()
        for message_id, item in recalled.items():
            if not isinstance(item, dict):
                continue
            message = item.get("message") if isinstance(item.get("message"), dict) else {}
            if not message:
                continue
            item_scope = _single_line(item.get("scope") or message.get("scope"), 160)
            if scope and item_scope and item_scope != scope:
                continue
            unique_id = _single_line(message.get("message_id"), 120) or message_id
            if unique_id in seen_messages:
                continue
            seen_messages.add(unique_id)
            rows.append({**message, "message_id": message_id, "recalled_ts": _safe_float(item.get("ts"), 0)})
        rows.sort(key=lambda row: _safe_float(row.get("recalled_ts"), 0), reverse=True)
        return rows[: max(1, limit)]

    def _format_recalled_messages_for_event(self, event: AstrMessageEvent, *, limit: int = 5) -> str:
        rows = self._recent_recalled_messages_for_scope(self._event_scope_key(event), limit=limit)
        if not rows:
            return "当前会话没有可转述的撤回消息，或缓存已经过期。"
        lines = ["最近撤回消息："]
        for index, row in enumerate(rows, 1):
            sender = _single_line(row.get("sender_name"), 40) or _single_line(row.get("sender_id"), 40) or "未知"
            text = _single_line(row.get("text"), 360)
            if row.get("cache_miss"):
                text = "[已收到撤回通知，但原消息没有进入短期缓存，无法恢复内容]"
            image_status = self._recall_image_status_summary(row)
            if image_status:
                text = f"{text}（{image_status}）"
            elapsed = self._format_timestamp_elapsed(row.get("recalled_ts", 0))
            lines.append(f"{index}. {sender}｜{elapsed}撤回：{text}")
        return "\n".join(lines)

    def _image_component_for_recall_source(self, source: str) -> Any | None:
        text = str(source or "").strip()
        if not text:
            return None
        local_text = text[len("file://"):] if text.startswith("file://") else text
        if not re.match(r"^https?://", text, flags=re.I) and not text.startswith(("data:", "base64://")):
            try:
                path = Path(local_text)
                exists = path.exists() and path.is_file()
            except (OSError, ValueError):
                exists = False
            if exists:
                text = str(path)
                for method_name in ("fromFileSystem", "from_file_system"):
                    method = getattr(Image, method_name, None)
                    if callable(method):
                        try:
                            return method(text)
                        except Exception:
                            continue
            elif re.match(r"^(?:[A-Za-z]:[\\/]|/|\\\\|file://)", text):
                return None
        candidates = (
            {"file": text, "url": text},
            {"file": text},
            {"url": text},
        )
        for kwargs in candidates:
            try:
                return Image(**kwargs)
            except Exception:
                continue
        return None

    def _recalled_message_media_components_for_event(self, event: AstrMessageEvent, *, limit: int = 5) -> list[Any]:
        rows = self._recent_recalled_messages_for_scope(self._event_scope_key(event), limit=limit)
        components: list[Any] = []
        for index, row in enumerate(rows, 1):
            images = [
                item.get("source", "")
                for item in self._recall_image_items_from_snapshot(row)
                if item.get("source")
            ]
            image_components: list[Any] = []
            for source in images[:5]:
                component = self._image_component_for_recall_source(str(source or ""))
                if component is not None:
                    image_components.append(component)
            if not image_components:
                continue
            components.append(Plain(f"\n第 {index} 条撤回原图："))
            components.extend(image_components)
        return components

    def _user_asks_recalled_messages(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or "")).lower()
        if not compact:
            return False
        if not any(token in compact for token in ("撤回", "撤了", "撤掉", "收回", "防撤回")):
            return False
        ask_tokens = (
            "什么", "啥", "哪条", "哪句", "内容", "刚才", "刚刚", "最近", "上一条",
            "谁", "看", "看看", "说了什么", "发了什么", "发的啥", "撤的啥",
        )
        if any(token in compact for token in ask_tokens):
            return True
        return bool(re.search(r"撤[回了掉]?.*(说|发|讲|聊)", compact))

    def _format_recalled_messages_for_natural_query(self, event: AstrMessageEvent, *, limit: int = 5) -> str:
        if not getattr(self, "enable_recall_enhancement", True) or not getattr(self, "enable_recall_transcribe_command", True):
            return (
                "【撤回消息查询】\n"
                "用户正在问当前会话刚才撤回了什么,但撤回消息转述功能没有开启。请自然说明这边看不到可转述的撤回内容。"
            )
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = False
        allowed = self._can_manage_private_companion(event) if is_private else self._can_manage_group_companion(event)
        if not allowed:
            return (
                "【撤回消息查询】\n"
                "用户正在问当前会话刚才撤回了什么,但这类内容只能由 Bot 管理员、配置目标用户或群管理员查看。"
                "请自然说明权限边界,不要猜测或编造撤回内容。"
            )
        rows = self._recent_recalled_messages_for_scope(self._event_scope_key(event), limit=limit)
        if not rows:
            return (
                "【撤回消息查询】\n"
                "用户正在问当前会话刚才撤回了什么,但当前会话没有可转述的撤回消息,或短期缓存已经过期。"
                "请自然说明没有查到,不要编造。"
            )
        lines = [
            "【撤回消息查询】",
            "用户正在问当前会话刚才撤回了什么。下面是可转述的短期撤回记录；请用自然口吻回答,不要提插件、缓存或内部记录机制。",
        ]
        for index, row in enumerate(rows, 1):
            sender = _single_line(row.get("sender_name"), 40) or _single_line(row.get("sender_id"), 40) or "未知"
            text = _single_line(row.get("text"), 360)
            if row.get("cache_miss"):
                text = "[已收到撤回通知，但原消息没有进入短期缓存，不能编造具体内容]"
            image_status = self._recall_image_status_summary(row)
            if image_status:
                text = f"{text}（{image_status}；如需可恢复图片,请使用撤回消息命令查看）"
            elapsed = self._format_timestamp_elapsed(row.get("recalled_ts", 0))
            lines.append(f"{index}. {sender}｜{elapsed}撤回：{text}")
        return "\n".join(lines)

    def _forbidden_recall_words(self) -> list[str]:
        words = getattr(self, "recall_forbidden_words", [])
        return [str(item) for item in words if str(item or "").strip()]

    def _forbidden_recall_hit(self, text: str) -> str:
        if not getattr(self, "enable_recall_enhancement", True):
            return ""
        if not getattr(self, "enable_forbidden_word_recall", False):
            return ""
        text = str(text or "")
        if not text:
            return ""
        case_sensitive = bool(getattr(self, "recall_forbidden_word_case_sensitive", False))
        haystack = text if case_sensitive else text.lower()
        for word in self._forbidden_recall_words():
            needle = word if case_sensitive else word.lower()
            if needle and needle in haystack:
                return word
        return ""

    def _chain_text_for_forbidden_recall(self, chain: list[Any], *, limit: int = 2000) -> str:
        parts = [self._recall_component_text(comp) for comp in chain or []]
        return _single_line(" ".join(item for item in parts if item), limit)

    async def _try_delete_message(self, event: AstrMessageEvent, message_id: str, *, reason: str = "") -> bool:
        message_id = _single_line(message_id, 120)
        if not message_id:
            return False
        call = getattr(self, "_call_platform_action", None)
        if not callable(call):
            return False
        attempts: list[Any] = [message_id]
        try:
            attempts.append(int(message_id))
        except (TypeError, ValueError):
            pass
        for value in attempts:
            try:
                await call(event, "delete_msg", message_id=value)
                logger.info("[PrivateCompanion] 已尝试撤回消息: message_id=%s reason=%s", message_id, _single_line(reason, 80))
                return True
            except Exception as exc:
                logger.debug("[PrivateCompanion] 撤回消息失败: message_id=%s error=%s", message_id, _single_line(exc, 120))
        return False

    def _candidate_trigger_message_id(self, candidate: dict[str, Any]) -> str:
        for key in ("trigger_message_id", "message_id", "msg_id"):
            value = _single_line(candidate.get(key), 120)
            if value:
                return value
        context = candidate.get("context")
        if isinstance(context, dict):
            for key in ("trigger_message_id", "message_id", "msg_id"):
                value = _single_line(context.get(key), 120)
                if value:
                    return value
        return ""

    def _clear_planned_proactive_trigger(self, user: dict[str, Any]) -> None:
        user["planned_proactive_trigger_message_id"] = ""
        user["planned_proactive_trigger_umo"] = ""
        user["planned_proactive_trigger_ts"] = 0

    def _set_planned_proactive_trigger(
        self,
        user: dict[str, Any],
        *,
        message_id: str,
        umo: str = "",
        created_at: float = 0,
    ) -> None:
        message_id = _single_line(message_id, 120)
        if not message_id:
            self._clear_planned_proactive_trigger(user)
            return
        user["planned_proactive_trigger_message_id"] = message_id
        user["planned_proactive_trigger_umo"] = _single_line(umo, 160)
        user["planned_proactive_trigger_ts"] = created_at if created_at > 0 else _now_ts()

    def _planned_proactive_quote_message_id(self, user: dict[str, Any], umo: str) -> str:
        if not getattr(self, "enable_proactive_quote_trigger_message", False):
            return ""
        if not getattr(self, "enable_quote_private_proactive", True):
            return ""
        message_id = _single_line(user.get("planned_proactive_trigger_message_id"), 120)
        if not message_id:
            return ""
        trigger_umo = _single_line(user.get("planned_proactive_trigger_umo"), 160)
        if trigger_umo and trigger_umo != _single_line(umo, 160):
            return ""
        trigger_ts = _safe_float(user.get("planned_proactive_trigger_ts"), 0)
        if trigger_ts > 0 and _now_ts() - trigger_ts > max(1, self.proactive_reply_context_hours) * 3600:
            return ""
        return message_id

    def _quote_cache_key(self, event: AstrMessageEvent | None = None) -> str:
        if event is None:
            return "default"
        try:
            platform = str(event.get_platform_name() or "").strip()
        except Exception:
            platform = ""
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 160)
        origin = umo.split(":", 1)[0] if ":" in umo else umo
        return platform or origin or "default"

    def _make_reply_component(self, message_id: str, event: AstrMessageEvent | None = None) -> Any | None:
        if Reply is None:
            logger.debug("[PrivateCompanion] 当前 AstrBot 运行环境缺少 Reply 组件，引用触发消息已降级。")
            return None
        message_id = _single_line(message_id, 120)
        if not message_id:
            return None
        cache_key = self._quote_cache_key(event)
        style_cache = getattr(self, "_reply_component_style_cache", None)
        if not isinstance(style_cache, dict):
            style_cache = {}
            self._reply_component_style_cache = style_cache
        candidate_ids: list[Any] = [message_id]
        try:
            candidate_ids.append(int(message_id))
        except (TypeError, ValueError):
            pass
        cached = style_cache.get(cache_key)
        if isinstance(cached, tuple) and len(cached) == 2:
            style, value_kind = cached
            for value in candidate_ids:
                if value_kind == "int" and not isinstance(value, int):
                    continue
                if value_kind == "str" and not isinstance(value, str):
                    continue
                try:
                    if style == "positional":
                        return Reply(value)
                    return Reply(**{style: value})
                except Exception:
                    break
            style_cache.pop(cache_key, None)
        for value in candidate_ids:
            for kwargs in ({"id": value}, {"message_id": value}, {"msg_id": value}):
                try:
                    style_cache[cache_key] = (next(iter(kwargs.keys())), "int" if isinstance(value, int) else "str")
                    return Reply(**kwargs)
                except Exception:
                    continue
            try:
                style_cache[cache_key] = ("positional", "int" if isinstance(value, int) else "str")
                return Reply(value)
            except Exception:
                continue
        logger.info(
            "[PrivateCompanion] 当前平台未能构造 Reply 引用组件，已降级为普通发送: platform=%s message_id=%s",
            cache_key,
            message_id,
        )
        return None

    def _with_optional_reply(self, chain: list[Any], message_id: str, event: AstrMessageEvent | None = None) -> list[Any]:
        reply = self._make_reply_component(message_id, event=event)
        if reply is None:
            return chain
        return [reply, *chain]

    def _chain_has_reply_component(self, chain: list[Any]) -> bool:
        for item in chain:
            if Reply is not None and isinstance(item, Reply):
                return True
            if item.__class__.__name__.lower() == "reply":
                return True
        return False

    def _quote_plain_text_len(self, value: Any) -> int:
        if isinstance(value, list):
            parts: list[str] = []
            for comp in value:
                if hasattr(comp, "text"):
                    parts.append(str(getattr(comp, "text", "") or ""))
            text = "".join(parts)
        else:
            text = str(value or "")
        return len(re.sub(r"\s+", "", text))

    def _quote_skip_reason_for_short_reply(self, text_or_chain: Any = None) -> str:
        threshold = _safe_int(getattr(self, "quote_skip_short_reply_chars", 0), 0, 0)
        if threshold <= 0 or text_or_chain is None:
            return ""
        length = self._quote_plain_text_len(text_or_chain)
        if 0 < length <= threshold:
            return f"short_reply:{length}<={threshold}"
        return ""

    def _event_quoted_original_message_id(self, event: AstrMessageEvent) -> str:
        for message_id in self._event_reply_message_ids(event):
            if message_id and message_id != self._event_message_id(event):
                return message_id
        return ""

    def _quote_scene_allowed(self, scene_name: str) -> bool:
        if not getattr(self, "enable_proactive_quote_trigger_message", False):
            return False
        if scene_name == "group_reply":
            return bool(getattr(self, "enable_quote_group_reply", True))
        if scene_name == "group_interjection":
            return bool(getattr(self, "enable_quote_group_interjection", True))
        if scene_name == "private_proactive":
            return bool(getattr(self, "enable_quote_private_proactive", True))
        return True

    def _resolve_quote_message_id(
        self,
        event: AstrMessageEvent,
        *,
        scene_name: str = "group_reply",
        text_or_chain: Any = None,
        force_refresh: bool = False,
    ) -> str:
        if not self._quote_scene_allowed(scene_name):
            return ""
        fixed = _single_line(getattr(event, "private_companion_quote_message_id", ""), 120)
        fixed_scene = _single_line(getattr(event, "private_companion_quote_scene", ""), 40)
        if fixed and not force_refresh and (not fixed_scene or fixed_scene == scene_name):
            if self._quote_skip_reason_for_short_reply(text_or_chain):
                return ""
            return fixed
        if scene_name in {"group_reply", "group_interjection"}:
            checker = getattr(self, "_feature_enabled_or_temp_unlocked", None)
            group_enabled = checker("enable_group_companion") if callable(checker) else self.enable_group_companion
            if not group_enabled:
                return ""
            if not self._extract_group_id_from_event(event):
                return ""
            if scene_name == "group_reply":
                scene = getattr(event, "private_companion_group_scene", None)
                triggered = False
                if isinstance(scene, dict):
                    if str(scene.get("talking_to") or "") == "bot":
                        triggered = True
                    if str(scene.get("trigger") or "") in {
                        "at_bot",
                        "reply_bot",
                        "mention_bot_name",
                        "group_wakeup_direct_word",
                        "group_wakeup_context_word",
                        "group_wakeup_interest",
                        "group_wakeup_question",
                        "group_wakeup_cold_group",
                        "bot_conversation_followup",
                    }:
                        triggered = True
                if getattr(event, "is_at_or_wake_command", False) or getattr(event, "is_wake", False):
                    triggered = True
                if not triggered:
                    return ""
        current_id = self._event_message_id(event)
        if not current_id:
            return ""
        quote_id = current_id
        reason = "current_trigger"
        if scene_name == "group_reply":
            scene = getattr(event, "private_companion_group_scene", None)
            trigger = _single_line((scene or {}).get("trigger") if isinstance(scene, dict) else "", 40)
            quoted_id = self._event_quoted_original_message_id(event)
            strategy = _single_line(getattr(self, "quote_target_strategy", "current"), 20).lower()
            if strategy not in {"current", "quoted", "auto"}:
                strategy = "current"
            if quoted_id and trigger == "reply_bot" and strategy in {"quoted", "auto"}:
                quote_id = quoted_id
                reason = f"{strategy}_quoted_bot_message"
        short_reason = self._quote_skip_reason_for_short_reply(text_or_chain)
        if short_reason:
            setattr(event, "private_companion_quote_skip_reason", short_reason)
            return ""
        setattr(event, "private_companion_quote_message_id", quote_id)
        setattr(event, "private_companion_quote_scene", scene_name)
        setattr(event, "private_companion_quote_reason", reason)
        return quote_id

    def _group_current_reply_quote_message_id(self, event: AstrMessageEvent, text_or_chain: Any = None) -> str:
        return self._resolve_quote_message_id(event, scene_name="group_reply", text_or_chain=text_or_chain)

    def _strip_internal_identity_anchors(self, text: str) -> str:
        cleaned = str(text or "")
        cleaned = re.sub(r"\[QQ:\d{5,12}\]", "", cleaned)
        cleaned = re.sub(r"(?<![\w])QQ[:：]\d{5,12}", "", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        return cleaned.strip()

    def _event_component_stable_fingerprint_part(self, item: Any) -> str:
        class_name = item.__class__.__name__.lower()
        if isinstance(item, dict):
            class_name = str(item.get("type") or item.get("post_type") or "dict").lower()
            data = item.get("data") if isinstance(item.get("data"), dict) else item
        else:
            data = getattr(item, "data", None)
            if not isinstance(data, dict):
                data = {}
        if class_name == "image":
            values: list[str] = []
            for attr in (
                "file_unique",
                "file_id",
                "md5",
                "sha1",
                "file",
                "summary",
                "url",
            ):
                value = data.get(attr) if isinstance(data, dict) and attr in data else getattr(item, attr, None)
                text = _single_line(value, 260)
                if not text:
                    continue
                if attr == "url":
                    text = re.sub(r"[?#].*$", "", text).rstrip("/")
                    text = text.rsplit("/", 1)[-1] or text
                values.append(f"{attr}={text}")
            if values:
                return "Image:" + "|".join(values[:4])
            return "Image"
        text = (
            getattr(item, "text", None)
            or getattr(item, "message", None)
            or getattr(item, "content", None)
            or (data.get("text") if isinstance(data, dict) else "")
            or (data.get("file") if isinstance(data, dict) else "")
            or (data.get("id") if isinstance(data, dict) else "")
        )
        return f"{item.__class__.__name__}:{_single_line(text, 160)}"

    def _event_has_media_component(self, event: AstrMessageEvent) -> bool:
        for item in self._event_components(event):
            class_name = item.__class__.__name__.lower()
            if isinstance(item, dict):
                class_name = str(item.get("type") or "").lower()
            if class_name in {"image", "record", "video", "file", "face"}:
                return True
        raw = str(getattr(event, "message_str", "") or "")
        return bool(re.search(r"\[CQ:(?:image|record|video|file|face)\b", raw))

    def _event_content_fingerprint(self, event: AstrMessageEvent, text: str) -> str:
        pieces = [_single_line(text, 260)]
        message_obj = getattr(event, "message_obj", None)
        if message_obj is not None:
            chain = getattr(message_obj, "message", None)
            if isinstance(chain, list):
                pieces.extend(self._event_component_stable_fingerprint_part(item) for item in chain[:8])
            raw = getattr(message_obj, "raw_message", None)
            if raw and not any(part.startswith("Image:") for part in pieces):
                normalized = re.sub(r"(?:url|cache|proxy|token|file_size|size)=[^,\]]+", "", str(raw)[:800])
                pieces.append(normalized)
        elif self._event_components(event):
            pieces.extend(self._event_component_stable_fingerprint_part(item) for item in self._event_components(event)[:8])
        source = "\n".join(part for part in pieces if part)
        if not source:
            source = _single_line(getattr(event, "message_str", ""), 260) or "empty"
        return hashlib.sha1(source.encode("utf-8", errors="ignore")).hexdigest()

    def _note_inbound_debounce_hit(self, *, kind: str, scope: str, sender_id: str, text: str, now: float) -> None:
        stats = self.data.setdefault("inbound_debounce_stats", {})
        if not isinstance(stats, dict):
            stats = {}
            self.data["inbound_debounce_stats"] = stats
        today = _today_key()
        if stats.get("day") != today:
            stats.clear()
            stats["day"] = today
            stats["total"] = 0
            stats["by_kind"] = {}
            stats["recent"] = []
        stats["total"] = _safe_int(stats.get("total"), 0, 0) + 1
        by_kind = stats.setdefault("by_kind", {})
        if not isinstance(by_kind, dict):
            by_kind = {}
            stats["by_kind"] = by_kind
        by_kind[kind] = _safe_int(by_kind.get(kind), 0, 0) + 1
        recent = stats.setdefault("recent", [])
        if not isinstance(recent, list):
            recent = []
            stats["recent"] = recent
        recent.append(
            {
                "ts": now,
                "kind": kind,
                "scope": _single_line(scope, 80),
                "sender_id": _single_line(sender_id, 40),
                "text": _single_line(text, 80),
            }
        )
        del recent[:-20]

    def _is_duplicate_inbound_message(
        self,
        event: AstrMessageEvent,
        *,
        scope: str,
        sender_id: str,
        text: str,
        now: float | None = None,
    ) -> bool:
        seconds = max(0.0, float(getattr(self, "inbound_message_debounce_seconds", 0.0) or 0.0))
        if seconds <= 0:
            return False
        now = now or _now_ts()
        cache = getattr(self, "_recent_inbound_message_debounce", None)
        if not isinstance(cache, dict):
            cache = {}
            self._recent_inbound_message_debounce = cache
        id_window = max(60.0, seconds)
        fp_window = seconds
        prune_before = now - max(90.0, id_window, fp_window * 8)
        for key, ts in list(cache.items()):
            if _safe_float(ts, 0) < prune_before:
                cache.pop(key, None)
        message_id = self._event_message_id(event)
        media_like = self._event_has_media_component(event) or not _single_line(text, 40) or _single_line(text, 40) in {"[图片]", "【图片】", "图片"}
        checks: list[tuple[str, float, str]] = []
        if message_id:
            checks.append((f"id:{scope}:{sender_id}:{message_id}", id_window, "message_id"))
        if (not message_id) or media_like:
            checks.append((f"fp:{scope}:{sender_id}:{self._event_content_fingerprint(event, text)}", fp_window, "fingerprint"))
        if not checks:
            return False
        for key, window, kind in checks:
            last = _safe_float(cache.get(key), 0)
            if last <= 0 or now - last > window:
                continue
            logger.info(
                "[PrivateCompanion] 用户消息防抖拦截: kind=%s scope=%s sender=%s msg_id=%s text=%s",
                kind,
                scope,
                sender_id,
                message_id or "-",
                _single_line(text, 80),
            )
            self._note_inbound_debounce_hit(kind=kind, scope=scope, sender_id=sender_id, text=text, now=now)
            cache[key] = now
            return True
        for key, _, _ in checks:
            cache[key] = now
        if len(cache) > 500:
            for key, _ in sorted(cache.items(), key=lambda item: _safe_float(item[1], 0))[:100]:
                cache.pop(key, None)
        return False

    def _semantic_buffer_key(self, scope: str, sender_id: str) -> str:
        return f"{scope}:{sender_id}"

    def _semantic_buffer_identity(self, key: str) -> tuple[str, str]:
        cleaned = str(key or "")
        if ":" not in cleaned:
            return cleaned, ""
        scope, sender_id = cleaned.rsplit(":", 1)
        return scope, sender_id

    def _group_high_intensity_buffer_key(self, group_id: str, sender_id: str = "") -> str:
        scope = str(getattr(self, "group_high_intensity_merge_scope", "group") or "group").lower()
        if scope == "same_user" and sender_id:
            return self._semantic_buffer_key(f"group:{group_id}:__high_intensity_sender__", str(sender_id))
        return self._semantic_buffer_key(f"group:{group_id}", "__high_intensity__")

    def _group_high_intensity_merge_wait_seconds(self) -> float:
        return max(1.0, min(30.0, float(getattr(self, "group_high_intensity_merge_seconds", 8) or 8)))

    def _message_debounce_max_wait_seconds(self, kind: str = "text") -> float:
        if kind not in {"text", "group_text"}:
            return 0.0
        return max(0.0, _safe_float(getattr(self, "text_message_debounce_max_wait_seconds", 12.0), 12.0, 0.0))

    def _message_debounce_max_merge_messages(self, kind: str = "text") -> int:
        if kind == "group_high_intensity":
            return max(0, _safe_int(getattr(self, "group_high_intensity_max_merge_messages", 8), 8, 0))
        return max(0, _safe_int(getattr(self, "message_debounce_max_merge_messages", 8), 8, 0))

    def _semantic_buffer_active_snapshot(self, key: str, *, wait_seconds: float | None = None, force: bool = False) -> dict[str, Any]:
        default_wait = self._message_debounce_seconds("text") if hasattr(self, "_message_debounce_seconds") else getattr(self, "semantic_message_debounce_seconds", 0.0)
        wait = max(0.0, float(wait_seconds if wait_seconds is not None else default_wait or 0.0))
        if (not force and not bool(getattr(self, "enable_message_debounce", getattr(self, "enable_semantic_message_debounce", True)))) or wait <= 0:
            return {}
        buffers = getattr(self, "_semantic_message_buffers", None)
        if not isinstance(buffers, dict):
            return {}
        buffer = buffers.get(key)
        if not isinstance(buffer, dict):
            return {}
        wait = max(0.0, _safe_float(buffer.get("wait_seconds"), wait, 0.0) or wait)
        now = _now_ts()
        first_ts = _safe_float(buffer.get("first_ts"), 0.0, 0.0)
        updated_ts = _safe_float(buffer.get("updated_ts"), first_ts, first_ts)
        max_deadline_ts = _safe_float(buffer.get("max_deadline_ts"), 0.0, 0.0)
        target_ts = updated_ts + wait
        if max_deadline_ts > 0:
            target_ts = min(target_ts, max_deadline_ts)
        if first_ts <= 0 or now - target_ts > 0.8:
            return {}
        messages = buffer.get("messages") if isinstance(buffer.get("messages"), list) else []
        texts = []
        for item in messages[-8:]:
            if isinstance(item, dict):
                text = _single_line(item.get("text"), 260)
                if text:
                    texts.append(text)
        return {
            "active": True,
            "kind": _single_line(buffer.get("kind"), 40),
            "first_ts": first_ts,
            "updated_ts": updated_ts,
            "remaining": max(0.0, target_ts - now),
            "texts": texts,
        }

    def _note_semantic_message_buffer(
        self,
        key: str,
        text: str,
        *,
        sender_name: str = "",
        now: float | None = None,
        wait_seconds: float | None = None,
        force: bool = False,
        smart_debounce: dict[str, Any] | None = None,
        kind: str = "text",
    ) -> bool:
        if not force and not bool(getattr(self, "enable_message_debounce", getattr(self, "enable_semantic_message_debounce", True))):
            return False
        default_wait = self._message_debounce_seconds("text") if hasattr(self, "_message_debounce_seconds") else getattr(self, "semantic_message_debounce_seconds", 0.0)
        wait = max(0.0, float(wait_seconds if wait_seconds is not None else default_wait or 0.0))
        if wait <= 0:
            return False
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        now = now or _now_ts()
        buffers = getattr(self, "_semantic_message_buffers", None)
        if not isinstance(buffers, dict):
            buffers = {}
            self._semantic_message_buffers = buffers
        for item_key, item in list(buffers.items()):
            if not isinstance(item, dict) or now - _safe_float(item.get("updated_ts"), item.get("first_ts"), 0) > max(20.0, wait + 8.0):
                buffers.pop(item_key, None)
        current = buffers.get(key)
        scope, sender_id = self._semantic_buffer_identity(key)
        debounce_mode = "smart" if isinstance(smart_debounce, dict) and smart_debounce.get("enabled") else "fixed"
        buffer_kind = _single_line(kind, 40) or "text"
        max_merge_messages = self._message_debounce_max_merge_messages(buffer_kind)
        max_wait_seconds = self._message_debounce_max_wait_seconds(buffer_kind)
        if isinstance(current, dict) and now - _safe_float(current.get("updated_ts"), current.get("first_ts"), 0) <= wait + 0.8:
            current_deadline_ts = _safe_float(current.get("deadline_ts"), 0.0, 0.0)
            if current_deadline_ts > 0 and now >= current_deadline_ts:
                messages = current.setdefault("messages", [])
                if not isinstance(messages, list):
                    messages = []
                    current["messages"] = messages
                if cleaned not in [_single_line(item.get("text"), 260) for item in messages if isinstance(item, dict)]:
                    messages.append({"ts": now, "text": cleaned, "sender_name": _single_line(sender_name, 40)})
                current["deadline_ts"] = now
                logger.info(
                    "[PrivateCompanion] 消息收口固定窗口已到,准备立刻收口: kind=%s scope=%s sender=%s count=%s text=%s",
                    buffer_kind,
                    scope,
                    sender_id,
                    len(messages),
                    _single_line(cleaned, 80),
                )
                return True
            current_max_deadline_ts = _safe_float(current.get("max_deadline_ts"), 0.0, 0.0)
            if current_max_deadline_ts > 0 and now >= current_max_deadline_ts:
                messages = current.setdefault("messages", [])
                if not isinstance(messages, list):
                    messages = []
                    current["messages"] = messages
                if cleaned not in [_single_line(item.get("text"), 260) for item in messages if isinstance(item, dict)]:
                    messages.append({"ts": now, "text": cleaned, "sender_name": _single_line(sender_name, 40)})
                current["deadline_ts"] = now
                current["max_wait_reached"] = True
                logger.info(
                    "[PrivateCompanion] 消息收口达到最长等待,准备立刻收口: kind=%s scope=%s sender=%s max_wait=%.1fs count=%s text=%s",
                    buffer_kind,
                    scope,
                    sender_id,
                    max_wait_seconds,
                    len(messages),
                    _single_line(cleaned, 80),
                )
                return True
            current["wait_seconds"] = wait
            current["kind"] = buffer_kind
            if buffer_kind in {"image", "forward", "group_high_intensity", "group_short_wakeup"} and _safe_float(current.get("deadline_ts"), 0) <= 0:
                first_ts = _safe_float(current.get("first_ts"), now, now)
                current["deadline_ts"] = first_ts + wait
            if max_wait_seconds > 0 and _safe_float(current.get("max_deadline_ts"), 0.0) <= 0:
                first_ts = _safe_float(current.get("first_ts"), now, now)
                current["max_deadline_ts"] = first_ts + max_wait_seconds
            if smart_debounce:
                current["smart_debounce"] = dict(smart_debounce)
            messages = current.setdefault("messages", [])
            if not isinstance(messages, list):
                messages = []
                current["messages"] = messages
            appended = False
            if cleaned not in [_single_line(item.get("text"), 260) for item in messages if isinstance(item, dict)]:
                messages.append({"ts": now, "text": cleaned, "sender_name": _single_line(sender_name, 40)})
                appended = True
            if appended:
                current["updated_ts"] = now
            if max_merge_messages > 0 and len(messages) >= max_merge_messages:
                current["deadline_ts"] = now
                current["max_merge_reached"] = True
                logger.info(
                    "[PrivateCompanion] 消息收口达到最大合并条数,准备立刻收口: kind=%s scope=%s sender=%s max=%s text=%s",
                    buffer_kind,
                    scope,
                    sender_id,
                    max_merge_messages,
                    _single_line(cleaned, 80),
                )
            logger.info(
                "[PrivateCompanion] 消息收口合并补话: kind=%s mode=%s scope=%s sender=%s wait=%.1fs count=%s appended=%s text=%s",
                buffer_kind,
                debounce_mode,
                scope,
                sender_id,
                wait,
                len(messages),
                appended,
                _single_line(cleaned, 80),
            )
            return True
        buffer = {
            "first_ts": now,
            "updated_ts": now,
            "wait_seconds": wait,
            "kind": buffer_kind,
            "messages": [{"ts": now, "text": cleaned, "sender_name": _single_line(sender_name, 40)}],
        }
        if buffer_kind in {"group_high_intensity", "group_short_wakeup"}:
            buffer["deadline_ts"] = now + wait
        if buffer_kind in {"image", "forward"}:
            buffer["deadline_ts"] = now + wait
        if max_wait_seconds > 0:
            buffer["max_deadline_ts"] = now + max_wait_seconds
        buffers[key] = buffer
        if smart_debounce:
            buffers[key]["smart_debounce"] = dict(smart_debounce)
        logger.info(
            "[PrivateCompanion] 消息收口创建缓冲: kind=%s mode=%s scope=%s sender=%s wait=%.1fs text=%s",
            buffer_kind,
            debounce_mode,
            scope,
            sender_id,
            wait,
            _single_line(cleaned, 80),
        )
        return False

    def _smart_message_debounce_enabled(self) -> bool:
        return bool(getattr(self, "enable_message_debounce", True)) and bool(getattr(self, "enable_smart_message_debounce", False))

    def _message_debounce_command_text(self, event: AstrMessageEvent, text: str) -> bool:
        """Command-like messages should not be delayed or merged as chat follow-ups."""
        if bool(getattr(event, "is_command", False)) or bool(getattr(event, "is_admin_command", False)):
            return True
        cleaned = _single_line(text, 260).strip()
        if not cleaned:
            return False
        if cleaned.startswith(("陪伴", "/陪伴", "私聊陪伴", "主动陪伴", "陪伴群", "/陪伴群", "群陪伴", "群聊陪伴")):
            return True
        if cleaned.startswith(("/", "／", "!", "！", "#")) and re.search(r"[\w\u4e00-\u9fff]", cleaned[1:]):
            return True
        return False

    def _smart_message_debounce_store(self) -> dict[str, Any]:
        store = self.data.setdefault("smart_message_debounce", {})
        if not isinstance(store, dict):
            store = {}
            self.data["smart_message_debounce"] = store
        store.setdefault("last_decisions", {})
        store.setdefault("examples", [])
        store.setdefault("recent_logs", [])
        return store

    def _record_smart_message_debounce_log(
        self,
        *,
        scope: str,
        sender_id: str,
        text: str = "",
        decision: str = "",
        confidence: float = 0.0,
        reason: str = "",
        wait_seconds: float = 0.0,
        outcome: str = "",
        note: str = "",
        source: str = "model",
        raw: str = "",
        message_count: int = 0,
        private_chat: bool | None = None,
    ) -> None:
        store = self._smart_message_debounce_store()
        logs = store.setdefault("recent_logs", [])
        if not isinstance(logs, list):
            logs = []
            store["recent_logs"] = logs
        logs.append(
            {
                "ts": _now_ts(),
                "scope": _single_line(scope, 80),
                "sender_id": _single_line(sender_id, 40),
                "text": _single_line(text, 180),
                "decision": _single_line(decision, 40),
                "confidence": max(0.0, min(1.0, float(confidence or 0.0))),
                "reason": _single_line(reason, 120),
                "wait_seconds": max(0.0, float(wait_seconds or 0.0)),
                "outcome": _single_line(outcome, 40),
                "note": _single_line(note, 160),
                "source": _single_line(source, 40),
                "raw": _single_line(raw, 180),
                "message_count": max(0, _safe_int(message_count, 0, 0)),
                "chat": "private" if private_chat is True else "group" if private_chat is False else "",
            }
        )
        del logs[:-80]

    def _smart_message_debounce_examples(self) -> list[dict[str, Any]]:
        if not self._smart_message_debounce_enabled():
            return []
        limit = max(0, _safe_int(getattr(self, "smart_message_debounce_examples_limit", 8), 8, 0))
        if limit <= 0:
            return []
        store = self._smart_message_debounce_store()
        examples = store.get("examples") if isinstance(store.get("examples"), list) else []
        return [item for item in examples[-limit:] if isinstance(item, dict)]

    def _record_smart_message_debounce_example(
        self,
        *,
        kind: str,
        scope: str,
        sender_id: str,
        messages: list[str],
        previous_decision: str = "",
        note: str = "",
    ) -> None:
        if not self._smart_message_debounce_enabled():
            return
        cleaned = [_single_line(item, 160) for item in messages if _single_line(item, 160)]
        if not cleaned:
            return
        store = self._smart_message_debounce_store()
        examples = store.setdefault("examples", [])
        if not isinstance(examples, list):
            examples = []
            store["examples"] = examples
        signature = hashlib.sha1("\n".join([kind, scope, sender_id, *cleaned]).encode("utf-8", errors="ignore")).hexdigest()
        if any(isinstance(item, dict) and item.get("sig") == signature for item in examples[-20:]):
            return
        examples.append(
            {
                "sig": signature,
                "ts": _now_ts(),
                "kind": _single_line(kind, 40),
                "scope": _single_line(scope, 80),
                "sender_id": _single_line(sender_id, 40),
                "messages": cleaned[:4],
                "previous_decision": _single_line(previous_decision, 40),
                "note": _single_line(note, 120),
            }
        )
        del examples[:- max(20, _safe_int(getattr(self, "smart_message_debounce_examples_limit", 8), 8, 0) * 4 or 20)]
        logger.info(
            "[PrivateCompanion] 智能防抖学习样本已记录: kind=%s scope=%s messages=%s note=%s",
            kind,
            scope,
            len(cleaned),
            _single_line(note, 80),
        )
        self._record_smart_message_debounce_log(
            scope=scope,
            sender_id=sender_id,
            text=" / ".join(cleaned[:3]),
            decision=previous_decision,
            outcome="learned",
            note=note,
            source=kind,
            message_count=len(cleaned),
        )

    def _remember_smart_message_debounce_decision(
        self,
        *,
        scope: str,
        sender_id: str,
        text: str,
        decision: str,
        confidence: float = 0.0,
        reason: str = "",
    ) -> None:
        if not self._smart_message_debounce_enabled():
            return
        store = self._smart_message_debounce_store()
        last = store.setdefault("last_decisions", {})
        if not isinstance(last, dict):
            last = {}
            store["last_decisions"] = last
        key = self._semantic_buffer_key(scope, sender_id)
        last[key] = {
            "ts": _now_ts(),
            "scope": _single_line(scope, 80),
            "sender_id": _single_line(sender_id, 40),
            "text": _single_line(text, 180),
            "decision": _single_line(decision, 40),
            "confidence": max(0.0, min(1.0, float(confidence or 0.0))),
            "reason": _single_line(reason, 120),
        }
        if len(last) > 200:
            for item_key, _ in sorted(last.items(), key=lambda item: _safe_float(item[1].get("ts"), 0) if isinstance(item[1], dict) else 0)[:40]:
                last.pop(item_key, None)

    def _maybe_record_smart_message_debounce_followup(
        self,
        *,
        scope: str,
        sender_id: str,
        text: str,
        now: float | None = None,
    ) -> None:
        if not self._smart_message_debounce_enabled():
            return
        store = self._smart_message_debounce_store()
        last = store.get("last_decisions") if isinstance(store.get("last_decisions"), dict) else {}
        key = self._semantic_buffer_key(scope, sender_id)
        previous = last.get(key) if isinstance(last, dict) else None
        if not isinstance(previous, dict):
            return
        if str(previous.get("decision") or "") != "complete":
            return
        now = now or _now_ts()
        window = max(1.0, _safe_float(getattr(self, "smart_message_debounce_learning_window_seconds", 8.0), 8.0, 1.0))
        if now - _safe_float(previous.get("ts"), 0) > window:
            return
        self._record_smart_message_debounce_example(
            kind="false_complete",
            scope=scope,
            sender_id=sender_id,
            messages=[str(previous.get("text") or ""), text],
            previous_decision="complete",
            note="模型判断已说完后,用户很快继续补充。",
        )

    def _smart_message_debounce_heuristic_incomplete(self, text: str) -> bool:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        if self._smart_message_debounce_suspense_intro_reason(cleaned):
            return True
        if re.search(r"(等下|等一下|稍等|我想想|我组织下|先别回|等等|还有|另外|然后|接着|顺便|就是)$", cleaned):
            return True
        if re.search(r"[,，、:：;；]$", cleaned):
            return True
        return False

    def _smart_message_debounce_suspense_intro_reason(self, text: str) -> str:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return ""
        compact = re.sub(r"\s+", "", cleaned)
        compact = re.sub(r"[?？。.!！~～…]+$", "", compact)
        bot_name = re.sub(r"\s+", "", str(getattr(self, "bot_name", "") or ""))
        if bot_name and compact.startswith(bot_name) and len(compact) > len(bot_name):
            addressed_tail = compact[len(bot_name):].lstrip("，,、:： ")
            if re.fullmatch(r"(你)?知道(吗|嘛|么|不|吧)?", addressed_tail):
                return ""
            if re.fullmatch(r"(你)?(懂|明白|晓得|听懂)(吗|嘛|么|不)?", addressed_tail):
                return ""
            compact = addressed_tail
        if not compact or len(compact) > 10:
            return ""
        if re.fullmatch(r"(你)?知道(吗|嘛|么|不|吧)?", compact):
            return "悬念式问句"
        if re.fullmatch(r"(你)?(懂|明白|晓得|听懂)(吗|嘛|么|不)?", compact):
            return "确认式铺垫"
        if re.fullmatch(r"(你)?猜(猜)?(看|呢|嘛|吗|么)?", compact):
            return "猜测式铺垫"
        if re.fullmatch(r"(我)?(跟|和)?你说(个事|一下|哦|嗷|哈)?", compact):
            return "说话起手式"
        if re.fullmatch(r"(问|说)(你)?个事", compact):
            return "说话起手式"
        return ""

    def _smart_message_debounce_fast_complete_reason(self, text: str) -> str:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return "空文本"
        compact = re.sub(r"\s+", "", cleaned)
        if self._smart_message_debounce_suspense_intro_reason(compact):
            return ""
        if re.search(r"[?？。.!！]$", compact):
            return "句末完整标点"
        if re.search(r"^(其实)?我(是|叫|就是).{1,24}$", compact):
            return "完整身份陈述"
        if re.search(r"(吗|么|嘛|呢|呀|啊|谁|什么|怎么|咋|为何|为什么|哪[个里儿]?|多少|几|是不是|能不能|可不可以|行不行)[?？]?$", compact):
            return "完整疑问句"
        if re.search(r"(摸摸|贴贴|抱抱|亲亲|捏捏|早安|晚安|早上好|晚上好|你好|在吗|谢谢|好呀|好的|嗯嗯|哈哈|草|笑死)[~～。.!！]?$", compact):
            return "短互动完整"
        if len(compact) <= 8 and not self._smart_message_debounce_heuristic_incomplete(compact):
            return "短句完整"
        if not self._smart_message_debounce_heuristic_incomplete(compact):
            return "未命中补话特征"
        return ""

    def _group_short_wakeup_wait_seconds(
        self,
        event: AstrMessageEvent,
        text: str,
        *,
        smart_result: dict[str, Any] | None = None,
    ) -> float:
        if not bool(getattr(self, "enable_group_wakeup_enhancement", True)):
            return 0.0
        wait = max(0.0, min(30.0, _safe_float(getattr(self, "group_wakeup_short_text_wait_seconds", 0.0), 0.0, 0.0)))
        if wait <= 0:
            return 0.0
        cleaned = _single_line(text, 80)
        compact = re.sub(r"\s+", "", cleaned)
        if not compact or len(compact) > 2:
            return 0.0
        if re.search(r"[?？。.!！~～…]$", compact):
            return 0.0
        if re.fullmatch(r"(好+|嗯+|哦+|啊+|哈+|草+|在|早|晚|是|不|行|可|对|谢谢|谢了)", compact):
            return 0.0
        scene = getattr(event, "private_companion_group_scene", None)
        if not isinstance(scene, dict) or str(scene.get("talking_to") or "") != "bot":
            return 0.0
        trigger = str(scene.get("trigger") or "")
        if trigger not in {
            "at_bot",
            "reply_bot",
            "mention_bot_name",
            "group_wakeup_direct_word",
            "group_wakeup_context_word",
            "group_wakeup_interest",
            "group_wakeup_question",
            "group_wakeup_cold_group",
            "bot_conversation_followup",
        }:
            return 0.0
        decision = str((smart_result or {}).get("decision") or "")
        reason = str((smart_result or {}).get("reason") or "")
        if decision and decision != "complete":
            return 0.0
        if reason and reason not in {"短句完整", "未命中补话特征"}:
            return 0.0
        try:
            setattr(
                event,
                "private_companion_smart_message_debounce_result",
                {
                    "decision": "incomplete",
                    "wait_seconds": wait,
                    "original_wait_seconds": _safe_float((smart_result or {}).get("original_wait_seconds"), 0.0, 0.0),
                    "elapsed_ms": _safe_int((smart_result or {}).get("elapsed_ms"), 0, 0),
                    "source": "group_short_wakeup",
                    "reason": "群聊短唤醒等待补话",
                },
            )
        except Exception:
            pass
        logger.info(
            "[PrivateCompanion] 群聊短唤醒进入补话等待: wait=%.1fs trigger=%s text=%s",
            wait,
            trigger,
            _single_line(cleaned, 40),
        )
        return wait

    def _parse_smart_message_debounce_decision(self, raw: str) -> tuple[str, float, str]:
        text = str(raw or "").strip()
        if not text:
            return "complete", 0.0, "empty"
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                decision = str(data.get("decision") or data.get("status") or "").strip().lower()
                confidence = float(data.get("confidence") or 0)
                reason = _single_line(data.get("reason"), 120)
                if decision in {"incomplete", "wait", "continue", "unfinished", "未说完", "等待"}:
                    return "incomplete", max(0.0, min(1.0, confidence)), reason
                if decision in {"complete", "done", "reply", "finished", "已说完", "回复"}:
                    return "complete", max(0.0, min(1.0, confidence)), reason
            except Exception:
                pass
        upper = text.upper()
        if upper.startswith("INCOMPLETE") or text.startswith(("未说完", "等待", "继续")):
            return "incomplete", 0.7, text[:80]
        return "complete", 0.6, text[:80]

    async def _smart_message_debounce_wait_seconds_for_event(
        self,
        event: AstrMessageEvent,
        *,
        key: str,
        text: str,
        sender_id: str,
        sender_name: str = "",
        private_chat: bool = True,
    ) -> float:
        def _set_smart_result(
            decision: str,
            *,
            wait_seconds: float = 0.0,
            original_wait_seconds: float = 0.0,
            elapsed_ms: int = 0,
            source: str = "",
            reason: str = "",
        ) -> None:
            try:
                setattr(
                    event,
                    "private_companion_smart_message_debounce_result",
                    {
                        "decision": _single_line(decision, 40),
                        "wait_seconds": max(0.0, float(wait_seconds or 0.0)),
                        "original_wait_seconds": max(0.0, float(original_wait_seconds or 0.0)),
                        "elapsed_ms": max(0, int(elapsed_ms or 0)),
                        "source": _single_line(source, 40),
                        "reason": _single_line(reason, 120),
                    },
                )
            except Exception:
                pass

        if not self._smart_message_debounce_enabled():
            return 0.0
        started_at = time.perf_counter()
        scope = key.rsplit(":", 1)[0]
        cleaned = _single_line(text, 260)
        if not cleaned:
            return 0.0
        wait = max(0.0, min(15.0, _safe_float(getattr(self, "smart_message_debounce_wait_seconds", 3.0), 3.0, 0.0)))
        if wait <= 0:
            return 0.0
        buffers = getattr(self, "_semantic_message_buffers", None)
        existing = buffers.get(key) if isinstance(buffers, dict) else None
        if isinstance(existing, dict):
            self._record_smart_message_debounce_log(
                scope=scope,
                sender_id=sender_id,
                text=cleaned,
                decision="incomplete",
                confidence=1.0,
                reason="已有收口缓冲",
                wait_seconds=wait,
                outcome="extend_wait",
                note="同一用户已有等待中的消息,继续合并补话。",
                source="buffer",
                private_chat=private_chat,
            )
            logger.info(
                "[PrivateCompanion] 智能防抖沿用等待缓冲: scope=%s sender=%s wait=%.1fs text=%s",
                scope,
                sender_id,
                wait,
                _single_line(cleaned, 80),
            )
            _set_smart_result("incomplete", wait_seconds=wait, original_wait_seconds=wait, source="buffer", reason="已有收口缓冲")
            return wait
        suspense_reason = self._smart_message_debounce_suspense_intro_reason(cleaned)
        if suspense_reason:
            self._remember_smart_message_debounce_decision(
                scope=scope,
                sender_id=sender_id,
                text=cleaned,
                decision="incomplete",
                confidence=0.9,
                reason=suspense_reason,
            )
            self._record_smart_message_debounce_log(
                scope=scope,
                sender_id=sender_id,
                text=cleaned,
                decision="incomplete",
                confidence=0.9,
                reason=suspense_reason,
                wait_seconds=wait,
                outcome="wait",
                note="短引子常用于铺垫下一句,直接等待补话。",
                source="fast_rule",
                private_chat=private_chat,
            )
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            _set_smart_result(
                "incomplete",
                wait_seconds=wait,
                original_wait_seconds=wait,
                elapsed_ms=elapsed_ms,
                source="fast_rule",
                reason=suspense_reason,
            )
            logger.info(
                "[PrivateCompanion] 智能防抖本地判定等待补话: scope=%s sender=%s elapsed=%sms reason=%s wait=%.1fs text=%s",
                scope,
                sender_id,
                elapsed_ms,
                _single_line(suspense_reason, 80),
                wait,
                _single_line(cleaned, 80),
            )
            return wait
        fast_complete_reason = self._smart_message_debounce_fast_complete_reason(cleaned)
        if fast_complete_reason:
            self._remember_smart_message_debounce_decision(
                scope=scope,
                sender_id=sender_id,
                text=cleaned,
                decision="complete",
                confidence=1.0,
                reason=fast_complete_reason,
            )
            self._record_smart_message_debounce_log(
                scope=scope,
                sender_id=sender_id,
                text=cleaned,
                decision="complete",
                confidence=1.0,
                reason=fast_complete_reason,
                wait_seconds=0.0,
                outcome="reply_now",
                note="本地快判已确认完整,不调用小模型。",
                source="fast_rule",
                private_chat=private_chat,
            )
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            _set_smart_result(
                "complete",
                wait_seconds=0.0,
                original_wait_seconds=wait,
                elapsed_ms=elapsed_ms,
                source="fast_rule",
                reason=fast_complete_reason,
            )
            logger.info(
                "[PrivateCompanion] 智能防抖本地快判放行: scope=%s sender=%s elapsed=%sms reason=%s text=%s",
                scope,
                sender_id,
                elapsed_ms,
                _single_line(fast_complete_reason, 80),
                _single_line(cleaned, 80),
            )
            return 0.0
        examples = self._smart_message_debounce_examples()
        example_lines = []
        for item in examples[-8:]:
            messages = item.get("messages") if isinstance(item.get("messages"), list) else []
            if not messages:
                continue
            example_lines.append(
                f"- {item.get('kind')}: {' / '.join(_single_line(msg, 80) for msg in messages[:3])} => {item.get('note') or ''}"
            )
        recent = []
        try:
            snapshot = self._semantic_buffer_active_snapshot(key, force=True)
            recent = snapshot.get("texts", []) if isinstance(snapshot, dict) else []
        except Exception:
            recent = []
        prompt = f"""
判断用户当前这句话是否明显还没说完，需要 Bot 等一小会儿再回复。

只输出 JSON：{{"decision":"complete|incomplete","confidence":0-1,"reason":"不超过20字"}}

会话类型：{"私聊" if private_chat else "群聊"}
用户：{_single_line(sender_name, 40) or _single_line(sender_id, 40)}
当前消息：{cleaned}
缓冲中的前文：{(" / ".join(recent[-3:]) if recent else "无")}

已学习的误判样本：
{chr(10).join(example_lines) or "无"}

判断规则：
- “知道吗/你知道吗/懂吗/明白吗/猜猜/问你个事/跟你说”这类短引子通常是在铺垫下一句，倾向 incomplete。
- 如果用户像是在起手、列举、转折、说“等下/还有/然后/我想想”、句子停在逗号冒号分号，倾向 incomplete。
- 如果是完整问题、完整请求、完整情绪表达、问候、贴贴、摸摸、表情或短回复，倾向 complete。
- 不要因为消息短就等待；只有真的像还会补一句才 incomplete。
- 宁可少等，也不要让正常对话变慢。
""".strip()
        raw = ""
        model_error = ""
        timeout_seconds = max(0.2, min(5.0, _safe_float(getattr(self, "smart_message_debounce_model_timeout_seconds", 0.8), 0.8, 0.2)))
        provider_selector = getattr(self, "_task_provider", None)
        if callable(provider_selector):
            debounce_provider_id = provider_selector(
                getattr(self, "smart_message_debounce_provider_id", ""),
                getattr(self, "llm_provider_id", ""),
            )
        else:
            debounce_provider_id = str(getattr(self, "smart_message_debounce_provider_id", "") or getattr(self, "llm_provider_id", "") or "")
        try:
            raw = await asyncio.wait_for(
                self._llm_call(
                    prompt,
                    max_tokens=80,
                    provider_id=debounce_provider_id or None,
                    task="smart_message_debounce",
                ),
                timeout=timeout_seconds,
            ) or ""
        except asyncio.TimeoutError:
            logger.info(
                "[PrivateCompanion] 智能防抖模型判断超时,使用启发式: scope=%s sender=%s timeout=%.1fs text=%s",
                scope,
                sender_id,
                timeout_seconds,
                _single_line(cleaned, 80),
            )
            model_error = f"timeout>{timeout_seconds:.1f}s"
        except Exception as exc:
            logger.info("[PrivateCompanion] 智能防抖模型判断失败,使用启发式: %s", _single_line(exc, 120))
            model_error = _single_line(exc, 120)
        decision, confidence, reason = self._parse_smart_message_debounce_decision(raw)
        source = "model" if raw else "heuristic"
        if not raw and self._smart_message_debounce_heuristic_incomplete(cleaned):
            decision, confidence, reason = "incomplete", 0.55, "启发式未说完"
            source = "heuristic"
        if decision != "incomplete":
            self._remember_smart_message_debounce_decision(
                scope=scope,
                sender_id=sender_id,
                text=cleaned,
                decision="complete",
                confidence=confidence,
                reason=reason,
            )
            self._record_smart_message_debounce_log(
                scope=scope,
                sender_id=sender_id,
                text=cleaned,
                decision="complete",
                confidence=confidence,
                reason=reason,
                wait_seconds=0.0,
                outcome="reply_now",
                note="判定用户已说完,不额外等待。",
                source=source,
                raw=raw or model_error,
                private_chat=private_chat,
            )
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            _set_smart_result(
                "complete",
                wait_seconds=0.0,
                original_wait_seconds=wait,
                elapsed_ms=elapsed_ms,
                source=source,
                reason=reason,
            )
            logger.info(
                "[PrivateCompanion] 智能防抖判定放行: scope=%s sender=%s elapsed=%sms source=%s confidence=%.2f reason=%s text=%s",
                scope,
                sender_id,
                elapsed_ms,
                source,
                confidence,
                _single_line(reason, 80),
                _single_line(cleaned, 80),
            )
            return 0.0
        self._remember_smart_message_debounce_decision(
            scope=scope,
            sender_id=sender_id,
            text=cleaned,
            decision="incomplete",
            confidence=confidence,
            reason=reason,
        )
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        elapsed_seconds = max(0.0, elapsed_ms / 1000.0)
        remaining_wait = max(0.0, wait - elapsed_seconds)
        self._record_smart_message_debounce_log(
            scope=scope,
            sender_id=sender_id,
            text=cleaned,
            decision="incomplete",
            confidence=confidence,
            reason=reason,
            wait_seconds=remaining_wait,
            outcome="wait",
            note=f"判定用户可能没说完,判断耗时计入防抖,剩余等待 {remaining_wait:.1f}s。",
            source=source,
            raw=raw or model_error,
            private_chat=private_chat,
        )
        _set_smart_result(
            "incomplete",
            wait_seconds=remaining_wait,
            original_wait_seconds=wait,
            elapsed_ms=elapsed_ms,
            source=source,
            reason=reason,
        )
        logger.info(
            "[PrivateCompanion] 智能防抖判定等待补话: scope=%s sender=%s elapsed=%sms source=%s wait=%.1fs remaining=%.1fs confidence=%.2f reason=%s text=%s",
            scope,
            sender_id,
            elapsed_ms,
            source,
            wait,
            remaining_wait,
            confidence,
            _single_line(reason, 80),
            _single_line(cleaned, 80),
        )
        return remaining_wait

    def _group_active_conversation(self, group: dict[str, Any]) -> dict[str, Any]:
        active = group.setdefault("active_bot_conversation", {})
        if not isinstance(active, dict):
            active = {}
            group["active_bot_conversation"] = active
        return active

    async def _group_followup_llm_judge(
        self,
        group: dict[str, Any],
        *,
        sender_id: str,
        sender_name: str,
        text: str,
        active: dict[str, Any],
        scene: dict[str, Any],
    ) -> bool | None:
        provider_id = self._task_provider(self.group_followup_judge_provider_id)
        if not provider_id:
            return None
        flow_formatter = getattr(self, "_format_group_recent_flow_for_review", None)
        recent_flow = (
            flow_formatter(group, sender_id=sender_id, text=text, max_lines=10, max_chars=1200)
            if callable(flow_formatter)
            else ""
        )
        prompt = f"""
判断群聊里当前这句话是否仍然是在和 Bot 对话。

只回答 YES 或 NO，不要解释。

已知：
- 上一次明确和 Bot 对话的人：{self._group_member_identity_label(str(active.get('sender_id') or sender_id), active.get('sender_name'), limit=24)}
- 上一次明确对 Bot 说的话：{_single_line(active.get('last_text'), 120)}
- Bot 上一次回复：{_single_line(active.get('last_bot_reply'), 180) or "（未记录）"}
- 当前发言者：{self._group_member_identity_label(sender_id, sender_name, limit=24)}
- 当前发言者身份锚点：{self._group_member_identity_anchor_note(sender_id, sender_name, limit=120) or "无显示名冲突"}
- 当前消息：{_single_line(text, 180)}
- 规则初判：trigger={_single_line(scene.get('trigger'), 40)} talking_to={_single_line(scene.get('talking_to'), 40)}

真实最近群聊（按时间顺序，包含当前句；判断时必须参考整段聊天流，不要只看上一条唤醒消息）：
{recent_flow or "（无）"}

判断标准：
- 如果当前消息是在承接 Bot 的回答、追问 Bot、纠正 Bot、继续问 Bot，回答 YES。
- 如果当前消息明显转向群友、全群、第三人、另一个话题，回答 NO。
- 如果中间有人插话，但当前消息仍明确指向 Bot，可以回答 YES。
- 不要因为同一用户还在窗口内就直接 YES。
- 方括号里的 QQ 是内部身份锚点；不同 QQ 即使外号相似也不是同一人。
""".strip()
        raw = await self._llm_call(
            prompt,
            max_tokens=8,
            provider_id=provider_id,
            task="group_followup_judge",
        )
        answer = str(raw or "").strip().upper()
        if answer.startswith("YES") or answer.startswith("是"):
            return True
        if answer.startswith("NO") or answer.startswith("否"):
            return False
        return None

    async def _group_message_is_bot_continuation(
        self,
        group: dict[str, Any],
        sender_id: str,
        sender_name: str,
        scene: dict[str, Any],
        text: str,
        *,
        allow_llm: bool = True,
    ) -> bool | None:
        if (
            not self.enable_group_scene_awareness
            or not self.enable_group_conversation_followup
            or self.group_conversation_followup_seconds <= 0
            or self.group_conversation_followup_max_turns <= 0
        ):
            return False
        active = self._group_active_conversation(group)
        if str(active.get("sender_id") or "") != str(sender_id or ""):
            return False
        now = _now_ts()
        if _safe_float(active.get("expires_at"), 0) <= now:
            return False
        if str(scene.get("talking_to") or "") not in {"group", "bot"}:
            return False
        if str(scene.get("trigger") or "") in {"at_other", "reply_other", "at_all"}:
            return False
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        if _safe_int(active.get("contextual_followups"), 0, 0) >= self.group_conversation_followup_max_turns:
            return False

        recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
        active_ts = _safe_float(active.get("last_ts"), 0)
        after_active = [
            item for item in recent[-12:]
            if isinstance(item, dict) and _safe_float(item.get("ts"), 0) >= active_ts
        ]
        short_interjection_checker = getattr(self, "_group_scene_short_interjection", None)
        other_after_active = [
            item for item in after_active
            if str(item.get("sender_id") or "") and str(item.get("sender_id") or "") != str(sender_id or "")
            and not (callable(short_interjection_checker) and short_interjection_checker(item.get("text")))
        ]
        seconds_since = now - active_ts if active_ts > 0 else 9999
        direct_markers = (
            "你", "妳", self.bot_name, "bot", "Bot", "刚才你", "你刚才", "你说", "你觉得", "你看",
            "那你", "问你", "回你", "跟你说", "不是说你", "不是问你", "你来", "按你说",
        )
        continuation_markers = (
            "所以", "那", "那我", "那你", "还有", "然后", "不过", "但是", "刚刚", "刚才",
            "这个", "这样", "怎么", "为什么", "可以吗", "行吗", "是不是", "对吗", "对吧",
            "是吧", "然后呢", "后来呢", "接着呢", "你呢", "所以呢", "你觉得", "？", "?",
        )
        redirect_markers = ("你们", "大家", "群里", "有人", "谁", "他", "她", "它", "他们", "她们")
        has_direct_cue = any(marker and marker in cleaned for marker in direct_markers)
        has_continuation_cue = any(marker in cleaned for marker in continuation_markers)
        looks_redirected_to_group = any(marker in cleaned for marker in redirect_markers) and not has_direct_cue

        if looks_redirected_to_group:
            return False
        if has_direct_cue:
            return True
        score_getter = getattr(self, "_group_implicit_reply_score", None)
        implicit_score = score_getter(cleaned) if callable(score_getter) else 0
        if seconds_since <= 45 and implicit_score >= 40:
            return True
        if other_after_active:
            if not allow_llm:
                return None
            judged = await self._group_followup_llm_judge(
                group,
                sender_id=sender_id,
                sender_name=sender_name,
                text=cleaned,
                active=active,
                scene=scene,
            )
            if judged is not None:
                return judged
            return False
        if seconds_since <= 25 and has_continuation_cue:
            return True
        if seconds_since <= max(45, self.group_conversation_followup_seconds) and has_continuation_cue:
            if not allow_llm:
                return None
            judged = await self._group_followup_llm_judge(
                group,
                sender_id=sender_id,
                sender_name=sender_name,
                text=cleaned,
                active=active,
                scene=scene,
            )
            return bool(judged) if judged is not None else False
        return False

    def _mark_group_bot_conversation(
        self,
        group: dict[str, Any],
        sender_id: str,
        sender_name: str,
        *,
        active: bool,
        text: str = "",
        contextual_followup: bool = False,
    ) -> None:
        store = self._group_active_conversation(group)
        if not active or not self.enable_group_conversation_followup or self.group_conversation_followup_seconds <= 0:
            if str(store.get("sender_id") or "") == str(sender_id or ""):
                store.clear()
            return
        now = _now_ts()
        previous_turns = _safe_int(store.get("contextual_followups"), 0, 0) if str(store.get("sender_id") or "") == str(sender_id or "") else 0
        contextual_turns = previous_turns + 1 if contextual_followup else 0
        if contextual_followup and contextual_turns >= self.group_conversation_followup_max_turns:
            store.clear()
            return
        store.update(
            {
                "sender_id": str(sender_id or ""),
                "sender_name": _single_line(sender_name, 40),
                "last_ts": now,
                "last_text": _single_line(text, 120),
                "contextual_followups": contextual_turns,
                "message_count": _safe_int(group.get("message_count"), 0, 0),
                "expires_at": now + max(5, self.group_conversation_followup_seconds),
            }
        )

    async def _mark_group_conversation_from_llm_request(self, event: AstrMessageEvent) -> None:
        checker = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        group_enabled = checker("enable_group_companion") if callable(checker) else self.enable_group_companion
        if not group_enabled:
            return
        if bool(getattr(event, "is_private_chat", lambda: False)()):
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id or not self._group_enabled_for_event(group_id):
            return
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        if not sender_id:
            return
        sender_name = _single_line(
            getattr(event, "private_companion_group_sender_name", "") or self._sender_display_name(event),
            40,
        )
        text = _single_line(
            getattr(event, "private_companion_group_text", "") or getattr(event, "message_str", ""),
            260,
        )
        scene = getattr(event, "private_companion_group_scene", None)
        async with self._data_lock:
            group = self._get_group(group_id)
            if not isinstance(scene, dict):
                scene = self._infer_group_scene(event, group, sender_id=sender_id, sender_name=sender_name, text=text)
            if str(scene.get("talking_to") or "") != "bot":
                return
            self._mark_group_bot_conversation(
                group,
                sender_id,
                sender_name,
                active=True,
                text=text,
                contextual_followup=bool(getattr(event, "private_companion_group_contextual_followup", False)),
            )
            self._save_data_sync()

    def _extract_group_id_from_event(self, event: AstrMessageEvent) -> str:
        raw = self._event_raw_payload(event)
        for key in ("group_id", "group", "group_no", "group_uin"):
            value = _single_line(raw.get(key), 80)
            if value and value.isdigit():
                return value
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        match = re.search(r":GroupMessage:(\d+)", umo)
        if match:
            return match.group(1)
        message_obj = getattr(event, "message_obj", None)
        for attr in ("group_id", "group", "group_no", "group_uin"):
            value = getattr(message_obj, attr, None) if message_obj is not None else None
            if value and str(value).strip().isdigit():
                return str(value)
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return ""
        except Exception:
            pass
        message_type = str(raw.get("message_type") or raw.get("detail_type") or "").lower()
        event_message_type = getattr(event, "message_type", None)
        event_message_type_text = str(getattr(event_message_type, "name", event_message_type) or "").lower()
        is_group_hint = (
            message_type == "group"
            or event_message_type_text in {"group", "group_message", "messagetype.group"}
            or ":GroupMessage:" in umo
        )
        session_id = str(getattr(event, "session_id", "") or "").strip()
        sender_id = _single_line(raw.get("user_id"), 80)
        if is_group_hint and session_id.isdigit() and session_id != sender_id:
            return session_id
        return ""

    def _sender_display_name(self, event: AstrMessageEvent) -> str:
        for name in ("get_sender_name", "get_sender_nickname"):
            func = getattr(event, name, None)
            if callable(func):
                try:
                    value = _single_line(func(), 30)
                    if value:
                        return value
                except Exception:
                    pass
        message_obj = getattr(event, "message_obj", None)
        sender = getattr(message_obj, "sender", None) if message_obj is not None else None
        for attr in ("nickname", "card", "name", "user_id"):
            value = getattr(sender, attr, None) if sender is not None else None
            if value:
                return _single_line(value, 30)
        try:
            return str(event.get_sender_id())
        except Exception:
            return "群友"

    def _event_self_id(self, event: AstrMessageEvent) -> str:
        for name in ("get_self_id", "get_bot_id"):
            func = getattr(event, name, None)
            if callable(func):
                try:
                    value = str(func() or "").strip()
                    if value:
                        return value
                except Exception:
                    pass
        message_obj = getattr(event, "message_obj", None)
        value = getattr(message_obj, "self_id", None) if message_obj is not None else None
        return str(value or "").strip()

    def _event_components(self, event: AstrMessageEvent) -> list[Any]:
        getter = getattr(event, "get_messages", None)
        if callable(getter):
            try:
                value = getter()
                return list(value) if isinstance(value, (list, tuple)) else []
            except Exception:
                return []
        message_obj = getattr(event, "message_obj", None)
        value = getattr(message_obj, "message", None) if message_obj is not None else None
        return list(value) if isinstance(value, (list, tuple)) else []

    def _event_scene_signals(self, event: AstrMessageEvent) -> dict[str, Any]:
        self_id = self._event_self_id(event)
        at_targets: list[dict[str, str]] = []
        at_all = False
        reply_to_id = ""

        def _component_attr(comp: Any, names: tuple[str, ...]) -> str:
            for name in names:
                try:
                    value = getattr(comp, name, None)
                except Exception:
                    value = None
                if value is None:
                    continue
                if isinstance(value, dict):
                    for key in ("user_id", "qq", "id", "target", "name", "nickname", "card"):
                        nested = value.get(key)
                        if nested:
                            return str(nested).strip()
                    continue
                text = str(value or "").strip()
                if text:
                    return text
            return ""

        def _is_bot_at(user_id: str, name: str) -> bool:
            bot_name = str(getattr(self, "bot_name", "") or "").strip()
            clean_name = str(name or "").strip().lstrip("@")
            if self_id and user_id and user_id == self_id:
                return True
            if bot_name and clean_name and (clean_name == bot_name or bot_name in clean_name):
                return True
            return False

        for comp in self._event_components(event):
            class_name = comp.__class__.__name__.lower()
            if class_name == "at" or class_name.endswith("at"):
                qq = _component_attr(comp, ("qq", "target", "user_id", "uin", "id", "at", "at_user", "target_id"))
                name = _single_line(
                    _component_attr(comp, ("name", "display_name", "nickname", "card", "text")) or qq,
                    40,
                )
                if qq.lower() == "all":
                    at_all = True
                    continue
                if qq or _is_bot_at(qq, name):
                    target_id = qq or self_id or "bot"
                    at_targets.append({"user_id": target_id, "name": name or target_id, "is_bot": _is_bot_at(target_id, name)})
            elif class_name == "atall":
                at_all = True
            elif class_name == "reply":
                value = _component_attr(comp, ("sender_id", "sender", "user_id", "target_id", "reply_to", "sender_uin"))
                if value:
                    reply_to_id = str(value).strip()
        return {"self_id": self_id, "at_targets": at_targets, "at_all": at_all, "reply_to_id": reply_to_id}

    def _event_at_user_ids(self, event: AstrMessageEvent) -> set[str]:
        ids: set[str] = set()
        for item in self._event_scene_signals(event).get("at_targets", []):
            if not isinstance(item, dict) or item.get("is_bot"):
                continue
            user_id = re.sub(r"\D+", "", str(item.get("user_id") or ""))
            if user_id:
                ids.add(user_id)
        raw_parts = [str(getattr(event, "message_str", "") or "")]
        message_obj = getattr(event, "message_obj", None)
        if message_obj is not None:
            raw_parts.append(str(getattr(message_obj, "raw_message", "") or ""))
        raw = "\n".join(raw_parts)
        for match in re.finditer(r"\[At:(\d+)\]|@(\d{5,})", raw):
            ids.add(match.group(1) or match.group(2))
        return ids

    def _group_resting_mention_notice(
        self,
        event: AstrMessageEvent,
        group: dict[str, Any],
        *,
        sender_id: str,
        now: float | None = None,
    ) -> tuple[str, str]:
        check_now = _now_ts() if now is None else now
        self_id = self._event_self_id(event)
        users = self.data.get("users", {})
        if not isinstance(users, dict):
            return "", ""
        for target_id in sorted(self._event_at_user_ids(event)):
            if not target_id or target_id == sender_id or target_id == self_id:
                continue
            user = users.get(target_id)
            if not isinstance(user, dict):
                continue
            rest_until = self._user_rest_silence_until(user, now=check_now)
            if rest_until <= check_now:
                continue
            log = group.setdefault("resting_at_notice_log", [])
            if not isinstance(log, list):
                log = []
                group["resting_at_notice_log"] = log
            kept = [
                item for item in log
                if isinstance(item, dict) and check_now - _safe_float(item.get("ts"), 0) <= 3600
            ]
            signature = f"{sender_id}:{target_id}"
            if any(
                str(item.get("signature") or "") == signature
                and check_now - _safe_float(item.get("ts"), 0) <= 10 * 60
                for item in kept
            ):
                group["resting_at_notice_log"] = kept
                return "", ""
            kept.append({"ts": check_now, "signature": signature, "sender_id": sender_id, "target_id": target_id})
            group["resting_at_notice_log"] = kept[-50:]
            target_name = _single_line(
                user.get("nickname")
                or user.get("last_display_name")
                or user.get("display_name")
                or target_id,
                24,
            )
            logger.info(
                "[PrivateCompanion] 群聊 @ 休息用户提醒: group=%s sender=%s target=%s until=%s",
                self._extract_group_id_from_event(event),
                sender_id,
                target_id,
                datetime.fromtimestamp(rest_until).strftime("%m-%d %H:%M"),
            )
            return target_id, f"{target_name}现在在休息，晚点再叫他吧。"
        return "", ""

    def _event_priority(self, event: dict[str, Any]) -> tuple[int, float]:
        reason = str(event.get("reason") or "")
        action = str(event.get("action") or "")
        topic = _single_line(event.get("topic"), 40)
        window = str(event.get("window") or "")
        start, _ = self._parse_window_minutes(window)
        start_minutes = start if start is not None else 24 * 60
        priority = 0
        if reason == "morning_greeting":
            priority += 42 if self._is_sticky_greeting_event(event) else 10
        elif reason == "important_date_share":
            priority += 20
        elif reason == "noon_greeting":
            priority += 38 if self._is_sticky_greeting_event(event) else 10
        elif reason == "evening_greeting":
            priority += 28 if self._is_sticky_greeting_event(event) else 6
        elif reason in {"quiet_care", "state_share"}:
            priority += 12
        if action in {"poke", "voice"}:
            priority += 2
        if any(token in topic for token in ("早安", "起床", "赖床", "闹钟")):
            priority += 8
        return (-priority, start_minutes)

    def _chain_has_media_component(self, chain: list[Any]) -> bool:
        media_types = {"image", "record", "video", "file", "node", "forward"}
        for item in chain if isinstance(chain, list) else []:
            try:
                type_name = self._component_type_name(item)
            except Exception:
                type_name = str(getattr(item, "type", "") or item.__class__.__name__).strip().lower()
            if type_name in media_types:
                return True
        return False

    def _build_result_from_chain(self, chain: list[Any]) -> Any:
        try:
            from astrbot.api.event import MessageEventResult
        except ImportError:
            from astrbot.core.message.message_event_result import MessageEventResult
        prefer_chain_result = self._chain_has_media_component(chain)
        if prefer_chain_result:
            try:
                result = MessageEventResult().chain_result(chain)
            except Exception:
                try:
                    result = MessageEventResult(chain=chain)
                except TypeError:
                    result = MessageEventResult().chain_result(chain)
        else:
            try:
                result = MessageEventResult(chain=chain)
            except TypeError:
                result = MessageEventResult().chain_result(chain)
        result = self._disable_result_t2i(result)
        return result

    def _disable_result_t2i(self, result: Any) -> Any:
        if result is None:
            return result
        try:
            if hasattr(result, "use_t2i"):
                result = result.use_t2i(False)
            elif hasattr(result, "use_t2i_"):
                result.use_t2i_ = False
        except Exception:
            pass
        return result

    def _is_silent_control_reply_text(self, text: str) -> bool:
        cleaned = _single_line(text, 160)
        if not cleaned:
            return False
        stripped = re.sub(r"^[\s\(\（\[\【]+|[\s\)\）\]\】。.!！?？]+$", "", cleaned).strip()
        compact = re.sub(r"\s+", "", stripped)
        if not compact:
            return False
        no_reply_markers = ("不回复", "无需回复", "不要回复", "不用回复", "别回复", "静默", "忽略")
        context_markers = ("群友之间", "群友互动", "群聊背景", "不是对你", "不需要接话", "无需接话", "不要接话")
        if any(marker in compact for marker in no_reply_markers) and any(marker in compact for marker in context_markers):
            return True
        if compact in {"不回复", "无需回复", "不要回复", "不用回复", "别回复", "静默", "忽略"}:
            return True
        if compact.startswith("不回复") and len(compact) <= 28:
            return True
        return False

    def _is_proactive_delivery_receipt_text(self, text: str) -> bool:
        cleaned = _single_line(text, 240)
        if not cleaned:
            return True
        compact = re.sub(r"\s+", "", cleaned)
        if compact in {
            "我主动开口了。",
            "我主动开口了",
            "我主动发了一段语音。",
            "我主动发了一段语音",
            "我主动分享了一点东西。",
            "我主动分享了一点东西",
            "我主动做了一次小互动。",
            "我主动做了一次小互动",
        }:
            return True
        if re.fullmatch(r"(?:图|图片|照片)(?:好|好了|生成好了|出来了|完成了)[啦了~～。!！]*", cleaned):
            return True
        if re.fullmatch(r"(?:生图|出图|图片生成)(?:完成|好了|成功)[啦了~～。!！]*", cleaned):
            return True
        if re.search(r"(?:还在|正在|继续)?(?:排队|队列|等待生成|等图|等图片|等它出图)", cleaned):
            return True
        if re.match(r"^(?:已经|已)(?:发|发送)过去[啦了]?[，,。!！~～\s]*(?:等(?:着|他|你|对方)|等回复|等回我)?.*$", cleaned):
            return True
        if re.match(r"^等(?:着)?(?:他|你|对方)?回(?:我|复)?[啦了~～。!！]*$", cleaned):
            return True
        if re.match(r"^消息已送达[，,。]?", cleaned):
            return True
        if re.match(r"^这是[^。！？\n]{0,80}(?:发的|发送的|收到的)[^。！？\n]{0,80}(?:消息|打招呼|问候|回复)", cleaned):
            return True
        if re.match(r"^这(?:条|是)[^。！？\n]{0,80}(?:语气|内容|消息)[^。！？\n]{0,80}$", cleaned):
            return True
        if "消息已送达" in cleaned and "收到了" in cleaned:
            return True
        return False

    def _split_proactive_text(
        self,
        text: str,
        *,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        disable_segmenting: bool = False,
    ) -> list[str]:
        # Media attachments are sent by the caller as separate components/messages.
        # Segment limits only apply to text, so image_path/extra_components are
        # intentionally ignored here and kept only for compatibility.
        normalized = str(text or "").strip()
        if not normalized:
            return []
        tts_normalizer = getattr(self, "_normalize_tts_tags", None)
        if callable(tts_normalizer) and re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", normalized, flags=re.IGNORECASE):
            try:
                normalized = str(tts_normalizer(normalized) or normalized).strip()
            except Exception:
                pass
        if disable_segmenting:
            return [normalized]
        checker = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        if callable(checker):
            if not checker("enable_segmented_proactive_reply"):
                return [normalized]
        elif not self.enable_segmented_proactive_reply:
            return [normalized]
        if len(normalized) > self.segmented_proactive_threshold:
            return [normalized]

        cleanup_pattern: re.Pattern[str] | None = None
        cleanup_words: list[str] = []
        if self.enable_segmented_proactive_content_cleanup:
            if self.segmented_proactive_split_mode == "words":
                configured_words = getattr(self, "segmented_proactive_content_cleanup_words", [])
                if isinstance(configured_words, list):
                    cleanup_words = [str(item) for item in configured_words if str(item) != ""]
                raw_cleanup_rule = str(self.segmented_proactive_content_cleanup_rule or "")
                parsed_words: list[Any] | None = None
                if not cleanup_words and raw_cleanup_rule:
                    try:
                        parsed = json.loads(raw_cleanup_rule)
                        if isinstance(parsed, list):
                            parsed_words = parsed
                    except Exception:
                        parsed_words = None
                    if parsed_words is None:
                        parsed_words = re.split(r"[,，、\n]+", raw_cleanup_rule)
                    cleanup_words = [str(item) for item in parsed_words if str(item) != ""]
            elif self.segmented_proactive_content_cleanup_rule:
                try:
                    cleanup_pattern = re.compile(self.segmented_proactive_content_cleanup_rule)
                except re.error as e:
                    logger.warning("[PrivateCompanion] 主动分段内容清理正则无效,跳过清理: %s", e)

        def _protected_cleanup_chunks(value: str) -> list[tuple[str, bool]]:
            protected_pattern = re.compile(
                r"(?is)"
                r"<(?:image|img|video|record|audio|file)\b[^>]*(?:>.*?</(?:image|img|video|record|audio|file)>|/?>)"
                r"|<tts\b[^>]*>.*?</tts>"
                r"|<[^>\n]{1,240}\bpath=\"[^\"]{1,500}\"[^>\n]*>"
                r"|<[^>\n]{1,240}\b(?:url|src)=\"[^\"]{1,500}\"[^>\n]*>"
                r"|(?i:\b(?:https?://|www\.)[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+)"
            )
            bracket_pairs = {
                "(": ")",
                "（": "）",
                "[": "]",
                "【": "】",
                "{": "}",
                "「": "」",
                "『": "』",
                "《": "》",
            }
            bracket_closers = {closer: opener for opener, closer in bracket_pairs.items()}
            quote_pairs = {"\"": "\"", "“": "”", "'": "'", "‘": "’"}
            chunks: list[tuple[str, bool]] = []
            current: list[str] = []
            protected = False
            bracket_stack: list[str] = []
            quote_close = ""
            text_value = str(value or "")
            last_pos = 0

            def flush() -> None:
                nonlocal current
                if current:
                    chunks.append(("".join(current), protected))
                    current = []

            def feed_plain(text_part: str) -> None:
                nonlocal protected, quote_close
                for char in text_part:
                    if not protected and char in bracket_pairs:
                        flush()
                        protected = True
                        bracket_stack.append(bracket_pairs[char])
                        current.append(char)
                        continue
                    if not protected and char in quote_pairs:
                        flush()
                        protected = True
                        quote_close = quote_pairs[char]
                        current.append(char)
                        continue

                    current.append(char)
                    if protected:
                        if quote_close:
                            if char == quote_close:
                                quote_close = ""
                                if not bracket_stack:
                                    flush()
                                    protected = False
                        elif char in bracket_pairs:
                            bracket_stack.append(bracket_pairs[char])
                        elif bracket_stack and char == bracket_stack[-1]:
                            bracket_stack.pop()
                            if not bracket_stack:
                                flush()
                                protected = False
                        elif char in bracket_closers and not bracket_stack:
                            flush()
                            protected = False

            for match in protected_pattern.finditer(text_value):
                feed_plain(text_value[last_pos:match.start()])
                flush()
                chunks.append((match.group(0), True))
                last_pos = match.end()
            feed_plain(text_value[last_pos:])
            flush()
            return chunks

        def _protect_segmented_literals(value: str) -> tuple[str, dict[str, str]]:
            replacements: dict[str, str] = {}
            protected_parts: list[str] = []
            for chunk, protected in _protected_cleanup_chunks(str(value or "")):
                if not protected:
                    protected_parts.append(chunk)
                    continue
                token = f"PCSEGTOKEN{len(replacements)}X"
                replacements[token] = chunk
                protected_parts.append(token)
            return "".join(protected_parts), replacements

        def _restore_segmented_literals(value: str, replacements: dict[str, str]) -> str:
            restored = str(value or "")
            for token, original in replacements.items():
                restored = restored.replace(token, original)
            return restored

        protected_normalized, protected_literals = _protect_segmented_literals(normalized)

        def _split_words_outside_protected(value: str, words: list[str]) -> list[str]:
            sorted_words = sorted({str(word) for word in words if str(word) != ""}, key=len, reverse=True)
            if not sorted_words:
                return [str(value or "")]
            segments: list[str] = []
            current: list[str] = []
            url_pattern = re.compile(r"(?i)^(?:https?://|www\.)")

            def protected_starts_with_split_word(chunk: str) -> bool:
                stripped = str(chunk or "").lstrip()
                return any(stripped.startswith(word) for word in sorted_words)

            def push_current() -> None:
                if current:
                    segments.append("".join(current))
                    current.clear()

            def feed_plain(chunk: str) -> None:
                index = 0
                text_chunk = str(chunk or "")
                cjk_char_pattern = re.compile(r"[\u3400-\u9fff\u3040-\u30ff]")

                def next_non_space_char(start: int) -> str:
                    pos = start
                    while pos < len(text_chunk) and text_chunk[pos].isspace():
                        pos += 1
                    return text_chunk[pos] if pos < len(text_chunk) else ""

                while index < len(text_chunk):
                    matched = ""
                    for word in sorted_words:
                        if text_chunk.startswith(word, index):
                            matched = word
                            break
                    if matched:
                        delimiter = matched
                        if matched == ".":
                            end = index + len(matched)
                            while end < len(text_chunk) and text_chunk[end] == ".":
                                delimiter += text_chunk[end]
                                end += 1
                            current.append(delimiter)
                            push_current()
                            index = end
                            continue
                        if matched in {"…", "~", "～"}:
                            end = index + len(matched)
                            while end < len(text_chunk) and text_chunk.startswith(matched, end):
                                delimiter += matched
                                end += len(matched)
                            current.append(delimiter)
                            push_current()
                            index = end
                            continue
                        current.append(delimiter)
                        push_current()
                        index += len(matched)
                    else:
                        char = text_chunk[index]
                        if char.isspace():
                            previous = current[-1] if current else ""
                            following = next_non_space_char(index)
                            if previous and following and cjk_char_pattern.fullmatch(previous) and cjk_char_pattern.fullmatch(following):
                                current.append("，")
                                push_current()
                                index += 1
                                while index < len(text_chunk) and text_chunk[index].isspace():
                                    index += 1
                                continue
                        current.append(text_chunk[index])
                        index += 1

            for chunk, protected in _protected_cleanup_chunks(str(value or "")):
                if protected:
                    if current and protected_starts_with_split_word(chunk):
                        push_current()
                    current.append(chunk)
                    if url_pattern.match(chunk.strip()):
                        push_current()
                else:
                    feed_plain(chunk)
            push_current()
            return segments

        def _normalize_cjk_chat_spaces(value: str) -> str:
            cjk = r"\u3400-\u9fff\u3040-\u30ff"
            cjk_punct = r"，。！？；：、…~～"
            parts: list[str] = []
            for chunk, protected in _protected_cleanup_chunks(str(value or "")):
                if protected:
                    parts.append(chunk)
                    continue
                cleaned_chunk = re.sub(rf"(?<=[{cjk_punct}])\s+(?=[{cjk}])", "", chunk)
                cleaned_chunk = re.sub(rf"(?<=[{cjk}])\s+(?=[{cjk_punct}])", "", cleaned_chunk)
                cleaned_chunk = re.sub(rf"(?<=[{cjk_punct}])\s+(?=[{cjk_punct}])", "", cleaned_chunk)
                cleaned_chunk = re.sub(rf"(?<=[{cjk}])\s+(?=[{cjk}])", "，", cleaned_chunk)
                parts.append(cleaned_chunk)
            return "".join(parts).strip()

        def _clean_segment(segment: str) -> str:
            original = str(segment or "")
            cleaned_parts: list[str] = []
            cleanup_scope = str(getattr(self, "segmented_proactive_content_cleanup_scope", "all") or "all")

            def _strip_trailing_words(value: str, words: list[str]) -> str:
                stripped = str(value or "").rstrip()
                if not words:
                    return stripped
                sorted_words = sorted({str(word) for word in words if str(word) != ""}, key=len, reverse=True)
                changed = True
                while changed and stripped:
                    changed = False
                    for word in sorted_words:
                        if stripped.endswith(word):
                            stripped = stripped[: -len(word)].rstrip()
                            changed = True
                            break
                return stripped

            def _strip_trailing_pattern(value: str, pattern: re.Pattern[str]) -> str:
                stripped = str(value or "").rstrip()
                while stripped:
                    trailing_match = None
                    for match in pattern.finditer(stripped):
                        if match.end() == len(stripped) and match.start() != match.end():
                            trailing_match = match
                    if trailing_match is None:
                        break
                    stripped = stripped[: trailing_match.start()].rstrip()
                return stripped

            for chunk, protected in _protected_cleanup_chunks(original):
                if protected:
                    cleaned_parts.append(chunk)
                    continue
                cleaned_chunk = chunk
                if cleanup_words:
                    if cleanup_scope == "trailing":
                        cleaned_chunk = _strip_trailing_words(cleaned_chunk, cleanup_words)
                    else:
                        for word in cleanup_words:
                            cleaned_chunk = cleaned_chunk.replace(word, "")
                elif cleanup_pattern:
                    if cleanup_scope == "trailing":
                        cleaned_chunk = _strip_trailing_pattern(cleaned_chunk, cleanup_pattern)
                    else:
                        cleaned_chunk = cleanup_pattern.sub("", cleaned_chunk)
                cleaned_parts.append(cleaned_chunk)
            cleaned = "".join(cleaned_parts)
            cleaned = self._strip_leading_sentence_boundary_artifacts(cleaned)
            return _normalize_cjk_chat_spaces(cleaned)

        def _visible_len(value: str) -> int:
            return len(re.sub(r"\s+", "", str(value or "")))

        def _is_soft_short_segment(value: str) -> bool:
            cleaned = _single_line(value, 60)
            if not cleaned:
                return False
            body = re.sub(r"[。！？!?…~～,.，、\s]+$", "", cleaned)
            if _visible_len(cleaned) <= max(1, self.segmented_proactive_min_segment_chars):
                return True
            if re.search(r"(?:\.{2,}|…{1,}|~{2,}|～{2,})$", cleaned):
                return False
            return body in {
                "哈哈",
                "哈",
                "嗯",
                "唔",
                "诶",
                "欸",
                "啊",
                "呀",
                "我也觉得",
                "确实",
                "真的",
                "对吧",
                "不是",
                "那个",
                "还有",
            }

        def _join_segment_pair(left: str, right: str) -> str:
            left = str(left or "").strip()
            right = str(right or "").strip()
            if not left:
                return right
            if not right:
                return left
            if re.search(r"[！？!?]$", left):
                return _normalize_cjk_chat_spaces(f"{left} {right}".strip())
            softened = re.sub(r"[。…~～]+$", "，", left)
            softened = re.sub(r"[!?！？]+$", "，", softened)
            if not re.search(r"[，,、\s]$", softened):
                softened += "，"
            return _normalize_cjk_chat_spaces(f"{softened}{right.lstrip()}")

        def _merge_segments(raw: list[str]) -> list[str]:
            segments = [str(item or "").strip() for item in raw if str(item or "").strip()]
            if len(segments) <= 1:
                return segments
            min_chars = max(1, _safe_int(getattr(self, "segmented_proactive_min_segment_chars", 8), 8, 1))
            merged: list[str] = []
            index = 0
            while index < len(segments):
                current = segments[index]
                while index + 1 < len(segments) and (
                    _visible_len(current) < min_chars
                    or _is_soft_short_segment(current)
                    or (len(merged) >= max(0, self.segmented_proactive_max_segments - 1))
                ):
                    current = _join_segment_pair(current, segments[index + 1])
                    index += 1
                if merged and (_visible_len(current) < min_chars or _is_soft_short_segment(current)):
                    merged[-1] = _join_segment_pair(merged[-1], current)
                else:
                    merged.append(current)
                index += 1
            max_segments = max(1, _safe_int(getattr(self, "segmented_proactive_max_segments", 3), 3, 1))
            if len(merged) > max_segments:
                kept = merged[: max_segments - 1]
                tail = merged[max_segments - 1]
                for item in merged[max_segments:]:
                    tail = _join_segment_pair(tail, item)
                merged = kept + [tail]
            return merged

        if self.segmented_proactive_split_mode == "words":
            split_words = [word for word in self.segmented_proactive_split_words if word]
            if "\n" not in split_words:
                split_words.append("\n")
            if not split_words:
                return [normalized]
            raw_segments = _split_words_outside_protected(normalized, split_words)
            segments: list[str] = []
            for segment in raw_segments:
                content = segment[0] if isinstance(segment, tuple) else segment
                if not isinstance(content, str):
                    continue
                cleaned = _clean_segment(content)
                if cleaned:
                    segments.append(cleaned)
            segments = _merge_segments(segments)
            return segments if segments and (len(segments) > 1 or self.enable_segmented_proactive_content_cleanup) else [normalized]

        try:
            raw_segments = re.findall(
                self.segmented_proactive_regex or r".*?[。？！~…\n]+|.+$",
                protected_normalized,
                re.DOTALL | re.MULTILINE,
            )
        except re.error as e:
            logger.warning("[PrivateCompanion] 主动分段正则无效,使用默认规则: %s", e)
            raw_segments = re.findall(r".*?[。？！~…\n]+|.+$", protected_normalized, re.DOTALL | re.MULTILINE)

        segments = []
        for segment in raw_segments:
            content = segment[0] if isinstance(segment, tuple) else segment
            if not isinstance(content, str):
                continue
            cleaned = _restore_segmented_literals(_clean_segment(content), protected_literals)
            if cleaned:
                segments.append(cleaned)
        segments = _merge_segments(segments)
        return segments if segments and (len(segments) > 1 or self.enable_segmented_proactive_content_cleanup) else [normalized]

    async def _calc_segmented_proactive_interval(self, text: str) -> float:
        if self.segmented_proactive_interval_method == "log":
            if all(ord(ch) < 128 for ch in text):
                word_count = len(text.split())
            else:
                word_count = len([ch for ch in text if ch.isalnum()])
            interval = math.log(word_count + 1, self.segmented_proactive_log_base)
            return random.uniform(interval, interval + 0.5)
        return random.uniform(
            self.segmented_proactive_interval_min,
            self.segmented_proactive_interval_max,
        )

    def _event_topic_signature(self, event: dict[str, Any] | None) -> str:
        if not isinstance(event, dict):
            return ""
        return self._proactive_topic_signature(
            event.get("topic"),
            event.get("motive"),
            event.get("why"),
            event.get("scene"),
            event.get("impulse"),
        )

    def _apply_group_wakeup_to_humanized_state(self, scene: dict[str, Any], text: str) -> dict[str, Any]:
        if not self.enable_humanized_states or not isinstance(scene, dict):
            return {}
        trigger = str(scene.get("trigger") or "")
        if not trigger.startswith("group_wakeup_"):
            return {}
        state = self.data.setdefault("daily_state", {})
        if not isinstance(state, dict):
            state = {}
            self.data["daily_state"] = state
        try:
            runtime = self._refresh_sleep_runtime_state()
        except Exception:
            runtime = self._sleep_runtime_state()
        phase = str(runtime.get("phase") or "")
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        word = _single_line(scene.get("wakeup_word"), 60)
        wakeup_type = trigger.replace("group_wakeup_", "")
        strength = _single_line(scene.get("wakeup_strength"), 24) or self._group_wakeup_strength(wakeup_type, {}, scene)
        strength_label = self._group_wakeup_strength_label(strength)
        fatigue = scene.get("wakeup_fatigue") if isinstance(scene.get("wakeup_fatigue"), dict) else {}
        fatigue_label = _single_line(fatigue.get("label"), 20)
        fatigue_suffix = f"；最近群聊唤醒疲劳为{fatigue_label},回应应更省力" if str(fatigue.get("level") or "") in {"medium", "high"} else ""
        is_sleep_phase = phase in {"falling_asleep", "light_sleep", "sleeping_again", "woken"} or bool(self._is_sleepy_plan_item(self._get_current_plan_item(self.data.get("daily_plan", {}))))
        if is_sleep_phase:
            runtime = self._mark_sleep_woken_by_group_wakeup(text, wakeup_type=wakeup_type)
            prior = _safe_int(runtime.get("woken_count"), 1, 1)
            state_note = (
                "当前处在睡眠/休息段,群聊唤醒把她从睡意里轻轻拽起来；回复应短、慢半拍,带一点刚醒的迷糊语气,但必须看清上下文再回应。"
                if prior <= 1
                else "当前处在睡眠/休息段且已被多次叫醒；回复更应短、慢,语气像半梦半醒,不要突然精神饱满,但不要答非所问或乱接。"
            ) + fatigue_suffix
            updates = [
                "清醒程度：睡眠/休息中被群聊唤醒",
                "语气：短、轻、慢半拍",
                "后续安排：群里不继续叫她就继续睡回去",
                f"唤醒强度：{strength_label}",
            ]
            self._record_group_wakeup_state_adjustment(
                scene=scene,
                text=text,
                state_note=state_note,
                updates=updates,
                intensity="中",
                carry_rule="群聊回复必须承接睡眠中被叫醒的语气感觉,但不得降低上下文理解和回答质量；如果后续没有继续对她说话,后续细化应让她继续休息或睡回去。",
            )
            return {"note": state_note, "updates": updates, "sleep_phase": runtime.get("label"), "intensity": "中", "strength": strength, "strength_label": strength_label, "fatigue": fatigue}
        if "interest" in trigger:
            if energy <= 38:
                state_note = "当前能量偏低,但群里碰到她感兴趣的话题；会有一点被勾起的精神,仍然不要长篇抢话。"
                updates = ["兴趣：被群聊话题勾起", "能量：低电量中轻微回亮", "回复策略：短句接话,不抢主导权"]
            else:
                state_note = "群里碰到她感兴趣的话题；她可以像被话题勾住一样自然冒头,但仍要尊重原本群聊走向。"
                updates = ["兴趣：上升", "分享欲：小幅上升", "回复策略：轻轻接话"]
            state_note += fatigue_suffix
            updates.append(f"唤醒强度：{strength_label}")
            self._record_group_wakeup_state_adjustment(scene=scene, text=text, state_note=state_note, updates=updates, intensity="轻")
            return {"note": state_note, "updates": updates, "interest_word": word, "intensity": "轻", "strength": strength, "strength_label": strength_label, "fatigue": fatigue}
        if energy <= 38:
            state_note = "当前能量偏低,群里叫到她时会反应慢一点；可以回应,但应更短、更省力。"
            updates = ["清醒/注意力：被群里叫回一点", "语气：省力、短句", "主动欲：不额外扩张"]
        elif energy >= 82:
            state_note = "当前状态偏有精神,群里叫到她时可以更快接住,但仍不要像主持人一样抢话。"
            updates = ["注意力：快速转向群聊", "语气：更轻快", "回复策略：自然接一句"]
        else:
            state_note = "群里提到她或出现需要她接话的词；她会把注意力从当前日程挪到群聊里,像被自然叫到。"
            updates = ["注意力：转向群聊", "回复姿态：被叫到后自然接话", "边界：不暴露触发逻辑"]
        state_note += fatigue_suffix
        updates.append(f"唤醒强度：{strength_label}")
        self._record_group_wakeup_state_adjustment(scene=scene, text=text, state_note=state_note, updates=updates, intensity="轻")
        return {"note": state_note, "updates": updates, "wakeup_word": word, "intensity": "轻", "strength": strength, "strength_label": strength_label, "fatigue": fatigue}

