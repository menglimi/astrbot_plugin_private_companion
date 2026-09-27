# -*- coding: utf-8 -*-
"""生日/特殊日域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 263 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations

import hashlib
import random
import re
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from datetime import datetime, timedelta
from typing import Any

from .logging_util import get_module_logger
try:
    from lunarcalendar import Converter, Solar
except Exception:
    Converter = None
    Solar = None

from .proactive_engine_shared import _engine_host

logger = get_module_logger(__name__)



class ProactiveEngineBirthdayMixin:
    """生日/特殊日域（从 ProactiveEngineMixin 拆出）。"""


    def _birthday_profile_matches_on_date(self, user: dict[str, Any], current: datetime) -> bool:
        profile = user.get("birthday_profile")
        if not isinstance(profile, dict):
            return False
        month = _safe_int(profile.get("month"), 0)
        day = _safe_int(profile.get("day"), 0)
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return False
        if _single_line(profile.get("calendar"), 12).lower() != "lunar":
            return current.month == month and current.day == day
        if not (Converter and Solar):
            return False
        try:
            lunar = Converter.Solar2Lunar(Solar(current.year, current.month, current.day))
            return int(lunar.month) == month and int(lunar.day) == day and not bool(getattr(lunar, "isleap", False))
        except Exception as exc:
            logger.debug("农历生日匹配失败: %s", _single_line(exc, 120))
            return False

    def _birthday_stage_for_date(self, user: dict[str, Any], current: datetime) -> str:
        if self._birthday_profile_matches_on_date(user, current):
            return "birthday"
        if self._birthday_profile_matches_on_date(user, current + timedelta(days=1)):
            return "eve"
        if self._birthday_profile_matches_on_date(user, current - timedelta(days=1)):
            return "after"
        return ""

    def _pick_birthday_celebration_event(
        self,
        user: dict[str, Any],
        now: float | None = None,
    ) -> dict[str, Any] | None:
        now = now or _engine_host._now_ts()
        if self._private_user_role(user) != "owner" or bool(user.get("birthday_celebration_opt_out")):
            return None
        current = self._environment_fromtimestamp(now)
        stage = self._birthday_stage_for_date(user, current)
        if not stage:
            return None
        event = user.get("birthday_event") if isinstance(user.get("birthday_event"), dict) else {}
        minute = current.hour * 60 + current.minute
        # The observance year is the year of the actual birthday date.  Using
        # current.year +/- 1 here made almost every non-New-Year birthday carry
        # the wrong receipt year and could cause duplicate greetings.
        year = (
            (current + timedelta(days=1)).year
            if stage == "eve"
            else (current - timedelta(days=1)).year
            if stage == "after"
            else current.year
        )

        if stage == "eve":
            recent_activity = self._latest_private_user_activity_ts(user)
            if (
                minute >= 21 * 60 + 30
                and _safe_int(event.get("celebrated_year"), 0) != year
                and _safe_int(user.get("ignored_streak"), 0, 0) <= 0
            ):
                tomorrow = current.date() + timedelta(days=1)
                target = datetime.combine(tomorrow, datetime.min.time(), tzinfo=current.tzinfo)
                target += timedelta(minutes=_engine_host.random.randint(1, 7))
                return {
                    "window": "00:00-00:15",
                    "date": tomorrow.isoformat(),
                    "reason": "birthday_celebration",
                    "action": "message",
                    "why": "生日刚开始，想在零点后轻轻送上第一句祝福",
                    "topic": "零点后的生日小惊喜",
                    "motive": "对方的生日刚刚开始，想第一时间留一句祝福",
                    "_scheduled_ts": target.timestamp(),
                    "_proactive_source": "birthday_celebration",
                    "_midnight_ritual": True,
                    "_birthday_stage": "birthday",
                    "context_key": "planned_birthday_event_context",
                    "context": {"observance_year": year, "delivery_timing": "midnight"},
                }
            if _safe_int(event.get("eve_year"), 0) == year or _safe_int(user.get("ignored_streak"), 0) > 0:
                return None
            if recent_activity <= 0 or now - recent_activity > 7 * 24 * 3600 or not (17 * 60 + 30 <= minute < 21 * 60 + 30):
                return None
            scheduled = now + _engine_host.random.randint(8, 38) * 60
            return {
                "window": self._window_from_delay_minutes(max(5, int((scheduled - now) / 60)), width_minutes=48),
                "reason": "birthday_eve_hint",
                "action": "message",
                "why": "明天是一个值得为自己留一点空白的日子，先轻轻递一句，不提前揭开仪式",
                "topic": "明天给自己留一点空白",
                "motive": "明天想让对方放松一点",
                "_scheduled_ts": scheduled,
                "_birthday_stage": "eve",
                "context_key": "planned_birthday_event_context",
                "context": {"observance_year": year},
            }

        if stage == "birthday":
            if _safe_int(event.get("celebrated_year"), 0) == year or minute >= 22 * 60:
                return None
            if _safe_int(user.get("ignored_streak"), 0, 0) > 0 and minute < 18 * 60:
                return None
            midnight = minute < 15
            if midnight:
                midnight_end = current.replace(hour=0, minute=15, second=0, microsecond=0).timestamp()
                remaining = int(midnight_end - now)
                if remaining > 10:
                    scheduled = now + _engine_host.random.randint(5, min(150, remaining - 5))
                    window = "00:00-00:15"
                else:
                    midnight = False
            if not midnight and minute < 9 * 60 + 30:
                scheduled = current.replace(hour=10, minute=_engine_host.random.randint(5, 45), second=0, microsecond=0).timestamp()
                window = "09:30-21:55"
            elif not midnight and minute < 18 * 60 + 30:
                scheduled = now + _engine_host.random.randint(12, 75) * 60
                window = "09:30-21:55"
            elif not midnight:
                daytime_end = current.replace(hour=21, minute=55, second=0, microsecond=0).timestamp()
                remaining = max(20, int(daytime_end - now) - 5)
                scheduled = now + _engine_host.random.randint(min(8 * 60, remaining), min(35 * 60, remaining))
                window = "09:30-21:55"
            action = (
                "photo_text"
                if not midnight and self._photo_text_available(user) and _engine_host.random.random() < 0.58
                else "message"
            )
            return {
                "window": window,
                "reason": "birthday_celebration",
                "action": action,
                "why": "今天是用户明确允许记住的生日，想认真递上一份不造成压力的小惊喜",
                "topic": "今天只属于你的生日小惊喜",
                "motive": "今天是对方生日，想留一份小惊喜",
                "_scheduled_ts": scheduled,
                "_proactive_source": "birthday_celebration",
                "_midnight_ritual": midnight,
                "_birthday_stage": "birthday",
                "context_key": "planned_birthday_event_context",
                "context": {"observance_year": year, "delivery_timing": "midnight" if midnight else "daytime"},
            }

        celebrated_at = _safe_float(event.get("celebrated_at"), 0)
        if _safe_int(event.get("celebrated_year"), 0) != year:
            if minute >= 14 * 60:
                return None
            scheduled = now + _engine_host.random.randint(8, 35) * 60
            return {
                "window": self._window_from_delay_minutes(max(5, int((scheduled - now) / 60)), width_minutes=58),
                "reason": "birthday_makeup",
                "action": "message",
                "why": "昨天的生日仪式因时机错过，只在第二天午前低调补上一句",
                "topic": "迟到一点的生日祝福",
                "motive": "昨天错过了祝福，今天补上",
                "_scheduled_ts": scheduled,
                "_birthday_stage": "makeup",
                "context_key": "planned_birthday_event_context",
                "context": {"observance_year": year},
            }
        if _safe_int(event.get("afterglow_year"), 0) == year or celebrated_at <= 0:
            return None
        last_user_at = _safe_float(user.get("last_user_message_at"), 0)
        if last_user_at <= celebrated_at or minute >= 21 * 60 + 30:
            return None
        scheduled = now + _engine_host.random.randint(18, 70) * 60
        return {
            "window": self._window_from_delay_minutes(max(5, int((scheduled - now) / 60)), width_minutes=55),
            "reason": "birthday_afterglow",
            "action": "message",
            "why": "用户已经在生日祝福后有过回应，轻轻接住那点余温，不再重复庆祝",
            "topic": "昨天留下的一点开心",
            "motive": "昨天的开心还没散",
            "_scheduled_ts": scheduled,
            "_birthday_stage": "afterglow",
            "context_key": "planned_birthday_event_context",
            "context": {"observance_year": year},
        }

    def _special_day_observance(self, current: datetime) -> dict[str, Any] | None:
        month_day = current.strftime("%m-%d")
        if month_day == "02-14":
            return {"key": "valentines_day", "title": "情人节", "year": current.year}
        if Converter and Solar:
            try:
                lunar = Converter.Solar2Lunar(Solar(current.year, current.month, current.day))
                if int(lunar.month) == 7 and int(lunar.day) == 7 and not bool(getattr(lunar, "isleap", False)):
                    return {"key": "qixi", "title": "七夕", "year": current.year}
            except Exception:
                pass
        entries = self.data.get("important_dates", [])
        if not isinstance(entries, list):
            return None
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("enabled", True):
                continue
            base = self._parse_date_value(entry.get("date"))
            if base is None or (base.month, base.day) != (current.month, current.day):
                continue
            if not entry.get("repeat_yearly", True) and base.year != current.year:
                continue
            joined = _single_line(
                f"{entry.get('title', '')} {entry.get('type', '')} {entry.get('note', '')}",
                180,
            )
            if "生日" in joined or not any(token in joined for token in ("纪念日", "周年", "相识", "恋爱", "情人")):
                continue
            key = _single_line(entry.get("id"), 60) or hashlib.sha1(joined.encode("utf-8", errors="ignore")).hexdigest()[:12]
            return {"key": f"custom:{key}", "title": _single_line(entry.get("title"), 40) or "这个特别的日子", "year": current.year}
        return None

    def _birthday_curiosity_has_known_birthday(self, user: dict[str, Any]) -> bool:
        profile = user.get("birthday_profile")
        if isinstance(profile, dict) and (_safe_int(profile.get("month"), 0) or _single_line(profile.get("raw"), 80)):
            return True
        memory = user.get("companion_memory")
        items = memory.get("items") if isinstance(memory, dict) else []
        if not isinstance(items, list):
            return False
        for item in items:
            text = _single_line(item.get("text"), 260) if isinstance(item, dict) else _single_line(item, 260)
            if not text or "生日" not in text:
                continue
            if re.search(r"(?:不想|不愿|不方便|别|不要|不告诉).{0,8}生日", text):
                continue
            if re.search(r"(?:生日.{0,12}(?:是|在|：|:)|(?:农历|公历).{0,8}生日|我.{0,4}生日).{0,30}", text):
                return True
        return False

    def _pick_birthday_curiosity_event(
        self,
        user: dict[str, Any],
        now: float | None = None,
    ) -> dict[str, Any] | None:
        now = now or _engine_host._now_ts()
        if self._private_user_role(user) != "owner":
            return None
        if bool(user.get("birthday_curiosity_opt_out")) or self._birthday_curiosity_has_known_birthday(user):
            return None
        if _safe_float(user.get("birthday_curiosity_asked_at"), 0) > 0:
            return None
        if _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            return None
        last_message_at = _safe_float(user.get("last_user_message_at"), 0)
        if last_message_at <= 0 or now - last_message_at > 14 * 24 * 3600:
            return None
        memory = user.get("companion_memory")
        memory_items = memory.get("items") if isinstance(memory, dict) else []
        if not isinstance(memory_items, list) or len(memory_items) < 3:
            return None
        if _safe_float(user.get("last_sent"), 0) > 0 and now - _safe_float(user.get("last_sent"), 0) < 18 * 3600:
            return None
        next_check_at = _safe_float(user.get("birthday_curiosity_next_check_at"), 0)
        if next_check_at > now:
            return None
        user["birthday_curiosity_next_check_at"] = now + _engine_host.random.randint(21, 45) * 24 * 3600
        if _engine_host.random.random() > 0.16:
            return None
        scheduled = now + _engine_host.random.randint(35, 130) * 60
        scheduled = self._move_timestamp_into_reason_window(scheduled, "birthday_curiosity", user)
        return {
            "window": self._window_from_delay_minutes(max(5, int((scheduled - now) / 60)), width_minutes=42),
            "reason": "birthday_curiosity",
            "action": "message",
            "why": "相处了一阵后，想低调地知道一个将来可以认真记住的小日子",
            "topic": "你的生日是哪一天",
            "motive": "好奇对方的生日",
            "_scheduled_ts": scheduled,
            "_birthday_curiosity": True,
        }
