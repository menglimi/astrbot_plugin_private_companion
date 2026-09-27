# -*- coding: utf-8 -*-
"""DailyStateWeatherEnvironmentChangeMixin。

由 tools/split_mixin_domain.py 从 daily_state_weather.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 344 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateWeatherMixin）。
"""
from __future__ import annotations

from .daily_state_weather_shared import _now_ts, logger
from .daily_state_weather_shared import Any
from .daily_state_weather_shared import PromptRenderMode
from .daily_state_weather_shared import PromptSection
from .daily_state_weather_shared import _safe_float
from .daily_state_weather_shared import _safe_int
from .daily_state_weather_shared import _single_line
from .daily_state_weather_shared import asyncio
from .daily_state_weather_shared import deepcopy
from .daily_state_weather_shared import hashlib
from .daily_state_weather_shared import prompt_section
from .daily_state_weather_shared import random
from .daily_state_weather_shared import re
from .daily_state_weather_shared import render_prompt_sections
from .daily_state_weather_shared import runtime_persona_setting



class DailyStateWeatherEnvironmentChangeMixin:
    """DailyStateWeatherEnvironmentChangeMixin（从 DailyStateWeatherMixin 拆出）。"""


    def _pick_weather_window(self, weather_kind: str) -> str:
        hour = self._environment_now().hour
        if weather_kind == "rain":
            if 6 <= hour < 11:
                return "08:20-10:40"
            if 11 <= hour < 17:
                return "12:40-16:30"
            if 17 <= hour < 23:
                return "18:10-21:10"
            return "09:00-10:30"
        if 16 <= hour < 20:
            return "17:10-19:20"
        return "15:30-18:10"

    def _environment_weather_observation(self, weather: dict[str, Any] | None) -> dict[str, Any]:
        text = self._weather_summary_text(weather)
        if text == "暂无天气信息":
            return {}
        compact = text.lower()
        category = "other"
        severity = 1
        category_rules = (
            ("thunder", 4, ("雷暴", "雷阵雨", "打雷", "雷电")),
            ("heavy_rain", 4, ("暴雨", "大暴雨", "特大暴雨", "大雨")),
            ("snow", 4, ("暴雪", "大雪", "中雪", "小雪", "雨夹雪", "降雪")),
            ("dust", 4, ("沙尘暴", "扬沙", "浮尘")),
            ("rain", 3, ("阵雨", "中雨", "小雨", "降雨", "下雨", "雨天")),
            ("fog", 3, ("大雾", "浓雾", "雾霾", "雾")),
            ("wind", 3, ("大风", "强风", "狂风", "阵风")),
            ("clear", 1, ("晴朗", "晴天", "晴")),
            ("cloud", 1, ("阴天", "多云", "阴")),
        )
        for candidate, candidate_severity, markers in category_rules:
            if any(marker in compact for marker in markers):
                category = candidate
                severity = candidate_severity
                break
        temperature = None
        match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:°\s*c|℃|摄氏度)", compact, flags=re.I)
        if match:
            try:
                temperature = float(match.group(1))
            except (TypeError, ValueError):
                temperature = None
        return {
            "text": _single_line(text, 120),
            "category": category,
            "severity": severity,
            "temperature": temperature,
            "fetched_ts": _safe_float((weather or {}).get("fetched_ts"), 0),
        }

    def _detect_environment_weather_change(
        self,
        previous: dict[str, Any] | None,
        current: dict[str, Any] | None,
    ) -> dict[str, Any]:
        before = self._environment_weather_observation(previous)
        after = self._environment_weather_observation(current)
        if not before or not after:
            return {}
        old_category = _single_line(before.get("category"), 24)
        new_category = _single_line(after.get("category"), 24)
        old_temp = before.get("temperature")
        new_temp = after.get("temperature")
        category_labels = {
            "thunder": "突然开始打雷",
            "heavy_rain": "雨势突然变大",
            "rain": "外面开始下雨",
            "snow": "外面开始下雪",
            "fog": "外面突然起雾",
            "wind": "外面的风突然变大",
            "dust": "外面出现沙尘天气",
            "clear": "外面的天突然放晴",
            "cloud": "外面的天色明显转阴",
        }
        wet_categories = {"rain", "heavy_rain", "thunder", "snow"}
        kind = ""
        topic = ""
        score = 0
        if old_category != new_category and new_category in category_labels:
            if old_category in wet_categories and new_category in {"clear", "cloud", "other"}:
                kind = "precipitation_stopped"
                topic = "外面的雨雪停了"
                score = 78
            elif new_category in {"thunder", "heavy_rain", "snow", "dust"}:
                kind = f"weather_to_{new_category}"
                topic = category_labels[new_category]
                score = 92
            elif new_category in {"rain", "fog", "wind"}:
                kind = f"weather_to_{new_category}"
                topic = category_labels[new_category]
                score = 86
            elif old_category in wet_categories and new_category == "clear":
                kind = "weather_cleared"
                topic = category_labels[new_category]
                score = 76

        temperature_delta = None
        if isinstance(old_temp, (int, float)) and isinstance(new_temp, (int, float)):
            temperature_delta = float(new_temp) - float(old_temp)
            if abs(temperature_delta) >= 5.0 and not kind:
                kind = "temperature_jump"
                topic = "气温突然升高了" if temperature_delta > 0 else "气温突然降下来了"
                score = 84 if abs(temperature_delta) >= 8.0 else 78
        if not kind:
            return {}
        fingerprint = f"{kind}:{old_category}>{new_category}:{round(float(new_temp), 1) if isinstance(new_temp, (int, float)) else 'na'}"
        return {
            "kind": kind,
            "topic": topic,
            "score": score,
            "fingerprint": fingerprint,
            "previous": before,
            "current": after,
            "temperature_delta": temperature_delta,
        }

    def _environment_change_owner_users(self) -> list[tuple[str, dict[str, Any]]]:
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(users, dict):
            return []
        targets: list[tuple[str, dict[str, Any]]] = []
        for raw_user_id, user in users.items():
            user_id = str(raw_user_id or "").strip()
            if not user_id or not isinstance(user, dict) or not user.get("umo"):
                continue
            if self._private_user_role(user, user_id) != "owner":
                continue
            if not self._user_enabled_for_proactive(user_id, user):
                continue
            targets.append((user_id, user))
        return targets

    def _queue_environment_change_candidates_locked(self, change: dict[str, Any], *, now: float) -> int:
        if not isinstance(change, dict) or not _single_line(change.get("topic"), 80):
            return 0
        current = self._environment_fromtimestamp(now)
        minute = current.hour * 60 + current.minute
        if not (6 * 60 <= minute < 23 * 60 + 30):
            return 0
        offered = 0
        for user_id, user in self._environment_change_owner_users():
            scheduled = now + random.uniform(1.0, 3.0) * 60.0
            change_fingerprint = _single_line(change.get("fingerprint"), 160)
            candidate = {
                "source": "environment_change",
                "reason": "environment_change",
                "action": "message",
                "scheduled_ts": scheduled,
                "window_start_at": scheduled,
                "preferred_ts": scheduled,
                "best_until_at": scheduled + 20 * 60,
                "expire_at": scheduled + 45 * 60,
                "topic": _single_line(change.get("topic"), 80),
                "motive": "刚注意到外界环境发生了明显变化，想趁变化还新鲜时自然提一句",
                "score": _safe_int(change.get("score"), 82, 0, 100),
                "origin_event_id": (
                    "environment:" + hashlib.sha1(change_fingerprint.encode("utf-8", errors="ignore")).hexdigest()[:24]
                    if change_fingerprint
                    else ""
                ),
                "context_key": "planned_environment_change_context",
                "context": deepcopy(change),
            }
            if self._offer_proactive_candidate(user_id, user, candidate):
                offered += 1
        return offered

    def _format_environment_change_prompt(self, user: dict[str, Any], *, reason: str = "") -> str:
        section = self._format_environment_change_prompt_section(user, reason=reason)
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _format_environment_change_prompt_section(
        self,
        user: dict[str, Any],
        *,
        reason: str = "",
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="environment.change",
                title="刚发生的环境变化",
                source="daily_state",
                content=content,
            )

        if reason != "environment_change" or not isinstance(user, dict):
            return build_section()
        context = user.get("planned_environment_change_context")
        if not isinstance(context, dict):
            return build_section()
        before = context.get("previous") if isinstance(context.get("previous"), dict) else {}
        after = context.get("current") if isinstance(context.get("current"), dict) else {}
        body = (
            f"- 变化前：{_single_line(before.get('text'), 120) or '无可靠信息'}\n"
            f"- 当前：{_single_line(after.get('text'), 120) or '无可靠信息'}\n"
            f"- 可用切口：{_single_line(context.get('topic'), 80)}\n"
            "这是刚刷新到的实时环境变化，只能贴着上述事实自然说一句。不要说监测、接口、天气缓存或系统提醒；"
            "不要扩写成天气预报，也不要虚构用户正在室外。"
        )
        return build_section(body)

    def _format_weather_alert_prompt(self, user: dict[str, Any], *, reason: str = "") -> str:
        """Render one structured alert for the proactive generation prompt."""

        section = self._format_weather_alert_prompt_section(user, reason=reason)
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _format_weather_alert_prompt_section(
        self,
        user: dict[str, Any],
        *,
        reason: str = "",
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="weather.alert",
                title="当前气象预警",
                source="daily_state",
                content=content,
            )

        if reason != "weather_alert" or not isinstance(user, dict):
            return build_section()
        context = user.get("planned_weather_alert_context")
        if not isinstance(context, dict):
            return build_section()
        alert = context.get("alert") if isinstance(context.get("alert"), dict) else context
        if not isinstance(alert, dict):
            return build_section()
        kind = _single_line(context.get("kind"), 20)
        status = _single_line(context.get("status"), 32) or {
            "new": "刚发布",
            "updated": "刚更新",
            "cancelled": "已解除",
            "resolved": "已解除",
            "expired": "已过期",
        }.get(kind, "有变化")
        level = _single_line(alert.get("color") or alert.get("severity"), 24)
        event = _single_line(alert.get("event") or "天气", 48)
        headline = _single_line(alert.get("headline") or alert.get("description"), 220)
        instruction = _single_line(alert.get("instruction"), 500)
        sender = _single_line(alert.get("sender"), 100)
        expire = _single_line(alert.get("expire_time"), 60)
        lines = [
            f"- 状态：{status}",
            f"- 等级/现象：{level + '｜' if level else ''}{event}",
            f"- 标题：{headline or '暂无标题'}",
        ]
        if instruction:
            lines.append(f"- 防护建议：{instruction}")
        if sender:
            lines.append(f"- 发布方：{sender}")
        if expire:
            lines.append(f"- 预计结束：{expire}")
        lines.append(
            "这是一条与主要用户所在地点相关的结构化预警事实。只把它转成一句自然、克制、及时的私聊；"
            "可以提醒减少外出、留意雷雨或按防护建议行动，但不要虚构用户正在室外、已经受灾或一定会发生的结果。"
            "不要说监测、接口、缓存、轮询、API、数据源或内部字段，也不要把普通天气背景和这条预警混成播报清单。"
        )
        return build_section("\n".join(lines))

    async def _maybe_refresh_environment_change(self) -> None:
        if not bool(runtime_persona_setting(self, "enable_environment_change_proactive", True)) or not runtime_persona_setting(self, "enable_weather_context", True):
            return
        lock = getattr(self, "_environment_change_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self._environment_change_lock = lock
        async with lock:
            now = _now_ts()
            state = self.data.setdefault("environment_change_awareness", {})
            if not isinstance(state, dict):
                state = {}
                self.data["environment_change_awareness"] = state
            if now < _safe_float(state.get("next_check_at"), 0):
                return
            initialized = bool(state.get("initialized"))
            interval_minutes = max(
                5,
                    _safe_int(runtime_persona_setting(self, "environment_change_check_minutes", 10), 10, 5, 60),
            )
            state["next_check_at"] = now + interval_minutes * 60
            previous = deepcopy(self.data.get("daily_weather", {}))
            current = await self._ensure_weather_context(force=True)
            state["last_check_at"] = now
            state["last_observation"] = self._environment_weather_observation(current)
            state["initialized"] = True
            if not initialized:
                self._schedule_data_save(
                    sections={"environment_change_awareness", "daily_weather"},
                    delay=0.5,
                )
                return
            change = self._detect_environment_weather_change(previous, current)
            if not change:
                self._schedule_data_save(
                    sections={"environment_change_awareness", "daily_weather"},
                    delay=0.5,
                )
                return
            fingerprint = _single_line(change.get("fingerprint"), 160)
            cooldown_minutes = max(
                20,
                    _safe_int(runtime_persona_setting(self, "environment_change_cooldown_minutes", 90), 90, 20, 360),
            )
            last_prompted_at = _safe_float(state.get("last_prompted_at"), 0)
            recent_fingerprints = state.get("recent_fingerprints")
            if not isinstance(recent_fingerprints, dict):
                recent_fingerprints = {}
                state["recent_fingerprints"] = recent_fingerprints
            cutoff = now - cooldown_minutes * 60
            for old_key, old_ts in list(recent_fingerprints.items()):
                if _safe_float(old_ts, 0) < cutoff:
                    recent_fingerprints.pop(old_key, None)
            recently_repeated = _safe_float(recent_fingerprints.get(fingerprint), 0) >= cutoff
            # 通用冷却：距上一次环境突变提示 < cooldown 内，非紧急（score<90）的变化不
            # 重复开口。不同变化（雨停/下雨/雨势变大）指纹不同，recently_repeated 拦不住；
            # 旧代码用写死的 20 分钟，cooldown_minutes 只对同指纹去重，360 分钟设置等于
            # 形同虚设（实测 30-40 分钟就 offer 一条）。score>=90 的极端变化保留逃生口。
            too_soon = now - last_prompted_at < cooldown_minutes * 60 and _safe_int(change.get("score"), 0) < 90
            if recently_repeated or too_soon:
                self._schedule_data_save(
                    sections={"environment_change_awareness", "daily_weather"},
                    delay=0.5,
                )
                return
            offered = 0
            async with self._data_lock:
                offered = self._queue_environment_change_candidates_locked(change, now=now)
                state["last_change"] = deepcopy(change)
                state["last_change_at"] = now
                if offered:
                    state["last_fingerprint"] = fingerprint
                    state["last_prompted_at"] = now
                    state["last_prompted_users"] = offered
                    recent_fingerprints[fingerprint] = now
                self._save_data_sync(
                    sections={
                        "environment_change_awareness",
                        "daily_weather",
                        "users",
                        "proactive_candidate_pool",
                    }
                )
            if offered:
                logger.info(
                    "环境突变已进入即时主动候选: kind=%s targets=%s topic=%s",
                    _single_line(change.get("kind"), 40),
                    offered,
                    _single_line(change.get("topic"), 80),
                )
