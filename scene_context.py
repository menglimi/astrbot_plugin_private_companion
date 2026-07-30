# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .helpers import _now_ts, _path_text, _safe_int, _single_line


SCENE_CONTEXT_VERSION = 1


def infer_companion_scene_category(schedule_text: Any = "", location_text: Any = "") -> tuple[str, str]:
    """Infer a coarse visual scene without inventing a location when context is ambiguous."""
    location = _single_line(location_text, 120).lower().replace(" ", "")
    schedule = _single_line(schedule_text, 360).lower().replace(" ", "")
    home_markers = (
        "在家", "家里", "家中", "回到家", "已经到家", "居家", "宅家",
        "宿舍", "公寓", "租房", "房间", "卧室", "客厅", "书桌", "床上", "被窝", "室内日常",
    )
    outdoor_markers = (
        "外出", "通勤", "路上", "外面", "出门", "上班", "上学", "逛街", "旅行",
        "商场", "公司", "办公室", "工作地点", "教室", "学校", "图书馆", "咖啡店", "食堂", "街头",
    )

    if location in {"家", "家里", "家中"} or any(marker in location for marker in home_markers):
        return "home", "居家室内"
    if any(marker in location for marker in outdoor_markers):
        return "outdoor", "外出"
    if any(marker in schedule for marker in home_markers):
        return "home", "居家室内"
    if any(marker in schedule for marker in outdoor_markers):
        return "outdoor", "外出"
    return "", ""


