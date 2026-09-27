# -*- coding: utf-8 -*-
"""用餐/日常关怀域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（11 个方法 + 0 个模块级名字 + 0 个类级赋值 / 239 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations
from .proactive_engine_shared import _engine_host

import random
import re
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from datetime import datetime, timedelta
from typing import Any



class ProactiveEngineMealMixin:
    """用餐/日常关怀域（从 ProactiveEngineMixin 拆出）。"""


    @staticmethod
    def _food_prompt_cooldown_remaining(user: dict[str, Any], *, now: float) -> float:
        return max(0.0, _safe_float(user.get("last_food_prompt_at"), 0) + 7 * 3600 - now)

    def _meal_care_interval_remaining(self, user: dict[str, Any], *, now: float) -> float:
        interval_hours = _safe_int(
            runtime_persona_setting(self, "meal_care_min_interval_hours", 48),
            48,
            0,
            168,
        )
        if interval_hours <= 0:
            return 0.0
        return max(
            0.0,
            _safe_float(user.get("last_food_prompt_at"), 0) + interval_hours * 3600 - now,
        )

    def _reset_meal_care_day(self, user: dict[str, Any]) -> None:
        today = _today_key()
        if str(user.get("meal_care_day") or "") == today:
            return
        user["meal_care_day"] = today
        user["meal_care_asked"] = []
        user["meal_care_satisfied"] = []
        context = user.get("meal_check_context")
        if not isinstance(context, dict) or str(context.get("date") or "") != today:
            user["meal_check_context"] = {}

    def _meal_care_slots(self) -> tuple[tuple[str, str, str], ...]:
        return (
            ("breakfast", "07:50-10:05", "早餐"),
            ("lunch", "11:40-14:05", "午饭"),
            ("dinner", "17:40-20:35", "晚饭"),
        )

    def _breakfast_waiting_for_morning_reply(self, user: dict[str, Any]) -> bool:
        if not bool(runtime_persona_setting(self, "enable_daily_greetings", True)):
            return False
        self._reset_daily_counter_if_needed(user)
        morning_sent_at = _safe_float(user.get("morning_greeting_sent_at"), 0)
        morning_reply_at = _safe_float(user.get("morning_greeting_reply_at"), 0)
        return morning_sent_at <= 0 or morning_reply_at < morning_sent_at

    def _meal_care_followup_blocked_by_newer_food_prompt(
        self,
        user: dict[str, Any],
        context: dict[str, Any],
        *,
        now: float,
    ) -> bool:
        """Keep a meal follow-up from bypassing a newer food-topic cooldown.

        The initial meal-care message itself sets ``last_food_prompt_at`` to
        the same timestamp as ``asked_at`` and must not cancel its own optional
        follow-up. Any later food prompt, however, supersedes the old question.
        """
        asked_at = _safe_float(context.get("asked_at"), 0)
        last_food_prompt_at = _safe_float(user.get("last_food_prompt_at"), 0)
        return bool(
            asked_at > 0
            and last_food_prompt_at > asked_at + 1
            and self._food_prompt_cooldown_remaining(user, now=now) > 0
        )

    def _meal_care_followup_event(self, user: dict[str, Any], *, now: float) -> dict[str, Any] | None:
        context = user.get("meal_check_context")
        if not isinstance(context, dict) or not context.get("active"):
            return None
        if str(context.get("date") or "") != _today_key():
            user["meal_check_context"] = {}
            return None
        if _safe_int(context.get("followup_count"), 0, 0, 1) >= 1:
            return None
        if self._meal_care_followup_blocked_by_newer_food_prompt(user, context, now=now):
            context.update(
                {
                    "active": False,
                    "stage": "closed_newer_food_prompt",
                    "closed_at": now,
                    "followup_due_at": 0,
                }
            )
            user["meal_check_context"] = context
            return None
        due_at = _safe_float(context.get("followup_due_at"), 0)
        expires_at = _safe_float(context.get("expires_at"), 0)
        if due_at <= 0 or (expires_at > 0 and now > expires_at):
            return None
        stage = _single_line(context.get("stage"), 24) or "awaiting_status"
        meal_label = _single_line(context.get("meal_label"), 12) or "这顿饭"
        if stage == "awaiting_detail":
            topic = f"{meal_label}具体吃了什么"
            motive = f"用户只说已经吃过{meal_label}，还想自然问清具体吃了什么并记住"
        elif stage == "not_eaten":
            topic = f"{meal_label}后来有没有吃上"
            motive = f"用户刚才还没吃{meal_label}，隔一会儿想低压确认有没有垫上"
        else:
            topic = f"{meal_label}吃了吗"
            motive = f"刚才问过用户{meal_label}，还没得到具体饮食信息，想只补问一次"
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(max(1, int((due_at - now) / 60)), width_minutes=20),
            "reason": "meal_care_followup",
            "action": "message",
            "why": "一顿饭的关心还没有落到具体信息，只允许低压补问一次",
            "topic": topic,
            "motive": self._normalize_internal_motive_text(motive),
            "scene": "前一次吃饭关心之后",
            "tone": "自然、简短，不催促",
            "impulse": "想确认用户有没有好好吃东西",
            "_scheduled_ts": due_at,
            "_daily_meal_care": True,
            "_meal_care_followup": True,
            "context_key": "planned_meal_care_context",
            "context": dict(context),
        }

    def _pick_meal_care_event(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any] | None:
        if not bool(runtime_persona_setting(self, "enable_meal_care_proactive", True)):
            return None
        if self._private_user_role(user) != "owner":
            return None
        self._reset_meal_care_day(user)
        check_now = _engine_host._now_ts() if now is None else now
        followup = self._meal_care_followup_event(user, now=check_now)
        if isinstance(followup, dict):
            return followup
        if self._meal_care_interval_remaining(user, now=check_now) > 0:
            return None
        if self._food_prompt_cooldown_remaining(user, now=check_now) > 0:
            return None
        asked = user.get("meal_care_asked") if isinstance(user.get("meal_care_asked"), list) else []
        satisfied = user.get("meal_care_satisfied") if isinstance(user.get("meal_care_satisfied"), list) else []
        max_daily = _safe_int(
            runtime_persona_setting(self, "meal_care_max_daily", 1), 1, 0, 3
        )
        if max_daily <= 0 or len(asked) >= max_daily:
            return None
        now_dt = self._environment_fromtimestamp(check_now)
        minute = now_dt.hour * 60 + now_dt.minute
        today = now_dt.date()
        candidates: list[tuple[float, dict[str, Any]]] = []
        for meal_key, window, meal_label in self._meal_care_slots():
            if meal_key in asked or meal_key in satisfied:
                continue
            if meal_key == "breakfast" and self._breakfast_waiting_for_morning_reply(user):
                continue
            start, end = self._parse_window_minutes(window)
            if start is None or end is None or minute >= end:
                continue
            start_dt = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo) + timedelta(minutes=start)
            end_dt = datetime.combine(today, datetime.min.time(), tzinfo=now_dt.tzinfo) + timedelta(minutes=end)
            earliest = max(now_dt + timedelta(minutes=1), start_dt)
            if earliest >= end_dt:
                continue
            latest = min(end_dt, earliest + timedelta(minutes=42))
            scheduled = _engine_host.random.uniform(earliest.timestamp(), max(earliest.timestamp() + 60, latest.timestamp()))
            context = {
                "active": False,
                "date": _today_key(),
                "meal_key": meal_key,
                "meal_label": meal_label,
                "stage": "planned",
                "followup_count": 0,
            }
            candidates.append(
                (
                    scheduled,
                    {
                        "date": _today_key(),
                        "window": window,
                        "reason": "meal_care",
                        "action": "message",
                        "why": f"到了{meal_label}时段，惦记用户有没有按时吃东西",
                        "topic": f"{meal_label}吃了吗",
                        "motive": self._normalize_internal_motive_text(f"想问对方{meal_label}吃了没有"),
                        "scene": f"{meal_label}时段",
                        "tone": "关心但不管教",
                        "impulse": "想确认用户有没有好好吃东西",
                        "_scheduled_ts": scheduled,
                        "_daily_meal_care": True,
                        "context_key": "planned_meal_care_context",
                        "context": context,
                    },
                )
            )
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _daily_plan_morning_wake_minutes(self) -> int | None:
        """Return the Bot wake point represented by the active daily plan."""
        plan_getter = getattr(self, "_get_active_plan", None)
        plan = plan_getter() if callable(plan_getter) else self.data.get("daily_plan", {})
        if not isinstance(plan, dict):
            return None
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            return None

        starts_getter = getattr(self, "_normalized_plan_item_starts", None)
        starts = starts_getter(items) if callable(starts_getter) else []
        if not isinstance(starts, list) or len(starts) != len(items):
            return None

        sleeping_ends: list[tuple[int, int]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict) or starts[index] is None or not self._is_sleepy_plan_item(item):
                continue
            start = int(starts[index])
            next_start = next((value for value in starts[index + 1 :] if value is not None), None)
            end = self._plan_item_end_minutes(start, item, next_start=next_start)
            wake_minute = end % (24 * 60)
            if 4 * 60 <= wake_minute <= 11 * 60 + 30:
                sleeping_ends.append((end, wake_minute))
        if sleeping_ends:
            return max(sleeping_ends, key=lambda value: value[0])[1]

        # Some plans begin at waking and omit the preceding overnight sleep segment.
        waking_items: list[tuple[int, int]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict) or starts[index] is None:
                continue
            text = " ".join(
                _single_line(item.get(key), 100)
                for key in ("activity", "mood", "message_seed")
                if _single_line(item.get(key), 100)
            )
            wake_minute = int(starts[index]) % (24 * 60)
            if 4 * 60 <= wake_minute <= 11 * 60 + 30 and re.search(r"睡醒|醒来|醒后|刚醒|起床|洗漱", text):
                waking_items.append((int(starts[index]), wake_minute))
        return min(waking_items, key=lambda value: value[0])[1] if waking_items else None

    def _morning_greeting_window(self) -> tuple[int, int]:
        wake_minute = self._daily_plan_morning_wake_minutes()
        if wake_minute is None:
            return 7 * 60 + 45, 10 * 60 + 20
        start = wake_minute + 3
        end = min(12 * 60, wake_minute + 50)
        if end - start < 15:
            return 7 * 60 + 45, 10 * 60 + 20
        return start, end

    def _insomnia_night_key(self, now: float | None = None) -> str:
        current = self._environment_fromtimestamp(_engine_host._now_ts() if now is None else now)
        # 23:00-05:59 is one night, even though it crosses midnight.
        return (current - timedelta(hours=6)).date().isoformat()
