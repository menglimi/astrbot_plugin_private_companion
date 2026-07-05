# -*- coding: utf-8 -*-
"""
WorldbookMixin — 从 main.py 重新拆分出的Worldbook/关系网管理
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

class WorldbookMixin:
    """Worldbook/关系网管理"""

    def _worldbook_config_path_candidates(self) -> list[Path]:
        data_root = Path(get_astrbot_data_path())
        configured = [
            Path(part.strip())
            for part in re.split(r"[\n,;；]+", str(self.worldbook_config_paths or ""))
            if part.strip()
        ]
        defaults = [
            data_root / "config" / "plugin_upload_astrbot_plugin_worldbook_config.json",
            data_root / "config" / "astrbot_plugin_worldbook_config.json",
        ]
        paths: list[Path] = []
        for path in [*configured, *defaults]:
            resolved = path if path.is_absolute() else data_root / path
            if resolved not in paths:
                paths.append(resolved)
        return paths

    @staticmethod
    def _worldbook_entry_template(raw: dict[str, Any]) -> str:
        return str(raw.get("__template_key") or raw.get("template") or "").strip().lower()

    def _normalize_worldbook_entry(self, raw: dict[str, Any], *, source: str) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        name = _single_line(raw.get("name"), 80)
        content = str(raw.get("content") or "").strip()
        if not name and not content:
            return None
        scope = (
            [_single_line(item, 40).strip() for item in raw.get("scope", []) if _single_line(item, 40).strip()]
            if isinstance(raw.get("scope"), list)
            else []
        )
        aliases = (
            [_single_line(item, 40).strip() for item in raw.get("aliases", []) if _single_line(item, 40).strip()]
            if isinstance(raw.get("aliases"), list)
            else []
        )
        gender = _single_line(raw.get("gender") or raw.get("性别"), 40)
        return {
            "template": self._worldbook_entry_template(raw),
            "name": name,
            "gender": gender,
            "enabled": bool(raw.get("enabled", True)),
            "priority": _safe_int(raw.get("priority"), 100, -1000, 10000),
            "scope": scope,
            "keywords": [_single_line(item, 80) for item in raw.get("keywords", []) if _single_line(item, 80)]
            if isinstance(raw.get("keywords"), list)
            else [],
            "aliases": aliases,
            "content": content,
            "source": source,
            "raw": raw,
        }

    def _import_worldbook_entries_from_sources(self) -> bool:
        entries: list[dict[str, Any]] = []
        source_files: list[str] = []
        deleted_member_ids = {
            str(item).strip()
            for item in self.data.get("worldbook_deleted_member_ids", [])
            if str(item).strip()
        } if isinstance(self.data.get("worldbook_deleted_member_ids"), list) else set()
        deleted_group_ids = {
            str(item).strip()
            for item in self.data.get("worldbook_deleted_group_ids", [])
            if str(item).strip()
        } if isinstance(self.data.get("worldbook_deleted_group_ids"), list) else set()
        for path in self._worldbook_config_path_candidates():
            if not path.exists() or not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception as e:
                logger.warning(f"[PrivateCompanion] 读取关系网配置失败: {path} ({e})")
                continue
            raw_entries = payload.get("entry_storage") if isinstance(payload, dict) else None
            if not isinstance(raw_entries, list):
                continue
            source_files.append(str(path))
            for raw in raw_entries:
                item = self._normalize_worldbook_entry(raw, source=str(path))
                if item:
                    entries.append(item)
        if not entries:
            return False

        profiles: dict[str, dict[str, Any]] = {}
        groups: dict[str, dict[str, Any]] = {}
        for item in entries:
            template = item.get("template")
            digit_scopes = [str(scope).strip() for scope in item.get("scope", []) if str(scope).strip().isdigit()]
            if template == "user":
                for user_id in digit_scopes:
                    if user_id in deleted_member_ids:
                        continue
                    profile = profiles.setdefault(
                        user_id,
                        {
                            "user_id": user_id,
                            "name": item.get("name") or user_id,
                            "gender": "",
                            "aliases": [],
                            "content": "",
                            "identity_note": "",
                            "boundary_note": "",
                            "important_memories": [],
                            "enabled": bool(item.get("enabled", True)),
                            "priority": item.get("priority", 120),
                            "source_entries": [],
                            "observed_names": [],
                        },
                    )
                    for alias in [item.get("name"), *(item.get("aliases") or [])]:
                        alias = _single_line(alias, 40)
                        if alias and alias not in profile["aliases"] and alias != user_id:
                            profile["aliases"].append(alias)
                    content = _single_line(item.get("content"), 1200)
                    if content and content not in profile.get("content", ""):
                        profile["content"] = "\n".join(part for part in (profile.get("content"), content) if part).strip()
                    if content and not profile.get("note"):
                        profile["note"] = content
                    if content and not profile.get("identity_note"):
                        profile["identity_note"] = content
                    if item.get("gender") and not profile.get("gender"):
                        profile["gender"] = item.get("gender")
                    profile["enabled"] = bool(profile.get("enabled", True) and item.get("enabled", True))
                    profile["source_entries"].append(item.get("name") or user_id)
            elif template == "group":
                for group_id in digit_scopes:
                    if group_id in deleted_group_ids:
                        continue
                    groups[group_id] = {
                        "group_id": group_id,
                        "name": item.get("name") or group_id,
                        "content": item.get("content") or "",
                        "enabled": bool(item.get("enabled", True)),
                        "priority": item.get("priority", 110),
                        "aliases": item.get("aliases") or [],
                        "source_entries": [item.get("name") or group_id],
                    }

        old_profiles = self.data.get("worldbook_member_profiles") if isinstance(self.data.get("worldbook_member_profiles"), dict) else {}
        for user_id, profile in profiles.items():
            old = old_profiles.get(user_id) if isinstance(old_profiles.get(user_id), dict) else {}
            if old.get("manual_edit_ts"):
                for key in ("name", "gender", "aliases", "content", "identity_note", "boundary_note", "important_memories", "enabled", "priority", "note", "manual_edit_ts"):
                    if key in old:
                        profile[key] = old[key]
            profile["gender"] = _single_line(profile.get("gender"), 40)
            observed = old.get("observed_names") if isinstance(old.get("observed_names"), list) else []
            profile["observed_names"] = [_single_line(item, 40) for item in observed if _single_line(item, 40)]
            old_note = str(old.get("note") or "").strip()
            if old_note:
                profile["note"] = old_note
            elif not profile.get("note"):
                profile["note"] = profile.get("content", "")
            if not profile.get("identity_note"):
                profile["identity_note"] = profile.get("note") or profile.get("content", "")
            if not isinstance(profile.get("important_memories"), list):
                profile["important_memories"] = []
        for user_id, old in old_profiles.items():
            if user_id in profiles or not isinstance(old, dict):
                continue
            if old.get("manual_edit_ts") or "手动维护" in (old.get("source_entries") or []):
                profiles[user_id] = old

        old_groups = self.data.get("worldbook_group_profiles") if isinstance(self.data.get("worldbook_group_profiles"), dict) else {}
        for group_id, group in list(groups.items()):
            old = old_groups.get(group_id) if isinstance(old_groups.get(group_id), dict) else {}
            if old.get("manual_edit_ts"):
                for key in ("name", "content", "enabled", "priority", "aliases", "manual_edit_ts"):
                    if key in old:
                        group[key] = old[key]
        for group_id, old in old_groups.items():
            if group_id in groups or not isinstance(old, dict):
                continue
            if old.get("manual_edit_ts") or "手动维护" in (old.get("source_entries") or []):
                groups[group_id] = old

        changed = (
            self.data.get("worldbook_entries") != entries
            or self.data.get("worldbook_member_profiles") != profiles
            or self.data.get("worldbook_group_profiles") != groups
        )
        self.data["worldbook_entries"] = entries
        self.data["worldbook_member_profiles"] = profiles
        self.data["worldbook_group_profiles"] = groups
        self.data["worldbook_import_state"] = {
            "last_import_at": _now_ts(),
            "source_files": source_files,
            "entry_count": len(entries),
            "member_count": len(profiles),
            "group_count": len(groups),
        }
        return changed

    def _remember_worldbook_observed_name(self, user_id: str, name: str) -> None:
        if not self.enable_worldbook_member_recognition:
            return
        user_id = str(user_id or "").strip()
        name = _single_line(name, 40)
        if not user_id or not name or name == user_id:
            return
        profiles = self.data.get("worldbook_member_profiles")
        if not isinstance(profiles, dict):
            return
        profile = profiles.get(user_id)
        if not isinstance(profile, dict):
            return
        observed = profile.setdefault("observed_names", [])
        if not isinstance(observed, list):
            observed = []
            profile["observed_names"] = observed
        if name not in observed and name not in profile.get("aliases", []):
            observed.append(name)
            del observed[:-8]

    def _worldbook_profile_by_user_id(self, user_id: str) -> dict[str, Any] | None:
        if not self.enable_worldbook_member_recognition:
            return None
        user_id = str(user_id or "").strip()
        if not user_id:
            return None
        profiles = self.data.get("worldbook_member_profiles")
        if not isinstance(profiles, dict):
            return None
        profile = profiles.get(user_id)
        if isinstance(profile, dict) and profile.get("enabled", True):
            view = dict(profile)
            view["user_id"] = _single_line(profile.get("linked_qq_user_id") or profile.get("user_id") or user_id, 40)
            return view
        for profile_key, item in profiles.items():
            if not isinstance(item, dict) or not item.get("enabled", True):
                continue
            linked_id = _single_line(item.get("linked_qq_user_id") or item.get("user_id") or profile_key, 40)
            if linked_id == user_id:
                view = dict(item)
                view["user_id"] = linked_id
                return view
        return None

    def _group_member_identity_name(self, user_id: str, fallback: str = "", *, limit: int = 30) -> str:
        profile = self._worldbook_profile_by_user_id(user_id)
        if isinstance(profile, dict):
            name = _single_line(profile.get("name"), limit)
            if name and name != str(user_id or ""):
                return name
        return _single_line(fallback, limit) or str(user_id or "") or "群友"

    def _group_member_identity_label(self, user_id: str, fallback: str = "", *, limit: int = 24) -> str:
        uid = _single_line(user_id, 40)
        name = self._group_member_identity_name(uid, fallback, limit=limit)
        if not uid:
            return name
        if not name or name == uid:
            return f"QQ:{uid}"
        return f"{name}[QQ:{uid}]"

    def _group_member_identity_anchor_note(self, user_id: str, display_name: str = "", *, limit: int = 120) -> str:
        uid = _single_line(user_id, 40)
        if not uid:
            return ""
        display = _single_line(display_name, 40)
        stable_name = self._group_member_identity_name(uid, display, limit=30)
        if not display or display == uid or display == stable_name:
            return ""
        return f"{stable_name}[QQ:{uid}] 现在显示成“{display}”，这是群名片/临时显示名。"

    def _format_display_name_rename_events(self, events: Any, *, limit: int = 3) -> str:
        if not isinstance(events, list):
            return ""
        lines = []
        for item in events[-max(1, limit):]:
            if not isinstance(item, dict):
                continue
            old = _single_line(item.get("old"), 24)
            new = _single_line(item.get("new"), 24)
            if old and new and old != new:
                lines.append(f"{old} -> {new}")
        return "；".join(lines)

    def _group_member_identity_note(self, user_id: str, *, limit: int = 120) -> str:
        profile = self._worldbook_profile_by_user_id(user_id)
        if not isinstance(profile, dict):
            return ""
        gender = _single_line(profile.get("gender"), 40)
        note = _single_line(profile.get("identity_note") or profile.get("note") or profile.get("content"), limit)
        if gender and note:
            return _single_line(f"性别：{gender}；{note}", limit)
        if gender:
            return _single_line(f"性别：{gender}", limit)
        return note

    def _worldbook_member_matches_name(self, profile: dict[str, Any], keyword: str) -> bool:
        query = _single_line(keyword, 40).lower()
        if not query:
            return False
        tokens = self._worldbook_profile_tokens(profile)
        for token in tokens:
            value = _single_line(token, 40).lower()
            if value and (query == value or query in value or value in query):
                return True
        return False

    def _resolve_worldbook_member_by_name(self, keyword: str) -> list[dict[str, Any]]:
        if not self.enable_worldbook_member_recognition:
            return []
        query = _single_line(keyword, 40)
        if not query:
            return []
        profiles = self.data.get("worldbook_member_profiles")
        if not isinstance(profiles, dict):
            return []
        matches: list[dict[str, Any]] = []
        for user_id, profile in profiles.items():
            if not isinstance(profile, dict) or not profile.get("enabled", True):
                continue
            profile_uid = _single_line(profile.get("linked_qq_user_id") or profile.get("user_id") or user_id, 40)
            if str(user_id) == query or profile_uid == query or self._worldbook_member_matches_name(profile, query):
                matches.append({
                    "user_id": profile_uid or str(user_id),
                    "name": _single_line(profile.get("name"), 60) or profile_uid or str(user_id),
                    "gender": _single_line(profile.get("gender"), 40),
                    "aliases": self._normalize_string_list(profile.get("aliases"), limit=8, item_limit=40),
                    "observed_names": self._normalize_string_list(profile.get("observed_names"), limit=8, item_limit=40),
                    "identity_note": _single_line(profile.get("identity_note") or profile.get("note") or profile.get("content"), 160),
                    "source": "worldbook",
                })
        query_lower = query.lower()

        def match_rank(item: dict[str, Any]) -> tuple[int, str]:
            name = _single_line(item.get("name"), 60)
            aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
            observed = item.get("observed_names") if isinstance(item.get("observed_names"), list) else []
            tokens = [name, *aliases, *observed]
            lowered = [_single_line(token, 40).lower() for token in tokens if _single_line(token, 40)]
            if str(item.get("user_id") or "") == query:
                return (0, str(item.get("user_id") or ""))
            if query_lower in lowered:
                return (1, str(item.get("user_id") or ""))
            if any(token.startswith(query_lower) or query_lower.startswith(token) for token in lowered if token):
                return (2, str(item.get("user_id") or ""))
            return (3, str(item.get("user_id") or ""))

        matches.sort(key=match_rank)
        return matches

    def _worldbook_profile_memory_lines(self, profile: dict[str, Any], *, limit: int = 3) -> list[str]:
        memories = profile.get("important_memories")
        if not isinstance(memories, list):
            return []
        valid = [item for item in memories if isinstance(item, dict) and item.get("enabled", True)]
        valid.sort(key=lambda item: (_safe_int(item.get("weight"), 50, -1000), _safe_float(item.get("updated_at"), 0)), reverse=True)
        lines: list[str] = []
        for item in valid[:limit]:
            title = _single_line(item.get("title"), 36)
            content = _single_line(item.get("content"), 120)
            if not content:
                continue
            privacy = _single_line(item.get("privacy"), 12) or "internal"
            prefix = f"{title}：" if title else ""
            lines.append(f"{prefix}{content}｜{privacy}｜权重{_safe_int(item.get('weight'), 50, -1000)}")
        return lines

    @staticmethod
    def _worldbook_name_skeleton(value: Any) -> str:
        text = _single_line(value, 40).lower()
        text = re.sub(r"[\s\-_·・.。,:：，;；'\"“”‘’/\\|()\[\]{}<>《》]+", "", text)
        if not text:
            return ""
        table = str.maketrans(
            {
                "跌": "爹",
                "叠": "爹",
                "迭": "爹",
                "蝶": "爹",
                "碟": "爹",
                "谍": "爹",
                "耶": "爷",
                "椰": "爷",
                "噎": "爷",
                "粑": "爸",
                "芭": "爸",
                "巴": "爸",
                "叭": "爸",
                "吧": "爸",
                "八": "爸",
                "麻": "妈",
                "嘛": "妈",
                "吗": "妈",
                "骂": "妈",
                "煮": "主",
                "嘱": "主",
                "竹": "主",
                "住": "主",
                "铸": "主",
                "統": "统",
                "發": "发",
                "開": "开",
                "貓": "猫",
                "妳": "你",
                "您": "你",
            }
        )
        return text.translate(table)

    def _normalize_worldbook_self_name(self, value: Any) -> str:
        text = _single_line(value, 24)
        text = re.sub(r"^(叫|是|为|做|作)", "", text)
        text = re.sub(r"(就行|好了|吧|呀|啦|哦|啊)$", "", text).strip()
        text = text.strip("「」『』“”\"'`[]()（）<>《》:：,，.。!！?？")
        if not text or len(text) > 6:
            return ""
        if text in {"来", "想", "要", "不是", "可以", "不用", "大家", "群友", "机器人"}:
            return ""
        if re.search(r"(怎么|为什么|什么|不是|不要|别|吗|呢|吧|请问)", text):
            return ""
        skeleton = WorldbookMixin._worldbook_name_skeleton(text)
        unsafe_patterns = (
            r"^(?:你|妳|您|bot|Bot|BOT|机器人|小星)?(?:爹|爸|爸爸|父亲|妈|妈妈|母亲|爷|爷爷|奶奶|祖宗|主人|老公|老婆|男友|女友|对象)$",
            r"^(?:群主|管理员|管理|号主|官方|客服|系统|开发者|作者|插件作者|超级用户|root|admin)$",
            r"(傻|蠢|笨蛋|废物|垃圾|滚|死|爹味|逆子|儿子|孙子)",
            r"(我是你|我是妳|我是您|我是bot|我是机器人)",
        )
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in unsafe_patterns):
            return ""
        if any(re.search(pattern, skeleton, re.IGNORECASE) for pattern in unsafe_patterns):
            return ""
        protected_getter = getattr(self, "_protected_owner_nickname_tokens", None)
        protected_names = protected_getter() if callable(protected_getter) else set()
        protected_keys = {
            WorldbookMixin._worldbook_name_skeleton(item)
            for item in protected_names
            if WorldbookMixin._worldbook_name_skeleton(item)
        }
        if skeleton and skeleton in protected_keys:
            return ""
        if re.search(r"^(?:你|妳|您).{0,4}$", text):
            return ""
        if re.search(r"^你.{0,4}$", skeleton):
            return ""
        return text

    def _worldbook_self_registration_conflict(self, sender_id: str, names: list[str]) -> str:
        profiles = self.data.get("worldbook_member_profiles")
        if not isinstance(profiles, dict):
            return ""
        normalized_names = {
            self._worldbook_name_skeleton(name)
            for name in names
            if self._worldbook_name_skeleton(name)
        }
        if not normalized_names:
            return ""
        for user_id, profile in profiles.items():
            other_id = str(user_id or "")
            if other_id == str(sender_id or "") or not isinstance(profile, dict):
                continue
            tokens = self._worldbook_profile_tokens(profile)
            for token in tokens:
                token_key = self._worldbook_name_skeleton(token)
                if not token_key:
                    continue
                if token_key in normalized_names:
                    return f"{other_id}:{_single_line(profile.get('name'), 40) or token}"
                for name_key in normalized_names:
                    if len(name_key) >= 3 and len(token_key) >= 3 and (name_key in token_key or token_key in name_key):
                        return f"{other_id}:{_single_line(profile.get('name'), 40) or token}"
        return ""

    def _worldbook_registration_pending_map(self, group: dict[str, Any]) -> dict[str, Any]:
        pending = group.setdefault("worldbook_registration_confirmations", {})
        if not isinstance(pending, dict):
            pending = {}
            group["worldbook_registration_confirmations"] = pending
        now = _now_ts()
        for user_id in list(pending.keys()):
            item = pending.get(user_id)
            if not isinstance(item, dict) or now - _safe_float(item.get("created_ts"), 0) > 6 * 60:
                pending.pop(user_id, None)
        return pending

    @staticmethod
    def _worldbook_registration_confirmation_intent(text: str) -> str:
        cleaned = _single_line(text, 80)
        cleaned = re.sub(r"\[CQ:at,[^\]]+\]", " ", cleaned)
        cleaned = re.sub(r"@\S+", " ", cleaned).strip()
        if not cleaned:
            return ""
        if re.search(r"(不行|不是|别|不要|算了|错了|不对|改一下|等等|拒绝)", cleaned):
            return "reject"
        if re.search(r"^(可以|可|行|好|好的|嗯|嗯嗯|对|对的|是|是的|没错|叫吧|就这样|可以呀|可以啊)[。！？!?\s]*$", cleaned):
            return "accept"
        return ""

    def _create_worldbook_self_registration_profile(
        self,
        *,
        group_id: str,
        sender_id: str,
        sender_name: str,
        text: str,
        name: str,
        aliases: list[str],
        group: dict[str, Any],
    ) -> dict[str, Any]:
        profiles = self.data.setdefault("worldbook_member_profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
            self.data["worldbook_member_profiles"] = profiles
        deleted = self.data.get("worldbook_deleted_member_ids")
        if isinstance(deleted, list) and sender_id in deleted:
            self.data["worldbook_deleted_member_ids"] = [item for item in deleted if str(item) != sender_id]
        content = f"{name}在群 {group_id} 主动向 Bot 自我介绍。"
        profile = {
            "user_id": sender_id,
            "name": name,
            "gender": "",
            "aliases": aliases,
            "content": content,
            "identity_note": f"QQ {sender_id}，自称{name}。",
            "boundary_note": "",
            "important_memories": [],
            "enabled": True,
            "priority": 120,
            "source_entries": ["群聊自登记"],
            "observed_names": [item for item in [_single_line(sender_name, 40)] if item and item not in aliases],
            "auto_registered_ts": _now_ts(),
            "auto_registration_pending": True,
            "self_intro_text": _single_line(text, 260),
        }
        profiles[sender_id] = profile
        recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
        logger.info(
            "[PrivateCompanion] 群聊关系网自登记节点: group=%s user=%s name=%s aliases=%s",
            group_id or "-",
            sender_id,
            name,
            "、".join(aliases) or "-",
        )
        return {
            "group_id": str(group_id),
            "user_id": sender_id,
            "name": name,
            "aliases": aliases,
            "text": _single_line(text, 260),
            "sender_name": _single_line(sender_name, 40),
            "recent": [dict(item) for item in recent[-6:] if isinstance(item, dict)],
        }

    def _worldbook_self_registration_block_word_hit(self, *texts: Any) -> str:
        raw_words = getattr(self, "worldbook_self_registration_block_words", [])
        if not isinstance(raw_words, list) or not raw_words:
            return ""
        haystacks = []
        for text in texts:
            normalized = _single_line(text, 80)
            if not normalized:
                continue
            haystacks.append(normalized.lower())
            haystacks.append(re.sub(r"\s+", "", normalized).lower())
        for raw_word in raw_words:
            word = _single_line(raw_word, 40)
            if not word:
                continue
            lowered = word.lower()
            compact = re.sub(r"\s+", "", lowered)
            for haystack in haystacks:
                if lowered and lowered in haystack:
                    return word
                if compact and compact in haystack:
                    return word
        return ""

    def _worldbook_self_registration_block_reply_text(self) -> str:
        reply = _single_line(getattr(self, "worldbook_self_registration_block_reply", ""), 80)
        if reply in {"这个称呼我先不记。", "你是小猪"}:
            reply = ""
        return reply or "这个称呼我不记。"

    def _extract_worldbook_self_intro(self, text: str) -> dict[str, Any] | None:
        cleaned = str(text or "")
        cleaned = re.sub(r"\[CQ:at,[^\]]+\]", " ", cleaned)
        if self.bot_name:
            cleaned = cleaned.replace(self.bot_name, " ")
        cleaned = re.sub(r"@\S+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned or len(cleaned) > 20:
            return None
        if re.search(r"(我是不是|我不是|我是说|我是想|我是觉得|我是因为|我是来|我是要|我是在|我是什么|我是谁|你觉得我是)", cleaned):
            return None
        if not re.search(r"^(?:我是|我叫|叫我|以后叫我|可以叫我|你可以叫我)\S{1,24}(?:\s*(?:你可以叫我|可以叫我|以后叫我|叫我)\S{1,16})?[。！？!?\s]*$", cleaned):
            return None
        aliases: list[str] = []
        primary = ""
        attempted = False
        for match in re.finditer(r"^(?:我是|我叫)([\u4e00-\u9fffA-Za-z0-9_·・\-]{1,8})(?=$|[。！？!?\s,，、]|你可以叫我|可以叫我|以后叫我|叫我)", cleaned):
            attempted = True
            name = self._normalize_worldbook_self_name(match.group(1))
            if name:
                primary = primary or name
                aliases.append(name)
        for match in re.finditer(r"(?:你可以叫我|可以叫我|以后叫我|叫我)([^。！？\n]{1,32})[。！？!?\s]*$", cleaned):
            attempted = True
            raw = match.group(1)
            raw = re.split(r"(?:就行|好了|谢谢|麻烦|$)", raw, maxsplit=1)[0]
            for part in re.split(r"(?:或者|还是|和|或|、|/|,|，|;|；|\s+)", raw):
                name = self._normalize_worldbook_self_name(part)
                if name:
                    primary = primary or name
                    aliases.append(name)
        aliases = list(dict.fromkeys(item for item in aliases if item))
        if not primary and aliases:
            primary = aliases[0]
        if not primary:
            return {"blocked": True} if attempted else None
        return {"name": primary, "aliases": aliases[:8]}

    def _maybe_worldbook_self_register_from_group_message(
        self,
        event: AstrMessageEvent,
        *,
        group_id: str,
        sender_id: str,
        sender_name: str,
        text: str,
        group: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not (self.enable_worldbook_member_recognition and self.worldbook_self_registration):
            return None
        sender_id = str(sender_id or "").strip()
        if not sender_id:
            return None
        pending = self._worldbook_registration_pending_map(group)
        pending_item = pending.get(sender_id)
        if isinstance(pending_item, dict):
            intent = self._worldbook_registration_confirmation_intent(text)
            if intent == "reject":
                pending.pop(sender_id, None)
                return {"confirm_reply": "好，那我不记。"}
            if intent == "accept":
                name = _single_line(pending_item.get("name"), 40) or sender_id
                aliases = [
                    _single_line(item, 40)
                    for item in (pending_item.get("aliases") if isinstance(pending_item.get("aliases"), list) else [])
                    if _single_line(item, 40) and _single_line(item, 40) != sender_id
                ]
                block_word = self._worldbook_self_registration_block_word_hit(
                    name,
                    *aliases,
                    pending_item.get("text"),
                )
                if block_word:
                    pending.pop(sender_id, None)
                    logger.info(
                        "[PrivateCompanion] 群聊关系网自登记确认时拒绝: group=%s user=%s name=%s reason=命中自登记屏蔽词 %s",
                        group_id or "-",
                        sender_id,
                        name,
                        block_word,
                    )
                    return {"blocked_reply": self._worldbook_self_registration_block_reply_text()}
                conflict = self._worldbook_self_registration_conflict(sender_id, [name, *aliases])
                if conflict:
                    pending.pop(sender_id, None)
                    logger.info(
                        "[PrivateCompanion] 群聊关系网自登记确认时拒绝: group=%s user=%s name=%s reason=名称疑似冒领已有节点 %s",
                        group_id or "-",
                        sender_id,
                        name,
                        conflict,
                    )
                    return {"blocked_reply": self._worldbook_self_registration_block_reply_text()}
                pending.pop(sender_id, None)
                payload = self._create_worldbook_self_registration_profile(
                    group_id=group_id,
                    sender_id=sender_id,
                    sender_name=_single_line(pending_item.get("sender_name"), 40) or sender_name,
                    text=_single_line(pending_item.get("text"), 260) or text,
                    name=name,
                    aliases=aliases,
                    group=group,
                )
                payload["confirm_reply"] = f"好，那我记住你是{name}。"
                return payload
        existing_profiles = self.data.get("worldbook_member_profiles")
        if isinstance(existing_profiles, dict) and isinstance(existing_profiles.get(sender_id), dict):
            return None
        if not self._group_message_explicitly_ats_bot(event):
            return None
        intro = self._extract_worldbook_self_intro(text)
        if not intro:
            return None
        if intro.get("blocked"):
            logger.info(
                "[PrivateCompanion] 群聊关系网自登记已拒绝: group=%s user=%s reason=称呼不合规或超过六字",
                group_id or "-",
                sender_id,
            )
            return {"blocked_reply": self._worldbook_self_registration_block_reply_text()}
        name = _single_line(intro.get("name"), 40) or sender_id
        aliases = [
            _single_line(item, 40)
            for item in (intro.get("aliases") if isinstance(intro.get("aliases"), list) else [])
            if _single_line(item, 40) and _single_line(item, 40) != sender_id
        ]
        block_word = self._worldbook_self_registration_block_word_hit(name, *aliases, text)
        if block_word:
            logger.info(
                "[PrivateCompanion] 群聊关系网自登记已拒绝: group=%s user=%s name=%s reason=命中自登记屏蔽词 %s",
                group_id or "-",
                sender_id,
                name,
                block_word,
            )
            return {"blocked_reply": self._worldbook_self_registration_block_reply_text()}
        conflict = self._worldbook_self_registration_conflict(sender_id, [name, *aliases])
        if conflict:
            logger.info(
                "[PrivateCompanion] 群聊关系网自登记已拒绝: group=%s user=%s name=%s reason=名称疑似冒领已有节点 %s",
                group_id or "-",
                sender_id,
                name,
                conflict,
            )
            return {"blocked_reply": self._worldbook_self_registration_block_reply_text()}
        profiles = self.data.setdefault("worldbook_member_profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
            self.data["worldbook_member_profiles"] = profiles
        pending[sender_id] = {
            "name": name,
            "aliases": aliases,
            "text": _single_line(text, 260),
            "sender_name": _single_line(sender_name, 40),
            "created_ts": _now_ts(),
        }
        logger.info(
            "[PrivateCompanion] 群聊关系网自登记待确认: group=%s user=%s name=%s aliases=%s",
            group_id or "-",
            sender_id,
            name,
            "、".join(aliases) or "-",
        )
        return {"confirm_reply": f"那我以后叫你{name}可以吗？"}

    async def _refresh_worldbook_self_registration_impression(self, payload: dict[str, Any]) -> None:
        user_id = str(payload.get("user_id") or "").strip()
        if not user_id:
            return
        name = _single_line(payload.get("name"), 40) or user_id
        aliases = payload.get("aliases") if isinstance(payload.get("aliases"), list) else []
        recent_lines = []
        recent_items = payload.get("recent") if isinstance(payload.get("recent"), list) else []
        for item in recent_items:
            if not isinstance(item, dict):
                continue
            speaker = _single_line(item.get("identity_name") or item.get("name"), 20) or "群友"
            msg = _single_line(item.get("text"), 80)
            if msg:
                recent_lines.append(f"- {speaker}: {msg}")
        prompt = f"""
