# -*- coding: utf-8 -*-
"""日常起居与消息记录面板域。

由 tools/split_mixin_domain.py 从 page_api_summary_panel.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 495 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiSummaryPanelMixin）。
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote



class PrivateCompanionPageApiSummaryPanelDailyMixin:
    """日常起居与消息记录面板域（从 PrivateCompanionPageApiSummaryPanelMixin 拆出）。"""


    def _daily_outfit_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        item = data.get("daily_outfit_photo") if isinstance(data.get("daily_outfit_photo"), dict) else {}
        path = self._single_line(item.get("path"), 300)
        exists = False
        if path:
            try:
                exists = Path(path).exists() and Path(path).is_file()
            except Exception:
                exists = False
        date_key = self._single_line(item.get("date"), 20)
        image_query = f"?date={quote(date_key)}&ts={self._single_line(item.get('generated_at'), 40)}" if exists else ""
        return {
            "enabled": bool(getattr(self.plugin, "enable_daily_outfit_photo", False)),
            "date": date_key,
            "available": bool(exists),
            "path": path if exists else "",
            "image_url": f"/daily_outfit/image{image_query}" if exists else "",
            "image_data_url": f"/daily_outfit/image_data{image_query}" if exists else "",
            "backend": self._single_line(item.get("backend"), 80),
            "error": self._single_line(item.get("error"), 220),
            "generated_at": self.plugin._format_timestamp_elapsed(item.get("generated_at", 0)) if item else "",
            "retry_count": int(item.get("retry_count", 0) or 0),
            "retry_max": 5,
        }

    def _passive_no_reply_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("passive_no_reply_records")
        if not isinstance(raw, dict):
            return {"total": 0, "items": []}
        items: list[dict[str, Any]] = []
        now = time.time()
        max_age_seconds = 2 * 60 * 60
        hidden_stale = 0
        hidden_obsolete = 0
        for item in raw.get("items", []):
            if not isinstance(item, dict):
                continue
            last_ts = self._float(item.get("last_ts"))
            if self._passive_no_reply_item_is_obsolete_fixed_error(item):
                hidden_obsolete += 1
                continue
            if last_ts > 0 and now - last_ts > max_age_seconds:
                hidden_stale += 1
                continue
            samples = []
            for sample in item.get("samples", []) if isinstance(item.get("samples"), list) else []:
                if not isinstance(sample, dict):
                    continue
                ts = self._float(sample.get("ts"))
                samples.append(
                    {
                        "time": self.plugin._format_timestamp_elapsed(ts) if ts else self._single_line(sample.get("time"), 40),
                        "session": self._single_line(sample.get("session"), 120),
                        "sender_id": self._single_line(sample.get("sender_id"), 80),
                        "inbound": self._single_line(sample.get("inbound"), 120),
                        "detail": self._single_line(sample.get("detail"), 160),
                        "reply_preview": self._single_line(sample.get("reply_preview"), 140),
                        "ts": ts,
                    }
                )
            items.append(
                {
                    "key": self._single_line(item.get("key"), 32),
                    "level": self._single_line(item.get("level"), 12) or "info",
                    "source": self._single_line(item.get("source"), 40) or "被动未回复",
                    "reason": self._single_line(item.get("reason"), 120) or "未说明原因",
                    "count": self._int(item.get("count")),
                    "first_ts": self._float(item.get("first_ts")),
                    "last_ts": last_ts,
                    "last_time": self.plugin._format_timestamp_elapsed(last_ts) if last_ts else "",
                    "last_session": self._single_line(item.get("last_session"), 120),
                    "last_sender_id": self._single_line(item.get("last_sender_id"), 80),
                    "last_inbound": self._single_line(item.get("last_inbound"), 120),
                    "last_detail": self._single_line(item.get("last_detail"), 160),
                    "last_action": self._single_line(item.get("last_action"), 120),
                    "last_reply_preview": self._single_line(item.get("last_reply_preview"), 140),
                    "samples": samples[:5],
                }
            )
        items.sort(key=lambda item: self._float(item.get("last_ts")), reverse=True)
        total = self._int(raw.get("total")) or sum(self._int(item.get("count")) for item in items)
        return {
            "total": total,
            "last_ts": self._float(raw.get("last_ts")),
            "items": items[:80],
            "hidden_stale": hidden_stale,
            "hidden_obsolete": hidden_obsolete,
            "max_age_seconds": max_age_seconds,
        }

    def _food_menu_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        state = data.get("food_menu") if isinstance(data.get("food_menu"), dict) else {}
        raw_items = state.get("items") if isinstance(state.get("items"), list) else []
        type_label = getattr(self.plugin, "_food_menu_type_label", None)
        time_label = getattr(self.plugin, "_food_menu_time_label", None)

        def _list(value: Any, *, limit: int = 12, item_limit: int = 24) -> list[str]:
            raw = value if isinstance(value, list) else re.split(r"[,，、\n/|]+", str(value or ""))
            items: list[str] = []
            for part in raw:
                item = self._single_line(part, item_limit)
                if item and item not in items:
                    items.append(item)
            return items[:limit]

        items: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            name = self._single_line(raw.get("name"), 40)
            if not name:
                continue
            item_type = self._single_line(raw.get("type"), 20) or "dish"
            counts[item_type] = counts.get(item_type, 0) + 1
            times = _list(raw.get("times"), limit=5, item_limit=16)
            items.append(
                {
                    "id": self._single_line(raw.get("id"), 48),
                    "name": name,
                    "type": item_type,
                    "type_label": type_label(item_type) if callable(type_label) else item_type,
                    "category": self._single_line(raw.get("category"), 24),
                    "tags": _list(raw.get("tags"), limit=10, item_limit=16),
                    "times": times,
                    "time_labels": [time_label(value) if callable(time_label) else value for value in times],
                    "avoid": _list(raw.get("avoid"), limit=8, item_limit=24),
                    "aliases": _list(raw.get("aliases"), limit=10, item_limit=24),
                    "note": self._single_line(raw.get("note"), 100),
                    "favorite": bool(raw.get("favorite")),
                    "hidden": bool(raw.get("hidden")),
                    "use_count": self._int(raw.get("use_count")),
                    "last_used": self.plugin._format_timestamp_elapsed(raw.get("last_used_at", 0)),
                    "last_recommended": self.plugin._format_timestamp_elapsed(raw.get("last_recommended_at", 0)),
                    "updated": self.plugin._format_timestamp_elapsed(raw.get("updated_ts", 0)),
                }
            )
        items.sort(key=lambda item: (bool(item.get("hidden")), not bool(item.get("favorite")), item.get("type_label", ""), item.get("name", "")))
        return {
            "items": items[:160],
            "total": len(items),
            "visible_count": sum(1 for item in items if not item.get("hidden")),
            "favorite_count": sum(1 for item in items if item.get("favorite")),
            "hidden_count": sum(1 for item in items if item.get("hidden")),
            "counts": counts,
            "updated": self.plugin._format_timestamp_elapsed(state.get("updated_ts", 0)),
        }

    def _message_debounce_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = data.get("smart_message_debounce")
        if not isinstance(raw, dict):
            raw = {}
        logs_raw = raw.get("recent_logs") if isinstance(raw.get("recent_logs"), list) else []
        examples_raw = raw.get("examples") if isinstance(raw.get("examples"), list) else []
        logs: list[dict[str, Any]] = []
        for item in logs_raw[-30:][::-1]:
            if not isinstance(item, dict):
                continue
            logs.append(
                {
                    "ts": self._float(item.get("ts")),
                    "time": self.plugin._format_timestamp_elapsed(self._float(item.get("ts"))) if self._float(item.get("ts")) else "",
                    "scope": self._single_line(item.get("scope"), 80),
                    "sender_id": self._single_line(item.get("sender_id"), 40),
                    "chat": self._single_line(item.get("chat"), 20),
                    "text": self._single_line(item.get("text"), 180),
                    "decision": self._single_line(item.get("decision"), 40),
                    "confidence": self._float(item.get("confidence")),
                    "reason": self._single_line(item.get("reason"), 120),
                    "wait_seconds": self._float(item.get("wait_seconds")),
                    "outcome": self._single_line(item.get("outcome"), 40),
                    "note": self._single_line(item.get("note"), 160),
                    "source": self._single_line(item.get("source"), 40),
                    "raw": self._single_line(item.get("raw"), 180),
                    "message_count": self._int(item.get("message_count")),
                }
            )
        examples: list[dict[str, Any]] = []
        for item in examples_raw[-10:][::-1]:
            if not isinstance(item, dict):
                continue
            messages = item.get("messages") if isinstance(item.get("messages"), list) else []
            examples.append(
                {
                    "time": self.plugin._format_timestamp_elapsed(self._float(item.get("ts"))) if self._float(item.get("ts")) else "",
                    "kind": self._single_line(item.get("kind"), 40),
                    "scope": self._single_line(item.get("scope"), 80),
                    "sender_id": self._single_line(item.get("sender_id"), 40),
                    "messages": [self._single_line(message, 120) for message in messages[:4]],
                    "previous_decision": self._single_line(item.get("previous_decision"), 40),
                    "note": self._single_line(item.get("note"), 120),
                }
            )
        return {
            "enabled": bool(getattr(self.plugin, "enable_message_debounce", False)),
            "smart_enabled": bool(getattr(self.plugin, "enable_smart_message_debounce", False)),
            "text_wait": self._float(getattr(self.plugin, "text_message_debounce_seconds", 0.0)),
            "max_wait": self._float(getattr(self.plugin, "text_message_debounce_max_wait_seconds", 0.0)),
            "max_merge": self._int(getattr(self.plugin, "message_debounce_max_merge_messages", 0)),
            "smart_wait": self._float(getattr(self.plugin, "smart_message_debounce_wait_seconds", 0.0)),
            "learning_window": self._float(getattr(self.plugin, "smart_message_debounce_learning_window_seconds", 0.0)),
            "provider_id": self._single_line(getattr(self.plugin, "smart_message_debounce_provider_id", ""), 160),
            "recent_logs": logs,
            "examples": examples,
        }

    def _daily_state_summary(self, state: Any) -> dict[str, Any]:
        if not isinstance(state, dict):
            return {}
        keys = ["date", "sleep", "dream", "health", "hunger", "body_cycle", "location", "weather", "mood_bias", "energy", "note"]
        summary = {key: state.get(key, "") for key in keys}
        cycle_runtime = state.get("cycle_runtime") if isinstance(state.get("cycle_runtime"), dict) else {}
        if cycle_runtime:
            discomfort = cycle_runtime.get("discomfort")
            summary["cycle_runtime"] = {
                "phase": self._single_line(cycle_runtime.get("phase"), 24),
                "phase_name": self._single_line(cycle_runtime.get("phase_name"), 24),
                "day_in_phase": self._int(cycle_runtime.get("day_in_phase")),
                "phase_days": self._int(cycle_runtime.get("phase_days")),
                "cycle_day": self._int(cycle_runtime.get("cycle_day")),
                "cycle_days": self._int(cycle_runtime.get("cycle_days")),
                "mood": self._single_line(cycle_runtime.get("mood"), 20),
                "energy_delta": self._int(cycle_runtime.get("energy_delta")),
                "next_phase_name": self._single_line(cycle_runtime.get("next_phase_name"), 24),
                "discomfort": [
                    {
                        "type": self._single_line(item.get("type"), 12),
                        "label": self._single_line(item.get("label"), 80),
                        "mood": self._single_line(item.get("mood"), 12),
                    }
                    for item in discomfort[:4]
                    if isinstance(item, dict)
                ]
                if isinstance(discomfort, list)
                else [],
            }
        location_getter = getattr(self.plugin, "_current_location_state_text", None)
        if callable(location_getter):
            try:
                effective_location = self._single_line(location_getter(state), 60)
            except Exception:
                effective_location = ""
            if effective_location:
                summary["location"] = effective_location
        summary["location_source"] = self._single_line(state.get("location_source"), 40)
        summary["location_confidence"] = self._float(state.get("location_confidence"), 0.0)
        runtime = state.get("sleep_runtime") if isinstance(state.get("sleep_runtime"), dict) else {}
        if runtime:
            summary["sleep_phase"] = self._single_line(runtime.get("label") or runtime.get("phase"), 40)
            summary["sleep_runtime"] = {
                "phase": self._single_line(runtime.get("phase"), 40),
                "label": self._single_line(runtime.get("label") or runtime.get("phase"), 40),
                "last_event": self._single_line(runtime.get("last_event"), 120),
                "source": self._single_line(runtime.get("source"), 40),
                "woken_count": self._int(runtime.get("woken_count")),
                "updated_at": self.plugin._format_timestamp_elapsed(runtime.get("updated_at", 0)),
            }
            delay_until = self._float(runtime.get("sleep_delay_until_ts"), 0)
            if delay_until > time.time():
                summary["sleep_delay_override"] = {
                    "active": True,
                    "until": self._single_line(runtime.get("sleep_delay_until_text"), 40)
                    or self.plugin._environment_fromtimestamp(delay_until).strftime("%m-%d %H:%M"),
                    "reason": self._single_line(runtime.get("sleep_delay_reason"), 120),
                    "user_text": self._single_line(runtime.get("sleep_delay_user_text"), 120),
                    "explicit_time": bool(runtime.get("sleep_delay_explicit_time")),
                }
        return summary

    def _life_observation_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        dream = data.get("daily_dream") if isinstance(data.get("daily_dream"), dict) else {}
        diaries = data.get("bot_diaries") if isinstance(data.get("bot_diaries"), list) else []
        fragments = data.get("dream_fragments") if isinstance(data.get("dream_fragments"), list) else []
        plan = data.get("daily_plan") if isinstance(data.get("daily_plan"), dict) else {}
        story = data.get("daily_story_plan") if isinstance(data.get("daily_story_plan"), dict) else {}
        current_item = {}
        current_lifecycle = ""
        current_evidence_lifecycle = ""
        current_clock_status = ""
        try:
            current_getter = getattr(self.plugin, "_agenda_current_context_item", None)
            picked = (
                current_getter()
                if callable(current_getter)
                else self.plugin._get_current_plan_item(plan)
            )
            if not isinstance(picked, dict):
                display_getter = getattr(self.plugin, "_get_clock_plan_item_for_display", None)
                picked = display_getter(plan) if callable(display_getter) else None
            current_item = picked if isinstance(picked, dict) else {}
            items = plan.get("items") if isinstance(plan.get("items"), list) else []
            current_index = next((index for index, item in enumerate(items) if item is picked), -1)
            if current_item:
                current_evidence_lifecycle = self.plugin._plan_item_runtime_status(
                    plan,
                    current_item,
                    current_index,
                )
                display_status = getattr(self.plugin, "_plan_item_display_status", None)
                current_clock_status = (
                    display_status(plan, current_item, current_index)
                    if callable(display_status)
                    else current_evidence_lifecycle
                )
                # Preserve the legacy display-oriented field for older page
                # consumers while exposing the evidence/clock split to new UI.
                current_lifecycle = current_clock_status
        except Exception:
            current_item = {}
            current_lifecycle = ""
            current_evidence_lifecycle = ""
            current_clock_status = ""

        return {
            "dream": {
                "date": self._single_line(dream.get("date"), 24),
                "label": self._single_line(dream.get("label"), 120),
                "dream_type": self._single_line(dream.get("dream_type"), 40),
                "content": self._single_line(dream.get("content"), 1000),
                "afterglow": self._single_line(dream.get("afterglow"), 220),
                "mood": self._single_line(dream.get("mood"), 30),
                "energy_delta": self._int(dream.get("energy_delta")),
                "duration_hours": self._int(dream.get("duration_hours")),
                "generated_at": self._single_line(dream.get("generated_at"), 24),
                "factors": [self._single_line(item, 40) for item in dream.get("factors", [])[:8] if self._single_line(item, 40)]
                if isinstance(dream.get("factors"), list)
                else [],
            },
            "diaries": [
                {
                    "date": self._single_line(item.get("date"), 24),
                    "summary": self.plugin._polish_diary_text(item.get("summary"), field="summary"),
                    "body": self.plugin._polish_diary_text(item.get("body"), field="body"),
                    "share_seed": self.plugin._polish_diary_text(item.get("share_seed"), field="share"),
                    "tags": [self._single_line(tag, 20) for tag in item.get("tags", [])[:6] if self._single_line(tag, 20)]
                    if isinstance(item.get("tags"), list)
                    else [],
                    "generated_at": self._single_line(item.get("generated_at"), 24),
                }
                for item in diaries[-4:]
                if isinstance(item, dict)
            ],
            "dream_fragments": self._limited_dream_fragments(fragments),
            "current_plan": {
                "time": self._single_line(current_item.get("time"), 12),
                "end": self._single_line(current_item.get("end"), 12),
                "lifecycle": current_lifecycle,
                "evidence_lifecycle": current_evidence_lifecycle,
                "clock_status": current_clock_status,
                "activity": self._single_line(current_item.get("activity"), 600),
                "mood": self._single_line(current_item.get("mood"), 40),
                "message_seed": self._single_line(current_item.get("message_seed"), 500),
            },
            "story": {
                "date": self._single_line(story.get("date"), 24),
                "today_events": self._limited_story_items(story.get("today_events"), 4),
                "proactive_events": self._limited_story_items(story.get("proactive_events"), 4),
            },
        }

    def _daily_timeline_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        plan = data.get("daily_plan") if isinstance(data.get("daily_plan"), dict) else {}
        raw_enhanced = data.get("detail_enhanced_segments") if isinstance(data.get("detail_enhanced_segments"), dict) else {}
        enhanced = self.plugin._detail_enhanced_segments_for_plan_date(
            plan.get("date"),
            raw_enhanced,
            detail_day=data.get("detail_enhanced_day"),
        )
        story = data.get("daily_story_plan") if isinstance(data.get("daily_story_plan"), dict) else {}
        adjustments = data.get("schedule_adjustments") if isinstance(data.get("schedule_adjustments"), list) else []
        presence = data.get("qq_presence_state") if isinstance(data.get("qq_presence_state"), dict) else {}

        segments: list[dict[str, Any]] = []
        seen_segment_keys: set[str] = set()
        plan_items = plan.get("items") if isinstance(plan.get("items"), list) else []
        normalized_starts = self.plugin._normalized_plan_item_starts(plan_items)
        for key, snapshot in enhanced.items():
            if not isinstance(snapshot, dict):
                continue
            segment = self._segment_from_key(str(key), plan, snapshot)
            seen_segment_keys.add(str(key))
            segment_item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
            evidence_lifecycle = self.plugin._plan_item_runtime_status(
                plan,
                segment_item,
                self._int(segment.get("index"), -1, -1),
            ) if segment_item else "planned"
            display_status = getattr(self.plugin, "_plan_item_display_status", None)
            clock_status = (
                display_status(plan, segment_item, self._int(segment.get("index"), -1, -1))
                if segment_item and callable(display_status)
                else self.plugin._plan_item_runtime_status(
                    plan,
                    segment_item,
                    self._int(segment.get("index"), -1, -1),
                ) if segment_item else "planned"
            )
            segments.append(
                {
                    "key": str(key),
                    "window": segment.get("window", str(key)),
                    "start": segment.get("start", 99999),
                    "end": segment.get("end", 99999),
                    # Keep lifecycle as the legacy display field for API
                    # compatibility; new clients should use the explicit
                    # evidence/clock split below.
                    "lifecycle": clock_status,
                    "evidence_lifecycle": evidence_lifecycle,
                    "clock_status": clock_status,
                    "activity": self._single_line(segment_item.get("activity"), 180),
                    "basis": self.plugin._normalize_schedule_basis(segment_item.get("basis"), default=["coarse_plan"]),
                    "confidence": self._float(segment_item.get("confidence"), 0.72, 0.0, 1.0),
                    "status": snapshot.get("status", ""),
                    "started_at": snapshot.get("started_at", ""),
                    "summary": snapshot.get("summary", ""),
                    "summary_basis": self.plugin._normalize_schedule_basis(snapshot.get("summary_basis"), default=["coarse_plan"]),
                    "summary_confidence": self._float(snapshot.get("summary_confidence"), 0.75, 0.0, 1.0),
                    "quality": snapshot.get("quality") if isinstance(snapshot.get("quality"), dict) else {},
                    "regeneration_error": self._single_line(snapshot.get("regeneration_error"), 180),
                    "error": snapshot.get("error", ""),
                    "retry_after": snapshot.get("retry_after", ""),
                    "state_variables": self._limited_state_variables(snapshot.get("state_variables")),
                    "presence_status": snapshot.get("presence_status") if isinstance(snapshot.get("presence_status"), dict) else {},
                    "interaction_updates": self._limited_interaction_updates(snapshot.get("interaction_updates")),
                    "today_events": self._timeline_story_items(
                        snapshot.get("today_events"),
                        5,
                        str(plan.get("date") or ""),
                        parent_start=self._int(segment.get("start"), -1, -1),
                        parent_end=self._int(segment.get("end"), -1, -1),
                    ),
                    "proactive_events": self._timeline_story_items(
                        snapshot.get("proactive_events"),
                        4,
                        str(plan.get("date") or ""),
                        parent_start=self._int(segment.get("start"), -1, -1),
                        parent_end=self._int(segment.get("end"), -1, -1),
                    ),
                }
            )
        for index, item in enumerate(plan_items):
            if not isinstance(item, dict):
                continue
            start_text = self._single_line(item.get("time"), 8)
            start = normalized_starts[index] if index < len(normalized_starts) else None
            if start is None:
                continue
            key = f"{plan.get('date')}:{index}:{start_text}"
            if key in seen_segment_keys:
                continue
            next_start = None
            for next_item in plan_items[index + 1 :]:
                if isinstance(next_item, dict):
                    next_start = self.plugin._parse_hhmm_to_minutes(next_item.get("time"))
                    if next_start is not None:
                        break
            end = self.plugin._plan_item_end_minutes(start, item, next_start=next_start)
            evidence_lifecycle = self.plugin._plan_item_runtime_status(plan, item, index)
            clock_status = (
                self.plugin._plan_item_display_status(plan, item, index)
                if callable(getattr(self.plugin, "_plan_item_display_status", None))
                else evidence_lifecycle
            )
            segments.append(
                {
                    "key": key,
                    "window": f"{self.plugin._minutes_to_hhmm(start)}-{self.plugin._minutes_to_hhmm(end)}",
                    "start": start,
                    "end": end,
                    "lifecycle": clock_status,
                    "evidence_lifecycle": evidence_lifecycle,
                    "clock_status": clock_status,
                    "activity": self._single_line(item.get("activity"), 180),
                    "basis": self.plugin._normalize_schedule_basis(item.get("basis"), default=["coarse_plan"]),
                    "confidence": self._float(item.get("confidence"), 0.72, 0.0, 1.0),
                    "status": "",
                    "summary": self._single_line(item.get("activity"), 180),
                    "summary_basis": self.plugin._normalize_schedule_basis(item.get("basis"), default=["coarse_plan"]),
                    "summary_confidence": self._float(item.get("confidence"), 0.72, 0.0, 1.0),
                    "quality": {},
                    "state_variables": [],
                    "presence_status": {},
                    "interaction_updates": [],
                    "today_events": [],
                    "proactive_events": [],
                }
            )
        segments.sort(key=lambda item: item.get("start", 99999))

        return {
            "plan_date": plan.get("date", ""),
            "story_date": story.get("date", ""),
            "detail_day": data.get("detail_enhanced_day", ""),
            "segment_count": len(segments),
            "plan_quality": plan.get("quality") if isinstance(plan.get("quality"), dict) else {},
            "segments": segments,
            "story_today_events": self._limited_story_items(story.get("today_events"), 48),
            "story_proactive_events": self._limited_story_items(story.get("proactive_events"), 32),
            "adjustments": self._limited_adjustments(adjustments),
            "qq_presence_state": presence,
        }
