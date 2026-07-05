# -*- coding: utf-8 -*-
"""
ProactiveMessageMixin — 主动消息生成、动作执行和发送链路
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
from .helpers import (
    _date_key,
    _normalize_outbound_punctuation_flow,
    _now_ts,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_internal_message_blocks,
    _today_key,
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


class ProactiveMessageMixin:
    """主动消息生成、动作执行和发送链路"""

    def _format_bilibili_video_action_context(self, user: dict[str, Any]) -> str:
        video = user.get("bilibili_video_context")
        if not isinstance(video, dict):
            return ""
        if _now_ts() - _safe_float(video.get("created_ts"), 0) > 6 * 3600:
            return ""
        title = _single_line(video.get("title"), 80)
        bvid = _single_line(video.get("bvid"), 32)
        up_name = _single_line(video.get("up_name"), 40)
        score = _safe_int(video.get("score"), 0, 0, 10)
        mood = _single_line(video.get("mood"), 24)
        comment = _single_line(video.get("comment"), 120)
        review = _single_line(video.get("review"), 180)
        source = _single_line(video.get("source"), 40)
        memory_context = video.get("memory_context") if isinstance(video.get("memory_context"), list) else []
        memory_lines = [_single_line(item, 160) for item in memory_context if _single_line(item, 160)][:3]
        parts = [
            "B站视频分享线索：刚刷到一个视频",
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
        topic = _single_line(news.get("topic"), 60)
        headline = _single_line(news.get("headline"), 100)
        source = _single_line(news.get("selected_source"), 40)
        impression = _single_line(news.get("impression"), 240)
        link = _single_line(news.get("selected_link"), 400)
        self_link = news.get("self_link") if isinstance(news.get("self_link"), dict) else {}
        self_link_text = _single_line(self_link.get("self_link") if isinstance(self_link, dict) else "", 180)
        self_link_tone = _single_line(news.get("share_tone") or (self_link.get("tone") if isinstance(self_link, dict) else ""), 80)
        self_link_boundary = _single_line(news.get("share_boundary") or (self_link.get("boundary") if isinstance(self_link, dict) else ""), 160)
        parts = [
            "新闻阅读线索：刚扫过几条新闻,其中一条让自己有点想私下提一句。",
            f"话题：{topic}" if topic else "",
            f"标题：{headline}" if headline else "",
            f"来源：{source}" if source else "",
            f"内部印象：{impression}" if impression else "",
            f"和自己有关的地方：{self_link_text}" if self_link_text else "",
            f"表达气质：{self_link_tone}" if self_link_tone else "",
            f"额外边界：{self_link_boundary}" if self_link_boundary else "",
            f"链接：{link}" if link else "",
            "表达要求：不要像播报新闻,不要夸大或补充未知事实；可以只轻轻提起,也可以按人格吐槽、担心、好奇或转移成日常感受。",
        ]
        return "\n".join(part for part in parts if part)

    def _format_web_exploration_action_context(self, user: dict[str, Any]) -> str:
        exploration = user.get("web_exploration_context")
        if not isinstance(exploration, dict):
            return ""
        if _now_ts() - _safe_float(exploration.get("created_ts"), 0) > 10 * 3600:
            return ""
        query = _single_line(exploration.get("query"), 80)
        topic = _single_line(exploration.get("topic"), 80)
        note = _single_line(exploration.get("note"), 260)
        source_title = _single_line(exploration.get("source_title"), 120)
        source_url = _single_line(exploration.get("source_url"), 420)
        reason = _single_line(exploration.get("reason"), 140)
        self_link = exploration.get("self_link") if isinstance(exploration.get("self_link"), dict) else {}
        self_link_text = _single_line(self_link.get("self_link") if isinstance(self_link, dict) else "", 180)
        self_link_tone = _single_line(exploration.get("share_tone") or (self_link.get("tone") if isinstance(self_link, dict) else ""), 80)
        self_link_boundary = _single_line(exploration.get("share_boundary") or (self_link.get("boundary") if isinstance(self_link, dict) else ""), 160)
        parts = [
            "网页探索线索：Bot 刚刚按自己的兴趣主动搜索并了解了一点新东西,这是一条内部探索笔记。",
            f"搜索词：{query}" if query else "",
            f"为什么想查：{reason}" if reason else "",
            f"探索主题：{topic}" if topic else "",
            f"留下的印象：{note}" if note else "",
            f"和自己有关的地方：{self_link_text}" if self_link_text else "",
            f"表达气质：{self_link_tone}" if self_link_tone else "",
            f"额外边界：{self_link_boundary}" if self_link_boundary else "",
            f"参考来源：{source_title}" if source_title else "",
            f"链接：{source_url}" if source_url else "",
            "表达要求：可以像随手分享发现、吐槽、好奇或继续想查；不要说成系统功能,不要编造来源外的信息。",
        ]
        return "\n".join(part for part in parts if part)

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
            "创作分享线索：她最近因为生活小事、日记碎片或梦境灵感开了一个自己的文本作品,一直私下慢慢写；现在到了一个适合轻轻提起的小节点。",
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
                parts.append(f"天气只作为画面感参考：{weather}。不要像播报天气一样说出来。")
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
        label = labeler(role) if callable(labeler) else ("主人" if role == "owner" else "朋友")
        profile = self._relationship_profile(user if isinstance(user, dict) else {})
        level = _single_line(profile.get("level"), 20) or "熟悉"
        preference = _single_line(profile.get("preference"), 20) or "普通"
        note = _single_line(user.get("proactive_boundary_note"), 80) if isinstance(user, dict) else ""
        parts = [f"当前用户角色：{label}", f"关系熟悉度：{level}", f"互动偏好：{preference}"]
        if note:
            parts.append(f"用户级备注：{note}")
        return "；".join(parts)

    def _default_proactive_prompt_template(self) -> str:
        return """
你要给 {{name}} 发一条主动私聊。不是回复刚收到的新消息,也不是解释任务。
当前时间 {{current_time}}。{{unanswered_hint}}
这会儿的小念头是“{{motive}}”,大概想聊“{{topic}}”,刚做的事是“{{action_context}}”,状态底色是“{{state_hint}}”,生活片段是“{{current_schedule}}”,时段情况是“{{time_guard}}”,最近已经说过“{{recent_topics}}”,关系事实是“{{relationship_fact}}”。
{{timer_hint}}