请根据这条群聊自我介绍，生成一段适合“关系节点资料正文”的简短人物印象。

【Bot 人格】
{_single_line(self._get_default_persona_prompt(), 500)}

【群号】
{_single_line(payload.get('group_id'), 40)}

【QQ】
{user_id}

【自称/称呼】
{name}{"；别名：" + "、".join(_single_line(item, 24) for item in aliases if _single_line(item, 24)) if aliases else ""}

【自我介绍原文】
{_single_line(payload.get('text'), 260)}

【附近群聊】
{chr(10).join(recent_lines) or "（暂无）"}

要求：
- 只输出 1 段中文，40 到 90 字
- 像人物画像插件里的初始印象：描述可观察信息、称呼和互动注意点
- 不要编造职业、性格、现实身份或私密事实
- 不要写“根据聊天记录/资料显示/模型判断”
""".strip()
        impression = await self._llm_call(
            prompt,
            max_tokens=180,
            provider_id=self._task_provider(self.relationship_analysis_provider_id, self.mai_style_provider_id),
            task="worldbook_registration",
        )
        cleaned = _single_line(impression, 220)
        if not cleaned:
            cleaned = f"{name}在群里主动告诉 Bot 可以这样称呼自己；目前只有自我介绍信息，后续需要通过群聊慢慢补充印象。"
        async with self._data_lock:
            profile = self._worldbook_profile_by_user_id(user_id)
            if not isinstance(profile, dict) or not profile.get("auto_registration_pending"):
                return
            profile["content"] = cleaned
            if not profile.get("identity_note"):
                profile["identity_note"] = f"QQ {user_id}，自称{name}。"
            memories = profile.setdefault("important_memories", [])
            if isinstance(memories, list) and not memories:
                memories.append(
                    {
                        "title": "自我介绍",
                        "content": _single_line(payload.get("text"), 180),
                        "weight": 70,
                        "privacy": "internal",
                        "source": "群聊自登记",
                        "enabled": True,
                        "updated_at": _now_ts(),
                    }
                )
            profile["auto_registration_pending"] = False
            profile["auto_impression_ts"] = _now_ts()
            self._save_data_sync()
        logger.info("[PrivateCompanion] 群聊关系网自登记印象已生成: user=%s name=%s", user_id, name)

    def _group_member_identity_label_for_token(self, group: dict[str, Any], token: str) -> str:
        query = re.sub(r"\s+", "", _single_line(token, 40))
        if not query:
            return ""
        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        for user_id, member in members.items():
            if not isinstance(member, dict):
                continue
            candidates = [
                member.get("name"),
                member.get("identity_name"),
                member.get("display_name"),
                member.get("nickname"),
                member.get("card"),
            ]
            profile = self._worldbook_profile_by_user_id(str(user_id))
            if isinstance(profile, dict):
                candidates.extend([profile.get("name"), *(profile.get("aliases") or []), *(profile.get("observed_names") or [])])
            for candidate in candidates:
                value = re.sub(r"\s+", "", _single_line(candidate, 40))
                if value and value == query:
                    return self._group_member_identity_label(str(user_id), candidate, limit=24)
        return ""

    def _worldbook_profile_tokens(self, profile: dict[str, Any]) -> list[str]:
        tokens: list[str] = []
        for token in [profile.get("name"), *(profile.get("aliases") or []), *(profile.get("observed_names") or [])]:
            token = _single_line(token, 40)
            if token and token not in tokens:
                tokens.append(token)
        for raw_id in (profile.get("linked_qq_user_id"), profile.get("user_id")):
            user_id = _single_line(raw_id, 40)
            if user_id and user_id not in tokens:
                tokens.append(user_id)
        return tokens

    @staticmethod
    def _worldbook_token_usable(token: str) -> bool:
        token = _single_line(token, 40)
        if not token:
            return False
        if token.isdigit():
            return len(token) >= 5
        if len(token) < 2:
            return False
        if token in {"群友", "老师", "同学", "朋友", "bot", "Bot", "BOT", "我", "你", "他", "她", "它"}:
            return False
        return True

    def _worldbook_token_mentioned(self, token: str, text: str) -> bool:
        token = _single_line(token, 40)
        text = str(text or "")
        if not token or not text:
            return False
        if token.isdigit():
            return token in text
        if not self._worldbook_token_usable(token):
            return False
        if len(token) <= 2:
            pattern = rf"(?<![\u4e00-\u9fffA-Za-z0-9_]){re.escape(token)}(?![\u4e00-\u9fffA-Za-z0-9_])"
            return bool(re.search(pattern, text))
        return token in text

    def _worldbook_token_mentioned_in_private_hint(self, token: str, text: str) -> bool:
        token = _single_line(token, 40)
        text = str(text or "")
        if self._worldbook_token_mentioned(token, text):
            return True
        if not token or token.isdigit() or len(token) > 2 or not self._worldbook_token_usable(token):
            return False
        # “帮我找某个两字昵称你认识吗”这类短昵称后面常直接接动词，
        # 明确问人或转述时不能沿用普通闲聊的强边界，否则会漏掉目标。
        before = r"(^|[\s，,。？?！!：:、@和跟找叫给对向把让问找一下])"
        after = r"(?=(你认识|认识|认得|知道|是谁|是|说|发|问|找|叫|踢|$|[\s，,。？?！!：:、]))"
        return bool(re.search(before + re.escape(token) + after, text))

    def _worldbook_profile_view(
        self,
        profile: dict[str, Any],
        *,
        match_reason: str,
        confidence: str,
        scope: str = "mentioned",
    ) -> dict[str, Any]:
        view = dict(profile)
        view["_match_reason"] = match_reason
        view["_match_confidence"] = confidence
        view["_match_scope"] = scope
        return view

    def _select_worldbook_member_profiles_for_private_text(self, text: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        if not self.enable_worldbook_member_recognition:
            return []
        profiles = self.data.get("worldbook_member_profiles")
        if not isinstance(profiles, dict):
            return []
        text = str(text or "")
        if not text:
            return []
        max_items = max(1, min(8, _safe_int(limit, min(self.worldbook_member_inject_limit, 4), 1)))
        selected: dict[str, dict[str, Any]] = {}
        for match in re.findall(r"\d{5,12}", text):
            profile = self._worldbook_profile_by_user_id(match)
            if isinstance(profile, dict) and profile.get("enabled", True):
                selected[match] = self._worldbook_profile_view(
                    profile,
                    match_reason="当前私聊消息提到 QQ 号",
                    confidence="mentioned",
                )
        for user_id, profile in profiles.items():
            if len(selected) >= max_items:
                break
            if not isinstance(profile, dict) or not profile.get("enabled", True):
                continue
            if str(user_id) in selected:
                continue
            for token in self._worldbook_profile_tokens(profile):
                if self._worldbook_token_mentioned_in_private_hint(token, text):
                    selected[str(user_id)] = self._worldbook_profile_view(
                        profile,
                        match_reason=f"当前私聊消息提到：{token}",
                        confidence="mentioned",
                    )
                    break
        ranked = sorted(
            selected.values(),
            key=lambda item: _safe_int(item.get("priority"), 120, -1000),
            reverse=True,
        )
        return ranked[:max_items]

    def _format_worldbook_private_mentions_for_prompt(self, text: str, *, limit: int | None = None) -> str:
        profiles = self._select_worldbook_member_profiles_for_private_text(text, limit=limit)
        if not profiles:
            return ""
        lines = ["【本轮提到的关系网对象】"]
        injected = []
        for profile in profiles:
            profile_uid = _single_line(profile.get("user_id"), 40)
            name = _single_line(profile.get("name"), 40) or profile_uid or "-"
            aliases = "、".join(token for token in self._worldbook_profile_tokens(profile)[:6] if token != profile_uid and token != name)
            identity = _single_line(profile.get("identity_note") or profile.get("note") or profile.get("content"), 140)
            boundary = _single_line(profile.get("boundary_note"), 80)
            parts = [f"{name}（QQ:{profile_uid or '-'}）"]
            if aliases:
                parts.append(f"称呼线索：{aliases}")
            if identity:
                parts.append(f"身份：{identity}")
            if boundary:
                parts.append(f"边界：{boundary}")
            lines.append("- " + "｜".join(parts))
            injected.append(f"{profile_uid}:{name}")
        logger.info("[PrivateCompanion] 本轮提及关系网对象注入: users=%s", "；".join(injected))
        return "\n".join(lines)

    def _select_worldbook_member_profiles_for_group(
        self,
        group: dict[str, Any],
        *,
        sender_id: str = "",
        text: str = "",
    ) -> list[dict[str, Any]]:
        if not self.enable_worldbook_member_recognition:
            return []
        profiles = self.data.get("worldbook_member_profiles")
        if not isinstance(profiles, dict):
            return []
        text = str(text or "")
        recent_speaker_ids: list[str] = []
        recent = group.get("recent_messages")
        if isinstance(recent, list):
            for item in recent[-6:]:
                if not isinstance(item, dict):
                    continue
                recent_id = _single_line(item.get("sender_id"), 40)
                if recent_id and recent_id not in recent_speaker_ids:
                    recent_speaker_ids.append(recent_id)
        selected: dict[str, dict[str, Any]] = {}
        if sender_id and isinstance(profiles.get(sender_id), dict) and profiles[sender_id].get("enabled", True):
            selected[sender_id] = self._worldbook_profile_view(
                profiles[sender_id],
                match_reason="当前发言者 QQ 精确匹配",
                confidence="confirmed",
                scope="current_sender",
            )
        if self.worldbook_member_match_aliases and text and len(selected) < self.worldbook_member_inject_limit:
            token_hits: dict[str, list[tuple[str, dict[str, Any]]]] = {}
            for user_id, profile in profiles.items():
                if not isinstance(profile, dict) or not profile.get("enabled", True):
                    continue
                if user_id in selected:
                    continue
                for token in self._worldbook_profile_tokens(profile):
                    if self._worldbook_token_mentioned(token, text) or self._worldbook_token_mentioned_in_private_hint(token, text):
                        token_hits.setdefault(token, []).append((str(user_id), profile))
            for token in sorted(token_hits, key=len, reverse=True):
                hits = token_hits[token]
                if len(hits) != 1:
                    continue
                user_id, profile = hits[0]
                if user_id in selected:
                    continue
                selected[user_id] = self._worldbook_profile_view(
                    profile,
                    match_reason=f"当前消息明确提到：{token}",
                    confidence="mentioned",
                    scope="mentioned",
                )
                if len(selected) >= self.worldbook_member_inject_limit:
                    break
        for recent_id in recent_speaker_ids:
            if len(selected) >= self.worldbook_member_inject_limit:
                break
            if recent_id in selected:
                continue
            profile = profiles.get(recent_id)
            if isinstance(profile, dict) and profile.get("enabled", True):
                selected[recent_id] = self._worldbook_profile_view(
                    profile,
                    match_reason="最近发言者 QQ 精确匹配",
                    confidence="context",
                    scope="recent_speaker",
                )
        ranked = sorted(
            selected.values(),
            key=lambda item: (
                {
                    "current_sender": 3,
                    "mentioned": 2,
                    "recent_speaker": 1,
                }.get(str(item.get("_match_scope") or ""), 0),
                1 if item.get("_match_confidence") == "confirmed" else 0,
                _safe_int(item.get("priority"), 120, -1000),
            ),
            reverse=True,
        )
        return ranked[: self.worldbook_member_inject_limit]

    def _format_worldbook_group_members_for_prompt(self, group: dict[str, Any], sender_id: str = "", text: str = "") -> str:
        if not self.enable_worldbook_member_recognition:
            return ""
        lines: list[str] = []
        group_id = _single_line(group.get("group_id"), 40)
        group_profiles = self.data.get("worldbook_group_profiles")
        if group_id and isinstance(group_profiles, dict):
            group_profile = group_profiles.get(group_id)
            if isinstance(group_profile, dict) and group_profile.get("enabled", True):
                lines.append(
                    f"群聊资料：{_single_line(group_profile.get('name'), 40) or group_id}｜"
                    f"{_single_line(group_profile.get('content'), 320)}"
                )
        profiles = self._select_worldbook_member_profiles_for_group(group, sender_id=sender_id, text=text)
        if profiles:
            injected = []
            for profile in profiles:
                injected.append(
                    f"{_single_line(profile.get('user_id'), 40) or '-'}:"
                    f"{_single_line(profile.get('name'), 40) or '-'}"
                    f"[{_single_line(profile.get('_match_confidence'), 20) or '-'}"
                    f"/{_single_line(profile.get('_match_scope'), 30) or '-'}"
                    f"/{_single_line(profile.get('_match_reason'), 80) or '-'}]"
                )
            logger.info(
                "[PrivateCompanion] 群聊关系网注入用户信息: group=%s sender=%s users=%s",
                group_id or "-",
                _single_line(sender_id, 40) or "-",
                "；".join(injected),
            )
        for profile in profiles:
            profile_uid = _single_line(profile.get("user_id"), 40)
            aliases = "、".join(token for token in self._worldbook_profile_tokens(profile)[:8] if token != profile_uid)
            reason = _single_line(profile.get("_match_reason"), 80)
            scope = _single_line(profile.get("_match_scope"), 30)
            if scope == "current_sender":
                label = "当前发言者"
            elif scope == "recent_speaker":
                label = "近期参与者（不是当前发言者）"
            else:
                label = "当前消息提到的人"
            gender = _single_line(profile.get("gender"), 40)
            identity = _single_line(profile.get("identity_note") or profile.get("note") or profile.get("content"), 220)
            boundary = _single_line(profile.get("boundary_note"), 140)
            memories = [] if scope == "recent_speaker" else self._worldbook_profile_memory_lines(profile, limit=3)
            parts = []
            if gender:
                parts.append(f"性别：{gender}")
            if identity:
                parts.append(f"身份：{identity}")
            if boundary:
                parts.append(f"边界：{boundary}")
            if memories:
                parts.append("重要记忆：" + "；".join(memories))
            if not parts:
                parts.append(_single_line(profile.get("content"), 360))
            lines.append(
                f"- {label}：{_single_line(profile.get('name'), 40) or profile_uid or '-'}"
                f"（QQ:{profile_uid or '-'}）"
                + (f"｜称呼线索：{aliases}" if aliases else "")
                + (f"｜来源：{reason}" if reason else "")
                + "：" + "｜".join(part for part in parts if part)
            )
        if not lines:
            return ""
        return (
            "【群聊关系网】\n"
            "下面是按 QQ 号确认的稳定关系资料；群名片、昵称和别名只当称呼线索。\n"
            "只有“当前发言者”可用于判断本轮对话对象；“当前消息提到的人”和“近期参与者”只用于理解上下文，不要把他们的身份、专属称呼或亲密关系套给当前发言者。\n"
            + "\n".join(lines)
        )

