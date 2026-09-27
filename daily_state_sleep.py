# -*- coding: utf-8 -*-
"""sleep 域。

由 tools/split_mixin_domain.py 从 daily_state.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / {8850, 8851, 8852, 8853, 8854, 8855, 8856, 8857, 8858, 8859, 8860, 8862, 8863, 8864, 8865, 8866, 8867, 8868, 8869, 8870, 8871, 8872, 8873, 8874, 8875, 8876, 8877, 8878, 8879, 8880, 8881, 8882, 8883, 8884, 8886, 8887, 8888, 8890, 8891, 8892, 8893, 8894, 8895, 8896, 8897, 8898, 8899, 8901, 8902, 8903, 8904, 8905, 8906, 8907, 8908, 8909, 8910, 8911, 8912, 8913, 8914, 8915, 8916, 8918, 8919, 8920, 8921, 8922, 8923, 8924, 8925, 8926, 8927, 8928, 8929, 8930, 8931, 8932, 8934, 8935, 8936, 8937, 8938, 8939, 8940, 8941, 8942, 8944, 8945, 8946, 8947, 8948, 8949, 8950, 8951, 8952, 8953, 8954, 8955, 8956, 8957, 8958, 8959, 8960, 8961, 8962, 8963, 8964, 8965, 8966, 8967, 8968, 8969, 8970, 8971, 8972, 8973, 8974, 8975, 8976, 8977, 8978, 8979, 8980, 8981, 8982, 8983, 8984, 8985, 8987, 8988, 8989, 8990, 8991, 8992, 8993, 8994, 8995, 8996, 8997, 8998, 8999, 9000, 9001, 9002, 9003, 9004, 9005, 9006, 9007, 9008, 9009, 9010, 9011, 9012, 9013, 9014, 9015, 9016, 9017, 9018, 9019, 9021, 9022, 9023, 9024, 9025, 9026, 9027, 9028, 9029, 9030, 9031, 9032, 9033, 9034, 9035, 9036, 9037, 9038, 9039, 9040, 9041, 9042, 9043, 9044, 9045, 9046, 9047, 9048, 9049, 9050, 9051, 9052, 9053, 9055, 9056, 9057, 9058, 9059, 9060, 9061, 9062, 9063, 9064, 9065, 9066, 9067, 9068, 9069, 9070, 9071, 9072, 9074, 9075, 9076, 9077, 9078, 9079, 9080, 9081, 9082, 9083, 9084, 9086, 9087, 9088, 9089, 9090, 9091, 9092, 9093, 9094, 9095, 9096, 9097, 9098, 9099, 9100, 9101, 9102, 9103, 9104, 9105, 9106, 9107, 9108, 9109, 9110, 9111, 9112, 9113, 9114, 9115, 9116, 9117, 9118, 9119, 9120, 9121, 9122, 9123, 9124, 9125, 9126, 9127, 9129, 9130, 9131, 9132, 9133, 9134, 9135, 9136, 9137, 9139, 9140, 9141, 9142, 9143, 9144, 9145, 9146, 9147, 9148} 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMixin）。
"""
from __future__ import annotations

import re
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from datetime import datetime, timedelta
from typing import Any





# ---- 宿主 patch 兼容层（由 tools/inject_host_patch_shim.py 注入）----
# PyTest 里 patch("...daily_state._today_key") 期望改动能被本模块感知。
# 原 import 会被下面的同名函数覆盖，方法体调用时实时转发到宿主模块。
def _now_ts(*args, **kwargs):
    from . import daily_state as _host
    return getattr(_host, "_now_ts")(*args, **kwargs)

