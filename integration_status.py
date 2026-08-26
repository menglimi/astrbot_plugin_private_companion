# -*- coding: utf-8 -*-
"""
IntegrationStatusMixin — 外部插件状态、世界观适配与运行环境描述
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
from .conversation_prompt_section import prompt_section
from .persona_config import runtime_persona_setting
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
from .plugin_identity import PLUGIN_ID
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
from .diagnostic_envelope import DIAGNOSTIC_ENVELOPE_VERSION, DIAGNOSTIC_PUBLIC_FIELDS
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




class IntegrationStatusMixin:
    """外部插件状态、世界观适配与运行环境描述"""

    @staticmethod
    def _diagnostic_operations_contract() -> dict[str, Any]:
        """Return the versioned public DTO contract used by operations."""
        return {
            "version": DIAGNOSTIC_ENVELOPE_VERSION,
            "fields": list(DIAGNOSTIC_PUBLIC_FIELDS),
            "history_projection": True,
        }

    def _patch_astrbot_plugin_page_asset_token_compat(self) -> None:
        """Give AstrBot plugin Pages a wider refresh window for this panel.

        AstrBot 4.26.x signs plugin Page static assets with a very short-lived
        asset_token. Some desktop/webview builds keep an iframe src around
        longer than that, so users see the raw JSON error before our page JS can
        refresh itself. The page assets are scoped static files; extending the
        window is a compatibility shim, not a permission bypass.
        """
        target_ttl_seconds = 6 * 60 * 60
        changed: list[str] = []
        try:
            from astrbot.dashboard.services import plugin_page_service as service_mod

            current = int(getattr(service_mod, "PLUGIN_PAGE_ASSET_TOKEN_TTL_SECONDS", 0) or 0)
            if 0 < current < target_ttl_seconds:
                setattr(service_mod, "PLUGIN_PAGE_ASSET_TOKEN_TTL_SECONDS", target_ttl_seconds)
                changed.append(f"service_ttl={current}->{target_ttl_seconds}")
        except Exception as exc:
            logger.debug("[PrivateCompanion] AstrBot 插件页 token TTL 兼容补丁跳过: %s", _single_line(exc, 120))

        try:
            from astrbot.dashboard.routes import plugin as legacy_route_mod

            current = int(getattr(legacy_route_mod, "_PLUGIN_PAGE_ASSET_TOKEN_TTL_SECONDS", 0) or 0)
            if 0 < current < target_ttl_seconds:
                setattr(legacy_route_mod, "_PLUGIN_PAGE_ASSET_TOKEN_TTL_SECONDS", target_ttl_seconds)
                changed.append(f"legacy_ttl={current}->{target_ttl_seconds}")
        except Exception as exc:
            logger.debug("[PrivateCompanion] AstrBot 旧插件页 token TTL 兼容补丁跳过: %s", _single_line(exc, 120))

        try:
            from astrbot.dashboard.plugin_page_auth import PluginPageAuth

            original = getattr(PluginPageAuth, "_private_companion_original_is_scope_valid", None)
            if original is None:
                original = PluginPageAuth.is_scope_valid
                setattr(PluginPageAuth, "_private_companion_original_is_scope_valid", original)

                def _is_scope_valid(cls, payload: dict, path: str) -> bool:
                    try:
                        if original(payload, path):
                            return True
                    except Exception:
                        return False
                    if not cls.is_protected_path(path) or path.startswith("/api/plugin/page/bridge-sdk.js"):
                        return False
                    token_plugin_name = payload.get("plugin_name")
                    token_page_name = payload.get("page_name")
                    request_plugin_name = cls.extract_plugin_name_from_path(path)
                    request_page_name = cls.extract_page_name_from_path(path)
                    page_aliases = {"陪伴面板", "companion-panel"}
                    return (
                        token_plugin_name == "astrbot_plugin_private_companion"
                        and request_plugin_name == "astrbot_plugin_private_companion"
                        and token_page_name in page_aliases
                        and request_page_name in page_aliases
                    )

                PluginPageAuth.is_scope_valid = classmethod(_is_scope_valid)
                changed.append("private_companion_page_alias_scope")
        except Exception as exc:
            logger.debug("[PrivateCompanion] AstrBot 插件页别名鉴权兼容补丁跳过: %s", _single_line(exc, 120))

        # A hot-reload can leave a StarMetadata object whose root_dir_name still
        # points at a temporary/old checkout.  The page and README routers use
        # that field directly, so a valid installed plugin can look missing.
        # Resolve only this plugin back to the directory of the currently loaded
        # module; other plugins keep AstrBot's normal path handling.
        companion_root = Path(__file__).resolve().parent

        def _is_companion_plugin(value: Any) -> bool:
            return str(getattr(value, "name", value) or "").strip() == PLUGIN_ID

        def _usable_companion_root() -> Path | None:
            if not companion_root.is_dir():
                return None
            if not (companion_root / "metadata.yaml").is_file():
                return None
            return companion_root

        try:
            from astrbot.dashboard.services import plugin_page_service as service_mod

            original_get_root = getattr(service_mod.PluginPageService, "get_plugin_root_dir", None)
            if callable(original_get_root) and not getattr(
                original_get_root, "_private_companion_root_fallback", False
            ):
                def _get_plugin_root_with_fallback(page_service, plugin):
                    try:
                        root = original_get_root(page_service, plugin)
                    except (FileNotFoundError, ValueError):
                        root = None
                    fallback = _usable_companion_root()
                    if _is_companion_plugin(plugin) and fallback is not None:
                        if root is None or not (root / "pages").is_dir():
                            return fallback
                    if root is None:
                        raise FileNotFoundError("Plugin directory metadata is missing")
                    return root

                _get_plugin_root_with_fallback._private_companion_root_fallback = True
                service_mod.PluginPageService.get_plugin_root_dir = _get_plugin_root_with_fallback
                changed.append("page_root_fallback")
        except Exception as exc:
            logger.debug("[PrivateCompanion] AstrBot 新插件页目录兼容补丁跳过: %s", _single_line(exc, 120))

        try:
            from astrbot.dashboard.routes import plugin as legacy_route_mod

            original_get_root = getattr(legacy_route_mod.PluginRoute, "_get_plugin_root_dir", None)
            if callable(original_get_root) and not getattr(
                original_get_root, "_private_companion_root_fallback", False
            ):
                def _get_plugin_root_with_fallback(route, plugin):
                    try:
                        root = original_get_root(route, plugin)
                    except (FileNotFoundError, ValueError):
                        root = None
                    fallback = _usable_companion_root()
                    if _is_companion_plugin(plugin) and fallback is not None:
                        if root is None or not (root / "pages").is_dir():
                            return fallback
                    if root is None:
                        raise FileNotFoundError("Plugin directory metadata is missing")
                    return root

                _get_plugin_root_with_fallback._private_companion_root_fallback = True
                legacy_route_mod.PluginRoute._get_plugin_root_dir = _get_plugin_root_with_fallback
                changed.append("legacy_page_root_fallback")

                original_logo_token = getattr(legacy_route_mod.PluginRoute, "get_plugin_logo_token", None)
                if callable(original_logo_token) and not getattr(
                    original_logo_token, "_private_companion_root_fallback", False
                ):
                    async def _get_logo_token_with_fallback(route, logo_path):
                        candidate = Path(str(logo_path or ""))
                        fallback = _usable_companion_root()
                        if (
                            fallback is not None
                            and candidate.name.lower() == "logo.png"
                            and not candidate.is_file()
                            and "private-companion" in str(candidate).lower()
                        ):
                            return await original_logo_token(route, str(fallback / "logo.png"))
                        return await original_logo_token(route, logo_path)

                    _get_logo_token_with_fallback._private_companion_root_fallback = True
                    legacy_route_mod.PluginRoute.get_plugin_logo_token = _get_logo_token_with_fallback
                    changed.append("legacy_logo_root_fallback")

                for method_name in ("get_plugin_readme", "get_plugin_changelog"):
                    original_method = getattr(legacy_route_mod.PluginRoute, method_name, None)
                    if not callable(original_method) or getattr(
                        original_method, "_private_companion_root_fallback", False
                    ):
                        continue

                    async def _read_document_with_fallback(route, _original=original_method, _kind=method_name):
                        result = await _original(route)
                        if not isinstance(result, dict) or result.get("status") != "error":
                            return result
                        try:
                            plugin_name = legacy_route_mod.request.args.get("name")
                        except Exception:
                            plugin_name = None
                        fallback = _usable_companion_root()
                        if str(plugin_name or "").strip() != PLUGIN_ID or fallback is None:
                            return result
                        candidates = (
                            ("README.md",)
                            if _kind == "get_plugin_readme"
                            else ("CHANGELOG.md", "changelog.md", "CHANGELOG", "changelog")
                        )
                        for filename in candidates:
                            path = fallback / filename
                            if not path.is_file():
                                continue
                            try:
                                content = path.read_text(encoding="utf-8")
                            except OSError:
                                return result
                            message = (
                                "成功获取README内容"
                                if _kind == "get_plugin_readme"
                                else "成功获取更新日志"
                            )
                            return legacy_route_mod.Response().ok(
                                {"content": content}, message
                            ).__dict__
                        return result

                    _read_document_with_fallback._private_companion_root_fallback = True
                    setattr(legacy_route_mod.PluginRoute, method_name, _read_document_with_fallback)
                changed.append("legacy_readme_root_fallback")
        except Exception as exc:
            logger.debug("[PrivateCompanion] AstrBot 旧插件页目录兼容补丁跳过: %s", _single_line(exc, 120))

        try:
            from astrbot.dashboard.services import plugin_service as service_mod

            original_resolve_dir = getattr(service_mod.PluginService, "resolve_plugin_dir", None)
            if callable(original_resolve_dir) and not getattr(
                original_resolve_dir, "_private_companion_root_fallback", False
            ):
                def _resolve_plugin_dir_with_fallback(plugin_service, plugin_name):
                    try:
                        return original_resolve_dir(plugin_service, plugin_name)
                    except Exception:
                        fallback = _usable_companion_root()
                        if str(plugin_name or "").strip() == PLUGIN_ID and fallback is not None:
                            return fallback
                        raise

                _resolve_plugin_dir_with_fallback._private_companion_root_fallback = True
                service_mod.PluginService.resolve_plugin_dir = _resolve_plugin_dir_with_fallback
                changed.append("readme_root_fallback")

            original_logo_url = getattr(service_mod.PluginService, "resolve_plugin_logo_url", None)
            if callable(original_logo_url) and not getattr(
                original_logo_url, "_private_companion_root_fallback", False
            ):
                async def _resolve_logo_url_with_fallback(plugin_service, plugin, logo_token_resolver):
                    fallback = _usable_companion_root()
                    logo_path = str(getattr(plugin, "logo_path", "") or "")
                    candidate = Path(logo_path)
                    if (
                        fallback is not None
                        and _is_companion_plugin(plugin)
                        and candidate.name.lower() == "logo.png"
                        and not candidate.is_file()
                    ):
                        logo_path = str(fallback / "logo.png")
                        token = await logo_token_resolver(logo_path)
                        return f"/api/file/{token}" if token else None
                    return await original_logo_url(plugin_service, plugin, logo_token_resolver)

                _resolve_logo_url_with_fallback._private_companion_root_fallback = True
                service_mod.PluginService.resolve_plugin_logo_url = _resolve_logo_url_with_fallback
                changed.append("logo_root_fallback")
        except Exception as exc:
            logger.debug("[PrivateCompanion] AstrBot README目录兼容补丁跳过: %s", _single_line(exc, 120))

        if changed:
            logger.info("[PrivateCompanion] AstrBot 插件页入口兼容已应用: %s", ", ".join(changed))

    def _register_page_api_if_available(self) -> None:
        try:
            from .page_api import PrivateCompanionPageApi

            self.page_api = PrivateCompanionPageApi(self)
        except Exception as e:
            logger.warning(f"[PrivateCompanion] 插件拓展页面 API 初始化失败: {e}", exc_info=True)
            return

        if hasattr(self.context, "register_web_api"):
            try:
                self.page_api.register_routes()
                logger.info("[PrivateCompanion] 插件拓展页面 API 已注册")
            except Exception as e:
                logger.warning(f"[PrivateCompanion] 插件拓展页面 API 注册失败: {e}", exc_info=True)
        else:
            logger.debug("[PrivateCompanion] 当前 AstrBot 版本未提供 register_web_api,跳过插件拓展页面 API 注册")

        try:
            from .standalone_webui import StandaloneWebUIServer

            self.standalone_webui = StandaloneWebUIServer(self, self.page_api)
        except Exception as e:
            self.standalone_webui = None
            logger.warning(f"[PrivateCompanion] 独立陪伴 WebUI 初始化失败: {e}", exc_info=True)

    def _patch_livingmemory_processor_compat(self) -> None:
        """Work around LivingMemory versions whose MemoryProcessor lacks config."""
        if not self.enable_livingmemory_integration:
            return
        module = (
            sys.modules.get("data.plugins.astrbot_plugin_livingmemory.core.processors.memory_processor")
            or sys.modules.get("astrbot_plugin_livingmemory.core.processors.memory_processor")
        )
        if module is None:
            return
        processor_cls = getattr(module, "MemoryProcessor", None)
        if processor_cls is None or hasattr(processor_cls, "config"):
            return
        try:
            setattr(processor_cls, "config", {"atom_enabled": True})
            logger.info("[PrivateCompanion] 已为 LivingMemory MemoryProcessor 添加 config 兼容兜底")
        except Exception as exc:
            logger.debug("[PrivateCompanion] LivingMemory 兼容补丁应用失败: %s", _single_line(exc, 120))

    def _integrated_plugin_installed(self, *names: str) -> bool:
        roots = [
            Path(__file__).resolve().parent.parent,
            Path(self.data_dir).parent.parent / "plugins",
        ]
        for root in roots:
            for name in names:
                try:
                    path = root / name
                    if (path / "main.py").exists() or (path / "metadata.yaml").exists():
                        return True
                except Exception:
                    continue
        return False

    def _report_integrated_feature_conflicts(self) -> None:
        rules = [
            (
                "enable_environment_perception",
                ("astrbot_plugin_llmperception", "astrbot_plugin_LLMPerception"),
                "检测到已安装 astrbot_plugin_LLMPerception,本插件内置环境感知仍按用户配置保持%s；如同时开启可能重复注入。",
            ),
            (
                "enable_group_scene_awareness",
                ("astrbot_plugin_context_aware",),
                "检测到已安装 astrbot_plugin_context_aware,本插件群聊场景感知仍按用户配置保持%s；如同时开启可能重复注入。",
            ),
            (
                "enable_atrelay_tools",
                ("astrbot_plugin_atrelay",),
                "检测到已安装 astrbot_plugin_atrelay,本插件跨群转述与 @ 群友工具仍按用户配置保持%s；如同时开启可能出现重复工具。",
            ),
        ]
        for key, plugin_names, message in rules:
            if self._integrated_plugin_installed(*plugin_names):
                state = "开启" if bool(getattr(self, key, False)) else "关闭"
                logger.info("[PrivateCompanion] %s", message % state)
        if self._integrated_plugin_installed("astrbot_plugin_proactive_chat"):
            enabled = bool(runtime_persona_setting(self, 'enable_proactive_chat_integration', True))
            review_mode = str(runtime_persona_setting(self, 'proactive_chat_bridge_review_mode', "local") or "local")
            logger.info(
                "[PrivateCompanion] 已检测到 Proactive Chat，私聊主动发送联动%s（复核模式=%s）；不会修改对方调度配置。",
                "开启" if enabled else "关闭",
                review_mode,
            )

    def _worldview_mode_effective(self) -> str:
        mode = str(runtime_persona_setting(self, 'worldview_adaptation_mode', "auto") or "auto")
        if mode != "auto":
            return mode
        source = " ".join(
            part
            for part in (
                runtime_persona_setting(self, 'schedule_persona_prompt', ''),
                runtime_persona_setting(self, 'schedule_worldview_prompt', ''),
                runtime_persona_setting(self, 'worldview_adaptation_prompt', ''),
                runtime_persona_setting(self, 'bot_name', '小星'),
            )
            if part
        )
        if any(token in source for token in ("异世界", "冒险者", "魔法", "公会", "地下城", "勇者", "魔王", "骑士", "法师", "精灵", "龙")):
            return "fantasy"
        if any(token in source for token in ("赛博", "星舰", "宇宙", "殖民", "仿生", "义体", "AI 城市", "空间站")):
            return "sci_fi"
        return "modern"

    def _worldview_terms(self) -> dict[str, str]:
        mode = self._worldview_mode_effective()
        if mode == "fantasy":
            return {
                "mode": "fantasy",
                "life_log": "旅记/营地手札",
                "bookshelf": "行囊书匣",
                "secret_drawer": "暗格",
                "screen": "水晶映像",
                "video": "吟游诗人的影像记录",
                "group_chat": "酒馆/公会里的闲谈",
                "private_chat": "私信",
                "schedule": "旅途安排",
                "bored_watch": "翻看见闻记录",
                "reading_archive": "翻暗格里的藏书",
            }
        if mode == "sci_fi":
            return {
                "mode": "sci_fi",
                "life_log": "航行日志",
                "bookshelf": "私人资料柜",
                "secret_drawer": "加密夹层",
                "screen": "终端画面",
                "video": "影像流记录",
                "group_chat": "频道通信",
                "private_chat": "私密通信",
                "schedule": "今日流程",
                "bored_watch": "浏览影像流",
                "reading_archive": "翻加密藏本",
            }
        return {
            "mode": "modern",
            "life_log": "日记",
            "bookshelf": "资料柜",
            "secret_drawer": "夹层",
            "screen": "屏幕",
            "video": "B 站视频",
            "group_chat": "群聊",
            "private_chat": "私聊",
            "schedule": "日程",
            "bored_watch": "刷视频",
            "reading_archive": "翻资料柜夹层",
        }

    def _format_worldview_adaptation_prompt(
        self,
        *,
        include_heading: bool = True,
    ) -> str:
        mode = self._worldview_mode_effective()
        if mode == "off":
            return ""
        terms = self._worldview_terms()
        custom = _single_line(runtime_persona_setting(self, 'worldview_adaptation_prompt', ""), 1200)
        base = [
            f"当前适配模式：{mode}。",
            "插件功能名称只代表现实实现；最终表达必须服从当前人格、身份和世界观。",
            f"可把日程理解为：{terms['schedule']}；资料柜理解为：{terms['bookshelf']}；隐藏夹层理解为：{terms['secret_drawer']}；群聊理解为：{terms['group_chat']}；私聊理解为：{terms['private_chat']}。",
            f"外部或整合动作可转译为世界内等价行为：看视频≈{terms['bored_watch']}；识屏≈观察{terms['screen']}；资料归档≈{terms['reading_archive']}；空间动态≈公开生活札记/状态栏。",
            "如果人格/世界观与现代词冲突，优先使用世界内说法；但不要编造会改变功能结果的事实，也不要向用户解释后台实现。",
            "未在人格、世界观、关系网、近期对话或用户输入中明确出现的人际关系不得凭空添加；家人、父母、兄弟姐妹、亲戚、室友、同学、老师、同事、朋友、邻居、前辈、后辈等关系只能在材料有依据时使用。",
        ]
        if include_heading:
            base.insert(0, "【世界观适配】")
        if custom:
            base.append(f"自定义适配：{custom}")
        knowledge_formatter = getattr(self, "_format_roleplay_knowledge_context", None)
        if callable(knowledge_formatter):
            knowledge_context = knowledge_formatter(purpose="worldview", max_chars=1800, max_chunks=10)
            if knowledge_context:
                base.append(knowledge_context)
        return "\n".join(base)

    def _format_worldview_adaptation_prompt_section(self) -> dict[str, Any]:
        return prompt_section(
            "世界观适配",
            self._format_worldview_adaptation_prompt(include_heading=False),
        )

    def _livingmemory_plugin_dir(self) -> Path:
        candidates = [
            Path(__file__).resolve().parent.parent / "astrbot_plugin_livingmemory",
            Path(self.data_dir).parent / "astrbot_plugin_livingmemory",
            Path(self.data_dir).parent.parent / "plugins" / "astrbot_plugin_livingmemory",
        ]
        for path in candidates:
            if (path / "main.py").exists():
                return path
        return candidates[0]

    def _livingmemory_tool_available(self) -> bool | None:
        """Return tool availability when AstrBot exposes its runtime tool registry.

        ``None`` means the host is too old to expose a tool manager. A tool is
        usable only when it is active and its handler belongs to LivingMemory;
        a directory on disk is not evidence that the plugin is loaded.
        """
        tool_name = _single_line(getattr(self, "livingmemory_tool_name", ""), 60) or "recall_long_term_memory"
        context = getattr(self, "context", None)
        manager = None
        getter = getattr(context, "get_llm_tool_manager", None)
        if callable(getter):
            try:
                manager = getter()
            except Exception:
                manager = None
        if manager is None:
            provider_manager = getattr(context, "provider_manager", None)
            manager = getattr(provider_manager, "llm_tools", None)
        if manager is None:
            return None

        candidates: list[Any] = []
        get_func = getattr(manager, "get_func", None)
        if callable(get_func):
            try:
                tool = get_func(tool_name)
                if tool is not None:
                    candidates.append(tool)
            except Exception:
                pass
        try:
            candidates.extend(
                tool
                for tool in list(getattr(manager, "func_list", []) or [])
                if str(getattr(tool, "name", "") or "") == tool_name
            )
        except Exception:
            pass

        for tool in candidates:
            if not bool(getattr(tool, "active", True)):
                continue
            module_path = str(getattr(tool, "handler_module_path", "") or "")
            if not module_path:
                handler = getattr(tool, "handler", None)
                module_path = str(getattr(handler, "__module__", "") or "")
            if "livingmemory" in module_path.lower():
                return True
        return False

    @staticmethod
    def _livingmemory_module_loaded() -> bool:
        prefixes = (
            "astrbot_plugin_livingmemory",
            "data.plugins.astrbot_plugin_livingmemory",
        )
        return any(
            module is not None
            and any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes)
            for name, module in sys.modules.items()
        )

    def _livingmemory_available(self) -> bool:
        runtime_available = self._livingmemory_tool_available()
        if runtime_available is not None:
            return runtime_available
        # Compatibility fallback for old AstrBot versions without a tool manager.
        return self._livingmemory_module_loaded()

    def _format_livingmemory_guidance(
        self,
        *,
        scope: str = "private",
        include_heading: bool = True,
    ) -> str:
        if not self.enable_livingmemory_integration or not self._livingmemory_available():
            return ""
        tool_name = _single_line(self.livingmemory_tool_name, 60) or "recall_long_term_memory"
        if scope == "group":
            boundary = "群聊只查当前群可公开使用的旧事。"
        else:
            boundary = "私聊可查当前用户相关的旧约定、偏好和共同经历。"
        return (
            ("【长期记忆检索】\n" if include_heading else "")
            +
            f"上下文不够时可用 `{tool_name}` 查记忆。只有当前工具列表确实提供该工具时才调用；工具未出现时不要猜测、重试或输出工具调用。{boundary}结果只作接话背景。\n"
            "召回结果里出现人名、昵称、QQ 或群成员别名时,不要直接当作稳定身份；能查关系网时先用关系网确认,不能确认就按召回文本里的具体说话人原样转述。\n"
            "如果召回到 Bot 曾说自己在吃饭、整理、犯困、路上、创作等状态/日程，只能理解为当时 Bot 的拟人化表达或历史自称；不要写成用户事实、现实证据或持续状态。"
        )

    def _format_livingmemory_status(self) -> str:
        # Check for "我会牢牢记住你" (RememberYou) bridge first
        companion_bridge = None
        try:
            companion_bridge = self._memory_companion_bridge()  # type: ignore[attr-defined]
        except Exception:
            pass
        if companion_bridge is not None:
            display_name = getattr(companion_bridge, "display_name", "") or "我会牢牢记住你"
            return (
                f"记忆插件协同：已检测到{display_name}。\n"
                f"协同开关：{'开启' if self.enable_livingmemory_integration else '关闭'}\n"
                f"情绪漂移联动：{'开启' if getattr(self, 'enable_memory_companion_emotional_drift', True) else '关闭'}\n"
                f"跨窗口情绪连续性：{'开启' if getattr(self, 'enable_memory_companion_cross_window_emotion', True) else '关闭'}\n"
                f"梦境碎片写入：{'开启' if getattr(self, 'enable_memory_companion_dream_fragment', True) else '关闭'}\n"
                f"未完成话题取材：{'开启' if getattr(self, 'enable_memory_companion_open_loop_search', True) else '关闭'}\n"
                f"功能上下文读取：{'开启' if getattr(self, 'enable_memory_companion_feature_context', True) else '关闭'}\n"
                "用途：长期记忆沉淀、短上下文补充、情绪漂移、梦境碎片和跨会话连续性。\n"
                "建议：保留本插件的生活状态/关系/群聊气氛层,把大规模长期检索交给记忆插件。"
            )
        companion_presence: dict[str, Any] = {}
        presence_getter = getattr(self, "_memory_companion_presence", None)
        if callable(presence_getter):
            try:
                candidate = presence_getter()
                if isinstance(candidate, dict):
                    companion_presence = candidate
            except Exception:
                companion_presence = {}
        if companion_presence.get("detected"):
            display_name = _single_line(companion_presence.get("display_name"), 80) or "我会牢牢记住你"
            version = _single_line(companion_presence.get("version"), 40) or "未知"
            plugin_path = _single_line(companion_presence.get("plugin_dir"), 260)
            reason = _single_line(companion_presence.get("reason"), 80)
            if reason == "bridge_disabled":
                detail = "陪伴插件侧的 MemoryCompanion Bridge 开关已关闭。"
            elif not companion_presence.get("activated", False):
                detail = "AstrBot 已发现插件，但插件当前未启用。"
            elif not companion_presence.get("loaded", False):
                detail = "已发现插件文件，但 AstrBot 尚未加载运行实例；可检查插件是否启用并在重载后刷新页面。"
            elif reason in {
                "capability_probe_missing",
                "capability_probe_exception",
                "capability_probe_invalid",
                "capability_contract_mismatch",
            }:
                detail = "插件已运行，但桥接能力协议未就绪或版本不兼容；请同步更新两个插件后重载。"
            elif reason == "optional_dependency_missing":
                detail = "插件已运行，但桥接所需的可选模型依赖缺失，当前已临时降级。"
            else:
                detail = "插件已检测到，但桥接实例暂未就绪；页面会自动重新探测。"
            lines = [
                f"记忆插件协同：已检测到{display_name}，但当前未建立可用桥接。",
                f"版本：{version}",
                f"协同开关：{'开启' if self.enable_livingmemory_integration else '关闭'}",
                detail,
            ]
            if plugin_path:
                lines.insert(2, f"路径：{plugin_path}")
            return "\n".join(lines)
        plugin_dir = self._livingmemory_plugin_dir()
        if not plugin_dir.exists():
            return (
                "记忆插件协同：未检测到\"我会牢牢记住你\"或 LivingMemory。\n"
                "当前会继续使用本插件内置的轻量记忆、片段记忆和群聊观察。"
            )
        metadata = plugin_dir / "metadata.yaml"
        version = ""
        if metadata.exists():
            try:
                text = metadata.read_text(encoding="utf-8")
                match = re.search(r"version:\s*([^\n]+)", text)
                if match:
                    version = match.group(1).strip()
            except Exception:
                version = ""
        if not self._livingmemory_available():
            return (
                "记忆插件协同：检测到 LivingMemory 文件，但插件未加载或召回工具未启用。\n"
                f"路径：{plugin_dir}\n"
                f"版本：{version or '未知'}\n"
                f"协同开关：{'开启' if self.enable_livingmemory_integration else '关闭'}\n"
                "当前不会注入 LivingMemory 召回提示；启用插件及其召回工具后会自动恢复。"
            )
        return (
            "记忆插件协同：已检测到 LivingMemory。\n"
            f"路径：{plugin_dir}\n"
            f"版本：{version or '未知'}\n"
            f"协同开关：{'开启' if self.enable_livingmemory_integration else '关闭'}\n"
            f"召回工具名：{self.livingmemory_tool_name or 'recall_long_term_memory'}\n"
            "用途：长期记忆、BM25/Faiss 混合检索、图谱记忆和 Agent 主动回忆。\n"
            "建议：保留本插件的生活状态/关系/群聊气氛层,把大规模长期检索交给 LivingMemory。"
        )

    def _llmperception_plugin_dir(self) -> Path:
        candidates = [
            Path(__file__).resolve().parent.parent / "astrbot_plugin_llmperception",
            Path(__file__).resolve().parent.parent / "astrbot_plugin_LLMPerception",
            Path(self.data_dir).parent.parent / "plugins" / "astrbot_plugin_llmperception",
            Path(self.data_dir).parent.parent / "plugins" / "astrbot_plugin_LLMPerception",
        ]
        for path in candidates:
            if (path / "main.py").exists():
                return path
        return candidates[0]

    def _llmperception_available(self) -> bool:
        try:
            return (self._llmperception_plugin_dir() / "main.py").exists()
        except Exception:
            return False

    def _context_aware_available(self) -> bool:
        return self._integrated_plugin_installed("astrbot_plugin_context_aware")

    def _atrelay_plugin_available(self) -> bool:
        return self._integrated_plugin_installed("astrbot_plugin_atrelay")

    def _platform_display_name(self, value: str) -> str:
        raw = _single_line(value, 40).lower()
        return _PLATFORM_DISPLAY_NAMES.get(raw, _single_line(value, 40) or self.target_platform or "未知平台")

    def _message_type_label(self, event: AstrMessageEvent) -> str:
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return "私聊"
        except Exception:
            pass
        try:
            if bool(getattr(event, "is_group_chat", lambda: False)()):
                return "群聊"
        except Exception:
            pass
        getter = getattr(event, "get_message_type", None)
        message_type = None
        if callable(getter):
            try:
                message_type = getter()
            except Exception:
                message_type = None
        friend_type = getattr(MessageType, "FRIEND_MESSAGE", None)
        group_type = getattr(MessageType, "GROUP_MESSAGE", None)
        if message_type == friend_type or str(message_type).lower().endswith("friend_message"):
            return "私聊"
        if message_type == group_type or str(message_type).lower().endswith("group_message"):
            return "群聊"
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        if ":GroupMessage:" in umo:
            return "群聊"
        if ":FriendMessage:" in umo:
            return "私聊"
        return "当前会话"

    async def _format_platform_perception(self, event: AstrMessageEvent) -> str:
        if not runtime_persona_setting(self, 'enable_platform_perception', True):
            return ""
        platform = ""
        getter = getattr(event, "get_platform_name", None)
        if callable(getter):
            try:
                platform = _single_line(getter(), 40)
            except Exception:
                platform = ""
        if not platform:
            platform = str(getattr(event, "unified_msg_origin", "") or "").split(":")[0]
        if not platform:
            platform = self.target_platform or "aiocqhttp"
        parts = [self._platform_display_name(platform), self._message_type_label(event)]
        group_id = self._extract_group_id_from_event(event)
        group_name = ""
        if group_id:
            message_obj = getattr(event, "message_obj", None)
            group_obj = getattr(message_obj, "group", None) if message_obj is not None else None
            for attr in ("group_name", "name", "display_name"):
                value = getattr(group_obj, attr, None) if group_obj is not None else None
                if value:
                    group_name = _single_line(value, 50)
                    break
            get_group = getattr(event, "get_group", None)
            if not group_name and callable(get_group):
                try:
                    value = get_group(group_id=group_id)
                    if hasattr(value, "__await__"):
                        value = await value
                    group_name = _single_line(getattr(value, "group_name", "") or getattr(value, "name", ""), 50)
                except Exception:
                    group_name = ""
            parts.append(f"群号{group_id}" + (f"({group_name})" if group_name else ""))
        component_types: set[str] = set()
        for comp in self._event_components(event):
            class_name = comp.__class__.__name__.lower()
            if class_name == "image":
                component_types.add("含图片")
            elif class_name in {"record", "voice", "audio"}:
                component_types.add("含语音")
            elif class_name == "video":
                component_types.add("含视频")
        parts.extend(sorted(component_types))
        return "、".join(part for part in parts if part)

    @staticmethod
    def _provider_config_value(provider: Any, *keys: str) -> str:
        config = getattr(provider, "provider_config", None) or getattr(provider, "config", None) or {}
        for key in keys:
            value = ""
            if isinstance(config, dict):
                value = str(config.get(key, "") or "")
            else:
                value = str(getattr(config, key, "") or "")
            if value.strip():
                return value.strip()
        return ""

    def _provider_identity_label(self, provider_id: str = "", provider: Any | None = None) -> str:
        safe_id = _single_line(provider_id, 120)
        if provider is None and safe_id:
            getter = getattr(self.context, "get_provider_by_id", None)
            if callable(getter):
                try:
                    provider = getter(safe_id)
                except Exception:
                    provider = None
        if provider is None:
            return safe_id or "AstrBot 默认会话模型"
        name = self._provider_display_name(provider, safe_id)
        model = self._provider_config_value(provider, "model", "model_name", "api_model", "model_id")
        provider_id = safe_id or self._provider_config_value(provider, "id", "provider_id") or _single_line(getattr(provider, "provider_id", ""), 120)
        pieces = []
        if name:
            pieces.append(name)
        if model and model not in pieces:
            pieces.append(model)
        if provider_id and provider_id not in pieces:
            pieces.append(provider_id)
        return " / ".join(_single_line(piece, 120) for piece in pieces if piece) or "AstrBot 默认会话模型"

    @classmethod
    def _provider_display_name(cls, provider: Any, provider_id: str = "") -> str:
        explicit_name = (
            cls._provider_config_value(provider, "name", "display_name", "label", "title")
            or _single_line(getattr(provider, "name", ""), 80)
            or _single_line(getattr(provider, "display_name", ""), 80)
        )
        if explicit_name and not cls._provider_name_is_protocol(explicit_name):
            return explicit_name
        safe_id = _single_line(provider_id, 120)
        if safe_id and not cls._provider_name_is_protocol(safe_id):
            return safe_id
        inferred = cls._provider_vendor_from_config(provider)
        if inferred:
            return inferred
        protocol_name = cls._provider_config_value(provider, "provider", "type", "provider_type")
        return explicit_name or protocol_name or safe_id

    @staticmethod
    def _provider_name_is_protocol(value: Any) -> bool:
        text = str(value or "").strip().lower()
        normalized = re.sub(r"[\s_\-]+", "", text)
        return normalized in {
            "openai",
            "openai兼容",
            "openaicompatible",
            "compatible",
            "兼容",
            "兼容模式",
        }

    @classmethod
    def _provider_vendor_from_config(cls, provider: Any) -> str:
        source = " ".join(
            cls._provider_config_value(
                provider,
                "api_base",
                "base_url",
                "api_base_url",
                "api_url",
                "endpoint",
                "url",
                "model",
                "model_name",
                "api_model",
                "model_id",
            ).split()
        ).lower()
        if not source:
            return ""
        try:
            parsed = urlparse(source if "://" in source else f"https://{source}")
            host = parsed.netloc.lower()
        except Exception:
            host = ""
        haystack = f"{source} {host}"
        vendors = [
            ("火山引擎", ("volces.com", "volcengine", "huoshan", "火山", "doubao", "ark.cn-beijing")),
            ("DeepSeek", ("deepseek.com", "deepseek")),
            ("阿里云百炼", ("dashscope", "aliyuncs.com", "bailian", "百炼", "qwen", "tongyi")),
            ("OpenRouter", ("openrouter.ai", "openrouter")),
            ("硅基流动", ("siliconflow.cn", "siliconflow")),
            ("智谱", ("bigmodel.cn", "zhipu", "glm")),
            ("月之暗面", ("moonshot.cn", "moonshot", "kimi")),
            ("Google", ("generativelanguage.googleapis.com", "googleapis.com", "gemini")),
            ("Anthropic", ("anthropic.com", "claude")),
            ("OpenAI", ("api.openai.com", "openai.com", "gpt-")),
        ]
        for label, needles in vendors:
            if any(needle in haystack for needle in needles):
                return label
        if host:
            core = host.split(":")[0]
            for prefix in ("api.", "ark.", "www."):
                if core.startswith(prefix):
                    core = core[len(prefix):]
            return core.split(".")[0] or ""
        return ""

    def _provider_model_label(self, provider_id: str = "", provider: Any | None = None) -> str:
        safe_id = _single_line(provider_id, 120)
        if provider is None and safe_id:
            getter = getattr(self.context, "get_provider_by_id", None)
            if callable(getter):
                try:
                    provider = getter(safe_id)
                except Exception:
                    provider = None
        if provider is None:
            return safe_id or "AstrBot 默认会话模型"
        model = self._provider_config_value(provider, "model", "model_name", "api_model", "model_id")
        return _single_line(model, 120) or safe_id or "AstrBot 默认会话模型"

    def _current_event_chat_provider_id(self, event: AstrMessageEvent) -> tuple[str, Any | None]:
        provider = None
        provider_id = ""
        try:
            provider_id = _single_line(event.get_extra("selected_provider"), 160)
        except Exception:
            provider_id = ""
        getter = getattr(self.context, "get_provider_by_id", None)
        if provider_id and callable(getter):
            try:
                provider = getter(provider_id)
            except Exception:
                provider = None
        if provider is None:
            get_using = getattr(self.context, "get_using_provider", None)
            if callable(get_using):
                umo = str(getattr(event, "unified_msg_origin", "") or "")
                try:
                    provider = get_using(umo=umo)
                except TypeError:
                    provider = None
                except Exception:
                    provider = None
                for args in ((umo,), ()):
                    if provider is not None:
                        break
                    try:
                        provider = get_using(*args)
                        break
                    except TypeError:
                        continue
                    except Exception:
                        provider = None
                        break
        if provider is not None and not provider_id:
            provider_id = self._provider_config_value(provider, "id", "provider_id") or _single_line(getattr(provider, "provider_id", ""), 160)
        return provider_id, provider

    def _format_model_perception(self, event: AstrMessageEvent) -> str:
        if not runtime_persona_setting(self, 'enable_model_perception', True):
            return ""
        lines: list[str] = []
        chat_provider_id, chat_provider = self._current_event_chat_provider_id(event)
        lines.append(f"对话模型={self._provider_model_label(chat_provider_id, chat_provider)}")
        vision_id, vision_source, _ = self._private_image_caption_provider_id(str(getattr(event, "unified_msg_origin", "") or ""))
        if vision_id:
            source_label = {
                "astrbot_image_caption": "首选识图模型",
                "plugin_vision": "备选识图模型",
                "plugin_vision_fallback": "备选识图模型兜底",
            }.get(vision_source, vision_source or "视觉转述模型")
            lines.append(f"视觉转述模型={source_label} / {self._provider_model_label(vision_id)}")
        photo_generation = self._format_photo_generation_perception()
        if photo_generation:
            lines.append(f"生图能力={photo_generation}")
        tts_perception = self._format_tts_model_perception(event)
        if tts_perception:
            lines.append(f"TTS能力={tts_perception}")
        return "；".join(lines)

    def _format_tts_model_perception(self, event: AstrMessageEvent) -> str:
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        config: dict[str, Any] = {}
        get_config = getattr(self.context, "get_config", None)
        if callable(get_config):
            try:
                config = get_config(umo) if umo else get_config()
                if not isinstance(config, dict):
                    config = {}
            except Exception:
                config = {}
        raw_settings = config.get("provider_tts_settings", {}) if isinstance(config, dict) else {}
        provider_settings = dict(raw_settings) if isinstance(raw_settings, dict) else {}
        provider = None
        provider_getter = getattr(self.context, "get_using_tts_provider", None)
        if callable(provider_getter):
            for args in ((umo,), ()):
                try:
                    provider = provider_getter(*args)
                    break
                except TypeError:
                    continue
                except Exception:
                    provider = None
                    break
        synthesis_resolver = getattr(self, "_resolve_tts_synthesis_provider", None)
        if callable(synthesis_resolver):
            try:
                provider = synthesis_resolver(event, provider)
            except Exception:
                pass

        enhancement_enabled = bool(runtime_persona_setting(self, 'enable_tts_enhancement', False))
        if provider is None:
            availability = "已配置但当前会话不可用" if bool(provider_settings.get("enable", False)) else "当前会话未配置 TTS Provider"
        else:
            provider_id = (
                self._provider_config_value(provider, "id", "provider_id")
                or _single_line(getattr(provider, "provider_id", ""), 120)
            )
            provider_label = self._provider_identity_label(provider_id, provider)
            if provider_label == "AstrBot 默认会话模型":
                provider_label = _single_line(provider.__class__.__name__, 80) or "可用 TTS Provider"
            kind_getter = getattr(self, "_tts_provider_kind", None)
            try:
                provider_kind = kind_getter(provider, provider_settings) if callable(kind_getter) else ""
            except Exception:
                provider_kind = ""
            kind_label = {
                "fishaudio": "FishAudio",
                "gsv": "GPT-SoVITS",
                "openai": "OpenAI TTS",
                "edge": "Edge TTS",
                "azure": "Azure TTS",
                "gemini": "Gemini TTS",
                "minimax": "MiniMax TTS",
                "mimo_tts": "MiMo TTS",
                "aliyun": "阿里云 TTS",
                "volcengine": "火山引擎 TTS",
            }.get(provider_kind, "")
            if kind_label and kind_label.lower() not in provider_label.lower():
                provider_label = f"{kind_label} / {provider_label}"
            availability = f"合成 Provider 可用 / {provider_label}"

        parts = [availability, f"TTS强化:{'开启' if enhancement_enabled else '关闭'}"]
        if enhancement_enabled:
            mode_label = {
                "fast_tag": "快速标签",
                "postprocess": "后处理",
            }.get(str(runtime_persona_setting(self, 'tts_generation_mode', "fast_tag") or "fast_tag"), "快速标签")
            scope_label = "全量转换" if str(runtime_persona_setting(self, 'tts_conversion_scope', "partial") or "partial") == "full" else "局部转换"
            delivery_label = "仅语音" if str(runtime_persona_setting(self, 'tts_delivery_mode', "voice_and_text") or "voice_and_text") == "voice_only" else "语音+文字"
            language_getter = getattr(self, "_tts_language_label", None)
            language_label = language_getter() if callable(language_getter) else {
                "ja": "日语",
                "zh": "中文",
                "en": "英语",
            }.get(str(runtime_persona_setting(self, 'tts_voice_language', "zh") or "zh"), "中文")
            conversion_id = _single_line(runtime_persona_setting(self, 'tts_conversion_provider_id', ""), 120)
            if conversion_id:
                conversion_label = self._provider_model_label(conversion_id)
            else:
                conversion_label = "跟随当前对话模型"
            parts.extend(
                [
                    f"文本转换模型:{conversion_label}",
                    f"路径:{mode_label}",
                    f"语种:{language_label}",
                    f"范围:{scope_label}",
                    f"交付:{delivery_label}",
                ]
            )
        return " / ".join(parts)

    def _format_photo_generation_perception(self) -> str:
        if not (
            bool(runtime_persona_setting(self, 'enable_photo_text_action', False))
            or bool(runtime_persona_setting(self, 'enable_natural_language_photo_generation', False))
        ):
            return ""
        preferred = _single_line(runtime_persona_setting(self, 'photo_generation_backend', ""), 30) or "auto"
        external_model = _single_line(getattr(self, "external_image_api_model", ""), 80)
        configured_endpoints = getattr(self, "external_image_api_endpoints", [])
        endpoint_queue: list[dict[str, Any]] = []
        if isinstance(configured_endpoints, list) and configured_endpoints:
            queue_getter = getattr(self, "_external_image_api_endpoint_queue", None)
            if callable(queue_getter):
                try:
                    endpoint_queue = [
                        endpoint
                        for endpoint in queue_getter(include_incomplete=True, include_disabled=True)
                        if isinstance(endpoint, dict)
                    ]
                except Exception:
                    endpoint_queue = []
        platform = "openai"
        resolver = getattr(self, "_resolved_external_image_api_platform", None)
        if callable(resolver):
            try:
                platform = str(resolver() or "openai")
            except Exception:
                platform = "openai"
        comfyui_workflow = _single_line(getattr(self, "comfyui_text2img_workflow_name", ""), 60)
        selfie_workflow = _single_line(getattr(self, "comfyui_selfie_workflow_name", ""), 60)
        comfyui_available = bool(getattr(self, "_comfyui_photo_available", lambda: False)())
        sdgen_available = bool(getattr(self, "_sdgen_photo_available", lambda: False)())
        external_available = bool(getattr(self, "_external_photo_available", lambda: False)())
        tool_call_available = bool(getattr(self, "_custom_tool_photo_available", lambda: False)())
        tool_call_name = _single_line(getattr(self, "custom_photo_tool_name", ""), 80)

        def comfyui_label() -> str:
            labels = []
            if comfyui_workflow:
                labels.append(f"文生图:{comfyui_workflow}")
            if selfie_workflow and selfie_workflow != comfyui_workflow:
                labels.append(f"自拍:{selfie_workflow}")
            suffix = f" / {', '.join(labels)}" if labels else ""
            return f"ComfyUI{suffix}"

        def external_label() -> str:
            if endpoint_queue:
                ready_count = 0
                note_getter = getattr(self, "_external_image_api_endpoint_unavailable_note", None)
                for endpoint in endpoint_queue:
                    if callable(note_getter):
                        try:
                            if not note_getter(endpoint):
                                ready_count += 1
                        except Exception:
                            pass
                first = endpoint_queue[0] if endpoint_queue else {}
                first_model = _single_line(first.get("model"), 60) if isinstance(first, dict) else ""
                return f"在线图片 API 队列 {ready_count}/{len(endpoint_queue)} 可用 / 优先 {first_model or '未填模型'}"
            prefix = (
                "阿里云百炼"
                if platform == "bailian"
                else "MiniMax"
                if platform == "minimax"
                else "在线图片 API"
            )
            return f"{prefix} / {external_model or '未填模型'}"

        if preferred == "nai":
            nai_available = bool(getattr(self, "_nai_image_available", lambda: False)())
            return "NAI 生图（直连）" if nai_available else "NAI 生图直连（未检测到 NAI 生图插件）"
        if preferred == "external":
            return external_label()
        if preferred == "comfyui":
            return comfyui_label()
        if preferred == "sdgen":
            return "SDGen"
        if preferred == "tool_call":
            return f"函数工具 / {tool_call_name or '未配置'}" if tool_call_available else f"函数工具（未找到 {tool_call_name or '未配置'}）"
        if external_available:
            return f"auto -> {external_label()}"
        if comfyui_available:
            return f"auto -> {comfyui_label()}"
        if sdgen_available:
            return "auto -> SDGen"
        if external_model:
            return f"auto（候选：{external_label()}）"
        return "auto（当前无可用生图后端）"

    async def _format_environment_perception(
        self,
        event: AstrMessageEvent,
        *,
        include_heading: bool = True,
    ) -> str:
        checker = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        if callable(checker):
            if not checker("enable_environment_perception"):
                return ""
        elif not runtime_persona_setting(self, 'enable_environment_perception', True):
            return ""
        current = self._environment_now()
        lines = [
            "这是当前消息的背景边界，主要影响语境判断、节奏和措辞；如果用户明确问到时间、节日、平台或环境线索，可以按需要自然回答，没问到时就把它当作背景参考。",
        ]
        if include_heading:
            lines.insert(0, "【环境感知】")
        holiday = self._format_holiday_perception(current)
        if holiday:
            lines.append(f"时间：{current.strftime('%Y-%m-%d %H:%M')}（{holiday}）")
        else:
            lines.append(f"时间：{current.strftime('%Y-%m-%d %H:%M')}")
        season_parts = []
        lunar = self._format_lunar_perception(current)
        if lunar:
            season_parts.append(f"农历{lunar}")
        solar_term = self._format_solar_term_perception(current)
        if solar_term:
            season_parts.append(f"节气{solar_term}")
        almanac = self._format_almanac_perception(current)
        if almanac:
            season_parts.append(almanac)
        if season_parts:
            lines.append(f"时令：{'；'.join(season_parts)}")
        platform = await self._format_platform_perception(event)
        if platform:
            lines.append(f"会话：{platform}")
        try:
            is_private_chat = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private_chat = False
        if not is_private_chat:
            try:
                sender_id = _single_line(str(event.get_sender_id()), 40)
            except Exception:
                sender_id = ""
            sender_name = ""
            try:
                sender_name = _single_line(self._sender_display_name(event), 40)
            except Exception:
                sender_name = ""
            if sender_id:
                label = f"{sender_name}[QQ:{sender_id}]" if sender_name and sender_name != sender_id else f"QQ:{sender_id}"
                lines.append(
                    "群聊身份边界：本轮当前发言者是"
                    f"{label}；环境感知只提供当前消息背景，不能把上一位说话人的专属关系身份继承给当前发言者；"
                    "该 ID 只供内部判断，不要在回复正文里复述。"
                )
        model = self._format_model_perception(event)
        if model:
            lines.append(f"模型：{model}")
        return "\n".join(lines)

    async def _format_environment_perception_prompt_section(
        self,
        event: AstrMessageEvent,
    ) -> dict[str, Any]:
        return prompt_section(
            "环境感知",
            await self._format_environment_perception(
                event,
                include_heading=False,
            ),
        )

    def _environment_timezone(self) -> zoneinfo.ZoneInfo | None:
        timezone_name = _single_line(self.environment_perception_timezone, 64) or "Asia/Shanghai"
        try:
            return zoneinfo.ZoneInfo(timezone_name)
        except Exception:
            return None

    def _environment_now(self) -> datetime:
        tz = self._environment_timezone()
        return datetime.now(tz) if tz is not None else datetime.now()

    def _environment_fromtimestamp(self, timestamp: float) -> datetime:
        tz = self._environment_timezone()
        return datetime.fromtimestamp(timestamp, tz) if tz is not None else datetime.fromtimestamp(timestamp)

    def _environment_today_key(self) -> str:
        return self._environment_now().strftime("%Y-%m-%d")

    def _environment_now_minutes(self) -> int:
        current = self._environment_now()
        return current.hour * 60 + current.minute

    def _format_holiday_perception(self, current: datetime) -> str:
        if not runtime_persona_setting(self, 'enable_holiday_perception', True):
            return ""
        label, _ = self._current_time_period_label(current)
        weekday = "一二三四五六日"[current.weekday()]
        parts = [f"周{weekday}"]
        if runtime_persona_setting(self, 'holiday_country', 'CN') != "CN" or calendar_cn is None:
            parts.append("周末" if current.weekday() >= 5 else "工作日")
        else:
            try:
                if calendar_cn.is_holiday(current.date()):
                    name = _single_line(calendar_cn.get_holiday_detail(current.date())[1], 30)
                    parts.append(name or "节假日")
                elif calendar_cn.is_workday(current.date()):
                    parts.append("工作日")
                else:
                    parts.append("休息日")
            except Exception:
                parts.append("周末" if current.weekday() >= 5 else "工作日")
        parts.append(label)
        return "、".join(part for part in parts if part)

    def _format_lunar_perception(self, current: datetime) -> str:
        if not runtime_persona_setting(self, 'enable_lunar_perception', True) or Converter is None or Solar is None:
            return ""
        try:
            lunar = Converter.Solar2Lunar(Solar(current.year, current.month, current.day))
            month_index = max(1, min(12, int(getattr(lunar, "month", 1)))) - 1
            day_index = max(1, min(30, int(getattr(lunar, "day", 1)))) - 1
            leap = "闰" if bool(getattr(lunar, "isleap", False)) else ""
            return f"{leap}{_LUNAR_MONTH_NAMES[month_index]}{_LUNAR_DAY_NAMES[day_index]}"
        except Exception:
            return ""

    def _format_solar_term_perception(self, current: datetime) -> str:
        if not runtime_persona_setting(self, 'enable_solar_term_perception', True):
            return ""
        today = (current.month, current.day)
        if today in _SOLAR_TERM_DATES:
            return _SOLAR_TERM_DATES[today]
        current_date = current.date()
        for offset in range(1, 4):
            next_day = current_date + timedelta(days=offset)
            name = _SOLAR_TERM_DATES.get((next_day.month, next_day.day))
            if name:
                return f"{offset}天后{name}"
        return ""

    def _format_almanac_perception(self, current: datetime) -> str:
        if not runtime_persona_setting(self, 'enable_almanac_perception', False):
            return ""
        seed = current.year * 10000 + current.month * 100 + current.day
        yi = _ALMANAC_YI[seed % len(_ALMANAC_YI)]
        ji = _ALMANAC_JI[(seed // 7) % len(_ALMANAC_JI)]
        return f"宜{yi}，忌{ji}"
