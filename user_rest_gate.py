# -*- coding: utf-8 -*-

from __future__ import annotations

import re
import zoneinfo
from datetime import datetime, timedelta
from typing import Any

from astrbot.api import logger

from .helpers import _now_ts, _safe_float, _single_line


class UserRestGateMixin:
    """Detect and maintain per-user rest silence windows."""

    @staticmethod
    def _user_rest_text_is_meta_discussion(cleaned: str) -> bool:
        if not cleaned:
            return False
        return bool(
            re.search(
                r"(?:关键词|关键字|正则|规则|命中|误判|拦截|挡了|工具|日志|之前对话|历史消息|提示词|注入|主动问候|主动消息|用户反馈|反馈|bug)",
                cleaned,
            )
            or re.search(r"(?:为什么|怎么|是否|会不会|是不是).{0,40}(?:晚安|睡|休息|别回|打扰)", cleaned)
        )

    @staticmethod
    def _user_rest_text_is_quoted_or_report(cleaned: str) -> bool:
        if not cleaned:
            return False
        return bool(
            re.search(r"(?:他说|她说|它说|bot说|模型说|原文|内容是|比如|例如|类似|这句|那句)", cleaned)
            or any(mark in cleaned for mark in ("“", "”", '"', "'"))
        )

    def _user_rest_signal_should_block_current_reply(self, text: str) -> bool:
        """Only explicit no-reply / do-not-disturb intent should silence this passive turn."""
        cleaned = _single_line(text, 260).lower()
        if not cleaned:
            return False
        if self._user_rest_text_is_meta_discussion(cleaned) or self._user_rest_text_is_quoted_or_report(cleaned):
            return False
        compact = re.sub(r"\s+", "", cleaned)
        no_reply_boundary = r"(?:了|啦|吧|我|这(?:个|条|句|段)(?:消息|话|话题|内容|问题)?|这(?:条)?消息|本条消息|消息|哈|噢|哦|$|[，。！？,.!?])"
        no_reply = re.search(
            r"(?:不用|不必|无需|别|不要|先别|暂时别|今晚别|今天别)(?:再)?(?:回(?:复)?|理我|搭理我|接话|说话|出声)"
            + no_reply_boundary,
            compact,
        )
        proactive_only = re.search(
            r"(?:别|不要|先别|暂时别|今晚别|今天别).{0,10}主动.{0,8}(?:打扰|吵|发消息|找我|回(?:复)?|理我|搭理我|接话|说话)",
            compact,
        )
        if proactive_only and not no_reply:
            return False
        hard_quiet = re.search(
            r"(?:别|不要|先别|暂时别|今晚别|今天别).{0,10}(?:打扰|吵我|叫我|主动|发消息|找我)"
            r"|(?:让我|叫我).{0,6}(?:安静|清静|静一静)"
            r"|(?:闭嘴|别说话|不要说话|安静点)",
            compact,
        )
        return bool(no_reply or hard_quiet)

    def _user_rest_silence_until(self, user: dict[str, Any], *, now: float | None = None) -> float:
        check_now = _now_ts() if now is None else now
        rest_until = _safe_float(user.get("user_rest_until"), 0)
        if rest_until <= 0:
            return 0.0
        if rest_until <= check_now:
            user["user_rest_until"] = 0
            user["user_rest_reason"] = ""
            user["user_rest_set_at"] = 0
            return 0.0
        return rest_until

    def _next_user_rest_morning_ts(self, *, now: float) -> float:
        timezone_name = _single_line(getattr(self, "environment_perception_timezone", ""), 64) or "Asia/Shanghai"
        try:
            tz = zoneinfo.ZoneInfo(timezone_name)
        except Exception:
            tz = zoneinfo.ZoneInfo("Asia/Shanghai")
        current = datetime.fromtimestamp(now, tz)
        target = current.replace(hour=8, minute=30, second=0, microsecond=0)
        if target.timestamp() <= now + 3600:
            target += timedelta(days=1)
        return max(target.timestamp(), now + 6 * 3600)

    def _detect_user_rest_silence_until(self, text: str, *, now: float | None = None) -> float:
        cleaned = _single_line(text, 260).lower()
        if not cleaned:
            return 0.0
        check_now = _now_ts() if now is None else now
        # Avoid treating keyword discussions or quoted histories as real rest requests.
        if self._user_rest_text_is_meta_discussion(cleaned):
            return 0.0
        quoted_or_report = self._user_rest_text_is_quoted_or_report(cleaned)
        cancel_pattern = (
            r"(?:我|俺|咱|人家).{0,10}(?:醒了|起床了|睡醒了|不睡了|回来了|可以聊)"
            r"|(?:睡醒了|起床了|不睡了|可以聊了|回来了)"
        )
        if re.search(cancel_pattern, cleaned):
            return -1.0
        if quoted_or_report:
            return 0.0
        no_reply_boundary = r"(?:了|啦|吧|我|这(?:个|条|句|段)(?:消息|话|话题|内容|问题)?|这(?:条)?消息|本条消息|消息|哈|噢|哦|$|[，。！？,.!?])"
        hard_quiet = re.search(
            r"(?:别|不要|先别|暂时别|今晚别|今天别).{0,10}(?:打扰|吵|主动|发消息|找我)"
            r"|(?:不用|不必|无需|别|不要|先别|暂时别|今晚别|今天别)(?:再)?(?:回(?:复)?|理我|搭理我|接话|说话|出声)"
            + no_reply_boundary,
            cleaned,
        )
        tomorrow = re.search(r"(?:明天|明早|早上)再(?:聊|说|回|看|找我)", cleaned)
        sleep = re.search(
            r"(?:晚安|睡觉去了|先睡了|去睡了|睡了哈|睡啦|我睡了|我先睡|我去睡|我要睡|我准备睡|我困了先睡|困死了先睡|补觉去了|我要补觉|先补觉)",
            cleaned,
        )
        nap = re.search(
            r"(?:我|俺|咱|人家).{0,10}(?:要|先|去|准备|现在|马上)?(?:午休|眯一会|歇会儿?|躺会儿?|休息一下|休息会儿?)",
            cleaned,
        )
        rest = re.search(
            r"(?:我|俺|咱|人家).{0,10}(?:要|先|去|准备|现在|马上)(?:休息(?:一下|会儿?|一会儿?)?|歇一下|躺一下|缓一会儿?)",
            cleaned,
        )
        if hard_quiet or tomorrow or sleep:
            return self._next_user_rest_morning_ts(now=check_now)
        if nap:
            return check_now + 90 * 60
        if rest:
            return check_now + 2 * 3600
        return 0.0

    def _clear_user_rest_pending_plan_fallback(self, user: dict[str, Any]) -> None:
        for key, value in (
            ("next_proactive_at", 0),
            ("planned_proactive_reason", ""),
            ("planned_proactive_action", ""),
            ("planned_proactive_source", ""),
            ("planned_proactive_motive", ""),
            ("planned_proactive_topic", ""),
            ("planned_proactive_impulse_id", ""),
            ("planned_proactive_window_start_at", 0),
            ("planned_proactive_best_until_at", 0),
            ("planned_proactive_expire_at", 0),
            ("planned_event_chain", []),
            ("planned_opener_mode", ""),
            ("planned_followup_kind", ""),
            ("planned_proactive_quota_exempt", False),
            ("planned_candidate_id", ""),
        ):
            user[key] = value
        clear_trigger = getattr(self, "_clear_planned_proactive_trigger", None)
        if callable(clear_trigger):
            try:
                clear_trigger(user)
            except Exception:
                pass

    def _apply_user_rest_silence_from_message(
        self,
        user: dict[str, Any],
        text: str,
        *,
        now: float | None = None,
    ) -> bool:
        check_now = _now_ts() if now is None else now
        rest_until = self._detect_user_rest_silence_until(text, now=check_now)
        if rest_until < 0:
            if _safe_float(user.get("user_rest_until"), 0) > check_now:
                user["user_rest_until"] = 0
                user["user_rest_reason"] = ""
                user["user_rest_set_at"] = 0
                logger.info("[PrivateCompanion] 用户休息静默已解除: user=%s", user.get("user_id") or user.get("id") or "")
                return True
            return False
        if rest_until <= check_now:
            return False
        user["user_rest_until"] = rest_until
        user["user_rest_reason"] = _single_line(text, 120)
        user["user_rest_set_at"] = check_now
        if str(user.get("planned_proactive_source") or "") != "timer":
            self._clear_user_rest_pending_plan_fallback(user)
        logger.info(
            "[PrivateCompanion] 已记录用户休息静默: user=%s until=%s reason=%s",
            user.get("user_id") or user.get("id") or "",
            self._environment_fromtimestamp(rest_until).strftime("%m-%d %H:%M"),
            _single_line(text, 80),
        )
        return True