只取一个最自然的切口开口,不要把洗漱/穿搭/天气/心情/日程逐项列成状态清单；如果已经有一个具体画面或一句话,就停在那里。
最后留下来的文本就是聊天窗口里的下一句话。像正常聊天一样收住，只保留角色真正要说出口的内容。
只输出这句正文。
""".strip()

    def _proactive_natural_delivery_hint(self) -> str:
        return (
            "【自然交付提醒】\n"
            "这一轮的最终文本会成为对话里的下一句。"
            "请把注意力放在这句聊天内容本身，像平时主动开口那样自然收住；"
            "过程中的执行状态只供系统判断，不需要写进正文。"
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
        state = self.data.get("daily_state", {})
        action_prompt_context = self._format_action_prompt_context(action, action_context)
        relationship_fact = self._format_proactive_relationship_fact(user)
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        current_schedule = self._format_schedule_context_for_prompt() or self._format_plan_item_for_prompt(current_item)
        source_focused_reasons = {
            "bili_video_share",
            "news_share",
            "web_exploration_share",
            "creative_share",
            "jm_cosmos_share",
            "jm_cosmos_recommendation_request",
        }
        if reason in source_focused_reasons:
            current_schedule = "（本轮不取生活片段，只围绕主动来源本身）"
        elif reason == "group_share":
            last_sidecar_at = _safe_float(user.get("last_group_share_life_sidecar_at"), 0)
            if last_sidecar_at > 0 and _now_ts() - last_sidecar_at < 6 * 3600:
                current_schedule = "（最近群分享已经顺手带过生活片段，本轮只围绕群里那件事）"
        state_hint = self._format_state_for_framework_prompt(
            state if isinstance(state, dict) else {},
            reason=reason,
            action=action,
        )
        timer_hint = self._format_llm_timer_context(user)
        time_guard = self._proactive_time_guard_hint(reason, current_item)
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
                        content_preview = _single_line(loop.get("content"), 80)
                        age = loop.get("age_days")
                        age_str = f"（{age:.0f}天前）" if age is not None else ""
                        loop_texts.append(f"- {content_preview}{age_str}")
                    open_loops_hint = "你心里还挂着这些没完成的事，如果自然可以提一下：\n" + "\n".join(loop_texts)
        except Exception:
            pass
        current_schedule = self._sanitize_schedule_context_for_private_user(current_schedule, user)
        compact_motive = _single_line(motive, 36) or "有一点想靠近对方"
        topic_hint = _single_line(user.get("planned_proactive_topic"), 40)
        unanswered_count = _safe_int(user.get("ignored_streak"), 0)
        unanswered_hint = f"这是第 {unanswered_count} 次主动后还没等到回复。" if unanswered_count > 0 else ""
        current_time = self._environment_now().strftime("%Y-%m-%d %H:%M")
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
        reply_style = self._format_reply_style_prompt() if callable(getattr(self, "_format_reply_style_prompt", None)) else ""
        reply_style_marker = "<!-- private_companion_reply_style_v1 -->"
        if reply_style and reply_style_marker not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{reply_style_marker}\n{reply_style}"
        delivery_hint = self._proactive_natural_delivery_hint()
        if delivery_hint and "自然交付提醒" not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{delivery_hint}"
        if open_loops_hint and "没完成的事" not in prompt:
            prompt = f"{prompt.rstrip()}\n\n{open_loops_hint}"
        memory_getter = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(memory_getter):
            user_id = _single_line(user.get("user_id") or user.get("id"), 80)
            query = " ".join(
                part
                for part in (
                    "主动消息正文生成",
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
        return prompt.strip()

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

        lines = ["【这次主动的内在约束】"]
        if kind or anchor_type:
            lines.append(
                f"候选语义：{kind or 'check_in'}/{anchor_type or 'vague'}；"
                f"自然度 {semantic_score:.2f}，打扰压力 {semantic_pressure:.2f}，风险 {semantic_risk:.2f}。"
                + (f" 备注：{semantic_note}。" if semantic_note else "")
            )
        if readiness_label or temperature_label:
            lines.append(
                f"开口欲：{readiness_label or '平稳'} {readiness_score:.2f}；"
                f"关系温度：{temperature_label or '平稳'} {temperature_score:.2f}。"
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
        if kind in {"continuation", "reminder"}:
            lines.append("这是有来源的续接/提醒：可以顺着来源，但不要写成用户刚刚又发了新消息。")
        elif kind in {"self_share", "external_share", "observation"}:
            lines.append("这是分享/观察型主动：只取一个最小切口，不写成报告、推荐文或观察总结。")
            true_external_info = reason in {"bili_video_share", "news_share", "web_exploration_share"} or anchor_type == "external_info"
            if true_external_info:
                lines.append("外界分享必须贴住这次看到的标题、视频、新闻或资料本身；如果只是低压地放一句，也要围绕来源表达感受，不要改成无关的个人状态或泛泛压力询问。")
            elif anchor_type == "group_context":
                lines.append("群聊见闻只是一段共同群里的小片段：可以轻轻转述一个具体笑点或画面，不要把内部话题名写成“标题/新闻/资料”。")
        elif kind in {"care", "check_in", "light_touch"}:
            lines.append("这是靠近型主动：不要直接说想念、关心或刷存在感，要侧着落到一个小动作或小片段。")

        hesitation_note = _single_line(user.get("last_proactive_hesitation_note"), 100)
        hesitation_at = _safe_float(user.get("last_proactive_hesitation_at"), 0)
        if hesitation_note and hesitation_at > 0 and _now_ts() - hesitation_at <= 12 * 3600:
            lines.append(f"前面有过一次犹豫：{hesitation_note}。如果要用，只能变成很淡的语气底色，不要明说系统延后。")

        if _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            lines.append("对方最近还没回应：不要连续提问，不要控诉，也不要把沉默写成对方故意不理。")
        if "message" == str(action or "message") and not _single_line(action_context, 120):
            lines.append("本轮没有真实媒体或工具结果：正文只围绕聊天内容本身，不描述动作结果。")
        lines.append("以上只用于决定怎么写，最终正文里不要出现“语义/自然度/压力/风险/开口欲/关系温度/犹豫”等分析词。")
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
            return f"{prefix} 这次只能像早晨刚醒、赖床、洗漱或刚开始一天时那样开口；不要写成下午、傍晚或睡前。"
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
            if captured_text_parts:
                break
        return "\n".join(captured_text_parts).strip()

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

    def _framework_session_lock(self, session_key: str) -> asyncio.Lock:
        normalized = str(session_key or "").strip() or "global"
        locks = getattr(self, "_framework_session_locks", None)
        if not isinstance(locks, dict):
            locks = {}
            self._framework_session_locks = locks
        lock = locks.get(normalized)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            locks[normalized] = lock
        if len(locks) > 300:
            for key, item in list(locks.items()):
                if key != normalized and isinstance(item, asyncio.Lock) and not item.locked():
                    locks.pop(key, None)
                if len(locks) <= 240:
                    break
        return lock

    def _framework_session_lock_required(self) -> bool:
        checker = getattr(self, "_framework_session_lock_enabled", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception as exc:
                logger.debug("[PrivateCompanion] 主链会话锁模式判断失败,默认不启用: %s", _single_line(exc, 120))
                return False
        return False

    def _framework_session_key_from_event(self, event: AstrMessageEvent) -> str:
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if umo:
            return umo
        try:
            return f"private:{event.get_sender_id()}"
        except Exception:
            return "unknown"

    def _is_sqlite_locked_error(self, exc: Exception) -> bool:
        text = str(exc or "").lower()
        return "database is locked" in text or "sqlite3.operationalerror" in text or "sqlalche.me/e/20/e3q8" in text

    async def _acquire_framework_session_lock_for_event(
        self,
        event: AstrMessageEvent,
        *,
        label: str = "llm",
        private_only: bool = True,
    ) -> None:
        if not self._framework_session_lock_required():
            return
        if bool(getattr(event, "private_companion_framework_session_lock_acquired", False)):
            return
        if bool(getattr(event, "private_companion_proactive_framework", False)):
            return
        is_private_chat = bool(getattr(event, "is_private_chat", lambda: False)())
        if private_only and not is_private_chat:
            return
        session_key = self._framework_session_key_from_event(event)
        lock = self._framework_session_lock(session_key)
        wait_started = time.time()
        if lock.locked():
            owner_label = _single_line(getattr(lock, "_private_companion_owner_label", ""), 80)
            owner_session = _single_line(getattr(lock, "_private_companion_owner_session", ""), 120)
            owner_since = _safe_float(getattr(lock, "_private_companion_owner_since", 0), 0)
            owner_age = max(0.0, wait_started - owner_since) if owner_since > 0 else 0.0
            logger.info(
                "[PrivateCompanion] 同一会话已有主链请求在执行,本轮排队等待: label=%s session=%s private=%s owner=%s owner_age=%.1fs",
                label,
                _single_line(session_key, 120),
                is_private_chat,
                owner_label or owner_session or "unknown",
                owner_age,
            )
        await lock.acquire()
        try:
            waited = max(0.0, time.time() - wait_started)
            setattr(event, "private_companion_framework_session_lock_acquired", True)
            setattr(event, "private_companion_framework_session_lock", lock)
            setattr(event, "private_companion_framework_session_key", session_key)
            setattr(event, "private_companion_framework_session_lock_label", label)
            setattr(event, "private_companion_framework_session_lock_acquired_at", time.time())
            try:
                setattr(lock, "_private_companion_owner_label", label)
                setattr(lock, "_private_companion_owner_session", session_key)
                setattr(lock, "_private_companion_owner_since", time.time())
            except Exception:
                pass
            if waited >= 0.5:
                logger.info(
                    "[PrivateCompanion] 主链会话锁排队结束: label=%s session=%s waited=%.1fs",
                    label,
                    _single_line(session_key, 120),
                    waited,
                )
            watchdog = asyncio.create_task(
                self._framework_session_lock_watchdog(event, lock, session_key, label)
            )
            setattr(event, "private_companion_framework_session_lock_watchdog", watchdog)
        except Exception:
            pass

    async def _framework_session_lock_watchdog(
        self,
        event: AstrMessageEvent,
        lock: asyncio.Lock,
        session_key: str,
        label: str,
        *,
        timeout_seconds: float = 180.0,
    ) -> None:
        try:
            await asyncio.sleep(timeout_seconds)
            if (
                bool(getattr(event, "private_companion_framework_session_lock_acquired", False))
                and getattr(event, "private_companion_framework_session_lock", None) is lock
                and lock.locked()
            ):
                setattr(event, "private_companion_framework_session_lock_acquired", False)
                setattr(event, "private_companion_framework_session_lock", None)
                lock.release()
                try:
                    setattr(lock, "_private_companion_owner_label", "")
                    setattr(lock, "_private_companion_owner_session", "")
                    setattr(lock, "_private_companion_owner_since", 0.0)
                except Exception:
                    pass
                acquired_at = _safe_float(getattr(event, "private_companion_framework_session_lock_acquired_at", 0), 0)
                held = max(0.0, time.time() - acquired_at) if acquired_at > 0 else timeout_seconds
                logger.warning(
                    "[PrivateCompanion] 主链会话锁超时释放: label=%s session=%s held=%.1fs",
                    label,
                    _single_line(session_key, 120),
                    held,
                )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.debug("[PrivateCompanion] 主链会话锁看门狗异常: %s", _single_line(exc, 120))

    def _release_framework_session_lock_for_event(self, event: AstrMessageEvent, *, label: str = "llm") -> None:
        if not bool(getattr(event, "private_companion_framework_session_lock_acquired", False)):
            return
        lock = getattr(event, "private_companion_framework_session_lock", None)
        session_key = str(getattr(event, "private_companion_framework_session_key", "") or "")
        watchdog = getattr(event, "private_companion_framework_session_lock_watchdog", None)
        if isinstance(watchdog, asyncio.Task) and not watchdog.done():
            watchdog.cancel()
        try:
            setattr(event, "private_companion_framework_session_lock_acquired", False)
            setattr(event, "private_companion_framework_session_lock", None)
            setattr(event, "private_companion_framework_session_lock_watchdog", None)
        except Exception:
            pass
        if isinstance(lock, asyncio.Lock) and lock.locked():
            acquired_at = _safe_float(getattr(event, "private_companion_framework_session_lock_acquired_at", 0), 0)
            held = max(0.0, time.time() - acquired_at) if acquired_at > 0 else 0.0
            lock.release()
            try:
                setattr(lock, "_private_companion_owner_label", "")
                setattr(lock, "_private_companion_owner_session", "")
                setattr(lock, "_private_companion_owner_since", 0.0)
            except Exception:
                pass
            logger.debug(
                "[PrivateCompanion] 已释放主链会话锁: label=%s session=%s held=%.1fs",
                label,
                _single_line(session_key, 120),
                held,
            )

    def _release_framework_session_lock_later(
        self,
        event: AstrMessageEvent,
        *,
        label: str = "llm",
        delay_seconds: float = 60.0,
    ) -> None:
        if not bool(getattr(event, "private_companion_framework_session_lock_acquired", False)):
            return
        old_task = getattr(event, "private_companion_framework_session_delayed_release_task", None)
        if isinstance(old_task, asyncio.Task) and not old_task.done():
            old_task.cancel()

        async def _delayed_release() -> None:
            try:
                await asyncio.sleep(max(0.0, float(delay_seconds or 0.0)))
                self._release_framework_session_lock_for_event(event, label=label)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.debug("[PrivateCompanion] 主链会话锁延迟释放异常: %s", _single_line(exc, 120))

        task = asyncio.create_task(_delayed_release())
        try:
            setattr(event, "private_companion_framework_session_delayed_release_task", task)
        except Exception:
            pass

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
        session = self._parse_message_session(umo)
        if not session:
            return None
        return SyntheticPrivateWakeEvent(
            context=self.context,
            session=session,
            message=prompt,
            sender_name=name or "PrivateCompanion",
        )

    async def _run_framework_agent_text(
        self,
        *,
        umo: str,
        prompt: str,
        name: str,
        label: str,
        max_steps: int = 20,
    ) -> str:
        event = self._proactive_synthetic_event(umo, prompt=prompt, name=name)
        if event is None:
            return ""
        try:
            setattr(event, "private_companion_skip_external_token_stats", True)
            setattr(event, "private_companion_proactive_framework", True)
            setattr(event, "private_companion_skip_passive_input_status", True)
        except Exception:
            pass
        cfg = self.context.get_config(umo=umo) if umo else self.context.get_config()
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
                    req.conversation = conv

                    async def _runner_factory():
                        return await build_main_agent(
                            event=event,
                            plugin_context=self.context,
                            config=build_cfg,
                            req=req,
                        )

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
        session_lock = self._framework_session_lock(umo) if self._framework_session_lock_required() else None
        if isinstance(session_lock, asyncio.Lock):
            async with session_lock:
                await _run_with_retries()
        else:
            await _run_with_retries()
        runner = getattr(result, "agent_runner", None) if result else None
        llm_resp = runner.get_final_llm_resp() if runner else None
        text = str(getattr(llm_resp, "completion_text", "") or "").strip()
        captured_text = self._captured_send_plain_text(captured_tool_sends)
        if captured_text:
            if text and self._framework_agent_meta_summary_leak(text):
                logger.warning(
                    "[PrivateCompanion] 主动主链 final 疑似工具循环摘要,改用已捕获发送文本: label=%s final=%s captured=%s",
                    label,
                    _single_line(text, 160),
                    _single_line(captured_text, 160),
                )
            text = captured_text
        elif text and self._framework_agent_meta_summary_leak(text):
            logger.warning(
                "[PrivateCompanion] 主动主链 final 疑似工具循环摘要且无可用捕获文本,已丢弃: label=%s text=%s",
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
        # Pull cross-session emotional drift before generating proactive message
        try:
            self._memory_companion_apply_emotional_drift(session_id="")
        except Exception:
            pass
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
        return "\n".join(lines[-max(1, limit):])

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
        refresher = getattr(self, "_refresh_default_persona_prompt", None)
        if callable(refresher):
            try:
                await refresher(umo)
            except Exception:
                pass
        persona = self._get_default_persona_prompt()
        reply_style = self._format_reply_style_prompt()
        if not history and isinstance(user, dict):
            try:
                history = await self._recent_private_conversation_for_proactive_review(user, limit=6)
            except Exception:
                history = ""
        prompt = f"""
你要把一条“参考意图”改写成当前人格会自然说出的聊天正文。参考意图只说明要表达什么，不是要照抄的句子。

【当前人格】
{persona or "保持自然、简洁、有边界。"}

【回复风格】
{reply_style or "像日常聊天一样短一点，不要报告式。"}

