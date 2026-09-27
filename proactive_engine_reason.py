# -*- coding: utf-8 -*-
"""理由/时间窗/问候域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（24 个方法 + 0 个模块级名字 + 0 个类级赋值 / 470 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations

import json
import random
import re
from .helpers import _now_ts, _safe_float, _single_line
from .persona_config import runtime_persona_setting
from datetime import datetime, timedelta
from typing import Any

from .logging_util import get_module_logger
from .proactive_engine_shared import _engine_host

logger = get_module_logger(__name__)



class ProactiveEngineReasonMixin:
    """理由/时间窗/问候域（从 ProactiveEngineMixin 拆出）。"""


    def _window_from_delay_minutes(self, delay_minutes: int, width_minutes: int = 24) -> str:
        start_dt = self._environment_fromtimestamp(_engine_host._now_ts() + max(5, delay_minutes) * 60)
        end_dt = start_dt + timedelta(minutes=max(12, width_minutes))
        return f"{start_dt.strftime('%H:%M')}-{end_dt.strftime('%H:%M')}"

    def _parse_window_minutes(self, window: str) -> tuple[int | None, int | None]:
        normalized = (
            str(window or "")
            .replace("：", ":")
            .replace("—", "-")
            .replace("–", "-")
            .replace("－", "-")
            .replace("~", "-")
            .replace("～", "-")
            .replace("至", "-")
            .replace("到", "-")
        )
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", normalized)
        if not match:
            return None, None
        sh, sm, eh, em = [int(part) for part in match.groups()]
        start = (sh % 24) * 60 + sm
        end = (eh % 24) * 60 + em
        if end <= start:
            end += 24 * 60
        return start, end

    def _choose_planned_reason(self) -> str:
        state = self.data.get("daily_state", {})
        can_do = self.data.get("can_do", [])
        diaries = self.data.get("bot_diaries", [])
        important_dates = self._get_relevant_important_dates()
        users = self.data.get("users", {})
        has_recent_user_message = False
        if isinstance(users, dict):
            for raw_user in users.values():
                if not isinstance(raw_user, dict):
                    continue
                if _single_line(raw_user.get("last_user_message"), 24):
                    has_recent_user_message = True
                    break
        has_contextual_source = bool(
            (isinstance(can_do, list) and can_do)
            or (isinstance(diaries, list) and diaries)
            or important_dates
            or runtime_persona_setting(self, "include_schedule_in_messages", True)
        )
        reasons = ["activity_share", "activity_share", "diary_share"]
        if not has_contextual_source:
            reasons.append("check_in")
        if self._has_active_insomnia_state():
            reasons.extend(["insomnia_night"] * 2)
        if isinstance(state, dict) and state.get("conditions"):
            reasons.extend(["quiet_care"])
        if isinstance(can_do, list) and can_do:
            reasons.extend(["activity_share"] * 3)
        if isinstance(diaries, list) and diaries:
            reasons.extend(["diary_share"] * 2)
        if important_dates:
            reasons.extend(["important_date_share"] * 2)
        if runtime_persona_setting(self, "include_schedule_in_messages", True):
            reasons.extend(["background_schedule"] * 2)
        state_note = _single_line(state.get("note"), 80) if isinstance(state, dict) else ""
        state_mood = _single_line(state.get("mood_bias"), 20) if isinstance(state, dict) else ""
        if any(token in state_note for token in ("疲惫", "收声", "安静", "慢一点")) or state_mood in {"安静", "疲惫"}:
            reasons.extend(["quiet_care"])
        if has_recent_user_message:
            reasons.extend(["quiet_care"])
        return _engine_host.random.choice(reasons)

    def _is_greeting_reason(self, reason: str) -> bool:
        return self._normalize_legacy_proactive_text(reason, limit=40) in {"morning_greeting", "noon_greeting", "evening_greeting"}

    def _is_sticky_greeting_reason(self, reason: str) -> bool:
        return self._normalize_legacy_proactive_text(reason, limit=40) in {"morning_greeting", "noon_greeting", "evening_greeting"}

    def _greeting_min_interval_seconds(self, reason: str) -> int:
        if reason == "morning_greeting":
            return 45 * 60
        if reason == "evening_greeting":
            return 60 * 60
        if reason == "noon_greeting":
            return 60 * 60
        return 120 * 60

    def _is_now_in_reason_window(
        self,
        reason: str,
        now: float | None = None,
        user: dict[str, Any] | None = None,
    ) -> bool:
        if not reason:
            return False
        now_dt = self._environment_fromtimestamp(now or _engine_host._now_ts())
        minute_of_day = now_dt.hour * 60 + now_dt.minute
        for start, end in self._reason_windows(reason, user):
            if start <= minute_of_day <= end:
                return True
        return False

    def _inbound_satisfies_greeting(
        self,
        reason: str,
        *,
        now: float | None = None,
        user: dict[str, Any] | None = None,
    ) -> bool:
        if not self._is_greeting_reason(reason):
            return False
        now_dt = self._environment_fromtimestamp(now or _engine_host._now_ts())
        minute_of_day = now_dt.hour * 60 + now_dt.minute
        lead_minutes = {
            "morning_greeting": 10,
            "noon_greeting": 10,
            "evening_greeting": 10,
        }.get(reason, 10)
        for start, end in self._reason_windows(reason, user):
            if start - lead_minutes <= minute_of_day < end:
                return True
        return False

    def _recent_activity_satisfies_greeting(
        self,
        user: dict[str, Any],
        reason: str,
        *,
        now: float | None = None,
    ) -> bool:
        if not self._is_greeting_reason(reason):
            return False
        check_now = _engine_host._now_ts() if now is None else now
        recent_at = self._latest_private_user_activity_ts(user)
        if recent_at <= 0:
            return False
        check_dt = self._environment_fromtimestamp(check_now)
        recent_dt = self._environment_fromtimestamp(recent_at)
        if check_dt.date() != recent_dt.date():
            return False
        if self._inbound_satisfies_greeting(reason, now=recent_at, user=user):
            return True
        idle_seconds = self._effective_user_greeting_idle_minutes(user) * 60
        elapsed = check_now - recent_at
        return (
            idle_seconds > 0
            and 0 <= elapsed < idle_seconds
            and self._is_now_in_reason_window(reason, now=check_now, user=user)
        )

    def _proactive_text_greeting_reason(self, text: str, *, now: float | None = None) -> str:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return ""
        compact = re.sub(r"\s+", "", cleaned)
        # Allow a short address before the greeting, e.g. "小林，早……" or "主人早".
        compact = re.sub(r"^[\u4e00-\u9fffA-Za-z0-9_\-]{1,12}[,，、:：]+", "", compact, count=1)
        for marker in ("早", "午安", "中午", "晚上", "晚好"):
            index = compact.find(marker)
            if 0 < index <= 6:
                compact = compact[index:]
                break
        now_dt = self._environment_fromtimestamp(now or _engine_host._now_ts())
        minute = now_dt.hour * 60 + now_dt.minute
        if compact == "早" or (
            compact.startswith("早")
            and (
                compact[1:2] in {"", ".", "。", "…", "·", "~", "～", "!", "！", ",", "，", "、", "呀", "啊", "安", "上", "哇", "哦", "欸", "诶"}
            )
        ):
            return "morning_greeting"
        if compact.startswith(("午安", "中午好", "午好")) or (compact.startswith("中午") and compact[2:3] in {"，", ",", "。", ".", "!", "！", "~", "～"}):
            return "noon_greeting"
        if compact.startswith(("晚上好", "晚好")) or (compact.startswith("晚上") and compact[2:3] in {"，", ",", "。", ".", "!", "！", "~", "～"}):
            return "evening_greeting"
        if re.search(r"(?:早晨|早上).{0,12}(?:安静|洗漱|刚醒|醒来|开机|早安|问候)", compact) and minute < 11 * 60:
            return "morning_greeting"
        return ""

    def _textual_greeting_duplicate_reason(
        self,
        user: dict[str, Any],
        text: str,
        *,
        now: float | None = None,
    ) -> str:
        reason = self._proactive_text_greeting_reason(text, now=now)
        if not reason:
            return ""
        self._reset_daily_counter_if_needed(user)
        sent = user.get("greetings_sent", [])
        if not isinstance(sent, list):
            sent = []
            user["greetings_sent"] = sent
        suppressed = user.get("greetings_suppressed_by_inbound", [])
        if not isinstance(suppressed, list):
            suppressed = []
            user["greetings_suppressed_by_inbound"] = suppressed
        if self._greeting_was_sent_today(user, reason):
            return "该问候时段今天已经主动问候过"
        if reason in suppressed:
            return "该问候时段已被用户自然互动占掉"
        return ""

    def _greeting_was_sent_today(self, user: dict[str, Any], reason: str) -> bool:
        if not self._is_greeting_reason(reason):
            return False
        self._reset_daily_counter_if_needed(user)
        sent = user.get("greetings_sent", [])
        if isinstance(sent, list) and reason in sent:
            return True
        return reason == "morning_greeting" and _safe_float(user.get("morning_greeting_sent_at"), 0) > 0

    def _mark_textual_greeting_sent(
        self,
        user: dict[str, Any],
        text: str,
        *,
        sent_at: float | None = None,
    ) -> bool:
        reason = self._proactive_text_greeting_reason(text, now=sent_at)
        if not reason:
            return False
        self._reset_daily_counter_if_needed(user)
        sent = user.setdefault("greetings_sent", [])
        if not isinstance(sent, list):
            sent = []
            user["greetings_sent"] = sent
        changed = False
        if reason not in sent:
            sent.append(reason)
            changed = True
        if reason == "morning_greeting" and _safe_float(user.get("morning_greeting_sent_at"), 0) <= 0:
            user["morning_greeting_sent_at"] = _safe_float(sent_at, 0) or _engine_host._now_ts()
            user["morning_greeting_reply_at"] = 0
            changed = True
        return changed

    def _mark_greeting_satisfied_by_inbound(self, user: dict[str, Any], reason: str) -> bool:
        if not self._is_greeting_reason(reason):
            return False
        self._reset_daily_counter_if_needed(user)
        suppressed = user.setdefault("greetings_suppressed_by_inbound", [])
        if not isinstance(suppressed, list):
            suppressed = []
            user["greetings_suppressed_by_inbound"] = suppressed
        if reason in suppressed:
            return False
        suppressed.append(reason)
        return True

    def _mark_greetings_satisfied_by_recent_activity(
        self,
        user: dict[str, Any],
        *,
        activity_ts: float,
    ) -> bool:
        if not isinstance(user, dict) or activity_ts <= 0:
            return False
        changed = False
        for reason in ("morning_greeting", "noon_greeting", "evening_greeting"):
            if self._inbound_satisfies_greeting(reason, now=activity_ts, user=user):
                changed = self._mark_greeting_satisfied_by_inbound(user, reason) or changed
        return changed

    @staticmethod
    def _parse_json_object(raw: Any) -> dict[str, Any] | None:
        text = str(raw or "").strip()
        if not text:
            return None
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
        candidates = [text]
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            candidates.append(match.group(0))
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    def _reason_windows(
        self,
        reason: str,
        user: dict[str, Any] | None = None,
    ) -> list[tuple[int, int]]:
        reason = self._normalize_legacy_proactive_text(reason, limit=40)
        if reason == "morning_greeting":
            windows: list[tuple[int, int]] = [self._morning_greeting_window()]
        else:
            windows = {
            "insomnia_night": [(23 * 60, 24 * 60), (0, 6 * 60)],
            "post_goodnight_group_activity": [(20 * 60, 24 * 60), (0, 2 * 60)],
            "group_share": [(9 * 60, 23 * 60)],
            "bili_video_share": [(10 * 60, 23 * 60)],
            "news_share": [(8 * 60, 23 * 60)],
            "web_exploration_share": [(9 * 60, 23 * 60)],
            "environment_change": [(6 * 60, 23 * 60 + 30)],
            "weather_alert": [(0, 24 * 60)],
            "health_alert": [(0, 24 * 60)],
            "creative_share": [(10 * 60, 23 * 60)],
            "memory_echo": [(10 * 60, 21 * 60 + 30)],
            "mood_checkin": [(9 * 60 + 30, 21 * 60 + 30)],
            "absence_miss": [(10 * 60, 21 * 60 + 30)],
            "game_invite": [(10 * 60, 22 * 60)],
            "personal_goal_progress": [(8 * 60, 22 * 60)],
            "memo_note_reminder": [(7 * 60, 23 * 60)],
            "state_share": [(8 * 60, 22 * 60 + 30)],
            "quiet_care": [(9 * 60, 22 * 60 + 30)],
            "activity_share": [(10 * 60, 18 * 60 + 30)],
            "diary_share": [(19 * 60, 23 * 60)],
            "important_date_share": [(8 * 60 + 30, 22 * 60)],
            "special_day_greeting": [(0, 15), (8 * 60 + 30, 21 * 60 + 30)],
            "birthday_curiosity": [(10 * 60, 12 * 60), (15 * 60, 20 * 60 + 30)],
            "birthday_eve_hint": [(17 * 60 + 30, 21 * 60 + 30)],
            "birthday_celebration": [(0, 15), (9 * 60 + 30, 21 * 60 + 55)],
            "birthday_makeup": [(9 * 60 + 30, 13 * 60 + 55)],
            "birthday_afterglow": [(10 * 60, 21 * 60 + 25)],
            "background_schedule": [(9 * 60, 22 * 60)],
            "check_in": [(9 * 60, 22 * 60 + 30)],
            "noon_greeting": [(12 * 60 + 5, 13 * 60 + 35)],
            "evening_greeting": [(20 * 60 + 10, 21 * 60 + 20)],
            "meal_care": [(7 * 60 + 50, 20 * 60 + 35)],
            "meal_care_followup": [(8 * 60 + 5, 22 * 60)],
        }.get(reason, [(9 * 60, 22 * 60)])
        if reason in {"special_day_greeting", "birthday_celebration", "insomnia_night"}:
            return windows
        return self._apply_chronotype_shift_to_windows(windows, user)

    def _apply_chronotype_shift_to_windows(
        self,
        windows: list[tuple[int, int]],
        user: dict[str, Any] | None,
    ) -> list[tuple[int, int]]:
        """按用户作息画像平移 reason 窗；无画像或无平移时原样返回。"""
        if not isinstance(user, dict):
            return list(windows)
        shift_getter = getattr(self, "_chronotype_reason_shift", None)
        shifter = getattr(self, "_shift_reason_windows", None)
        if not callable(shift_getter) or not callable(shifter):
            return list(windows)
        try:
            shift = shift_getter(user)
        except Exception:
            return list(windows)
        if not shift:
            return list(windows)
        return shifter(windows, shift)

    def _post_goodnight_group_activity_is_fresh(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> bool:
        if self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) != "post_goodnight_group_activity":
            return False
        context = user.get("post_goodnight_group_activity_context")
        if not isinstance(context, dict):
            return False
        check_now = _engine_host._now_ts() if now is None else now
        activity_at = _safe_float(context.get("group_activity_at"), 0)
        rest_set_at = _safe_float(context.get("rest_set_at"), 0)
        return bool(
            activity_at > rest_set_at > 0
            and 0 <= check_now - activity_at <= 50 * 60
        )

    def _proactive_sleep_phase_block_reason(self, reason: str) -> str:
        """睡眠相位门：Bot 睡着时不放行非豁免的主动消息。返回空串表示放行。"""
        if reason in self._PROACTIVE_SLEEP_EXEMPT_REASONS:
            return ""
        if not bool(runtime_persona_setting(self, "enable_rest_reply_simulation", False)):
            return ""
        state_getter = getattr(self, "_sleep_runtime_state", None)
        if not callable(state_getter):
            return ""
        try:
            runtime = state_getter()
        except Exception:
            return ""
        phase = str((runtime or {}).get("phase") or "awake")
        if phase in self._PROACTIVE_SLEEP_BLOCK_PHASES:
            return f"sleep_phase:{phase}"
        return ""

    def _is_reason_allowed_now(
        self,
        reason: str,
        user: dict[str, Any] | None = None,
    ) -> bool:
        reason = self._normalize_legacy_proactive_text(reason, limit=40)
        now = self._environment_now()
        minute = now.hour * 60 + now.minute
        for start, end in self._reason_windows(reason, user):
            if start <= minute < end:
                if reason == "insomnia_night":
                    # 失眠场景本就发生在入睡边缘相位，由失眠状态自身判定。
                    return self._has_active_insomnia_state()
                sleep_block = self._proactive_sleep_phase_block_reason(reason)
                if sleep_block:
                    logger.debug(
                        "主动消息被 Bot 睡眠相位拦下: reason=%s phase=%s",
                        reason,
                        sleep_block,
                    )
                    return False
                if reason == "diary_share":
                    return bool(self.data.get("bot_diaries"))
                if reason == "important_date_share":
                    return bool(self._get_relevant_important_dates())
                return True
        return False

    def _move_timestamp_into_reason_window(
        self,
        timestamp: float,
        reason: str,
        user: dict[str, Any] | None = None,
    ) -> float:
        dt = self._environment_fromtimestamp(timestamp)
        minute = dt.hour * 60 + dt.minute
        windows = self._reason_windows(reason, user)
        for start, end in windows:
            if start <= minute < end:
                # 窗口判定为半开区间 [start, end)，随机偏移收敛到 end-1 分钟。
                eh, em = divmod(max(start, end - 1), 60)
                window_end = datetime.combine(
                    dt.date(), datetime.min.time(), tzinfo=dt.tzinfo
                ).replace(hour=eh % 24, minute=em)
                return min(timestamp + _engine_host.random.randint(0, 17 * 60), window_end.timestamp())
        first_start = windows[0][0]
        target_date = dt.date()
        if all(minute >= end for _, end in windows):
            target_date = target_date + timedelta(days=1)
        hour, minute_part = divmod(first_start, 60)
        target = datetime.combine(target_date, datetime.min.time(), tzinfo=dt.tzinfo).replace(
            hour=hour % 24,
            minute=minute_part,
        )
        # 目标点同样按窗口上界收敛；check_in 等无二次保护的调用方依赖此处。
        tail_end_min = max(windows[0][0], windows[0][1] - 1)
        th, tm = divmod(tail_end_min, 60)
        tail_window_end = datetime.combine(
            target_date, datetime.min.time(), tzinfo=dt.tzinfo
        ).replace(hour=th % 24, minute=tm)
        return min(target.timestamp() + _engine_host.random.randint(0, 59 * 60), tail_window_end.timestamp())

    def _can_send_insomnia_night_message(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> bool:
        if not bool(runtime_persona_setting(self, "allow_insomnia_night_message", True)):
            return False
        if not self._has_active_insomnia_state():
            return False
        if self._private_user_role(user) != "owner":
            return False
        check_now = _engine_host._now_ts() if now is None else now
        current = self._environment_fromtimestamp(check_now)
        hour = current.hour
        if not (0 <= hour <= 5 or hour >= 23):
            return False
        daily_limit = self._effective_user_daily_limit(user)
        if daily_limit <= 0:
            return False
        night_key = self._insomnia_night_key(check_now)
        if _single_line(user.get("insomnia_night_sent_key"), 20) == night_key:
            return False
        if _safe_float(user.get("last_sent"), 0) > 0:
            elapsed = check_now - _safe_float(user.get("last_sent"), 0)
            # A dedicated night care slot can be closer than ordinary chatter,
            # but it still cannot stack immediately after another message.
            if elapsed < max(45 * 60, min(120 * 60, self._effective_user_min_interval_minutes(user) * 60)):
                return False
        return True

    def _has_active_insomnia_state(self) -> bool:
        state = self.data.get("daily_state", {})
        conditions = state.get("conditions", []) if isinstance(state, dict) else []
        if not isinstance(conditions, list):
            return False
        keywords = ("失眠", "睡得很浅", "睡得断断续续", "睡眠延续")
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            text = f"{cond.get('title', '')} {cond.get('label', '')}"
            if any(keyword in text for keyword in keywords):
                return True
        return False
