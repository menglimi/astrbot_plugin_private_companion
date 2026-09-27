# -*- coding: utf-8 -*-
"""private_preflight 域。

由 tools/split_main_domain_v2.py 从 main.py 机械抽取（3 个方法 / 111 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import hashlib
import re
from .helpers import _now_ts, _safe_float, _single_line
from astrbot.api.event import AstrMessageEvent

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginPrivatePreflightMixin:
    """private_preflight 域（从 PrivateCompanionPlugin 拆出）。"""

    def _is_private_companion_command_event(self, event: AstrMessageEvent) -> bool:
        text = _single_line(getattr(event, "message_str", ""), 160)
        if not text:
            return False
        stripped = text.lstrip()
        if stripped.startswith(("/", "／", "!", "！", "#", "＃", ".", "。")):
            return True
        prefixes = (
            "陪伴群", "/陪伴群", "群陪伴", "群聊陪伴",
            "陪伴", "/陪伴", "私聊陪伴", "主动陪伴",
        )
        return any(text == prefix or re.match(rf"^{re.escape(prefix)}\s+", text) for prefix in prefixes)

    def _should_skip_recent_outfit_command_send(
        self,
        event: AstrMessageEvent,
        *,
        text: str,
        image_path: str,
        ttl_seconds: float = 30.0,
    ) -> bool:
        cache = getattr(self, "_recent_outfit_command_sends", None)
        if not isinstance(cache, dict):
            cache = {}
            self._recent_outfit_command_sends = cache
        now = _now_ts()
        ttl = max(1.0, float(ttl_seconds or 30.0))
        for key, ts in list(cache.items()):
            if now - _safe_float(ts, 0.0) > ttl:
                cache.pop(key, None)
        try:
            scope = self._event_scope_key(event)
        except Exception:
            scope = _single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown"
        signature = hashlib.sha1(
            f"{scope}|daily_outfit_photo|{text}|{image_path}".encode("utf-8", errors="ignore")
        ).hexdigest()[:20]
        last_at = _safe_float(cache.get(signature), 0.0)
        if last_at and now - last_at <= ttl:
            logger.info(
                "已跳过重复的每日穿搭命令发图: scope=%s image=%s age=%.1fs",
                _single_line(scope, 120),
                _single_line(image_path, 160),
                now - last_at,
            )
            return True
        cache[signature] = now
        return False

    async def _handle_private_message_preflight(self, event: AstrMessageEvent) -> bool:
        feedback_text = str(getattr(event, "message_str", "") or "")
        if self._message_debounce_command_text(event, feedback_text):
            return False
        feedback_handler = getattr(self, "_maybe_handle_wakeup_feedback", None)
        pending_confirmation_handler = getattr(self, "_reality_touch_apply_pending_confirmation", None)
        if callable(pending_confirmation_handler):
            resolver = getattr(self, "_private_user_id_for_event", None)
            user_id = resolver(event) if callable(resolver) else str(event.get_sender_id() or "").strip()
            confirmation_reply = None
            camera_pending = False
            # 锁内只读数据并判定是否为遗留的摄像头待授权；外部插件调用一律
            # 放到锁外，避免拉长全局数据锁的持有时间。
            async with self._data_lock:
                users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
                user = users.get(user_id) if isinstance(users, dict) else None
                if isinstance(user, dict):
                    pending = user.get("reality_touch_pending_consent")
                    camera_pending = isinstance(pending, dict) and pending.get("capability") == self._REALITY_TOUCH_CAMERA_CAPABILITY
            if camera_pending:
                if self._reality_touch_camera_user_eligible(user_id):
                    try:
                        confirmation_reply = pending_confirmation_handler(user, feedback_text)
                    except Exception as exc:
                        logger.warning(
                            "现实触及待授权确认处理失败: %s",
                            _single_line(exc, 160),
                        )
                else:
                    async with self._data_lock:
                        users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
                        user = users.get(user_id) if isinstance(users, dict) else None
                        if isinstance(user, dict):
                            user.pop("reality_touch_pending_consent", None)
                            self._save_data_sync(sections={"users"})
                    confirmation_reply = "主机摄像头只允许 AstrBot 管理员或主要用户本人授权和使用。"
            elif isinstance(user, dict) and isinstance(
                user.get("reality_touch_pending_consent"), dict
            ):
                try:
                    confirmation_reply = pending_confirmation_handler(user, feedback_text)
                except Exception as exc:
                    logger.warning(
                        "现实触及待授权确认处理失败: %s",
                        _single_line(exc, 160),
                    )
            if confirmation_reply:
                await self._reply(event, confirmation_reply)
                event.stop_event()
                return True
        if callable(feedback_handler):
            raw_user_id = str(event.get_sender_id() or "").strip()
            normalizer = getattr(self, "_canonical_private_user_id", None)
            user_id = normalizer(raw_user_id) if callable(normalizer) else raw_user_id
            users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
            user = users.get(user_id) if isinstance(users, dict) else None
            if isinstance(user, dict) and await feedback_handler(
                event,
                user_id,
                user,
                feedback_text,
            ):
                return True
        return False