class SceneContextMixin:
    """Build a read-only life-context snapshot shared by visual integrations."""

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

    def _scene_context_current_schedule(
        self,
        plan: dict[str, Any],
    ) -> tuple[dict[str, Any], str, str]:
        current_item: dict[str, Any] = {}
        getter = getattr(self, "_get_current_plan_item", None)
        if callable(getter):
            try:
                value = getter(plan)
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

        if not bool(getattr(self, "enable_weather_context", True)) or not bool(getattr(self, "enable_weather_alerts", True)):
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
            alerts = filter_getter(raw_alerts, getattr(self, "weather_alert_min_severity", "blue")) if callable(filter_getter) else raw_alerts
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

    def _build_companion_scene_snapshot(
        self,
        user: dict[str, Any] | None = None,
        *,
        now: datetime | None = None,
        include_dialogue_outfit: bool = True,
    ) -> dict[str, Any]:
        captured = now if isinstance(now, datetime) else self._scene_context_now()
        data = getattr(self, "data", {})
        data = data if isinstance(data, dict) else {}
        state = data.get("daily_state")
        state = state if isinstance(state, dict) else {}
        plan = data.get("daily_plan")
        plan = plan if isinstance(plan, dict) else {}
        current_item, schedule_text, runtime_status = self._scene_context_current_schedule(plan)
        schedule_history = self._scene_context_schedule_history(plan, captured=captured)

        location = ""
        location_getter = getattr(self, "_current_location_state_text", None)
        if callable(location_getter):
            try:
                location = _single_line(location_getter(state), 80)
            except Exception:
                location = ""
        if not location:
            location = _single_line(state.get("location"), 80)
        location_source = _single_line(state.get("location_source"), 40)
        detail_location_getter = getattr(self, "_current_detail_model_location", None)
        if callable(detail_location_getter):
            try:
                detail_location = _single_line(detail_location_getter(), 80)
            except Exception:
                detail_location = ""
            if detail_location and detail_location == location:
                location_source = "detail_model"
        coarse_location = ""
        coarse_getter = getattr(self, "_coarse_roleplay_location_text", None)
        if location and callable(coarse_getter):
            try:
                coarse_location = _single_line(coarse_getter(location), 40)
            except Exception:
                coarse_location = ""
        scene_category, scene_category_label = infer_companion_scene_category(
            schedule_text,
            coarse_location or location,
        )

        weather_data = data.get("daily_weather")
        weather_data = weather_data if isinstance(weather_data, dict) else {}
        weather = ""
        weather_getter = getattr(self, "_weather_summary_text", None)
        if callable(weather_getter):
            try:
                weather = _single_line(weather_getter(weather_data), 220)
            except Exception:
                weather = ""
        if not weather:
            weather = _single_line(
                weather_data.get("prompt") or weather_data.get("summary"),
                220,
            )
        if weather == "暂无天气信息":
            weather = ""
        weather_alerts = self._scene_context_weather_alert_snapshot(data)

        today = captured.strftime("%Y-%m-%d")
        outfit_item = data.get("daily_outfit_photo")
        outfit_item = outfit_item if isinstance(outfit_item, dict) else {}
        outfit_profile = outfit_item.get("outfit_profile")
        outfit_profile = outfit_profile if isinstance(outfit_profile, dict) else {}
        outfit_path = _path_text(outfit_item.get("path"), 1000)
        outfit_is_today = _single_line(outfit_item.get("date"), 20) == today
        outfit_available = False
        if outfit_is_today and outfit_path:
            try:
                outfit_available = Path(outfit_path).is_file()
            except (OSError, ValueError):
                outfit_available = False

        current_user = user if isinstance(user, dict) else {}
        user_id = _single_line(current_user.get("user_id"), 80)
        role = _single_line(current_user.get("relationship_role"), 24)
        role_getter = getattr(self, "_private_user_role", None)
        if callable(role_getter) and current_user:
            try:
                role = _single_line(role_getter(current_user, user_id), 24)
            except TypeError:
                role = _single_line(role_getter(current_user), 24)
            except Exception:
                pass
        role_label = role
        role_labeler = getattr(self, "_private_user_role_label", None)
        if role and callable(role_labeler):
            try:
                role_label = _single_line(role_labeler(role), 32) or role
            except Exception:
                role_label = role

        dialogue_outfit_override: dict[str, Any] = {}
        if include_dialogue_outfit and role == "owner":
            override_getter = getattr(self, "_current_dialogue_outfit_override", None)
            if callable(override_getter):
                try:
                    dialogue_outfit_override = override_getter(user_id=user_id)
                except TypeError:
                    try:
                        dialogue_outfit_override = override_getter()
                    except Exception:
                        dialogue_outfit_override = {}
                except Exception:
                    dialogue_outfit_override = {}
        dialogue_outfit_instruction = _single_line(
            dialogue_outfit_override.get("instruction"),
            180,
        )
        if dialogue_outfit_instruction:
            # A dialogue outfit has no reusable image by itself. Keep the daily
            # image as a baseline only, so downstream selectors cannot mistake it
            # for the currently worn outfit.
            outfit_available = False
            outfit_path = ""

        # Location-specific warnings are private environment context. Keep
        # them out of secondary-user snapshots even when the shared cache is
        # present for the primary user.
        if current_user and role != "owner":
            weather_alerts = {
                "enabled": False,
                "stale": False,
                "fetched_ts": 0,
                "error": "",
                "count": 0,
                "highest_level": "",
                "alerts": [],
            }

        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(
            current_item.get("mood") or state.get("mood_bias"),
            32,
        ) or "平稳"
        topic = _single_line(current_user.get("planned_proactive_topic"), 80)
        motive = _single_line(current_user.get("planned_proactive_motive"), 140)
        visual_parts = [
            schedule_text,
            coarse_location or location,
            weather,
            (
                f"对话最新服装：{dialogue_outfit_instruction}"
                if dialogue_outfit_instruction
                else self._scene_context_outfit_description(outfit_profile)
            ),
            topic,
        ]
        visual_anchor = _single_line("；".join(part for part in visual_parts if part), 620)
        visual_signal_count = sum(bool(part) for part in visual_parts)

        return {
            "version": SCENE_CONTEXT_VERSION,
            "captured_at": captured.isoformat(timespec="seconds"),
            "captured_ts": captured.timestamp() if captured.tzinfo else _now_ts(),
            "date": today,
            "time": captured.strftime("%H:%M"),
            "daypart": self._scene_context_daypart(captured.hour),
            "state": {
                "date": _single_line(state.get("date"), 20),
                "energy": energy,
                "energy_label": self._scene_context_energy_label(energy),
                "mood": mood,
                "conditions": self._scene_context_condition_labels(state),
            },
            "schedule": {
                "date": _single_line(plan.get("date"), 20),
                "is_current_date": _single_line(plan.get("date"), 20) in {"", today},
                "active": bool(current_item),
                "status": runtime_status,
                "time": _single_line(current_item.get("time"), 12),
                "end": _single_line(current_item.get("end"), 12),
                "activity": _single_line(current_item.get("activity"), 160),
                "mood": _single_line(current_item.get("mood"), 32),
                "message_seed": _single_line(current_item.get("message_seed"), 160),
                "text": schedule_text,
                "history": schedule_history,
            },
            "location": {
                "raw": location,
                "coarse": coarse_location,
                "text": coarse_location or location,
                "source": location_source,
                "confidence": state.get("location_confidence") if location_source == "detail_model" else None,
                "category": scene_category,
                "category_label": scene_category_label,
            },
            "weather": {
                "text": weather,
                "source": _single_line(weather_data.get("source"), 60),
            },
            "weather_alerts": weather_alerts,
            "outfit": {
                "date": _single_line(outfit_item.get("date"), 20),
                "available": outfit_available,
                "reference_path": outfit_path if outfit_available else "",
                "source": "dialogue_override" if dialogue_outfit_instruction else "daily_baseline",
                "dialogue_instruction": dialogue_outfit_instruction,
                "description": (
                    f"对话最新服装：{dialogue_outfit_instruction}"
                    if dialogue_outfit_instruction
                    else self._scene_context_outfit_description(outfit_profile)
                ),
                "profile": {
                    str(key): _single_line(value, 160)
                    for key, value in outfit_profile.items()
                    if _single_line(value, 160)
                },
            },
            "relationship": {
                "user_id": user_id,
                "name": _single_line(
                    current_user.get("nickname")
                    or current_user.get("display_name"),
                    60,
                ),
                "role": role,
                "role_label": role_label,
                "style": _single_line(current_user.get("style"), 40),
            },
            "visual": {
                "anchor": visual_anchor,
                "signal_count": visual_signal_count,
                "shareable": visual_signal_count >= 2,
                "topic": topic,
                "motive": motive,
            },
        }

    def _format_companion_scene_snapshot(
        self,
        snapshot: dict[str, Any] | None = None,
        *,
        user: dict[str, Any] | None = None,
        purpose: str = "prompt",
    ) -> str:
        scene = snapshot if isinstance(snapshot, dict) else self._build_companion_scene_snapshot(user)
        state = scene.get("state") if isinstance(scene.get("state"), dict) else {}
        schedule = scene.get("schedule") if isinstance(scene.get("schedule"), dict) else {}
        location = scene.get("location") if isinstance(scene.get("location"), dict) else {}
        weather = scene.get("weather") if isinstance(scene.get("weather"), dict) else {}
        weather_alerts = scene.get("weather_alerts") if isinstance(scene.get("weather_alerts"), dict) else {}
        outfit = scene.get("outfit") if isinstance(scene.get("outfit"), dict) else {}
        relationship = scene.get("relationship") if isinstance(scene.get("relationship"), dict) else {}
        visual = scene.get("visual") if isinstance(scene.get("visual"), dict) else {}

        parts = [
            f"时间：{_single_line(scene.get('date'), 20)} {_single_line(scene.get('time'), 12)}（{_single_line(scene.get('daypart'), 12)}）",
            f"状态：精力{_single_line(state.get('energy_label'), 16)}，情绪{_single_line(state.get('mood'), 32)}",
        ]
        conditions = state.get("conditions") if isinstance(state.get("conditions"), list) else []
        if conditions and purpose not in {"image_search"}:
            parts.append(f"状态余波：{'、'.join(_single_line(item, 32) for item in conditions[:4] if _single_line(item, 32))}")
        if _single_line(schedule.get("text"), 320):
            parts.append(f"当前日程：{_single_line(schedule.get('text'), 320)}")
        if _single_line(location.get("text"), 80):
            parts.append(f"当前位置：{_single_line(location.get('text'), 80)}")
        if _single_line(location.get("category_label"), 24):
            parts.append(f"当前场景：{_single_line(location.get('category_label'), 24)}")
        if _single_line(weather.get("text"), 220):
            parts.append(f"天气背景：{_single_line(weather.get('text'), 220)}")
        alert_items = weather_alerts.get("alerts") if isinstance(weather_alerts.get("alerts"), list) else []
        if alert_items:
            alert_text = "；".join(
                " ".join(
                    part
                    for part in (
                        _single_line(item.get("level"), 16),
                        _single_line(item.get("event"), 36),
                        _single_line(item.get("headline"), 120),
                    )
                    if part
                )
                for item in alert_items[:3]
                if isinstance(item, dict)
            )
            if alert_text:
                if purpose in {"image_search", "selfie_scene", "proactive_photo"}:
                    parts.append(
                        f"安全环境提示：{alert_text}。优先选择符合防护建议的自然场景，不生成警报牌、文字水印或播报界面。"
                    )
                else:
                    parts.append(
                        f"气象预警背景：{alert_text}。只用于判断安全、室内外和语气，不要编造警报界面、播报口吻或未给出的影响。"
                    )
        dialogue_outfit = _single_line(outfit.get("dialogue_instruction"), 180)
        if dialogue_outfit:
            parts.append(
                f"对话最新服装：{dialogue_outfit}；在用户再次明确换装前，不恢复今日基础穿搭或人格默认衣着"
            )
        elif bool(outfit.get("available")):
            description = _single_line(outfit.get("description"), 360)
            parts.append(f"当天基础穿搭：{description or '已有可复用的当天基础穿搭参考图'}")
        if purpose not in {"selfie_scene", "image_search"}:
            relation_name = _single_line(relationship.get("name"), 60)
            relation_role = _single_line(relationship.get("role_label"), 32)
            if relation_name or relation_role:
                parts.append(
                    f"分享对象：{relation_name or '当前用户'}"
                    + (f"（{relation_role}）" if relation_role else "")
                )
        if _single_line(visual.get("topic"), 80):
            parts.append(f"视觉话题：{_single_line(visual.get('topic'), 80)}")
        return _single_line("；".join(part for part in parts if part), 1200)