【最近对话】
{history or "（无可用历史）"}

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
        external_info_reasons = {"bili_video_share", "news_share", "web_exploration_share"}
        external_share_active = reason in external_info_reasons or (
            semantic_kind == "external_share" and semantic_anchor_type == "external_info"
        )
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
            if has_cross_private_interaction or any(token in cleaned for token in ("朋友那边", "朋友用户", "朋友私聊")):
                return {"decision": "drop", "reason": "疑似混入其他私聊互动", "hard": True}
        if _safe_int(user.get("ignored_streak"), 0, 0) >= 1 and cleaned.count("？") + cleaned.count("?") >= 2:
            return {"decision": "rewrite", "reason": "未回应状态下问题太多", "text": re.split(r"[？?]", cleaned, maxsplit=1)[0].rstrip("，,。") + "。"}
        if _safe_int(user.get("ignored_streak"), 0, 0) >= 2 and len(cleaned) > 36:
            return {"decision": "rewrite", "reason": "连续未回应时主动偏长", "text": cleaned[:36].rstrip("，,。") + "。"}
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
            return None
        if self._external_share_text_mentions_source(cleaned, source_text):
            return None
        reference = self._external_share_fallback_reference(source_text)
        if reference:
            return {
                "decision": "rewrite",
                "reason": "外界分享正文偏离来源",
                "text": "",
                "reference_text": reference,
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
            text = _single_line(value, limit)
            if text and text not in parts:
                parts.append(text)

        add(topic, 180)
        add(action_context, 520)
        if isinstance(user, dict):
            for key in ("bilibili_video_context", "news_context", "web_exploration_context"):
                payload = user.get(key)
                if not isinstance(payload, dict):
                    continue
                for field in ("title", "headline", "topic", "summary", "comment", "review", "source", "selected_source"):
                    add(payload.get(field), 180)
        if not parts:
            add(motive, 140)
        return _single_line("；".join(parts), 760)

    def _external_share_text_mentions_source(self, text: str, source_text: str) -> bool:
        message = _single_line(text, 260).lower()
        source = _single_line(source_text, 760).lower()
        if not message or not source:
            return True
        source_markers = (
            "看到", "刷到", "看见", "标题", "视频", "新闻", "文章", "资料", "这条", "这个", "那条",
            "b站", "bili", "链接", "分享", "外面", "报道", "内容",
        )
        anchor_tokens = self._external_share_anchor_tokens(source)
        for token in anchor_tokens:
            if token and token in message:
                return True
        # If the source itself has no concrete title/topic tokens, do not over-block
        # a clearly source-shaped short share.
        if not anchor_tokens and any(marker in message for marker in source_markers):
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
        for pattern in (
            r"(?:标题|摘要重点|话题|headline|topic)[:：]\s*([^；。\n\r|｜]{2,90})",
            r"^([^；。\n\r]{4,90})",
        ):
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
            return ""
        title = title.strip(" ，。！？；：、,.!?;:|｜")
        if not title:
            return ""
        return _single_line(f"外部分享偏离来源时的参考意图：低压提到刚看到的内容“{title}”，像日常分享一样递给对方。", 180)

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

    @staticmethod
    def _proactive_review_hard_block_reason(reason: str) -> bool:
        text = str(reason or "")
        if not text:
            return False
        markers = (
            "隐私", "泄露", "越界", "风险", "危险", "敏感", "违规", "骚扰", "威胁",
            "其他私聊", "朋友私聊", "混入", "承诺工具", "承诺发图", "承诺语音", "承诺查询",
        )
        return any(marker in text for marker in markers)

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
        if local.get("decision") in {"drop", "defer"} and (strength != "lenient" or bool(local.get("hard"))):
            return local
        if local.get("decision") == "rewrite" and str(local.get("reference_text") or "").strip():
            rewritten_reference = await self._rewrite_reference_reply_with_persona(
                str(local.get("reference_text") or ""),
                scene=_single_line(f"主动消息兜底改写；reason={reason or 'check_in'}；action={action or 'message'}", 180),
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
            if rewritten_reference:
                local = dict(local)
                local["text"] = rewritten_reference
                local.pop("reference_text", None)
            else:
                return {
                    "decision": "defer",
                    "reason": _single_line(local.get("reason"), 80) or "兜底参考意图未能按人格改写",
                    "delay_minutes": 60,
                    "hard": True,
                }
        if local.get("decision") == "rewrite" and bool(local.get("hard")):
            return local
        if not bool(getattr(self, "enable_response_self_review", True)):
            return local
        mode = str(getattr(self, "response_review_mode", "severe_only") or "severe_only").strip().lower()
        if mode not in {"local_only", "severe_only", "full"}:
            mode = "severe_only"
        if mode == "local_only":
            return local
        history = await self._recent_private_conversation_for_proactive_review(user, limit=10)
        intent_hint = self._format_proactive_generation_intent_hint(
            user,
            reason=reason,
            action=action,
            motive=motive,
            action_context=review_context,
        )
        prompt = f"""
你是主动私聊发送前的价值复核模型。请判断候选主动消息是否适合现在进入对话。

只输出 JSON 对象，不要解释。

可选 decision：
- send：适合现在发，text 留空。
- rewrite：内容值得发，但需要改得更自然更短，text 填改写后的正文。
- defer：现在时机不合适，但稍后可发，delay_minutes 填 30 到 90。
- drop：没有发送价值、像回复空气、像承接刚刚不存在的新消息、太泛泛、容易打扰，直接取消。

判断原则：
- 先读最近私聊记录。候选消息放进去必须像自然聊天，不像突然插入的系统主动。
- 主动消息不是回复用户刚发来的话；如果它写成“好呀/刚看到/你问/我帮你查”等回复口吻，应 drop 或 rewrite。
- 如果只是“想你了/来看看你/忙不忙/吃了吗/辛苦了”且没有具体由头，通常 defer 或 drop。
- 不要把内部动机、犹豫过程或发送理由写进正文，例如“本来想跟你说”“怕说太早”“绕了一圈还是来找你”；这类内容应 rewrite 成具体片段，或 drop。
- 如果最近用户刚刚在聊正事或刚聊完，倾向 defer；如果候选本身没价值，drop。
- 候选消息必须贴合“本轮主动来源”和“内在约束”：分享型要有分享落点，关心型要低压，续接型要有真实来源，虚由头要短。
- 如果候选只是在汇报系统动作、发送状态或工具执行结果，而不是角色真正要说出口的聊天内容，decision=drop。
- rewrite 只能轻改写，不能新增事实，不能添加工具、转述、查询、发图等承诺。

【最近私聊记录】
{history or "（无可用历史）"}

【本轮主动来源】
reason={reason or "check_in"}；action={action or "message"}；topic={_single_line(topic, 80) or "无"}；motive={_single_line(motive, 120) or "无"}；summary={_single_line(action_summary, 80) or "无"}

【内在约束】
{intent_hint or "（无额外约束）"}

【候选主动消息】
{text}

请输出：
{{"decision":"send|rewrite|defer|drop","text":"","delay_minutes":45,"reason":"一句很短的原因"}}
""".strip()
        started = time.perf_counter()
        raw = await self._llm_call(
            prompt,
            max_tokens=220,
            provider_id=self._task_provider(self.response_review_provider_id, self.mai_style_provider_id),
            task="proactive_send_review",
        )
        payload = self._parse_json_object(raw)
        if not isinstance(payload, dict):
            return local
        decision = str(payload.get("decision") or "").strip().lower()
        if decision not in {"send", "rewrite", "defer", "drop"}:
            return local
        reviewed_text = str(payload.get("text") or "").strip()
        delay_minutes = _safe_int(payload.get("delay_minutes"), 45, 30, 90)
        note = _single_line(payload.get("reason"), 120)
        original_decision = decision
        if decision == "rewrite":
            if not reviewed_text:
                return local
            reviewed_text = self._sanitize_action_boundaries(
                self._sanitize_proactive_text(reviewed_text),
                reason=reason,
                action=action,
                action_context=review_context,
                has_real_image=bool(image_path) or "真实图片文件：" in review_context or "图片路径：" in review_context,
            )
            reviewed_text = self._normalize_proactive_sentence_flow(reviewed_text)
            if not reviewed_text:
                return local
            if self._framework_agent_meta_summary_leak(reviewed_text):
                return {
                    "decision": "drop",
                    "text": "",
                    "delay_minutes": 90,
                    "reason": "主动候选疑似工具循环/内部发送摘要泄漏",
                }
            if len(reviewed_text) > max(len(text) + 60, 240):
                return local
            if re.search(r"(提示词|系统|JSON|模型|工具调用|主动消息|无文字|附加组件)", reviewed_text, re.IGNORECASE):
                return local
        if decision in {"defer", "drop"} and strength != "strict":
            hard_block = self._proactive_review_hard_block_reason(note)
            if decision == "drop" and strength == "balanced" and not hard_block:
                decision = "defer"
                delay_minutes = max(45, delay_minutes)
                note = _single_line(f"{note or '复核偏保守'}；已按标准强度降级为延后", 120)
            elif strength == "lenient" and not hard_block:
                if reviewed_text:
                    decision = "rewrite"
                else:
                    decision = "send"
                note = _single_line(f"{note or '复核偏保守'}；已按宽松强度放行", 120)
            elif decision == "drop" and not hard_block:
                decision = "defer"
                note = _single_line(f"{note or '复核偏保守'}；已降级为延后", 120)
        if decision == "send" and str(local.get("decision") or "") == "rewrite" and str(local.get("text") or "").strip():
            reviewed_text = str(local.get("text") or "").strip()
            decision = "rewrite"
            note = _single_line(note or local.get("reason") or "本地轻改写后放行", 120)
        final_text = reviewed_text if decision == "rewrite" and reviewed_text else text
        if decision in {"send", "rewrite"} and self._framework_agent_meta_summary_leak(final_text):
            decision = "drop"
            reviewed_text = ""
            delay_minutes = 90
            note = "主动候选疑似工具循环/内部发送摘要泄漏"
        logger.info(
            "[PrivateCompanion] 主动消息发送前价值复核: decision=%s raw=%s strength=%s delay=%s elapsed=%dms reason=%s",
            decision,
            original_decision,
            strength,
            delay_minutes if decision == "defer" else "-",
            int((time.perf_counter() - started) * 1000),
            note or "无",
        )
        return {
            "decision": decision,
            "text": reviewed_text,
            "delay_minutes": delay_minutes,
            "reason": note or "主动发送前价值复核",
        }

    def _pop_framework_captured_send_payload(
        self,
        umo: str,
    ) -> tuple[str, str, list[Any]]:
        captured = self._framework_captured_send_cache.pop(str(umo or ""), [])
        if not captured:
            return "", "", []
        selected_call = None
        for call in captured:
            if any(
                isinstance(item, dict) and str(item.get("type") or "").strip().lower() == "image"
                for item in call.messages
            ):
                selected_call = call
                break
        if selected_call is None:
            selected_call = captured[0]
        text_parts: list[str] = []
        image_path = ""
        extra_components: list[Any] = []
        for item in selected_call.messages:
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
        return "\n".join(part for part in text_parts if part).strip(), image_path, extra_components

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
            kept.append(line)
        cleaned = "\n".join(kept).strip().strip('"').strip("'")
        cleaned = cleaned.replace("（图片已送达）", "").replace("(图片已送达)", "")
        tts_cleaner = getattr(self, "_clean_tool_plain_text_tts_markup", None)
        if callable(tts_cleaner):
            cleaned = tts_cleaner(cleaned)
        else:
            cleaned = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
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
        if not self.enable_llm_proactive_message:
            return ""
        raw_text = await self._generate_proactive_message_via_framework(
            user,
            name,
            reason,
            action_context=action_context,
            action=action,
            motive=motive,
        )
        if not raw_text:
            return ""
        cleaned = self._sanitize_action_boundaries(
            self._sanitize_proactive_text(raw_text),
            reason=reason,
            action=action,
            action_context=action_context,
            has_real_image="真实图片文件：" in action_context or "图片路径：" in action_context,
        )
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
        cleaned = self._visible_text_without_tts_reading(cleaned, limit=1000)
        relay_claim_note = self._unexecuted_relay_claim_reason(cleaned, action_context=action_context)
        if relay_claim_note:
            logger.info(
                "[PrivateCompanion] 主动消息含未执行转述承诺,已丢弃: reason=%s text=%s",
                relay_claim_note,
                _single_line(cleaned, 120),
            )
            return ""
        if self._should_drop_vague_generic_proactive(user, reason=reason, action=action, action_context=action_context, text=cleaned):
            return ""
        if self._should_drop_misstaged_proactive_text(cleaned, reason=reason, action=action):
            return ""
        reviewed = await self._review_proactive_message_stance(
            user,
            cleaned,
            reason=reason,
            action=action,
            action_context=action_context,
            motive=motive,
        )
        if not reviewed:
            return ""
        reviewed = self._trim_proactive_status_inventory(reviewed)
        reviewed = self._trim_performative_self_state_tail(reviewed)
        return self._normalize_proactive_sentence_flow(reviewed)

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
        flags = self._proactive_reply_air_flags(
            cleaned,
            reason=reason,
            action=action,
            action_context=action_context,
        )
        if not flags:
            return cleaned
        if not bool(getattr(self, "enable_response_self_review", True)):
            logger.info(
                "[PrivateCompanion] 主动消息疑似回复空气,自检关闭已丢弃: flags=%s text=%s",
                ",".join(flags),
                _single_line(cleaned, 120),
            )
            return ""
        mode = str(getattr(self, "response_review_mode", "severe_only") or "severe_only").strip().lower()
        if mode not in {"local_only", "severe_only", "full"}:
            mode = "severe_only"
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
{_single_line(motive or user.get("planned_proactive_motive"), 160)}
{_single_line(user.get("planned_proactive_topic"), 120)}

【动作上下文】
{_single_line(action_context, 260) or "（无）"}

【内在约束】
{intent_hint or "（无额外约束）"}

要求：
- 只输出要发送的正文
- 不要把“用户”“对方”“收信人”这类内部称呼写进正文；需要称呼时用自然的“你”或对方昵称
- 不要写成“好呀/确实/我也觉得/刚看到/你刚刚问我/你来找我了”
- 不要把历史消息当成当前正在发生的对话
- 没有真实图片或工具结果时，只写聊天内容本身，不描述动作结果
- 如果原文只是过程状态或工具结果，请不要改写成另一种状态汇报；改不成自然聊天就输出空文本
- 改写后仍要贴合内在约束里的候选语义；不能把分享型改成泛泛问候，也不能把低压关心改成追问
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
        cleaned = _single_line(text, 240)
        if not cleaned or action != "message":
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
        if repeated_opening:
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
            return {
                "success": "真实图片" in context and "图片路径：" in context,
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
        name = self._normalize_external_ability_name(ability_name)
        runtime = self._external_proactive_abilities.get(name)
        if not isinstance(runtime, dict) or not callable(runtime.get("executor")):
            return {"success": False, "context": "external：外部主动能力未注册或不可用", "extra_components": [], "summary": "外部能力不可用", "effective_action": "message"}
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
            self._note_external_ability_execution(name, success=False, status=f"执行失败: {exc}")
            return {"success": False, "context": f"external:{name}：执行失败", "extra_components": [], "summary": "外部能力失败", "effective_action": f"external:{name}"}
        payload = result if isinstance(result, dict) else {"text": str(result or "")}
        success = bool(payload.get("ok", payload.get("success", True)))
        text = _single_line(payload.get("text"), 500)
        context = str(payload.get("context") or payload.get("summary") or text or "").strip()
        image_path = str(payload.get("image_path") or "").strip()
        extra_components = list(payload.get("extra_components") or []) if isinstance(payload.get("extra_components"), list) else []
        if image_path and os.path.exists(image_path):
            extra_components.extend(self._build_outbound_chain("", image_path))
        memory = _single_line(payload.get("memory"), 500)
        if memory:
            user.setdefault("external_proactive_memory", [])
            memories = user.get("external_proactive_memory")
            if not isinstance(memories, list):
                memories = []
                user["external_proactive_memory"] = memories
            memories.append({"name": name, "ts": _now_ts(), "memory": memory})
            del memories[:-12]
        self._note_external_ability_execution(name, success=success, status=_single_line(payload.get("status") or context, 120), summary=_single_line(payload.get("summary") or text, 120))
        return {
            "success": success,
            "context": f"external:{name}：{context or '外部能力已执行'}",
            "extra_components": extra_components,
            "summary": _single_line(payload.get("summary") or runtime.get("label") or name, 60),
            "effective_action": f"external:{name}",
        }

    def _note_external_ability_execution(self, name: str, *, success: bool, status: str = "", summary: str = "") -> None:
        try:
            store = self._external_ability_store()
            item = store.get(name) if isinstance(store.get(name), dict) else {"name": name}
            item["last_executed_ts"] = _now_ts()
            item["last_status"] = status
            item["last_summary"] = summary
            item["success_count"] = _safe_int(item.get("success_count"), 0, 0) + (1 if success else 0)
            item["failure_count"] = _safe_int(item.get("failure_count"), 0, 0) + (0 if success else 1)
            store[name] = item
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
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return "poke：未找到可用的 QQ 客户端"
        user_id = str(user.get("user_id") or "").strip()
        if not user_id.isdigit():
            return "poke：目标 QQ 号无效"
        group_id = self._extract_group_id_from_umo(str(user.get("umo") or ""))
        try:
            from data.plugins.astrbot_plugin_pokepro.core.send_poke import PokeSender
        except Exception:
            try:
                from astrbot_plugin_pokepro.core.send_poke import PokeSender
            except Exception as e:
                return f"poke：pokepro 插件不可用,{e}"
        try:
            poke_count = max(1, int(explicit_count)) if explicit_count else self._choose_poke_repeat_count(user, reason)
            async with self._data_lock:
                current = self._get_user(user_id)
                current["poke_echo_suppress_until"] = _now_ts() + max(6.0, poke_count * 1.6 + 3.0)
                self._save_data_sync()
            for index in range(poke_count):
                await PokeSender.poke_func(client=client, user_id=user_id, group_id=group_id)
                if index + 1 < poke_count:
                    await asyncio.sleep(random.uniform(0.35, 0.9))
            if poke_count <= 1:
                return f"poke：已轻轻戳了 {name} 一下\n主动原因：{reason}"
            return f"poke：已轻轻连着戳了 {name} {poke_count} 下\n主动原因：{reason}"
        except Exception as e:
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
        if not self._poke_available() or self._effective_user_poke_daily_limit(user) <= 0:
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

    async def _call_onebot_action(self, client: Any, action: str, **params: Any) -> bool:
        ok, _ = await self._call_onebot_action_with_error(client, action, **params)
        return ok

    async def _call_onebot_action_with_error(self, client: Any, action: str, **params: Any) -> tuple[bool, str]:
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
                if hasattr(result, "__await__"):
                    result = await result
                if self._onebot_action_result_ok(result):
                    return True, ""
                last_error = f"{attr} 返回失败: {_single_line(result, 180)}"
            except TypeError:
                try:
                    result = func(action, params)
                    if hasattr(result, "__await__"):
                        result = await result
                    if self._onebot_action_result_ok(result):
                        return True, ""
                    last_error = f"{attr} 返回失败: {_single_line(result, 180)}"
                except Exception as exc:
                    last_error = self._format_send_exception(exc)
                    continue
            except Exception as exc:
                last_error = self._format_send_exception(exc)
                continue
        func = getattr(client, action, None)
        if callable(func):
            try:
                result = func(**params)
                if hasattr(result, "__await__"):
                    result = await result
                if self._onebot_action_result_ok(result):
                    return True, ""
                return False, f"{action} 返回失败: {_single_line(result, 180)}"
            except Exception as exc:
                return False, self._format_send_exception(exc)
        return False, last_error or f"OneBot 客户端不支持动作 {action}"

    def _input_status_user_id_from_umo(self, umo: str) -> str:
        if not umo or ":FriendMessage:" not in str(umo):
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
        variants = (
            {"status": status, "ext_status": ext_status, "battery_status": 0},
            {"status": status, "ext_status": ext_status},
            {"status": status},
            {"status_id": status},
            {"mode": str(mode or "online")},
        )
        for params in variants:
            if await self._call_onebot_action(client, "set_online_status", **params):
                return True, label
        return False, f"平台不支持 set_online_status：{label}"

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
        if not provider_settings.get("enable", False):
            return [], "当前会话未启用 TTS"
        try:
            tts_provider = self.context.get_using_tts_provider(target)
        except Exception as e:
            return [], f"读取 TTS provider 失败：{e}"
        if not tts_provider:
            return [], "当前会话没有可用的 TTS provider"
        if "<tts>" in spoken_text and "</tts>" in spoken_text:
            components, note = await self._build_tts_modify_components(
                spoken_text,
                tts_provider,
                provider_settings,
                config,
            )
            if components:
                return components, note
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
            return "photo_text：朋友关系不使用主动生图或复用主人图片,不能假装已经拍照"
        load_defer_note = self._photo_text_load_defer_note("photo_text", force_refresh=True)
        if load_defer_note:
            return f"photo_text：{load_defer_note},不能假装已经拍照"
        if not self._photo_text_available(user):
            return "photo_text：今日发图额度已用完或生图后端不可用,不能假装已经拍照"
        if not self._photo_text_available():
            return "photo_text：当前没有可用的生图后端,不能假装已经拍照"

        scene = await self._build_photo_scene_prompt(user, name, reason)
        workflow_kind = scene.get("kind", "text2img")
        session_key = str(user.get("umo") or user.get("user_id") or name)
        backend_name, image_path, workflow_note = await self._generate_photo_image(
            workflow_kind=workflow_kind,
            prompt_text=scene["prompt"],
            session_key=session_key,
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
        return (
            f"photo_text：已通过 {backend_name} 生成真实图片\n"
            f"图片类型：{workflow_kind}\n"
            f"后端：{backend_name}\n"
            f"图片路径：{image_path}\n"
            f"画面：{scene['caption']}\n"
            f"生图提示：{_single_line(scene['prompt'], 240)}"
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
            if existing.get("date") == today:
                return dict(existing)
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
        prompt_text = self._build_daily_outfit_photo_prompt(
            diary if isinstance(diary, dict) else {},
            memory_context=memory_context,
        )
        backend_name, image_path, note = await self._generate_photo_image(
            workflow_kind="selfie",
            prompt_text=prompt_text,
            session_key="daily_outfit",
            image_size="1024x1024",
            allow_daily_outfit_reference=False,
        )
        if image_path:
            return await self._record_daily_outfit_photo_result(
                today,
                image_path,
                "",
                backend=backend_name,
                prompt=prompt_text,
                note=note,
            )
        return await self._record_daily_outfit_photo_result(
            today,
            "",
            _single_line(note, 220) or "生图失败",
            backend=backend_name,
            prompt=prompt_text,
            note=note,
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
    ) -> dict[str, Any]:
        item = {
            "date": _single_line(date_key, 20),
            "path": _single_line(image_path, 300),
            "error": _single_line(error, 240),
            "backend": _single_line(backend, 80),
            "prompt": _single_line(prompt, 500),
            "note": _single_line(note, 220),
            "generated_at": _now_ts(),
        }
        async with self._data_lock:
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

    def _build_daily_outfit_photo_prompt(self, diary: dict[str, Any], *, memory_context: str = "") -> str:
        persona = self._daily_outfit_role_appearance_text()
        style_name, style_instruction = self._get_photo_style_instruction()
        state = self.data.get("daily_state", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        weather = self._format_weather_for_prompt() if callable(getattr(self, "_format_weather_for_prompt", None)) else ""
        schedule_hint = self._daily_outfit_schedule_text()
        diary_hint = _single_line(
            (diary or {}).get("summary")
            or (diary or {}).get("share_seed")
            or (diary or {}).get("body"),
            180,
        )
        custom = _single_line(getattr(self, "daily_outfit_photo_prompt", ""), 220)
        parts = [
            "在今日日程生成后,生成一张角色今天的自拍式穿搭照片,用于私人陪伴插件拓展页左上角封面。",
            f"角色外貌与身份线索：{persona or '参考当前人格设定与人设参考图,保持角色外观稳定。'}",
            f"今日状态：{self._format_state_for_prompt(state if isinstance(state, dict) else {})}",
            f"天气与时令：{weather or '按当前日期和日常生活氛围处理。'}",
            f"今日日程穿搭依据：{schedule_hint or '没有明确日程时,按普通居家日常处理。'}",
            "穿搭决策：优先服从日程里明确出现的衣服、外套、校服、睡衣、发夹、饰品、出门、上课、运动、雨天或居家线索；如果一天有多段活动,选最能代表白天主要生活/外出安排的一套,不要只按深夜睡前状态生成睡衣。",
            f"补充生活余味：{diary_hint or '暂无额外余味,主要服从今日日程、天气和状态来决定当天搭配。'}",
            (
                "我会牢牢记住你 穿搭连续性参考："
                f"{_single_line(memory_context, 700)}。使用方式：优先保持今日穿搭、近期自拍、用户常问衣服颜色和当前地点的一致性；不要在最终图片中出现文字说明。"
                if memory_context
                else ""
            ),
            "画面要求：角色本人必须露脸,头部、脸、发型和表情完整入镜；可以是半身、七分身或全身,但绝不能裁掉头、遮住脸或只拍衣服。衣服搭配要清楚,姿态自然,像今天顺手拍下来的 selfie outfit photo。",
            "尺寸与安全区：按 1:1 方形头像/封面构图生成,角色的脸、头发、肩颈和主要穿搭都要落在画面中央安全区内,四周留出足够边距,缩进方形卡片时也不能裁脸或裁身体主体。",
            "构图要求：主体居中或略偏中,脸部位于画面上半部但要和画面上边缘保持明显留白,肩颈和上半身自然可见；不要极近自拍、手臂占据前景、无头构图、背影、低头挡脸、书本/手机/头发遮脸、只拍身体局部、只拍裙子或外套。",
            "不要出现用户、其他人物、文字、水印、Logo、奇怪手指、暴露或色情内容。负面构图：cropped head, headless, faceless, face hidden, extreme close-up, arm in foreground, body only, outfit only, back view。",
            f"风格：{style_name}；{style_instruction}",
        ]
        if custom:
            parts.append(f"额外穿搭偏好：{custom}")
        return _single_line(" ".join(parts), 1400)

    def _daily_outfit_role_appearance_text(self) -> str:
        persona = str(getattr(self, "schedule_persona_prompt", "") or "")
        recognition = str(getattr(self, "private_image_self_recognition_hint", "") or "")
        labels = {
            "姓名",
            "种族",
            "性别",
            "识别点",
            "外貌",
            "主要识别点",
            "发型发色",
            "发色",
            "发型",
            "瞳色",
            "眼睛",
            "服饰风格",
            "服装",
            "衣着",
            "职业/身份",
        }
        parts: list[str] = []
        for line in persona.replace("\r", "\n").split("\n"):
            text = line.strip()
            if not text or ("：" not in text and ":" not in text):
                continue
            label, value = text.split("：", 1) if "：" in text else text.split(":", 1)
            label = label.strip()
            value = _single_line(value, 160)
            if label in labels and value:
                parts.append(f"{label}：{value}")
        if recognition:
            parts.append(f"补充识别线索：{_single_line(recognition, 180)}")
        seen: set[str] = set()
        unique = []
        for item in parts:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return _single_line("；".join(unique), 620)

    def _choose_photo_workflow_name(self, kind: str) -> str:
        normalized = str(kind or "").strip().lower()
        if normalized in {"selfie", "portrait", "自拍", "人像"}:
            return self.comfyui_selfie_workflow_name or self.comfyui_text2img_workflow_name
        return self.comfyui_text2img_workflow_name or self.comfyui_selfie_workflow_name

    def _photo_generation_trace_id(self, session_key: str, workflow_kind: str) -> str:
        seed = f"{session_key}|{workflow_kind}|{_now_ts()}|{uuid.uuid4().hex[:8]}"
        return hashlib.sha1(seed.encode("utf-8", "ignore")).hexdigest()[:10]

    def _photo_generation_file_detail(self, image_path: str) -> str:
        path_text = _single_line(image_path, 260)
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
            f"backup_base={backup_base or '-'}"
        )

    def _record_recent_photo_generation(
        self,
        *,
        trace_id: str,
        session_key: str,
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
    ) -> None:
        try:
            item = {
                "ts": _now_ts(),
                "trace": _single_line(trace_id, 40),
                "session": _single_line(session_key, 100),
                "kind": _single_line(workflow_kind, 30),
                "backend": _single_line(backend, 80),
                "ok": bool(ok),
                "prompt": _single_line(prompt_text, 900),
                "path": _single_line(image_path, 260),
                "note": _single_line(note, 240),
                "reference": bool(reference_image_path),
                "reference_path": _single_line(reference_image_path, 260),
                "image_size": _single_line(image_size, 40),
                "elapsed_ms": int(max(0, elapsed_ms or 0)),
                "presets": [_single_line(name, 40) for name in (presets or []) if _single_line(name, 40)][:6],
            }
            raw = self.data.setdefault("recent_photo_generations", [])
            if not isinstance(raw, list):
                raw = []
                self.data["recent_photo_generations"] = raw
            raw.insert(0, item)
            del raw[12:]
            self._save_data_sync()
        except Exception as exc:
            logger.debug("[PrivateCompanion] 记录最近生图提示词失败: %s", _single_line(exc, 120))

    def _apply_photo_generation_fixed_prompt(self, prompt_text: str) -> str:
        prompt = str(prompt_text or "").strip()
        fixed = _single_line(getattr(self, "photo_generation_fixed_prompt", ""), 500)
        if not fixed:
            return prompt
        if fixed in prompt:
            return _single_line(prompt, 1800)
        return _single_line(f"{prompt}\n\n【固定生图提示词】\n{fixed}".strip(), 1800)

    def _photo_generation_selfie_schedule_scene_hint(self) -> str:
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
        return _single_line("；".join(parts), 460)

    def _apply_photo_generation_selfie_schedule_scene_prompt(
        self,
        prompt_text: str,
        workflow_kind: str,
        *,
        allow_daily_outfit_reference: bool = True,
    ) -> tuple[str, str]:
        prompt = str(prompt_text or "").strip()
        normalized = str(workflow_kind or "").strip().lower()
        if normalized not in {"selfie", "portrait", "自拍", "人像"}:
            return _single_line(prompt, 1800), ""
        if not allow_daily_outfit_reference:
            return _single_line(prompt, 1800), ""
        if "【自拍当前场景约束】" in prompt:
            return _single_line(prompt, 1800), ""
        if any(token in prompt for token in ("基于用户提供或引用的参考图进行改图", "保留用户未要求修改的主体", "只改变明确要求的部分")):
            return _single_line(prompt, 1800), ""

        scene_hint = self._photo_generation_selfie_schedule_scene_hint()
        if not scene_hint:
            return _single_line(prompt, 1800), ""
        outfit_reference = self._daily_outfit_reference_image_path()
        outfit_hint = (
            "本次自拍参考图会优先使用今日穿搭图,画面中的衣服、配饰和整体穿搭要沿用今日穿搭。"
            if outfit_reference
            else "如已有今日穿搭或人设参考图,画面中的衣服、配饰和角色外观要保持一致。"
        )
        extra = (
            "【自拍当前场景约束】"
            f"{outfit_hint}"
            f"自拍必须发生在当前日程对应地点/场景中：{scene_hint}。"
            "不要生成棚拍、无关房间、默认背景或与当前日程冲突的地点；除非当前日程确实在家里/房间,否则不要默认室内卧室。"
        )
        return _single_line(f"{prompt}\n\n{extra}".strip(), 1800), scene_hint

    def _builtin_photo_generation_scene_presets(self) -> dict[str, str]:
        return {
            "角色自拍": (
                "角色本人出镜的自然自拍；必须露脸,脸、头发、表情和肩颈完整清晰；"
                "像手机随手拍,构图有生活感,避免裁头、遮脸、背影、只拍身体或手臂占据前景。"
            ),
            "COS自拍": (
                "角色穿着用户要求的 cosplay / 主题服装自拍；保留角色自身脸部、发色、瞳色和主要识别点；"
                "服装主题明确但不过度暴露,画面像线下活动或房间试装自拍。"
            ),
            "镜前穿搭": (
                "镜前或半身穿搭照片；衣服、外套、饰品和配色要清楚,角色脸部完整可见；"
                "主体在方形安全区内,不要只拍衣服局部。"
            ),
            "头像特写": (
                "适合头像的脸部特写；头发、眼睛和表情清晰,背景简洁,脸部居中且四周留白；"
                "不要加文字、水印或复杂道具。"
            ),
            "房间日常": (
                "室内生活碎片照片；桌面、小物、书本、杯子、窗边或床边等具体物件自然入镜；"
                "不要堆太多主体,只保留一个清楚的生活切口。"
            ),
            "可拍画面": (
                "像随手拍给熟人的一张具体画面；主体明确,光线自然,不写成空泛风景或天气播报；"
                "画面里不要出现隐私屏幕、真实个人信息、无关文字和水印。"
            ),
            "表情包场景": (
                "聊天可用的单张表情包或贴纸感画面；情绪明确、构图简洁、角色可爱夸张但仍可识别；"
                "如需文字,只使用用户明确要求的短中文。"
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

    def _photo_generation_preset_names_for_prompt(self, workflow_kind: str, prompt_text: str) -> list[str]:
        kind = str(workflow_kind or "").strip().lower()
        text = _single_line(prompt_text, 1200).lower()
        names: list[str] = []
        if kind in {"selfie", "portrait", "自拍", "人像"}:
            if re.search(r"\bcos\b|cosplay|角色扮演|扮成|神灯|制服|女仆|巫女|魔法少女", text, flags=re.I):
                names.append("COS自拍")
            elif any(token in text for token in ("穿搭", "衣服", "外套", "校服", "裙", "镜前")):
                names.append("镜前穿搭")
            elif any(token in text for token in ("头像", "特写", "大头")):
                names.append("头像特写")
            else:
                names.append("角色自拍")
        else:
            if any(token in text for token in ("表情包", "贴纸", "sticker", "meme")):
                names.append("表情包场景")
            elif any(token in text for token in ("房间", "桌", "书", "杯", "床", "窗边", "室内")):
                names.append("房间日常")
            else:
                names.append("可拍画面")
        return list(dict.fromkeys(names))[:3]

    def _apply_photo_generation_scene_presets(self, prompt_text: str, workflow_kind: str) -> tuple[str, list[str]]:
        prompt = str(prompt_text or "").strip()
        presets = self._photo_generation_scene_presets()
        names = [name for name in self._photo_generation_preset_names_for_prompt(workflow_kind, prompt) if name in presets]
        if not names:
            return _single_line(prompt, 1800), []
        blocks = []
        for name in names:
            content = _single_line(presets.get(name), 900)
            if content and content not in prompt:
                blocks.append(f"{name}: {content}")
        if not blocks:
            return _single_line(prompt, 1800), names
        merged = f"{prompt}\n\n【生图场景预设】\n" + "\n".join(blocks)
        return _single_line(merged, 1800), names

    async def _generate_photo_image(
        self,
        *,
        workflow_kind: str,
        prompt_text: str,
        session_key: str,
        reference_image_path: str = "",
        image_size: str = "",
        allow_daily_outfit_reference: bool = True,
    ) -> tuple[str, str, str]:
        started = time.time()
        trace_id = self._photo_generation_trace_id(session_key, workflow_kind)
        prompt_text, selfie_scene_hint = self._apply_photo_generation_selfie_schedule_scene_prompt(
            prompt_text,
            workflow_kind,
            allow_daily_outfit_reference=allow_daily_outfit_reference,
        )
        prompt_text, preset_names = self._apply_photo_generation_scene_presets(prompt_text, workflow_kind)
        prompt_text = self._apply_photo_generation_fixed_prompt(prompt_text)
        reference_image_path = _single_line(reference_image_path, 260)
        if not reference_image_path:
            reference_image_path = await self._photo_persona_reference_image_for_kind_async(
                workflow_kind,
                allow_daily_outfit=allow_daily_outfit_reference,
            )
        if selfie_scene_hint:
            logger.info(
                "[PrivateCompanion] 自拍生图已加入当前日程地点约束: trace=%s session=%s hint=%s reference=%s",
                trace_id,
                _single_line(session_key, 80),
                _single_line(selfie_scene_hint, 180),
                bool(reference_image_path),
            )
        preferred = self.photo_generation_backend
        try:
            reference_exists = bool(reference_image_path and Path(reference_image_path).exists())
        except (OSError, ValueError):
            reference_exists = False
        logger.info(
            "[PrivateCompanion] 生图开始: trace=%s session=%s kind=%s presets=%s prompt_chars=%s prompt_preview_chars=%s prompt_preview=%s reference=%s reference_exists=%s image_size=%s %s",
            trace_id,
            _single_line(session_key, 80),
            _single_line(workflow_kind, 30),
            ",".join(preset_names) or "-",
            len(str(prompt_text or "")),
            180,
            _single_line(prompt_text, 180),
            bool(reference_image_path),
            reference_exists,
            _single_line(image_size, 40) or "-",
            self._photo_generation_backend_config_summary(),
        )

        def finish(backend: str, image_path: str, note: str) -> tuple[str, str, str]:
            elapsed_ms = int((time.time() - started) * 1000)
            ok = bool(image_path)
            self._record_recent_photo_generation(
                trace_id=trace_id,
                session_key=session_key,
                workflow_kind=workflow_kind,
                backend=backend,
                ok=ok,
                prompt_text=prompt_text,
                image_path=image_path,
                note=note,
                reference_image_path=reference_image_path,
                image_size=image_size,
                elapsed_ms=elapsed_ms,
                presets=preset_names,
            )
            logger.info(
                "[PrivateCompanion] 生图结束: trace=%s ok=%s backend=%s elapsed=%sms note=%s %s",
                trace_id,
                ok,
                _single_line(backend, 60),
                elapsed_ms,
                _single_line(note, 220),
                self._photo_generation_file_detail(image_path),
            )
            return backend, image_path, note

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
            )
            return finish("ComfyUI", image_path, note)
        if preferred == "sdgen":
            if not self._sdgen_photo_available():
                return finish("SDGen", "", "SDGen 插件不可用或未配置")
            busy_state = self._local_photo_generation_busy_state(force_refresh=True)
            if busy_state:
                return finish("SDGen", "", f"电脑高负荷,已跳过本地生图（{busy_state.get('reason') or '负载偏高'}）")
            image_path, note = await self._run_sdgen_photo_generation(
                prompt_text,
                session_key=session_key,
            )
            return finish("SDGen", image_path, note)
        if preferred == "external":
            if not self._external_photo_available():
                return finish("在线图片 API", "", "在线图片 API 后端不可用或未配置")
            image_path, note = await self._run_external_photo_generation(
                prompt_text,
                session_key=session_key,
                reference_image_path=reference_image_path,
                image_size=image_size,
            )
            return finish("在线图片 API", image_path, note)
        external_note = ""
        if self._external_photo_available():
            image_path, note = await self._run_external_photo_generation(
                prompt_text,
                session_key=session_key,
                reference_image_path=reference_image_path,
                image_size=image_size,
            )
            if image_path:
                return finish("在线图片 API", image_path, note)
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
                    )
                    if image_path:
                        return finish("ComfyUI", image_path, note)
                    comfyui_note = note
                    logger.info("[PrivateCompanion] 生图后端回退: trace=%s backend=comfyui note=%s", trace_id, _single_line(note, 180))
                else:
                    comfyui_note = f"未配置 {workflow_kind} 对应的 ComfyUI 工作流"
                    logger.info("[PrivateCompanion] 生图后端跳过: trace=%s backend=comfyui note=%s", trace_id, _single_line(comfyui_note, 180))
        else:
            comfyui_note = "ComfyUI 后端不可用或未配置"
            logger.info("[PrivateCompanion] 生图后端跳过: trace=%s backend=comfyui note=%s", trace_id, comfyui_note)
        if self._sdgen_photo_available():
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
        return finish("在线图片 API", "", f"在线图片 API 失败：{external_note}；ComfyUI 失败：{comfyui_note}；SDGen 失败：{sdgen_note}")

    async def _build_photo_scene_prompt(
        self, user: dict[str, Any], name: str, reason: str
    ) -> dict[str, str]:
        persona = self._get_default_persona_prompt()
        state = self.data.get("daily_state", {})
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        style_name, style_instruction = self._get_photo_style_instruction()
        topic_hint = _single_line(user.get("planned_proactive_topic"), 60)
        motive_hint = _single_line(user.get("planned_proactive_motive"), 120)
        prompt = f"""
请根据 AstrBot 默认人格和主动原因,生成一张要通过生图后端制作的“社交媒体随手拍/自拍/生活碎片图”提示词。

【人格】
{persona}

【收信人】
{name}

【当前拟人状态】
{self._format_state_for_prompt(state if isinstance(state, dict) else {})}

【当前日程背景】
{self._format_plan_item_for_prompt(current_item)}

【这次想分享的画面钩子】
话题：{topic_hint or '（未指定）'}
那一刻的小动机：{motive_hint or '（未指定）'}

【内容选择菜单】
{self._format_content_choice_options_for_prompt("photo_text")}

【生图风格】
{style_name}
风格要求：{style_instruction}

主动原因：{reason}

输出 JSON：
{{
  "kind": "selfie 或 text2img；自拍/人像用 selfie,其他随手拍用 text2img",
  "prompt": "给生图后端的中文生图提示词,包含主体、场景、光线、构图、情绪；不要写聊天口吻",
  "caption": "图片完成后可转述给最终私聊模型的一句话画面描述"
}}

要求：
1. 画面必须符合当前时间、日程和人格,不要把身份设定里没有的场景、职业、服装或外观细节写进去。
2. 图片不要总是天气或窗外。先从“内容选择菜单”里单选一个视觉锚点；当前日程、话题和人格只用于筛选主体和调整画面气质,不要把多个主体拼在一张图里。
3. 可以是路上风景、桌面小物、随手自拍、偶遇小动物等,但不要每次都是自拍；没有明确自拍动机时优先 text2img。
4. `prompt` 里要明确体现上面的风格要求。
5. 不要包含 NSFW、隐私信息、用户真实电脑画面。
6. 如果“话题”已经很具体,就优先把那个具体视觉主体画出来；如果话题很抽象,从菜单里另选一个适合拍照的具体画面。不要退回成泛泛的天气图、手部动作或普通记录照。
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
        else:
            kind = "text2img"
            image_prompt = _single_line(text, 600)
            caption = image_prompt
        if kind not in {"selfie", "portrait", "自拍", "人像", "text2img", "scene", "photo", "风景"}:
            kind = "text2img"
        if kind in {"portrait", "自拍", "人像"}:
            kind = "selfie"
        if kind in {"scene", "photo", "风景"}:
            kind = "text2img"
        if not image_prompt:
            current = self._format_plan_item_for_prompt(current_item)
            image_prompt = (
                f"社交媒体随手拍,当前背景：{current},温柔自然的生活感,"
                f"清晰构图,柔和光线,{style_instruction}"
            )
        if not caption:
            caption = "今天看到一个很适合拍下来分享的小画面。"
        return {"kind": kind, "prompt": image_prompt, "caption": caption}

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

    def _photo_persona_reference_image_path(self) -> str:
        raw = _single_line(getattr(self, "photo_persona_reference_image_path", ""), 1000)
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

    def _daily_outfit_reference_image_path(self) -> str:
        item = self.data.get("daily_outfit_photo") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(item, dict):
            return ""
        if _single_line(item.get("date"), 20) != _today_key():
            return ""
        raw = _single_line(item.get("path"), 500).strip().strip('"').strip("'")
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
        if str(workflow_kind or "").strip().lower() not in {"selfie", "portrait", "自拍", "人像"}:
            return ""
        if allow_daily_outfit:
            outfit_path = self._daily_outfit_reference_image_path()
            if outfit_path:
                return outfit_path
        return self._photo_persona_reference_image_path()

    async def _photo_persona_reference_image_path_async(self) -> str:
        local_path = self._photo_persona_reference_image_path()
        if local_path:
            return local_path
        raw = _single_line(getattr(self, "photo_persona_reference_image_path", ""), 1000).strip().strip('"').strip("'")
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
                setter(stable_path)
            except Exception as exc:
                logger.info("[PrivateCompanion] 配置页人设参考图 URL 已下载但回写失败: %s path=%s", _single_line(exc, 120), _single_line(stable_path, 160))
        logger.info("[PrivateCompanion] 配置页人设参考图 URL 已缓存为本地文件: path=%s", _single_line(stable_path, 160))
        return stable_path

    async def _photo_persona_reference_image_for_kind_async(
        self,
        workflow_kind: str,
        *,
        allow_daily_outfit: bool = True,
    ) -> str:
        if str(workflow_kind or "").strip().lower() not in {"selfie", "portrait", "自拍", "人像"}:
            return ""
        if allow_daily_outfit:
            outfit_path = self._daily_outfit_reference_image_path()
            if outfit_path:
                logger.info(
                    "[PrivateCompanion] 自拍参考图优先使用今日穿搭图: path=%s",
                    _single_line(outfit_path, 160),
                )
                return outfit_path
        return await self._photo_persona_reference_image_path_async()

    async def _run_comfyui_photo_workflow(
        self,
        workflow_name: str,
        prompt_text: str,
        session_key: str,
        reference_image_path: str = "",
        image_size: str = "",
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
            reference_image_path = _single_line(reference_image_path, 260)
            use_reference_image = bool(reference_image_path and os.path.exists(reference_image_path))
            workflow_file = ""
            text_count = 1
            image_count = 0
            if use_reference_image:
                workflow_file = module.find_workflow_file(
                    workflow_name,
                    1,
                    1,
                    0,
                    workflow_dir,
                )
                image_count = 1 if workflow_file else 0
                if not workflow_file:
                    workflow_file, text_count, image_count = self._find_photo_workflow_with_text_count(
                        module,
                        workflow_dir,
                        workflow_name,
                        image_count=1,
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
                need = "images=1 或 images=0" if use_reference_image else "images=0"
                return "", f"未找到匹配工作流 {workflow_name}（需要 texts>=1、{need}、videos=0）"
            logger.info(
                "[PrivateCompanion] ComfyUI 生图提交准备: workflow=%s file=%s text_count=%s image_count=%s reference=%s wait=%ss prompt_preview=%s",
                _single_line(workflow_name, 80),
                _single_line(workflow_file, 160),
                text_count,
                image_count,
                use_reference_image,
                self.comfyui_photo_wait_seconds,
                _single_line(prompt_text, 180),
            )
            debug = bool(
                getattr(config, "debug_mode", False)
                if not isinstance(config, dict)
                else config.get("debug_mode", False)
            )
            workflow = module.ComfyUIWorkflow(server_ip, client_id)
            workflow.load_workflow_api(workflow_file)
            input_images = [reference_image_path] if image_count > 0 and use_reference_image else []
            reference_note = ""
            if input_images:
                reference_note = "；已使用本地人设参考图"
            elif use_reference_image:
                reference_note = "；自拍工作流未接收图片输入,已回退纯文生图"
            prompt_id = await workflow.submit_only(
                input_images,
                [prompt_text] * max(1, text_count),
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

    def _resolved_external_image_api_platform(self) -> str:
        normalizer = getattr(self, "_normalize_external_image_api_platform", None)
        configured = getattr(self, "external_image_api_platform", "auto")
        platform = normalizer(configured) if callable(normalizer) else str(configured or "auto").strip().lower()
        if platform in {"openai", "bailian"}:
            return platform
        base = self._normalized_external_image_api_base_url(platform=platform).lower()
        model = str(getattr(self, "external_image_api_model", "") or "").strip().lower()
        if any(token in base for token in ("dashscope.aliyuncs.com", "/services/aigc/", "/api/v1/tasks", "model-studio")):
            return "bailian"
        if model.startswith(("qwen-image", "wanx", "wan-", "wan2.", "wan2x", "wan")):
            return "bailian"
        return "openai"

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
            suffix = "/compatible-mode/v1" if resolved_platform == "openai" else "/api/v1"
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
        if resolved == "openai":
            return "OpenAI 兼容"
        return "在线图片 API"

    def _external_image_endpoint(self, endpoint_type: str = "generations") -> str:
        base = self._normalized_external_image_api_base_url(platform="openai").rstrip("/")
        if not base:
            return ""
        endpoint_type = str(endpoint_type or "generations").strip().lower()
        target = "edits" if endpoint_type == "edits" else "generations"
        if base.endswith(f"/images/{target}"):
            return base
        if base.endswith("/images/generations") or base.endswith("/images/edits"):
            return re.sub(r"/images/(?:generations|edits)$", f"/images/{target}", base)
        return f"{base}/images/{target}"

    def _sanitize_external_image_size(self, override: str = "") -> str:
        raw = str(override or self.external_image_api_size or "1024x1024").strip().lower()
        if re.fullmatch(r"\d{2,5}x\d{2,5}", raw):
            return raw
        return "1024x1024"

    def _sanitize_bailian_image_size(self, override: str = "") -> str:
        size = self._sanitize_external_image_size(override)
        return size.replace("x", "*")

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
            image_value = image_value.get("url") or image_value.get("image") or image_value.get("image_url") or ""
        raw = str(image_value or "").strip()
        if not raw:
            return "", "图片结果为空"
        if raw.lower().startswith("data:image") and "," in raw:
            header, encoded = raw.split(",", 1)
            mime_match = re.match(r"data:(image/[a-z0-9.+-]+);base64$", header.strip(), flags=re.I)
            ext = ".png"
            if mime_match:
                mime_type = mime_match.group(1).lower()
                if "jpeg" in mime_type or "jpg" in mime_type:
                    ext = ".jpg"
                elif "webp" in mime_type:
                    ext = ".webp"
            image_bytes = base64.b64decode(encoded)
            saved = await self._save_external_generated_image(image_bytes, session_key=session_key, ext=ext)
            return saved, success_note if saved else "保存在线图片失败"
        if re.fullmatch(r"[A-Za-z0-9+/=\\s]+", raw) and len(raw) > 128:
            try:
                image_bytes = base64.b64decode(raw)
                saved = await self._save_external_generated_image(image_bytes, session_key=session_key, ext=".png")
                if saved:
                    return saved, success_note
            except Exception:
                pass
        saved, note = await self._download_external_image_url(raw, session_key=session_key)
        return saved, success_note if saved else note

    def _external_image_model_misconfiguration_note(self) -> str:
        model = _single_line(getattr(self, "external_image_api_model", ""), 120)
        if not model:
            return "未配置在线图片模型"
        lowered = model.lower()
        platform = self._resolved_external_image_api_platform()
        image_tokens = ("image", "img", "dall", "flux", "sd", "stable-diffusion", "midjourney", "mj", "kolors", "wanx", "wan")
        text_model_prefixes = ("gpt-", "claude", "gemini", "deepseek", "qwen", "glm", "moonshot", "kimi", "yi-", "doubao")
        if platform == "bailian" and (lowered.startswith("qwen-image") or lowered.startswith("wan")):
            return ""
        if lowered.startswith(text_model_prefixes) and not any(token in lowered for token in image_tokens):
            return f"在线图片模型填成了文本/聊天模型：{model}。请改成该平台的图片模型名，例如支持 /images/generations 或 /images/edits 的模型。"
        return ""

    def _external_image_api_error_note(self, status: int, text: str, *, reference: bool = False) -> str:
        raw = _single_line(text, 260)
        lowered = raw.lower()
        if "requires an image model" in lowered or "image model" in lowered and "got" in lowered:
            return self._external_image_model_misconfiguration_note() or (
                f"在线图片模型不支持 images 接口。当前模型：{_single_line(getattr(self, 'external_image_api_model', ''), 80) or '-'}，请改成图片模型。"
            )
        prefix = "参考图接口" if reference else ""
        return f"{prefix}HTTP {status}: {_single_line(text, 180)}"

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

    async def _save_external_generated_image(
        self,
        image_bytes: bytes,
        *,
        session_key: str,
        ext: str = ".png",
    ) -> str:
        if not image_bytes:
            return ""
        safe_ext = ext if ext.startswith(".") else f".{ext}"
        if safe_ext.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            safe_ext = ".png"
        out_dir = Path(self.data_dir) / "generated_photos"
        out_dir.mkdir(parents=True, exist_ok=True)
        session_part = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(session_key or "private_companion"))[:60] or "private_companion"
        file_path = out_dir / f"{session_part}_{self._environment_now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{safe_ext}"
        await asyncio.to_thread(file_path.write_bytes, image_bytes)
        logger.info(
            "[PrivateCompanion] 生图文件已保存: session=%s path=%s bytes=%s",
            _single_line(session_key, 80),
            _single_line(str(file_path), 180),
            len(image_bytes),
        )
        return str(file_path)

    async def _download_external_image_url(self, url: str, *, session_key: str) -> tuple[str, str]:
        target = str(url or "").strip()
        if not target:
            return "", "图片地址为空"
        try:
            import aiohttp

            headers, header_note = self._external_image_download_headers(target)
            timeout = aiohttp.ClientTimeout(total=float(self.external_image_api_timeout_seconds))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(target, headers=headers) as response:
                    if response.status >= 400:
                        logger.info(
                            "[PrivateCompanion] 下载在线生图结果失败: status=%s headers=%s url=%s",
                            response.status,
                            header_note,
                            _single_line(target, 180),
                        )
                        return "", f"下载图片失败：HTTP {response.status}"
                    content = await response.read()
                    content_type = str(response.headers.get("Content-Type", "") or "").lower()
                    logger.info(
                        "[PrivateCompanion] 下载在线生图结果完成: status=%s content_type=%s bytes=%s url=%s",
                        response.status,
                        _single_line(content_type, 80),
                        len(content),
                        _single_line(target, 180),
                    )
            ext = ".png"
            if "jpeg" in content_type or "jpg" in content_type:
                ext = ".jpg"
            elif "webp" in content_type:
                ext = ".webp"
            path = await self._save_external_generated_image(content, session_key=session_key, ext=ext)
            return path, "ok" if path else "保存下载图片失败"
        except asyncio.TimeoutError:
            note = self._external_image_timeout_note(download=True)
            logger.info("[PrivateCompanion] 下载在线生图结果超时: url=%s note=%s", _single_line(target, 180), note)
            return "", note
        except Exception as e:
            logger.warning(f"[PrivateCompanion] 下载在线生图结果失败: {e}", exc_info=True)
            return "", str(e)

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
                if reference_value:
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
                _single_line(endpoint, 160),
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
                        _single_line(text, 220),
                    )
                    if response.status >= 400:
                        return "", f"百炼多模态生图 HTTP {response.status}: {_single_line(text, 180)}"
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
                _single_line(endpoint, 160),
                _single_line(self.external_image_api_model, 80),
                _safe_int(getattr(self, "external_image_api_timeout_seconds", 180), 180, 1),
            )
            return "", note
        except Exception as e:
            logger.warning(f"[PrivateCompanion] 百炼多模态生图失败: {e}", exc_info=True)
            return "", str(e)

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
                        _single_line(endpoint, 160),
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
                            _single_line(text, 220),
                        )
                        if response.status >= 400:
                            submit_note = f"百炼异步生图 HTTP {response.status}: {_single_line(text, 180)}"
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
                            _single_line(text, 220),
                        )
                        if response.status >= 400:
                            return "", f"百炼任务查询 HTTP {response.status}: {_single_line(text, 180)}"
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
            logger.warning(f"[PrivateCompanion] 百炼异步生图失败: {e}", exc_info=True)
            return "", str(e)

    async def _run_external_photo_generation(
        self,
        prompt_text: str,
        *,
        session_key: str,
        reference_image_path: str = "",
        image_size: str = "",
    ) -> tuple[str, str]:
        lock = getattr(self, "_external_image_api_runtime_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._external_image_api_runtime_lock = lock
        async with lock:
            return await self._run_external_photo_generation_serial(
                prompt_text,
                session_key=session_key,
                reference_image_path=reference_image_path,
                image_size=image_size,
            )

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

    async def _run_external_photo_generation_serial(
        self,
        prompt_text: str,
        *,
        session_key: str,
        reference_image_path: str = "",
        image_size: str = "",
    ) -> tuple[str, str]:
        primary_configured = bool(
            getattr(self, "external_image_api_base_url", "")
            and getattr(self, "external_image_api_key", "")
            and getattr(self, "external_image_api_model", "")
        )
        primary_note = ""
        if primary_configured:
            image_path, primary_note = await self._run_external_photo_generation_once(
                prompt_text,
                session_key=session_key,
                reference_image_path=reference_image_path,
                image_size=image_size,
            )
            if image_path:
                return image_path, primary_note
        else:
            primary_note = "主在线图片 API 未配置完整"

        if self._backup_external_photo_available():
            logger.info(
                "[PrivateCompanion] 主在线图片 API 未成功,尝试备选在线图片 API: primary_note=%s backup_model=%s",
                _single_line(primary_note, 180),
                _single_line(getattr(self, "backup_external_image_api_model", ""), 80),
            )
            image_path, backup_note = await self._run_external_photo_generation_with_backup(
                prompt_text,
                session_key=session_key,
                reference_image_path=reference_image_path,
                image_size=image_size,
            )
            if image_path:
                return image_path, f"{backup_note}；已使用备选在线图片 API"
            return "", f"{primary_note}；备选在线图片 API 失败：{_single_line(backup_note, 220)}"
        return "", primary_note

    async def _run_external_photo_generation_with_backup(
        self,
        prompt_text: str,
        *,
        session_key: str,
        reference_image_path: str = "",
        image_size: str = "",
    ) -> tuple[str, str]:
        keys = (
            "external_image_api_platform",
            "external_image_api_base_url",
            "external_image_api_key",
            "external_image_api_model",
            "external_image_api_size",
            "external_image_api_timeout_seconds",
            "external_image_api_custom_headers",
        )
        old_values = {key: getattr(self, key, None) for key in keys}
        backup_values = {
            "external_image_api_platform": getattr(self, "backup_external_image_api_platform", "auto"),
            "external_image_api_base_url": getattr(self, "backup_external_image_api_base_url", ""),
            "external_image_api_key": getattr(self, "backup_external_image_api_key", ""),
            "external_image_api_model": getattr(self, "backup_external_image_api_model", ""),
            "external_image_api_size": getattr(self, "backup_external_image_api_size", "1024x1024"),
            "external_image_api_timeout_seconds": getattr(self, "backup_external_image_api_timeout_seconds", 180),
            "external_image_api_custom_headers": getattr(self, "backup_external_image_api_custom_headers", ""),
        }
        try:
            for key, value in backup_values.items():
                setattr(self, key, value)
            return await self._run_external_photo_generation_once(
                prompt_text,
                session_key=session_key,
                reference_image_path=reference_image_path,
                image_size=image_size,
            )
        finally:
            for key, value in old_values.items():
                setattr(self, key, value)

    async def _run_external_photo_generation_once(
        self,
        prompt_text: str,
        *,
        session_key: str,
        reference_image_path: str = "",
        image_size: str = "",
    ) -> tuple[str, str]:
        platform = self._resolved_external_image_api_platform()
        endpoint = self._external_image_endpoint()
        if platform == "bailian":
            endpoint = self._bailian_multimodal_endpoint() or self._bailian_api_root()
        if not endpoint:
            return "", f"未配置{self._external_image_platform_label(platform)} API 地址"
        if not self.external_image_api_key:
            return "", f"未配置{self._external_image_platform_label(platform)} API Key"
        if not self.external_image_api_model:
            return "", f"未配置{self._external_image_platform_label(platform)}图片模型"
        model_note = self._external_image_model_misconfiguration_note()
        if model_note:
            return "", model_note
        reference_image_path = _single_line(reference_image_path, 260)
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
        if reference_image_path and os.path.exists(reference_image_path):
            logger.info(
                "[PrivateCompanion] 在线图片 API 尝试参考图接口: endpoint=%s model=%s size=%s reference=%s prompt_preview=%s",
                _single_line(self._external_image_endpoint("edits"), 160),
                _single_line(self.external_image_api_model, 80),
                self._sanitize_external_image_size(image_size),
                _single_line(reference_image_path, 160),
                _single_line(prompt_text, 180),
            )
            image_path, note = await self._run_external_photo_edit_generation(
                prompt_text,
                session_key=session_key,
                reference_image_path=reference_image_path,
                image_size=image_size,
            )
            if image_path:
                return image_path, note
            logger.info(
                "[PrivateCompanion] 在线图片 API 参考图生图失败,回退纯文生图: %s",
                _single_line(note, 180),
            )
            if "超时" in str(note or ""):
                return "", note
        try:
            import aiohttp

            payload = {
                "model": self.external_image_api_model,
                "prompt": prompt_text,
                "size": self._sanitize_external_image_size(image_size),
            }
            logger.info(
                "[PrivateCompanion] 在线图片 API 生图提交: endpoint=%s model=%s size=%s prompt_chars=%s prompt_preview_chars=%s prompt_preview=%s",
                _single_line(endpoint, 160),
                _single_line(self.external_image_api_model, 80),
                payload["size"],
                len(str(prompt_text or "")),
                180,
                _single_line(prompt_text, 180),
            )
            headers = {
                "Authorization": f"Bearer {self.external_image_api_key}",
                "Content-Type": "application/json",
            }
            headers.update(self._external_image_custom_headers())
            timeout = aiohttp.ClientTimeout(total=float(self.external_image_api_timeout_seconds))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(endpoint, headers=headers, json=payload) as response:
                    text = await response.text()
                    logger.info(
                        "[PrivateCompanion] 在线图片 API 生图响应: status=%s chars=%s preview=%s",
                        response.status,
                        len(text or ""),
                        _single_line(text, 220),
                    )
                    if response.status >= 400:
                        return "", self._external_image_api_error_note(response.status, text)
            data = self._extract_json_payload(text) if text else {}
            if not isinstance(data, dict):
                return "", "在线图片 API 返回格式无效"
            first = None
            items = data.get("data")
            if isinstance(items, list) and items:
                first = items[0]
            if not isinstance(first, dict):
                return "", "在线图片 API 未返回图片数据"
            b64 = str(first.get("b64_json") or "").strip()
            if b64:
                image_bytes = base64.b64decode(b64)
                path = await self._save_external_generated_image(
                    image_bytes,
                    session_key=session_key,
                    ext=".png",
                )
                logger.info(
                    "[PrivateCompanion] 在线图片 API 生图返回 base64: bytes=%s path=%s",
                    len(image_bytes),
                    _single_line(path, 160),
                )
                return path, "ok" if path else "保存在线图片失败"
            image_url = str(first.get("url") or "").strip()
            if image_url:
                logger.info("[PrivateCompanion] 在线图片 API 生图返回 URL: %s", _single_line(image_url, 180))
                return await self._download_external_image_url(
                    image_url,
                    session_key=session_key,
                )
            return "", "在线图片 API 未返回 url 或 b64_json"
        except asyncio.TimeoutError:
            note = self._external_image_timeout_note()
            logger.info(
                "[PrivateCompanion] 在线图片 API 生图超时: endpoint=%s model=%s timeout=%ss",
                _single_line(endpoint, 160),
                _single_line(self.external_image_api_model, 80),
                _safe_int(getattr(self, "external_image_api_timeout_seconds", 180), 180, 1),
            )
            return "", note
        except Exception as e:
            logger.warning(f"[PrivateCompanion] 在线图片 API 生图失败: {e}", exc_info=True)
            return "", str(e)

    async def _run_external_photo_edit_generation(
        self,
        prompt_text: str,
        *,
        session_key: str,
        reference_image_path: str,
        image_size: str = "",
    ) -> tuple[str, str]:
        endpoint = self._external_image_endpoint("edits")
        if not endpoint:
            return "", "未配置在线图片 API 地址"
        path = Path(str(reference_image_path or ""))
        if not path.exists() or not path.is_file():
            return "", "参考图路径不可用"
        suffix = path.suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            return "", "参考图格式不支持"
        try:
            import aiohttp

            image_bytes = await asyncio.to_thread(path.read_bytes)
            form = aiohttp.FormData()
            form.add_field("model", self.external_image_api_model)
            form.add_field("prompt", prompt_text)
            form.add_field("size", self._sanitize_external_image_size(image_size))
            content_type = "image/png"
            if suffix in {".jpg", ".jpeg"}:
                content_type = "image/jpeg"
            elif suffix == ".webp":
                content_type = "image/webp"
            form.add_field(
                "image",
                image_bytes,
                filename=f"persona_reference{suffix}",
                content_type=content_type,
            )
            headers = {"Authorization": f"Bearer {self.external_image_api_key}"}
            headers.update(self._external_image_custom_headers())
            timeout = aiohttp.ClientTimeout(total=float(self.external_image_api_timeout_seconds))
            logger.info(
                "[PrivateCompanion] 在线图片 API 参考图提交: endpoint=%s model=%s size=%s reference=%s bytes=%s prompt_preview=%s",
                _single_line(endpoint, 160),
                _single_line(self.external_image_api_model, 80),
                self._sanitize_external_image_size(image_size),
                _single_line(str(path), 160),
                len(image_bytes),
                _single_line(prompt_text, 180),
            )
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(endpoint, headers=headers, data=form) as response:
                    text = await response.text()
                    logger.info(
                        "[PrivateCompanion] 在线图片 API 参考图响应: status=%s chars=%s preview=%s",
                        response.status,
                        len(text or ""),
                        _single_line(text, 220),
                    )
                    if response.status >= 400:
                        return "", self._external_image_api_error_note(response.status, text, reference=True)
            data = self._extract_json_payload(text) if text else {}
            if not isinstance(data, dict):
                return "", "参考图接口返回格式无效"
            first = None
            items = data.get("data")
            if isinstance(items, list) and items:
                first = items[0]
            if not isinstance(first, dict):
                return "", "参考图接口未返回图片数据"
            b64 = str(first.get("b64_json") or "").strip()
            if b64:
                generated_bytes = base64.b64decode(b64)
                saved = await self._save_external_generated_image(
                    generated_bytes,
                    session_key=session_key,
                    ext=".png",
                )
                logger.info(
                    "[PrivateCompanion] 在线图片 API 参考图返回 base64: bytes=%s path=%s",
                    len(generated_bytes),
                    _single_line(saved, 160),
                )
                return saved, "ok；已使用本地人设参考图" if saved else "保存在线参考图生图失败"
            image_url = str(first.get("url") or "").strip()
            if image_url:
                logger.info("[PrivateCompanion] 在线图片 API 参考图返回 URL: %s", _single_line(image_url, 180))
                saved, note = await self._download_external_image_url(
                    image_url,
                    session_key=session_key,
                )
                return saved, ("ok；已使用本地人设参考图" if saved else note)
            return "", "参考图接口未返回 url 或 b64_json"
        except asyncio.TimeoutError:
            note = self._external_image_timeout_note(reference=True)
            logger.info(
                "[PrivateCompanion] 在线图片 API 参考图生图超时: endpoint=%s model=%s timeout=%ss reference=%s",
                _single_line(endpoint, 160),
                _single_line(self.external_image_api_model, 80),
                _safe_int(getattr(self, "external_image_api_timeout_seconds", 180), 180, 1),
                _single_line(str(path), 160),
            )
            return "", note
        except Exception as e:
            logger.warning(f"[PrivateCompanion] 在线图片 API 参考图生图失败: {e}", exc_info=True)
            return "", str(e)

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
        result = event.get_result()
        processed = getattr(result, "chain", None) if result is not None else None
        processed_chain = list(processed or []) if processed is not None else chain
        return self._filter_decorated_proactive_chain(chain, processed_chain)

    def _proactive_plain_segment_component(
        self,
        text: str,
        *,
        full_text: str = "",
        index: int = 0,
        count: int = 1,
    ) -> Plain:
        comp = Plain(text)
        clean_full = _single_line(full_text, 1200)
        if clean_full:
            try:
                setattr(comp, "_private_companion_proactive_full_text", clean_full)
                setattr(comp, "_private_companion_proactive_segment_index", max(0, int(index)))
                setattr(comp, "_private_companion_proactive_segment_count", max(1, int(count)))
            except Exception:
                pass
        return comp

    def _filter_decorated_proactive_chain(self, original_chain: list[Any], processed_chain: list[Any]) -> list[Any]:
        if not processed_chain:
            return original_chain

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
        return original_chain if removed_any else processed_chain

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
        }:
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
        has_media = bool(_single_line(image_path, 260)) or bool(extra_components)
        if not raw:
            if has_media:
                return {"decision": "send", "text": "", "reason": ""}
            return {"decision": "drop", "text": "", "reason": "主动行为没有产出可发送内容", "hard": True}

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
            return False
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
                if hasattr(result, "__await__"):
                    result = await result
                if self._onebot_forward_action_result_ok(result):
                    return True
            except TypeError:
                try:
                    result = func(action, params)
                    if hasattr(result, "__await__"):
                        result = await result
                    if self._onebot_forward_action_result_ok(result):
                        return True
                except Exception:
                    continue
            except Exception:
                continue
        func = getattr(client, action, None)
        if callable(func):
            try:
                result = func(**params)
                if hasattr(result, "__await__"):
                    result = await result
                return self._onebot_forward_action_result_ok(result)
            except Exception:
                return False
        return False

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
        raw_segments = [str(item or "").strip() for item in segments if str(item or "").strip()]
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
            return True
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
        ok, error = await self._call_onebot_action_with_error(client, action, **params)
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

    async def _send_chain_components(self, umo: str, chain: list[Any]) -> None:
        hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(chain))
        if hit:
            logger.warning(
                "[PrivateCompanion] 主动待发送消息命中违禁词，已拦截发送: umo=%s word=%s",
                umo,
                _single_line(hit, 40),
            )
            return
        processed_chain = await self._trigger_proactive_decorating_hooks(umo, chain)
        if not processed_chain:
            return
        tts_chain_guard = getattr(self, "_sanitize_outbound_tts_chain_without_event", None)
        if callable(tts_chain_guard):
            processed_chain = await tts_chain_guard(processed_chain, umo=umo)
            if not processed_chain:
                return
        hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(processed_chain))
        if hit:
            logger.warning(
                "[PrivateCompanion] 主动装饰后消息命中违禁词，已拦截发送: umo=%s word=%s",
                umo,
                _single_line(hit, 40),
            )
            return
        session = self._parse_message_session(umo)
        platform = self._get_platform_for_session(session) if session else None
        precise_error: Exception | None = None
        if self.enable_precise_platform_send and session and platform:
            try:
                status = getattr(platform, "status", None)
                if status is not None and status != PlatformStatus.RUNNING:
                    logger.warning("[PrivateCompanion] 目标平台未运行,跳过主动发送: %s", umo)
                    return
                session_obj = self._session_for_platform(session, platform)
                await platform.send_by_session(session_obj, MessageChain(processed_chain))
                return
            except Exception as e:
                precise_error = e
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
                return
            logger.warning(
                "[PrivateCompanion] 主动核心发送未找到匹配平台,尝试 OneBot 原生兜底: target=%s",
                self._describe_send_target(umo, session, platform),
            )
        except Exception as e:
            core_error = e
            target = self._describe_send_target(umo, session, platform)
            precise_text = self._format_send_exception(precise_error) or "未尝试或未失败"
            fallback_text = self._format_send_exception(e)
            logger.warning(
                "[PrivateCompanion] 主动核心发送失败: target=%s precise_error=%s fallback_error=%s",
                target,
                precise_text,
                fallback_text,
            )
        direct_ok, direct_error = await self._send_chain_components_via_onebot_direct(umo, session, processed_chain)
        if direct_ok:
            return
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
    ) -> None:
        trigger_message_id = _single_line(quote_message_id, 120)
        if self._contains_inline_image_tag(text):
            image_path = ""
            extra_components = []
        if text:
            await self._maybe_send_input_status(umo, text)
        segments = self._split_proactive_text(
            text,
            image_path="",
            extra_components=None,
            disable_segmenting=disable_segmenting or not self._segmented_scope_allows_umo(umo),
        )
        if len(segments) <= 1:
            outbound_text = segments[0] if segments else ""
            if quote_message_id and self._quote_skip_reason_for_short_reply(outbound_text):
                quote_message_id = ""
            if outbound_text:
                recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
                if recalled_message_id:
                    logger.info("[PrivateCompanion] 触发消息已撤回，取消主动文本发送: umo=%s message_id=%s", umo, recalled_message_id)
                    return
                await self._send_chain_components(
                    umo,
                    self._with_optional_reply(
                        [self._proactive_plain_segment_component(outbound_text, full_text=text, index=0, count=1)],
                        quote_message_id,
                    ),
                )
                quote_message_id = ""
        else:
            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
            if recalled_message_id:
                logger.info("[PrivateCompanion] 触发消息已撤回，取消主动合并分段发送: umo=%s message_id=%s", umo, recalled_message_id)
                return
            if await self._send_segmented_proactive_forward_message(umo, segments, source="proactive_media_text"):
                quote_message_id = ""
            else:
                for index, segment in enumerate(segments):
                    if index == 0 and quote_message_id and self._quote_skip_reason_for_short_reply(segment):
                        quote_message_id = ""
                    recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
                    if recalled_message_id:
                        logger.info("[PrivateCompanion] 触发消息已撤回，停止主动分段发送: umo=%s message_id=%s index=%s", umo, recalled_message_id, index + 1)
                        return
                    segment_comp = self._proactive_plain_segment_component(segment, full_text=text, index=index, count=len(segments))
                    chain = self._with_optional_reply([segment_comp], quote_message_id) if index == 0 else [segment_comp]
                    await self._send_chain_components(umo, chain)
                    quote_message_id = ""
                    if index < len(segments) - 1:
                        await asyncio.sleep(await self._calc_segmented_proactive_interval(segment))
        has_media = bool((extra_components or []) or (image_path and os.path.exists(image_path)))
        if has_media:
            logger.info(
                "[PrivateCompanion] 主动媒体发送: text_segments=%s image=%s extra_components=%s",
                len(segments),
                bool(image_path and os.path.exists(image_path)),
                len(extra_components or []),
            )
            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
            if recalled_message_id:
                logger.info("[PrivateCompanion] 触发消息已撤回，取消主动媒体发送: umo=%s message_id=%s", umo, recalled_message_id)
                return
            media_chain = self._build_outbound_chain("", image_path, extra_components=extra_components)
            media_chain = self._with_optional_reply(media_chain, quote_message_id)
            await self._send_chain_components(umo, media_chain)

    async def _send_proactive_message_chain(
        self,
        umo: str,
        text: str,
        image_path: str = "",
        *,
        extra_components: list[Any] | None = None,
        quote_message_id: str = "",
        disable_segmenting: bool = False,
    ) -> None:
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
        if image_path or extra_components:
            await self._send_media_proactive_chain(
                umo,
                text,
                image_path,
                extra_components=extra_components,
                quote_message_id=quote_message_id,
                disable_segmenting=disable_segmenting,
            )
            return
        if text:
            await self._maybe_send_input_status(umo, text)
        segments = self._split_proactive_text(
            text,
            image_path="",
            extra_components=None,
            disable_segmenting=disable_segmenting or not self._segmented_scope_allows_umo(umo),
        )
        if len(segments) <= 1:
            outbound_text = segments[0] if segments else text
            if quote_message_id and self._quote_skip_reason_for_short_reply(outbound_text):
                quote_message_id = ""
            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
            if recalled_message_id:
                logger.info("[PrivateCompanion] 触发消息已撤回，取消主动消息发送: umo=%s message_id=%s", umo, recalled_message_id)
                return
            await self._send_chain_components(
                umo,
                self._with_optional_reply(
                    [
                        self._proactive_plain_segment_component(outbound_text, full_text=text, index=0, count=1)
                    ],
                    quote_message_id,
                ),
            )
            return
        recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
        if recalled_message_id:
            logger.info("[PrivateCompanion] 触发消息已撤回，取消主动合并分段发送: umo=%s message_id=%s", umo, recalled_message_id)
            return
        if await self._send_segmented_proactive_forward_message(umo, segments, source="proactive_text"):
            return
        for index, segment in enumerate(segments):
            if index == 0 and quote_message_id and self._quote_skip_reason_for_short_reply(segment):
                quote_message_id = ""
            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
            if recalled_message_id:
                logger.info("[PrivateCompanion] 触发消息已撤回，停止主动消息分段发送: umo=%s message_id=%s index=%s", umo, recalled_message_id, index + 1)
                return
            segment_comp = self._proactive_plain_segment_component(segment, full_text=text, index=index, count=len(segments))
            chain = self._with_optional_reply([segment_comp], quote_message_id) if index == 0 else [segment_comp]
            await self._send_chain_components(umo, chain)
            quote_message_id = ""
            if index < len(segments) - 1:
                await asyncio.sleep(await self._calc_segmented_proactive_interval(segment))

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

    def _build_proactive_archive_assistant_text(
        self,
        *,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        action_summary: str = "",
    ) -> str:
        original_is_receipt = self._is_proactive_delivery_receipt_text(text)
        message_text = self._visible_text_without_tts_reading(text, limit=1000)
        attachment_notes: list[str] = []
        if image_path:
            attachment_notes.append("随消息发送了一张图片")
        if extra_components:
            tts_notes: list[str] = []
            note_builder = getattr(self, "_tts_component_log_note", None)
            for comp in extra_components:
                if isinstance(comp, Record) and callable(note_builder):
                    note = _single_line(note_builder(comp), 220)
                    if note:
                        tts_notes.append(note)
            if tts_notes:
                attachment_notes.extend(tts_notes[:3])
            else:
                attachment_notes.append(f"随消息发送了 {len(extra_components)} 个附加消息组件")
        if attachment_notes:
            suffix = "（" + ",".join(attachment_notes) + "）"
            message_text = f"{message_text}{suffix}" if message_text else suffix
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
    ) -> None:
        umo = str(user.get("umo") or "").strip()
        if not umo or not assistant_response:
            return
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
                    return
                if attempt > 0:
                    logger.info("[PrivateCompanion] 主动消息写入 AstrBot 会话历史成功: %s retry=%s", umo, attempt)
                else:
                    logger.info("[PrivateCompanion] 已将主动消息写入 AstrBot 会话历史: %s", umo)
                return
            except Exception as e:
                text = str(e or "").lower()
                if ("database is locked" in text or "sqlite3.operationalerror" in text) and attempt < 3:
                    await asyncio.sleep(0.25 * (attempt + 1))
                    continue
                logger.warning("[PrivateCompanion] 主动消息写入会话历史失败: %s", e)
                return

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
            ("突然想起你", ""),
            ("刚好想到你", ""),
            ("我刚刚想到你了。", ""),
            ("我刚刚想到你了", ""),
            ("没什么大不了的,就是", ""),
            ("没什么大道理,就是", ""),
            ("免得你又忘了我", "怕你忙过头"),
            ("最近忙不忙？", ""),
            ("最近忙不忙", ""),
            ("数据有意思吗？", ""),
            ("数据有意思吗", ""),
            ("发现你好像在忙。", "看你还没从那边抬头。"),
            ("发现你好像在忙", "看你还没从那边抬头"),
            ("辛苦啦。", "别太累。"),
            ("辛苦啦", "别太累"),
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
            return reason, "当前时段的首次早间开口；贴近早晨片段，不写模板早安。"

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
