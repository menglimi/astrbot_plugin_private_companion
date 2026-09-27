# -*- coding: utf-8 -*-
"""SceneContextPart01Mixin。

由 tools/split_mixin_domain.py 从 scene_context.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 337 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 SceneContextMixin）。
"""
from __future__ import annotations

from .helpers import _now_ts, _safe_float, _single_line
from .persona_config import runtime_persona_setting
from datetime import datetime
from typing import Any



class SceneContextPart01Mixin:
    """SceneContextPart01Mixin（从 SceneContextMixin 拆出）。"""


    @staticmethod
    def _scene_context_daypart(hour: int) -> str:
        if hour < 5:
            return "深夜"
        if hour < 9:
            return "早晨"
        if hour < 12:
            return "上午"
        if hour < 14:
            return "中午"
        if hour < 18:
            return "下午"
        if hour < 22:
            return "晚上"
        return "夜间"

    @staticmethod
    def _scene_context_energy_label(energy: int) -> str:
        if energy < 35:
            return "很低"
        if energy < 55:
            return "偏低"
        if energy >= 85:
            return "充足"
        return "平稳"

    def _scene_context_now(self) -> datetime:
        getter = getattr(self, "_environment_now", None)
        if callable(getter):
            try:
                value = getter()
                if isinstance(value, datetime):
                    return value
            except Exception:
                pass
        return datetime.now().astimezone()

    def _scene_context_realtime_extension(self, user_id: str, role: str = "") -> dict[str, Any]:
        """Read active extension state for the shared scene without exposing stale data."""
        now = _now_ts()
        activities = getattr(self, "_external_realtime_activities", None)
        active: dict[str, Any] = {}
        if isinstance(activities, dict):
            for key, item in list(activities.items()):
                if not isinstance(item, dict) or _safe_float(item.get("expires_at"), 0.0) <= now:
                    activities.pop(key, None)
                    continue
                item_user = _single_line(item.get("user_id"), 80)
                if user_id and item_user == user_id:
                    active = dict(item)
                    break
                if not active and role != "owner":
                    active = dict(item)
        continuity = getattr(self, "_external_realtime_continuity", None)
        recent: dict[str, Any] = {}
        if isinstance(continuity, dict):
            for key, item in list(continuity.items()):
                if not isinstance(item, dict) or _safe_float(item.get("expires_at"), 0.0) <= now:
                    continuity.pop(key, None)
                    continue
                if _single_line(item.get("user_id"), 80) == user_id:
                    recent = dict(item)
                    break
        if role != "owner":
            # Group/secondary-user snapshots may use the public coarse view only.
            recent = {
                **recent,
                "summary": _single_line(recent.get("public_summary"), 360),
                "facts": [],
            } if recent else {}
        return {"activity": active, "continuity": recent}

    def _scene_context_current_schedule(
        self,
        plan: dict[str, Any],
    ) -> tuple[dict[str, Any], str, str]:
        current_item: dict[str, Any] = {}
        getter = getattr(self, "_agenda_current_context_item", None)
        legacy_getter = getattr(self, "_get_current_plan_item", None)
        if callable(getter) or callable(legacy_getter):
            try:
                value = getter() if callable(getter) else legacy_getter(plan)
                if isinstance(value, dict):
                    current_item = value
            except Exception:
                current_item = {}

        schedule_text = ""
        formatter = getattr(self, "_format_plan_item_for_prompt", None)
        if current_item and callable(formatter):
            try:
                schedule_text = _single_line(formatter(current_item), 320)
            except Exception:
                schedule_text = ""
        if not schedule_text and current_item:
            window = "-".join(
                part
                for part in (
                    _single_line(current_item.get("time"), 12),
                    _single_line(current_item.get("end"), 12),
                )
                if part
            )
            activity = _single_line(current_item.get("activity"), 160)
            schedule_text = _single_line(" ".join(part for part in (window, activity) if part), 320)

        runtime_status = ""
        status_getter = getattr(self, "_plan_item_runtime_status", None)
        if current_item and callable(status_getter):
            try:
                items = plan.get("items") if isinstance(plan.get("items"), list) else []
                index = next(
                    (idx for idx, item in enumerate(items) if item is current_item),
                    -1,
                )
                runtime_status = _single_line(
                    status_getter(plan, current_item, index),
                    32,
                )
            except Exception:
                runtime_status = ""
        return current_item, schedule_text, runtime_status

    def _scene_context_schedule_history(
        self,
        plan: dict[str, Any],
        *,
        captured: datetime,
    ) -> list[dict[str, str]]:
        """Return today's started schedule items without treating cancelled plans as facts."""

        disclosure = getattr(self, "_agenda_disclosure_view", None)
        if callable(disclosure):
            try:
                view = disclosure("history_fact", now=captured, max_entries=24)
                entries = getattr(view, "entries", None)
                if entries is None and hasattr(view, "get"):
                    entries = view.get("entries", [])
            except Exception:
                entries = []
            history: list[dict[str, str]] = []
            for item in entries if isinstance(entries, list) else []:
                if not isinstance(item, dict):
                    continue
                start_at = _single_line(item.get("start_at") or item.get("start"), 48)
                end_at = _single_line(item.get("end_at") or item.get("end"), 48)
                start_text = start_at.split("T", 1)[1][:5] if "T" in start_at else _single_line(item.get("time"), 12)
                end_text = end_at.split("T", 1)[1][:5] if "T" in end_at else _single_line(item.get("end"), 12)
                history.append(
                    {
                        "time": start_text,
                        "end": end_text,
                        "status": _single_line(item.get("status"), 32),
                        "activity": _single_line(item.get("title") or item.get("activity"), 160),
                        "mood": _single_line(item.get("mood"), 32),
                    }
                )
            return history[:24]

        today = captured.strftime("%Y-%m-%d")
        if _single_line(plan.get("date"), 20) != today:
            return []
        items = plan.get("items")
        if not isinstance(items, list):
            return []
        starts_getter = getattr(self, "_normalized_plan_item_starts", None)
        end_getter = getattr(self, "_plan_item_end_minutes", None)
        status_getter = getattr(self, "_plan_item_runtime_status", None)
        lifecycle_normalizer = getattr(self, "_normalize_schedule_lifecycle_status", None)
        time_formatter = getattr(self, "_minutes_to_hhmm", None)
        if not all(callable(item) for item in (starts_getter, end_getter, status_getter, lifecycle_normalizer, time_formatter)):
            return []

        try:
            starts = starts_getter(items)
        except Exception:
            return []
        now_minutes = captured.hour * 60 + captured.minute
        started_items: list[tuple[int, int, dict[str, Any]]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            start = starts[index] if index < len(starts) else None
            if start is None or int(start) > now_minutes:
                continue
            started_items.append((int(start), index, item))

        history: list[dict[str, str]] = []
        for start, index, item in sorted(started_items, key=lambda value: (value[0], value[1])):
            explicit_status = lifecycle_normalizer(item.get("lifecycle_status"))
            if explicit_status == "cancelled":
                continue
            try:
                runtime_status = status_getter(plan, item, index)
            except Exception:
                runtime_status = ""
            status = "changed" if explicit_status == "changed" else runtime_status
            if status not in {"active", "completed", "changed"}:
                continue
            next_start = next(
                (value for value in starts[index + 1 :] if value is not None),
                None,
            )
            try:
                end = end_getter(start, item, next_start=next_start)
            except Exception:
                end = start
            history.append(
                {
                    "time": _single_line(time_formatter(start), 12),
                    "end": _single_line(time_formatter(int(end)), 12),
                    "status": status,
                    "activity": _single_line(item.get("activity"), 160),
                    "mood": _single_line(item.get("mood"), 32),
                }
            )
            if len(history) >= 24:
                break
        return history

    def _scene_context_weather_alert_snapshot(self, data: dict[str, Any]) -> dict[str, Any]:
        """Read the already-fetched alert cache without doing network I/O."""

        if not bool(runtime_persona_setting(self, "enable_weather_context", True)) or not bool(
            runtime_persona_setting(self, "enable_weather_alerts", True)
        ):
            return {
                "enabled": False,
                "stale": False,
                "fetched_ts": 0,
                "error": "",
                "count": 0,
                "highest_level": "",
                "alerts": [],
            }
        cache = data.get("weather_alerts") if isinstance(data.get("weather_alerts"), dict) else {}
        raw_alerts = cache.get("alerts") if isinstance(cache.get("alerts"), list) else []
        config_key_getter = getattr(self, "_weather_alert_config_key", None)
        if callable(config_key_getter):
            try:
                current_config_key = _single_line(config_key_getter(), 96)
            except Exception:
                current_config_key = ""
            cached_config_key = _single_line(cache.get("config_key"), 96)
            if not current_config_key or cached_config_key != current_config_key:
                # A failed refresh must not expose an alert from the previous
                # location or API host as if it belonged to the new one.
                raw_alerts = []
        filter_getter = getattr(self, "_filter_weather_alerts", None)
        try:
            alerts = (
                filter_getter(
                    raw_alerts,
                    runtime_persona_setting(self, "weather_alert_min_severity", "blue"),
                )
                if callable(filter_getter)
                else raw_alerts
            )
        except Exception:
            alerts = raw_alerts
        now = _now_ts()
        normalized: list[dict[str, Any]] = []
        for raw in alerts:
            if not isinstance(raw, dict):
                continue
            if bool(raw.get("is_cancelled")):
                continue
            expire_ts = 0.0
            parser = getattr(self, "_weather_alert_time_ts", None)
            if callable(parser):
                try:
                    expire_ts = float(parser(raw.get("expire_time")) or 0)
                except Exception:
                    expire_ts = 0.0
            if expire_ts > 0 and expire_ts <= now:
                continue
            normalized.append(
                {
                    "id": _single_line(raw.get("id") or raw.get("fingerprint"), 120),
                    "event": _single_line(raw.get("event") or "天气", 48),
                    "event_code": _single_line(raw.get("event_code"), 40),
                    "level": _single_line(raw.get("color") or raw.get("severity"), 24),
                    "severity": _single_line(raw.get("severity"), 24),
                    "headline": _single_line(raw.get("headline") or raw.get("description"), 180),
                    "instruction": _single_line(raw.get("instruction"), 320),
                    "sender": _single_line(raw.get("sender"), 80),
                    "issued_time": _single_line(raw.get("issued_time"), 48),
                    "expire_time": _single_line(raw.get("expire_time"), 48),
                }
            )
        rank_getter = getattr(self, "_qweather_alert_rank", None)
        if callable(rank_getter):
            normalized.sort(key=lambda item: rank_getter(item.get("level") or item.get("severity")), reverse=True)
        return {
            "enabled": bool(cache) and _single_line(cache.get("source"), 24) == "qweather",
            "stale": bool(cache.get("stale")),
            "fetched_ts": cache.get("fetched_ts", 0),
            "error": _single_line(cache.get("error"), 100),
            "count": len(normalized),
            "highest_level": _single_line((normalized[0] if normalized else {}).get("level"), 24),
            "alerts": normalized[:6],
        }

    @staticmethod
    def _scene_context_condition_labels(state: dict[str, Any]) -> list[str]:
        raw = state.get("conditions")
        if not isinstance(raw, list):
            return []
        labels: list[str] = []
        for item in raw[:8]:
            if isinstance(item, dict):
                label = _single_line(
                    item.get("label")
                    or item.get("name")
                    or item.get("effect")
                    or item.get("text"),
                    36,
                )
            else:
                label = _single_line(item, 36)
            if label and label not in labels:
                labels.append(label)
            if len(labels) >= 4:
                break
        return labels

    @staticmethod
    def _scene_context_outfit_description(profile: dict[str, Any]) -> str:
        fields = (
            ("top", "上装"),
            ("outer", "外搭"),
            ("bottom", "下装"),
            # 鞋子也是当天穿搭的一部分：衣柜接管的投影带 footwear，这里不收就等于
            # 私聊提示词的「当天基础穿搭」永远不提鞋（与 proactive_message 的字段表对齐）。
            ("footwear", "鞋履"),
            ("accessory", "配饰"),
            ("palette", "配色"),
            ("silhouette", "轮廓"),
        )
        parts = [
            f"{label}:{value}"
            for key, label in fields
            if (value := _single_line(profile.get(key), 100))
        ]
        return _single_line("；".join(parts), 360)
