# -*- coding: utf-8 -*-
"""util_small。

由 tools/split_main_domain.py 从 main.py 机械抽取（6 个方法 / 82 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import inspect
from .helpers import _now_ts, _safe_float, _single_line
from astrbot.api.event import AstrMessageEvent
from typing import Any


class PrivateCompanionPluginUtilSmallMixin:
    """util_small（从 PrivateCompanionPlugin 拆出）。"""

    @staticmethod
    async def _await_if_needed(value: Any) -> Any:
        return await value if inspect.isawaitable(value) else value

    @staticmethod
    def _safe_event_sender_id(event: AstrMessageEvent | None) -> str:
        if event is None:
            return ""
        getter = getattr(event, "get_sender_id", None)
        if callable(getter):
            try:
                return _single_line(getter(), 80)
            except Exception:
                pass
        return _single_line(getattr(event, "sender_id", "") or getattr(event, "user_id", ""), 80)

    @staticmethod
    def _safe_event_is_private(event: AstrMessageEvent | None) -> bool:
        if event is None:
            return False
        unified_msg_origin = str(getattr(event, "unified_msg_origin", "") or "")
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return True
        except Exception:
            pass
        return ":FriendMessage:" in unified_msg_origin

    def _is_owner_private_event(self, event: AstrMessageEvent | None) -> bool:
        if event is None:
            return False
        if not self._safe_event_is_private(event):
            return False
        try:
            resolver = getattr(self, "_private_user_id_for_event", None)
            requester_id = (
                resolver(event)
                if callable(resolver)
                else self._canonical_private_user_id(self._safe_event_sender_id(event))
            )
        except Exception:
            requester_id = ""
        if not requester_id:
            return False
        requester_profile = None
        try:
            requester_profile = self._get_user(requester_id)
        except Exception:
            users = self.data.get("users") if isinstance(getattr(self, "data", {}), dict) and isinstance(self.data.get("users"), dict) else {}
            requester_profile = users.get(requester_id) if isinstance(users, dict) else None
        try:
            return (
                bool(requester_id and self._is_target_private_user(requester_id, requester_profile if isinstance(requester_profile, dict) else None))
                and isinstance(requester_profile, dict)
                and bool(requester_profile.get("enabled", True))
                and self._private_user_role(requester_profile, requester_id) == "owner"
            )
        except Exception:
            return False

    def _format_timestamp_elapsed(self, timestamp: Any) -> str:
        ts = _safe_float(timestamp, 0)
        if ts <= 0:
            return "从未"
        delta = _now_ts() - ts
        if delta < -5:
            seconds = abs(delta)
            if seconds < 60:
                return f"{max(1, int(seconds))} 秒后"
            if seconds < 3600:
                return f"{max(1, int(seconds // 60))} 分钟后"
            if seconds < 86400:
                return f"{max(1, int(seconds // 3600))} 小时后"
            return f"{max(1, int(seconds // 86400))} 天后"
        seconds = max(0, delta)
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