class DailyStateSleepMixin:
    """sleep 域（从 DailyStateMixin 拆出）。"""


    @staticmethod
    def _sleep_phase_label(phase: str) -> str:
        return {
            "awake": "清醒",
            "falling_asleep": "入睡中",
            "light_sleep": "浅睡",
            "woken": "被叫醒",
            "staying_up": "临时晚睡",
            "sleeping_again": "继续睡",
            "natural_wake": "自然醒",
        }.get(str(phase or ""), "清醒")

    def _sleep_runtime_state(self) -> dict[str, Any]:
        state = self.data.setdefault("daily_state", {})
        if not isinstance(state, dict):
            state = {}
            self.data["daily_state"] = state
        runtime = state.setdefault("sleep_runtime", {})
        if not isinstance(runtime, dict):
            runtime = {}
            state["sleep_runtime"] = runtime
        if not runtime.get("phase"):
            now = _now_ts()
            runtime.update(
                {
                    "phase": "awake",
                    "label": self._sleep_phase_label("awake"),
                    "started_at": now,
                    "updated_at": now,
                    "woken_count": 0,
                    "last_event": "尚未进入睡眠段",
                    "source": "init",
                }
            )
        return runtime

    def _sleep_awake_grace_seconds(self) -> int:
        grace_minutes = _safe_int(runtime_persona_setting(self, "rest_reply_awake_grace_minutes", 30), 30, 0)
        return max(0, min(240, grace_minutes)) * 60

    def _sleep_rest_window_active(self) -> bool:
        if not bool(runtime_persona_setting(self, "enable_rest_reply_simulation", False)):
            return True
        checker = getattr(self, "_rest_reply_window_active", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return True
        return True

    @staticmethod
    def _sleep_delay_cn_number(value: Any) -> int | None:
        text = str(value or "").strip().replace("兩", "两").replace("〇", "零")
        if not text:
            return None
        if text.isdigit():
            return int(text)
        digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if text in digits:
            return digits[text]
        if "十" in text:
            left, _, right = text.partition("十")
            tens = digits.get(left, 1) if left else 1
            ones = digits.get(right, 0) if right else 0
            return tens * 10 + ones
        return None

    @classmethod
    def _sleep_delay_parse_minute(cls, value: Any) -> int:
        text = str(value or "").strip()
        if not text:
            return 0
        if text == "半":
            return 30
        if text == "一刻":
            return 15
        if text == "三刻":
            return 45
        parsed = cls._sleep_delay_cn_number(text)
        if parsed is None:
            return 0
        return max(0, min(59, parsed))

    def _sleep_delay_next_local_ts(self, hour: int, minute: int, *, now_dt: datetime | None = None) -> float:
        current = now_dt or self._environment_now()
        target = datetime.combine(current.date(), datetime.min.time(), tzinfo=current.tzinfo) + timedelta(
            hours=max(0, min(23, hour)),
            minutes=max(0, min(59, minute)),
        )
        if target.timestamp() <= current.timestamp() + 60:
            target += timedelta(days=1)
        return target.timestamp()

    def _parse_sleep_delay_until_ts(self, compact: str, *, now_dt: datetime | None = None) -> tuple[float, bool]:
        current = now_dt or self._environment_now()
        hour_token = r"(?:\d{1,2}|[零〇一二两兩三四五六七八九十]{1,3})"
        minute_token = r"(?:\d{1,2}|[零〇一二两兩三四五六七八九十]{1,3}|半|一刻|三刻)"
        match = re.search(
            rf"(?:陪(?:我|着我)?到|陪到|撑到|等到|到|至)"
            rf"(凌晨|半夜|今晚|今夜|夜里|晚上|明早|明天早上|明天)?"
            rf"({hour_token})(?:[:：点點时])({minute_token})?",
            compact,
        )
        if not match:
            return 0.0, False
        period = str(match.group(1) or "")
        hour = self._sleep_delay_cn_number(match.group(2))
        if hour is None:
            return 0.0, False
        minute = self._sleep_delay_parse_minute(match.group(3))
        if period in {"凌晨", "半夜"}:
            if hour == 12:
                hour = 0
        elif period in {"今晚", "今夜", "夜里", "晚上"}:
            if hour == 12:
                hour = 0
            elif 6 <= hour <= 11:
                hour += 12
        elif period in {"明早", "明天早上"}:
            if hour == 12:
                hour = 0
        elif current.hour >= 18:
            if hour == 12:
                hour = 0
            elif 6 <= hour <= 11:
                hour += 12
        if hour > 23:
            return 0.0, False
        target_ts = self._sleep_delay_next_local_ts(hour, minute, now_dt=current)
        if period in {"明早", "明天早上"}:
            target_dt = self._environment_fromtimestamp(target_ts)
            if target_dt.date() == current.date():
                target_ts = (target_dt + timedelta(days=1)).timestamp()
        explicit_cap = min(current.timestamp() + 6 * 3600, self._sleep_delay_next_local_ts(6, 0, now_dt=current))
        return min(target_ts, explicit_cap), True

    def _detect_sleep_delay_request(self, text: str) -> dict[str, Any] | None:
        normalized = _single_line(text, 220)
        compact = re.sub(r"\s+", "", normalized)
        if not compact:
            return None
        if re.search(r"(早点睡|早睡|快睡|去睡|睡觉吧|该睡|别熬夜|不要熬夜|别晚睡|不要晚睡|不许熬夜|不许晚睡|别睡太晚|不要睡太晚)", compact):
            return None
        delay_intent = bool(
            re.search(r"(今晚|今夜|今天晚上|夜里|凌晨|待会|等下|一会).{0,14}(晚点睡|迟点睡|晚睡|先不睡|不睡了|先别睡|别睡|熬夜)", compact)
            or re.search(r"(陪我|陪陪我|陪着我|和我).{0,12}(熬夜|晚点睡|迟点睡|先别睡|别睡|不睡)", compact)
            or re.search(r"(陪我|陪陪我|陪着我|和我).{0,12}到.{0,10}(?:点|點|时|:|：|半).{0,8}(?:再睡|睡觉|去睡)", compact)
            or re.search(r"(陪我|陪陪我|陪着我|和我).{0,12}到(?:凌晨|半夜|今晚|今夜|夜里).{0,10}(?:点|點|时|:|：|半)", compact)
            or re.search(r"(先别睡|别睡了?|别去睡)", compact)
        )
        if not delay_intent:
            return None
        now_dt = self._environment_now()
        explicit_until, explicit = self._parse_sleep_delay_until_ts(compact, now_dt=now_dt)
        if explicit_until > now_dt.timestamp() + 5 * 60:
            until_ts = explicit_until
        else:
            default_until = now_dt.timestamp() + 2 * 3600
            default_cap = self._sleep_delay_next_local_ts(3, 30, now_dt=now_dt)
            until_ts = min(default_until, default_cap)
            if until_ts < now_dt.timestamp() + 30 * 60:
                until_ts = min(now_dt.timestamp() + 60 * 60, self._sleep_delay_next_local_ts(6, 0, now_dt=now_dt))
        until_text = self._environment_fromtimestamp(until_ts).strftime("%m-%d %H:%M")
        return {
            "until_ts": until_ts,
            "until_text": until_text,
            "explicit_time": explicit,
            "user_text": normalized,
        }

    def _sleep_delay_override_state(
        self,
        runtime: dict[str, Any] | None = None,
        *,
        now: float | None = None,
        clear_expired: bool = True,
    ) -> dict[str, Any]:
        check_now = _now_ts() if now is None else now
        runtime = runtime if isinstance(runtime, dict) else self._sleep_runtime_state()
        until_ts = _safe_float(runtime.get("sleep_delay_until_ts"), 0)
        if until_ts <= check_now:
            if clear_expired and until_ts > 0:
                for key in (
                    "sleep_delay_until_ts",
                    "sleep_delay_until_text",
                    "sleep_delay_reason",
                    "sleep_delay_user_text",
                    "sleep_delay_set_at",
                    "sleep_delay_explicit_time",
                ):
                    runtime.pop(key, None)
            return {}
        until_text = _single_line(runtime.get("sleep_delay_until_text"), 24)
        if not until_text:
            until_text = self._environment_fromtimestamp(until_ts).strftime("%m-%d %H:%M")
            runtime["sleep_delay_until_text"] = until_text
        return {
            "until_ts": until_ts,
            "until_text": until_text,
            "reason": _single_line(runtime.get("sleep_delay_reason"), 120),
            "user_text": _single_line(runtime.get("sleep_delay_user_text"), 120),
            "explicit_time": bool(runtime.get("sleep_delay_explicit_time")),
        }

    def _apply_sleep_delay_override(self, delay: dict[str, Any], *, text: str = "") -> dict[str, Any]:
        until_ts = _safe_float(delay.get("until_ts"), 0)
        if until_ts <= _now_ts():
            return self._sleep_runtime_state()
        until_text = _single_line(delay.get("until_text"), 24) or self._environment_fromtimestamp(until_ts).strftime("%m-%d %H:%M")
        runtime = self._set_sleep_phase(
            "staying_up",
            event=f"用户约定今晚晚点休息，到 {until_text} 前按临时陪聊处理",
            source="user_sleep_delay",
            now=_now_ts(),
        )
        runtime["sleep_delay_until_ts"] = until_ts
        runtime["sleep_delay_until_text"] = until_text
        runtime["sleep_delay_reason"] = "用户临时要求今晚晚点睡或陪聊"
        runtime["sleep_delay_user_text"] = _single_line(text or delay.get("user_text"), 120)
        runtime["sleep_delay_set_at"] = _now_ts()
        runtime["sleep_delay_explicit_time"] = bool(delay.get("explicit_time"))
        return runtime

    def _set_sleep_phase(self, phase: str, *, event: str, source: str = "schedule", now: float | None = None) -> dict[str, Any]:
        now = now or _now_ts()
        runtime = self._sleep_runtime_state()
        if runtime.get("phase") != phase:
            runtime["started_at"] = now
        runtime["phase"] = phase
        runtime["label"] = self._sleep_phase_label(phase)
        runtime["updated_at"] = now
        runtime["last_event"] = _single_line(event, 120)
        runtime["source"] = source
        return runtime

    def _refresh_sleep_runtime_state(self, current_item: dict[str, Any] | None = None, *, now: float | None = None) -> dict[str, Any]:
        now = now or _now_ts()
        runtime = self._sleep_runtime_state()
        item = current_item if isinstance(current_item, dict) else self._get_current_plan_item(self.data.get("daily_plan", {}))
        rest_window_active = self._sleep_rest_window_active()
        base_sleepy = rest_window_active and self._is_sleepy_plan_item(item) if isinstance(item, dict) else False
        delay_override = self._sleep_delay_override_state(runtime, now=now)
        if delay_override and (base_sleepy or runtime.get("phase") == "staying_up"):
            return self._set_sleep_phase(
                "staying_up",
                event=f"用户约定今晚晚点休息，到 {delay_override.get('until_text')} 前不按睡眠段拦截",
                source="user_sleep_delay",
                now=now,
            )
        sleepy = base_sleepy and not delay_override
        if runtime.get("phase") == "staying_up" and not delay_override:
            if not sleepy:
                return self._set_sleep_phase("awake", event="临时晚睡约定已结束，当前不在休息段", source="time", now=now)
            text = " ".join(_single_line(item.get(key), 80) for key in ("activity", "mood", "message_seed")) if isinstance(item, dict) else ""
            if any(token in text for token in ("准备睡", "睡前", "入睡", "洗漱", "收声")):
                return self._set_sleep_phase("falling_asleep", event="临时晚睡约定结束，回到睡前段", source="schedule", now=now)
            return self._set_sleep_phase("light_sleep", event="临时晚睡约定结束，回到休息段", source="schedule", now=now)
        if runtime.get("phase") == "woken":
            last_woken = _safe_float(runtime.get("last_woken_at"), _safe_float(runtime.get("updated_at"), now))
            grace_seconds = self._sleep_awake_grace_seconds()
            if grace_seconds <= 0 or now - last_woken >= grace_seconds:
                if not sleepy:
                    return self._set_sleep_phase("natural_wake", event="醒后缓冲结束，当前已不在有效休息段", source="time", now=now)
                return self._set_sleep_phase("sleeping_again", event="用户没有继续打扰，睡意重新接上", source="quiet", now=now)
            return runtime
        if sleepy:
            text = " ".join(_single_line(item.get(key), 80) for key in ("activity", "mood", "message_seed"))
            if any(token in text for token in ("准备睡", "睡前", "入睡", "洗漱", "收声")):
                return self._set_sleep_phase("falling_asleep", event="日程进入睡前或入睡段", source="schedule", now=now)
            if runtime.get("phase") == "sleeping_again":
                return runtime
            return self._set_sleep_phase("light_sleep", event="日程处于睡眠或休息延续", source="schedule", now=now)
        if runtime.get("phase") in {"falling_asleep", "light_sleep", "sleeping_again"}:
            return self._set_sleep_phase("natural_wake", event="睡眠段结束，按日程自然醒来", source="schedule", now=now)
        if runtime.get("phase") == "natural_wake" and now - _safe_float(runtime.get("updated_at"), now) > 2 * 3600:
            return self._set_sleep_phase("awake", event="自然醒后的日常清醒状态", source="time", now=now)
        return runtime

    def _mark_sleep_woken_by_user(self, text: str) -> dict[str, Any]:
        now = _now_ts()
        runtime = self._sleep_runtime_state()
        count = _safe_int(runtime.get("woken_count"), 0, 0) + 1
        updated = self._set_sleep_phase("woken", event="用户消息把睡眠段轻轻叫醒", source="user_message", now=now)
        updated["woken_count"] = count
        updated["last_woken_at"] = now
        updated["last_user_text"] = _single_line(text, 80)
        return updated

    def _mark_sleep_woken_by_group_wakeup(self, text: str, *, wakeup_type: str = "") -> dict[str, Any]:
        now = _now_ts()
        runtime = self._sleep_runtime_state()
        count = _safe_int(runtime.get("woken_count"), 0, 0) + 1
        updated = self._set_sleep_phase("woken", event="群聊里被提到或被话题轻轻叫醒", source="group_wakeup", now=now)
        updated["woken_count"] = count
        updated["last_woken_at"] = now
        updated["last_group_wakeup_text"] = _single_line(text, 80)
        updated["last_group_wakeup_type"] = _single_line(wakeup_type, 40)
        return updated
