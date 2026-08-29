"""Module-tagged logging helpers for the private companion plugin.

Each module obtains its logger with ``logger = get_module_logger(__name__)``.
The returned proxy prefixes every message with a Chinese module label, e.g.
``[空间动态]``, so a log line can be attributed to its source module at a
glance and filtered by grep, in addition to the ``[source_file:line]``
metadata added by the AstrBot logging pipeline. Messages still flow through
the AstrBot plugin logger, so the per-plugin log level and the
``[astrbot_plugin_private_companion]`` tag remain effective.
"""

from __future__ import annotations

import logging
from typing import Any


def _get_plugin_logger() -> Any:
    """Resolve AstrBot's logger at call time so hot-loaded replacements work."""
    try:
        from astrbot.api import logger as plugin_logger
    except Exception:  # pragma: no cover - isolated import fallback
        return logging.getLogger("astrbot_plugin_private_companion")
    return plugin_logger

_PACKAGE_PREFIXES = (
    "data.plugins.astrbot_plugin_private_companion.",
    "astrbot_plugin_private_companion.",
)

# Chinese labels keyed by module path (last segment). Modules without an
# entry fall back to their raw module name so new modules keep working.
_MODULE_LABELS = {
    "astrbot_knowledge": "知识库",
    "atrelay": "@中继",
    "balance_awareness": "余额感知",
    "busy_reply_gate": "忙碌回复门控",
    "command_handlers": "指令处理",
    "config_migration": "配置迁移",
    "content_companion_bridge": "内容陪伴桥接",
    "core_store": "核心存储",
    "creative": "创作系统",
    "daily_review": "每日回顾",
    "daily_state": "日程状态",
    "daily_state_tick": "日程状态心跳",
    "desktop_pet_bridge": "桌宠桥接",
    "event_dispatch": "事件分发",
    "final_response_persistence": "回复持久化",
    "forward_message": "转发消息",
    "game_integration": "游戏联动",
    "group_member_safety": "群成员安全",
    "group_observation": "群聊观察",
    "group_wakeup": "群聊唤醒",
    "image_companion_bridge": "图像陪伴桥接",
    "integration_status": "集成状态",
    "llm_tool_actions": "LLM工具动作",
    "logging_util": "日志工具",
    "main": "主链",
    "memory_companion_adapter": "记忆陪伴适配",
    "message_pipeline": "消息管线",
    "nai_image_bridge": "NAI生图桥接",
    "news_exploration": "新闻探索",
    "page_api": "面板接口",
    "page_api_qzone": "面板接口·空间",
    "page_api_users_groups": "面板接口·用户群组",
    "passive_state_pipeline": "被动状态管线",
    "place_cognitive_map": "地点认知地图",
    "plugin_bootstrap": "插件引导",
    "private_image": "私聊图片",
    "proactive": "主动调度",
    "proactive_chat_runtime_bridge": "主动聊天桥接",
    "proactive_engine": "主动引擎",
    "proactive_message": "主动消息",
    "qzone_auth": "空间登录",
    "qzone_comments": "空间评论",
    "qzone_feed": "空间动态",
    "qzone_media": "空间媒体",
    "qzone_publish": "空间发布",
    "qzone_runtime": "空间运行时",
    "qzone_schedule": "空间调度",
    "reality_companion_bridge": "现实陪伴桥接",
    "sqlite_backend": "存储·SQLite后端",
    "json_backend": "存储·JSON后端",
    "migration": "存储·迁移",
    "store_manager": "存储·管理器",
    "token_budget": "Token预算",
    "tts_enhancement": "TTS增强",
    "tts_tool_sanitizer": "TTS工具清洗",
    "user_memory": "用户记忆",
    "user_rest_gate": "休息门控",
    "worldbook": "世界书",
}


def _module_tag(module_name: str | None) -> str:
    """Derive the log tag for a module from its ``__name__``.

    The relative module path is looked up in ``_MODULE_LABELS`` (full path
    first, then the last path segment) and replaced with its Chinese label.
    Modules without an entry keep their raw module name.

    Args:
        module_name: The ``__name__`` of the calling module.

    Returns:
        The dotted module path relative to the plugin package, replaced by
        its Chinese label when one is mapped. Modules outside the plugin
        package (for example under a test loader) are reduced to their last
        path component first.
    """
    if not module_name:
        return "未知"
    for prefix in _PACKAGE_PREFIXES:
        if module_name.startswith(prefix):
            module_name = module_name[len(prefix) :] or "root"
    tag = module_name.rpartition(".")[2] or module_name
    return _MODULE_LABELS.get(module_name) or _MODULE_LABELS.get(tag, tag)


class _ModuleLogger:
    """Logger proxy that prefixes messages with the source module tag.

    Level methods accept the same positional and keyword arguments as the
    standard ``logging`` methods, including lazy ``%``-style formatting and
    ``exc_info``. No ``__slots__`` is defined so tests can patch individual
    methods (e.g. ``mock.patch.object(logger, "info")``).
    """

    def __init__(self, tag: str) -> None:
        self._prefix = f"[{tag}] "

    def _format(self, msg: object) -> str:
        return f"{self._prefix}{msg}"

    def debug(self, msg: object, *args: object, **kwargs: object) -> None:
        _get_plugin_logger().debug(self._format(msg), *args, **kwargs)

    def info(self, msg: object, *args: object, **kwargs: object) -> None:
        _get_plugin_logger().info(self._format(msg), *args, **kwargs)

    def warning(self, msg: object, *args: object, **kwargs: object) -> None:
        _get_plugin_logger().warning(self._format(msg), *args, **kwargs)

    def error(self, msg: object, *args: object, **kwargs: object) -> None:
        _get_plugin_logger().error(self._format(msg), *args, **kwargs)

    def exception(self, msg: object, *args: object, **kwargs: object) -> None:
        _get_plugin_logger().exception(self._format(msg), *args, **kwargs)

    def critical(self, msg: object, *args: object, **kwargs: object) -> None:
        _get_plugin_logger().critical(self._format(msg), *args, **kwargs)

    def log(self, level: int, msg: object, *args: object, **kwargs: object) -> None:
        _get_plugin_logger().log(level, self._format(msg), *args, **kwargs)

    def warn(self, msg: object, *args: object, **kwargs: object) -> None:
        """Compatibility alias for the deprecated standard ``warn`` method."""
        self.warning(msg, *args, **kwargs)

    def isEnabledFor(self, level: int) -> bool:
        return bool(_get_plugin_logger().isEnabledFor(level))


def get_module_logger(module_name: str | None = None) -> _ModuleLogger:
    """Get the module-tagged logger for the calling module.

    Args:
        module_name: Pass ``__name__`` from the calling module.

    Returns:
        A logger proxy prefixing messages with the module tag.
    """
    return _ModuleLogger(_module_tag(module_name))
