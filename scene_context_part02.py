# -*- coding: utf-8 -*-
"""SceneContextPart02Mixin。

由 tools/split_mixin_domain.py 从 scene_context.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 394 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 SceneContextMixin）。
"""
from __future__ import annotations

import re
from .conversation_prompt_section import PromptRenderMode, render_prompt_sections
from .helpers import _now_ts, _path_text, _safe_int, _single_line
from .scene_context_shared import SCENE_CONTEXT_VERSION, _scene_temperature_facts, infer_companion_scene_category
from datetime import datetime
from pathlib import Path
from typing import Any



class SceneContextPart02Mixin:
    """SceneContextPart02Mixin（从 SceneContextMixin 拆出）。"""


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
        interruption_getter = getattr(self, "_agenda_current_interruption_context", None)
        interruption_context = None
        if callable(interruption_getter):
            try:
                interruption_context = interruption_getter(now=captured)
            except Exception:
                interruption_context = None

        calendar_snapshot: dict[str, Any] = {}
        calendar_getter = getattr(self, "_agenda_calendar_snapshot", None)
        if callable(calendar_getter):
            try:
                candidate = calendar_getter(captured.date().isoformat(), now=captured)
                if isinstance(candidate, dict):
                    calendar_snapshot = candidate
            except Exception:
                calendar_snapshot = {}
        calendar_timeline: dict[str, Any] = {}
        timeline_getter = getattr(self, "_agenda_calendar_timeline", None)
        if callable(timeline_getter):
            try:
                candidate = timeline_getter(captured.date().isoformat(), now=captured, history_days=2, horizon_days=7)
                if isinstance(candidate, dict):
                    calendar_timeline = candidate
            except Exception:
                calendar_timeline = {}
        calendar_candidates: list[dict[str, Any]] = []
        candidates_getter = getattr(self, "_agenda_calendar_candidates_store", None)
        if callable(candidates_getter):
            try:
                raw_candidates = candidates_getter()
                if isinstance(raw_candidates, list):
                    calendar_candidates = [
                        {
                            "candidate_id": _single_line(item.get("candidate_id") or item.get("calendar_id"), 160),
                            "title": _single_line(item.get("title"), 100),
                            "date": _single_line(item.get("start_date") or item.get("date"), 24),
                            "end_date": _single_line(item.get("end_date"), 24),
                            "confidence": item.get("confidence"),
                            "source_excerpt": _single_line(item.get("source_excerpt"), 220),
                            "lifecycle_status": _single_line(item.get("lifecycle_status") or "pending_confirmation", 32),
                            "source_message_at": _single_line(item.get("source_message_at"), 32),
                        }
                        for item in raw_candidates
                        if isinstance(item, dict)
                        and _single_line(item.get("title"), 100)
                        and str(item.get("lifecycle_state") or item.get("lifecycle") or "candidate") not in {"confirmed", "active", "completed", "cancelled", "expired"}
                    ][:8]
            except Exception:
                calendar_candidates = []
        # Keep the full daily calendar visible. ``effective_events`` is useful
        # for execution, but hiding overridden records makes the scene lose
        # the stable phase/rhythm context that the timeline is meant to carry.
        calendar_events = calendar_snapshot.get("events", calendar_snapshot.get("effective_events", []))
        calendar_events = [
            {
                "title": _single_line(item.get("title"), 100),
                "kind": _single_line(item.get("kind") or item.get("type"), 24),
                "occurrence_date": _single_line(item.get("occurrence_date") or item.get("date") or item.get("start_date"), 24),
                "end_date": _single_line(item.get("end_date"), 24),
                "calendar_effective": item.get("calendar_effective", True),
                "overridden_by": _single_line(item.get("overridden_by"), 100),
            }
            for item in calendar_events
            if isinstance(item, dict) and _single_line(item.get("title"), 100)
        ][:12] if isinstance(calendar_events, list) else []

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
                location_source = _single_line(state.get("location_source"), 40) or "detail_schedule"
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
        temperature_facts = _scene_temperature_facts(weather_data, weather)
        weather_alerts = self._scene_context_weather_alert_snapshot(data)

        sleep_runtime = state.get("sleep_runtime") if isinstance(state.get("sleep_runtime"), dict) else {}
        sleep_phase = _single_line(sleep_runtime.get("phase"), 40)
        sleep_label = _single_line(sleep_runtime.get("label"), 40)
        if not sleep_phase:
            sleep_text = f"{schedule_text} {coarse_location or location}".lower()
            if re.search(r"准备睡|睡前|入睡|睡觉|bedtime|going to bed", sleep_text, flags=re.I):
                sleep_phase, sleep_label = "falling_asleep", "准备入睡"
            elif scene_category == "home" and (captured.hour >= 22 or captured.hour < 5):
                sleep_phase, sleep_label = "preparing_for_sleep", "夜间居家"
            else:
                sleep_phase, sleep_label = "awake", "清醒"

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

        realtime_extension = self._scene_context_realtime_extension(user_id, role)

        mobile_context: dict[str, Any] = {}
        mobile_context_getter = getattr(self, "_reality_mobile_context", None)
        if user_id and callable(mobile_context_getter):
            try:
                candidate = mobile_context_getter(user_id)
                if isinstance(candidate, dict):
                    mobile_context = candidate
            except Exception:
                mobile_context = {}
        mobile_location = mobile_context.get("location") if isinstance(mobile_context.get("location"), dict) else {}
        mobile_telemetry = mobile_context.get("telemetry") if isinstance(mobile_context.get("telemetry"), dict) else {}
        cognitive_map: dict[str, Any] = {}
        map_observer = getattr(self, "_observe_mobile_place_context", None)
        if user_id and callable(map_observer):
            try:
                candidate = map_observer(user_id, mobile_location)
                if isinstance(candidate, dict):
                    cognitive_map = candidate
            except Exception:
                cognitive_map = {}

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
        afterglow_getter = getattr(self, "_game_afterglow_for_user", None)
        public_view = getattr(self, "_game_afterglow_public_view", None)
        raw_afterglow = afterglow_getter(current_user) if callable(afterglow_getter) else current_user.get("game_afterglow")
        game_afterglow = public_view(raw_afterglow) if callable(public_view) else (raw_afterglow if isinstance(raw_afterglow, dict) else {})

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
                "interruption": interruption_context if isinstance(interruption_context, dict) else {},
                "overridden_by_realtime_activity": bool(realtime_extension.get("activity")),
            },
            "calendar": {
                "date": _single_line(calendar_snapshot.get("date"), 20) or today,
                "events": calendar_events,
                "pending_candidates": calendar_candidates,
                "timeline": {
                    "current_phase": [item for item in (calendar_timeline.get("current_phase", []) if isinstance(calendar_timeline.get("current_phase"), list) else []) if isinstance(item, dict)][:6],
                    "rhythms": [item for item in (calendar_timeline.get("rhythms", []) if isinstance(calendar_timeline.get("rhythms"), list) else []) if isinstance(item, dict)][:6],
                    "recent_changes": [item for item in (calendar_timeline.get("recent_changes", []) if isinstance(calendar_timeline.get("recent_changes"), list) else []) if isinstance(item, dict)][:6],
                    "upcoming": [item for item in (calendar_timeline.get("upcoming", []) if isinstance(calendar_timeline.get("upcoming"), list) else []) if isinstance(item, dict)][:8],
                    "transitions": [item for item in (calendar_timeline.get("transitions", []) if isinstance(calendar_timeline.get("transitions"), list) else []) if isinstance(item, dict)][:6],
                    "uncertainties": [item for item in (calendar_timeline.get("uncertainties", []) if isinstance(calendar_timeline.get("uncertainties"), list) else []) if isinstance(item, dict)][:6],
                    "continuity": calendar_timeline.get("continuity") if isinstance(calendar_timeline.get("continuity"), dict) else {},
                },
                "conflicts": [
                    item for item in (calendar_snapshot.get("conflicts", []) if isinstance(calendar_snapshot.get("conflicts"), list) else [])
                    if isinstance(item, dict)
                ][:8],
                "applied_exceptions": [
                    _single_line(item, 100)
                    for item in (calendar_snapshot.get("applied_exceptions", []) if isinstance(calendar_snapshot.get("applied_exceptions"), list) else [])
                    if _single_line(item, 100)
                ][:8],
            },
            "realtime": realtime_extension,
            "location": {
                "raw": location,
                "coarse": coarse_location,
                "text": coarse_location or location,
                "source": location_source,
                "confidence": state.get("location_confidence") if location_source == "detail_model" else None,
                "category": scene_category,
                "category_label": scene_category_label,
                "mobile": mobile_location,
                "telemetry": mobile_telemetry,
                "cognitive_map": cognitive_map,
            },
            "weather": {
                "text": weather,
                "source": _single_line(weather_data.get("source"), 60),
                **temperature_facts,
            },
            "sleep": {
                "phase": sleep_phase,
                "label": sleep_label,
                "source": _single_line(sleep_runtime.get("source"), 40) or ("runtime" if sleep_runtime else "scene_inference"),
                "last_event": _single_line(sleep_runtime.get("last_event"), 120),
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
            "game_afterglow": game_afterglow,
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
        return render_prompt_sections(
            [
                self._format_companion_scene_snapshot_prompt_section(
                    snapshot,
                    user=user,
                    purpose=purpose,
                )
            ],
            mode=PromptRenderMode.BODY_ONLY,
        )
