# -*- coding: utf-8 -*-
"""DailyStateDetailPart01Mixin。

由 tools/split_mixin_domain.py 从 daily_state_detail.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 462 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateDetailMixin）。
"""
from __future__ import annotations

from .daily_state_detail_shared import _now_ts, logger
from .daily_state_detail_shared import Any
from .daily_state_detail_shared import _safe_float
from .daily_state_detail_shared import _safe_int
from .daily_state_detail_shared import _single_line
from .daily_state_detail_shared import generate_detail_enhancement
from .daily_state_detail_shared import pick_detail_segment
from .daily_state_detail_shared import re
from .daily_state_detail_shared import runtime_persona_setting
from .daily_state_detail_shared import uuid



class DailyStateDetailPart01Mixin:
    """DailyStateDetailPart01Mixin（从 DailyStateDetailMixin 拆出）。"""


    def _sync_detail_enhancement_day_locked(
        self,
        plan_date: Any,
        *,
        reset: bool = False,
    ) -> bool:
        """Keep live detail snapshots bound to the plan that owns them."""
        date_key = _single_line(plan_date, 16)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_key):
            return False
        enhanced = self.data.get("detail_enhanced_segments")
        changed = (
            bool(reset)
            or _single_line(self.data.get("detail_enhanced_day"), 16) != date_key
            or not isinstance(enhanced, dict)
        )
        if not changed:
            return False
        self.data["detail_enhanced_day"] = date_key
        self.data["detail_enhanced_segments"] = {}
        story = self.data.get("daily_story_plan")
        if bool(reset) or (
            isinstance(story, dict)
            and _single_line(story.get("date"), 16) not in {"", date_key}
        ):
            self.data["daily_story_plan"] = {}
        return True

    async def _ensure_detail_enhancement(self, force: bool = False) -> dict[str, Any] | None:
        if not runtime_persona_setting(self, "enable_detail_enhancement", False) and not force:
            return None
        async with self._data_lock:
            plan = dict(self.data.get("daily_plan", {}))
            plan_date = str(plan.get("date") or "")
            if not self._is_plan_date_active(plan_date):
                return None
            self._sync_detail_enhancement_day_locked(plan_date)
            state = dict(self.data.get("daily_state", {}))
            enhanced = self.data.setdefault("detail_enhanced_segments", {})
            if not isinstance(enhanced, dict):
                enhanced = {}
                self.data["detail_enhanced_segments"] = enhanced
            sanitized_existing = False
            if self._sanitize_detail_enhanced_segments_inplace(enhanced):
                sanitized_existing = True
            story_plan_existing = self.data.get("daily_story_plan", {})
            if isinstance(story_plan_existing, dict) and self._sanitize_story_plan_social_facts_inplace(story_plan_existing):
                sanitized_existing = True
            segments = self._collect_due_detail_segments(plan, enhanced, force=force)
            if not segments:
                if sanitized_existing:
                    self._save_data_sync(
                        sections={"detail_enhanced_segments", "daily_story_plan"}
                    )
                return None
            for segment in segments:
                generation_id = uuid.uuid4().hex
                segment["_generation_id"] = generation_id
                enhanced[segment["key"]] = {
                    "status": "generating",
                    "started_at": self._environment_now().strftime("%H:%M"),
                    "started_ts": _now_ts(),
                    "generation_id": generation_id,
                }
            self._save_data_sync(sections={"detail_enhanced_segments"})

        last_detail = None
        for segment in segments:
            try:
                detail = await self._generate_detail_enhancement(segment, plan, state)
                if not isinstance(detail.get("today_events"), list) or not detail.get("today_events"):
                    raise RuntimeError("日程细化结果为空或无法解析")
            except Exception as exc:
                now_ts = _now_ts()
                retry_after_ts = now_ts + 30 * 60
                failure_is_current = False
                async with self._data_lock:
                    failure_is_current = self._detail_generation_is_current(
                        segment,
                        str(segment.get("_generation_id") or ""),
                    )
                    if failure_is_current:
                        enhanced = self.data.setdefault("detail_enhanced_segments", {})
                        if not isinstance(enhanced, dict):
                            enhanced = {}
                            self.data["detail_enhanced_segments"] = enhanced
                        retry_after = self._environment_fromtimestamp(retry_after_ts).strftime("%H:%M")
                        enhanced[segment["key"]] = {
                            "status": "failed",
                            "updated_at": self._environment_now().strftime("%H:%M"),
                            "error": _single_line(exc, 180),
                            "retry_after": retry_after,
                            "retry_after_ts": retry_after_ts,
                            "summary": "这一段细化生成失败，稍后会自动重试。",
                            "today_events": [],
                            "proactive_events": [],
                            "state_variables": [],
                            "presence_status": {},
                            "interaction_updates": [],
                            "coverage_repair_done": bool(segment.get("_coverage_repair")),
                        }
                        self._save_data_sync(sections={"detail_enhanced_segments"})
                    else:
                        retry_after = ""
                if failure_is_current:
                    logger.warning(
                        "日程细化生成失败,已标记为可重试: segment=%s retry_after=%s error=%s",
                        _single_line(segment.get("key"), 80),
                        retry_after,
                        _single_line(exc, 180),
                    )
                else:
                    logger.info(
                        "日程细化失败结果已过期,不再回写: segment=%s error=%s",
                        _single_line(segment.get("key"), 80),
                        _single_line(exc, 180),
                    )
                if force and failure_is_current:
                    raise
                continue
            self._sanitize_detail_snapshot_for_segment_inplace(
                detail,
                segment,
                field=f"detail_enhanced_segments.{segment.get('key') or 'current'}",
            )
            async with self._data_lock:
                if not self._detail_generation_is_current(
                    segment,
                    str(segment.get("_generation_id") or ""),
                ):
                    continue
                story_plan = self.data.setdefault("daily_story_plan", {})
                if not isinstance(story_plan, dict) or story_plan.get("date") != plan_date:
                    story_plan = {
                        "date": plan_date,
                        "today_events": [],
                        "proactive_events": [],
                        "long_term_events": [],
                    }
                    self.data["daily_story_plan"] = story_plan
                self._merge_detail_enhancement(story_plan, detail)
                self._sanitize_story_plan_social_facts_inplace(story_plan)
                enhanced = self.data.setdefault("detail_enhanced_segments", {})
                enhanced[segment["key"]] = {
                    "status": "done",
                    "updated_at": self._environment_now().strftime("%H:%M"),
                    "summary": _single_line(detail.get("summary"), 120),
                    "summary_basis": self._normalize_schedule_basis(detail.get("summary_basis"), default=["coarse_plan"]),
                    "summary_confidence": min(1.0, _safe_float(detail.get("summary_confidence"), 0.75)),
                    "location": _single_line(detail.get("location"), 60),
                    "location_basis": self._normalize_schedule_basis(detail.get("location_basis"), default=["coarse_plan"]),
                    "location_confidence": min(1.0, _safe_float(detail.get("location_confidence"), 0.72)),
                    "today_events": detail.get("today_events", []),
                    "proactive_events": detail.get("proactive_events", []),
                    "state_variables": detail.get("state_variables", []),
                    "presence_status": detail.get("presence_status", {}),
                    "quality": detail.get("quality", {}),
                    "interaction_updates": [],
                    "coverage_repair_done": bool(segment.get("_coverage_repair")),
                }
                self._sanitize_detail_enhanced_segments_inplace(enhanced)
                meal_entries = self._append_self_meal_log(
                    self._collect_self_meal_events_from_detail(segment=segment, plan=plan, detail=detail),
                    segment=segment,
                    plan=plan,
                )
                self._remember_detail_enhancement_history(plan_date, enhanced, story_plan)
                self._refresh_daily_state_location_from_plan(
                    plan=plan,
                    detail=detail,
                    segment=segment,
                )
                self._reschedule_users_for_new_detail_events(segment)
                self._save_data_sync(
                    sections={
                        "daily_plan",
                        "daily_state",
                        "detail_enhanced_segments",
                        "detail_enhanced_history",
                        "daily_story_plan",
                        "daily_story_plan_history",
                        "users",
                        "self_meal_log",
                    }
                )
                last_detail = detail
            for meal_entry in meal_entries:
                await self._memory_companion_record_self_meal(meal_entry)
            if meal_entries:
                self._schedule_data_save(sections={"self_meal_log"})
            await self._apply_detail_presence_status(segment, detail)
        return last_detail

    def _collect_detail_segments(
        self,
        plan: dict[str, Any],
        enhanced: dict[str, Any],
        *,
        include_cancelled: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return []
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            return []
        starts = self._normalized_plan_item_starts(items)
        parsed = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            start = starts[index] if index < len(starts) else None
            if start is None:
                continue
            parsed.append((index, start, item))
        if not parsed:
            return []
        segments: list[dict[str, Any]] = []
        for pos, (index, start, item) in enumerate(parsed):
            if not include_cancelled and self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            key = f"{plan.get('date')}:{index}:{item.get('time')}"
            if self._detail_enhancement_snapshot_blocks_generation(enhanced.get(key) if isinstance(enhanced, dict) else None):
                continue
            next_start = (
                parsed[pos + 1][1]
                if pos + 1 < len(parsed)
                else None
            )
            end = self._plan_item_end_minutes(start, item, next_start=next_start)
            segments.append(
                {
                    "key": key,
                    "plan_date": str(plan.get("date") or ""),
                    "index": index,
                    "start": start,
                    "end": end,
                    "previous_item": next(
                        (
                            candidate[2]
                            for candidate in reversed(parsed[:pos])
                            if self._normalize_schedule_lifecycle_status(candidate[2].get("lifecycle_status")) != "cancelled"
                        ),
                        None,
                    ),
                    "item": item,
                    "next_item": next(
                        (
                            candidate[2]
                            for candidate in parsed[pos + 1 :]
                            if self._normalize_schedule_lifecycle_status(candidate[2].get("lifecycle_status")) != "cancelled"
                        ),
                        None,
                    ),
                }
            )
        return segments

    def _detail_generation_is_current(self, segment: dict[str, Any], generation_id: str) -> bool:
        key = _single_line(segment.get("key"), 120)
        if not key or not generation_id:
            return False
        enhanced = self.data.get("detail_enhanced_segments", {})
        snapshot = enhanced.get(key) if isinstance(enhanced, dict) else None
        if not isinstance(snapshot, dict):
            return False
        if _single_line(snapshot.get("status"), 24) != "generating":
            return False
        if _single_line(snapshot.get("generation_id"), 64) != generation_id:
            return False
        live_plan = self.data.get("daily_plan", {})
        plan_date = _single_line(segment.get("plan_date"), 16)
        if not isinstance(live_plan, dict) or _single_line(live_plan.get("date"), 16) != plan_date:
            return False
        items = live_plan.get("items")
        index = _safe_int(segment.get("index"), -1, minimum=-1)
        if not isinstance(items, list) or not (0 <= index < len(items)) or not isinstance(items[index], dict):
            return False
        item = items[index]
        expected_key = f"{plan_date}:{index}:{item.get('time')}"
        if expected_key != key:
            return False
        return self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) != "cancelled"

    def _detail_enhancement_snapshot_blocks_generation(self, snapshot: Any) -> bool:
        if not isinstance(snapshot, dict):
            return False
        status = _single_line(snapshot.get("status"), 24)
        if status in {"done", "cancelled"}:
            return True
        if status == "failed":
            retry_after_ts = _safe_float(snapshot.get("retry_after_ts"), 0)
            return retry_after_ts > _now_ts()
        if status == "generating":
            started_ts = _safe_float(snapshot.get("started_ts"), 0)
            if started_ts > 0:
                return _now_ts() - started_ts < 30 * 60
            started_at = _single_line(snapshot.get("started_at"), 8)
            started_minutes = self._parse_hhmm_to_minutes(started_at)
            if started_minutes is None:
                return False
            elapsed_minutes = self._environment_now_minutes() - started_minutes
            if elapsed_minutes < 0:
                elapsed_minutes += 24 * 60
            return elapsed_minutes < 30
        if status:
            return False
        return bool(snapshot.get("summary") or snapshot.get("today_events") or snapshot.get("proactive_events"))

    def _collect_due_detail_segments(
        self,
        plan: dict[str, Any],
        enhanced: dict[str, Any],
        *,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        segments = self._collect_detail_segments(plan, enhanced if isinstance(enhanced, dict) else {})
        if not segments:
            return []
        if force:
            picked = self._current_detail_segment_for_update() or self._pick_detail_segment(plan, {})
            return [picked] if isinstance(picked, dict) else segments[:1]
        due = [segment for segment in segments if self._detail_segment_is_due(segment)]
        if due:
            return due[:1]

        story_plan = self.data.get("daily_story_plan", {})
        if not isinstance(story_plan, dict):
            story_plan = {}
        repaired: list[dict[str, Any]] = []
        all_segments = self._collect_detail_segments(plan, {})
        for segment in all_segments:
            if not self._detail_segment_is_due(segment):
                continue
            key = str(segment.get("key") or "")
            status = enhanced.get(key) if isinstance(enhanced, dict) else None
            if not isinstance(status, dict) or status.get("status") != "done":
                continue
            if status.get("coverage_repair_done"):
                continue
            if self._detail_segment_has_story_coverage(segment, story_plan):
                continue
            repaired_segment = dict(segment)
            repaired_segment["_coverage_repair"] = True
            repaired.append(repaired_segment)
        return repaired[:1]

    def _detail_segment_is_due(self, segment: dict[str, Any]) -> bool:
        if not isinstance(segment, dict):
            return False
        plan_date = str(self.data.get("daily_plan", {}).get("date") or "")
        now_minutes = self._effective_plan_now_minutes(plan_date)
        if now_minutes is None:
            return False
        start = _safe_int(segment.get("start"), 0)
        end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
        lead = max(0, _safe_int(runtime_persona_setting(self, "detail_enhancement_lead_minutes", 3), 3, 0))
        return start - lead <= now_minutes < end

    def _detail_segment_has_story_coverage(
        self,
        segment: dict[str, Any],
        story_plan: dict[str, Any],
    ) -> bool:
        if not isinstance(segment, dict) or not isinstance(story_plan, dict):
            return False
        start = _safe_int(segment.get("start"), 0)
        end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
        for key in ("today_events", "proactive_events"):
            raw_items = story_plan.get(key, [])
            if not isinstance(raw_items, list):
                continue
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                item_start, item_end = self._parse_window_minutes(str(item.get("window") or ""))
                if item_start is None or item_end is None:
                    continue
                if item_end < item_start:
                    item_end += 24 * 60
                if item_start < end and item_end > start:
                    return True
        return False

    def _pick_detail_segment(
        self, plan: dict[str, Any], enhanced: dict[str, Any]
    ) -> dict[str, Any] | None:
        return pick_detail_segment(self, plan, enhanced)

    async def _generate_detail_enhancement(
        self,
        segment: dict[str, Any],
        plan: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        return await generate_detail_enhancement(self, segment, plan, state)

    def _merge_detail_enhancement(
        self, story_plan: dict[str, Any], detail: dict[str, Any]
    ) -> None:
        for key, limit in (
            ("today_events", 16),
            ("proactive_events", 12),
            ("long_term_events", 6),
        ):
            existing = story_plan.setdefault(key, [])
            if not isinstance(existing, list):
                existing = []
                story_plan[key] = existing
            additions = detail.get(key, [])
            if isinstance(additions, list):
                existing.extend(
                    item
                    for item in additions
                    if isinstance(item, dict)
                    and self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) != "cancelled"
                )
                story_plan[key] = self._trim_story_plan_items(key, existing, limit)

    def _rebuild_story_plan_from_detail_snapshots(self, plan_date: str) -> dict[str, Any]:
        rebuilt: dict[str, Any] = {
            "date": _single_line(plan_date, 16),
            "today_events": [],
            "proactive_events": [],
            "long_term_events": [],
        }
        enhanced = self._detail_enhanced_segments_for_plan_date(plan_date)
        for snapshot in enhanced.values():
            if snapshot.get("status") != "done":
                continue
            self._merge_detail_enhancement(rebuilt, snapshot)
        self._sanitize_story_plan_social_facts_inplace(rebuilt)
        self.data["daily_story_plan"] = rebuilt
        return rebuilt

    def _remember_detail_enhancement_history(
        self,
        date_text: str,
        enhanced: dict[str, Any],
        story_plan: dict[str, Any],
    ) -> None:
        date_key = _single_line(date_text, 16)
        if not date_key:
            return
        history = self.data.setdefault("detail_enhanced_history", [])
        if not isinstance(history, list):
            history = []
            self.data["detail_enhanced_history"] = history
        history[:] = [
            old
            for old in history
            if not (isinstance(old, dict) and _single_line(old.get("date"), 16) == date_key)
        ]
        history.append(
            {
                "date": date_key,
                "updated_at": self._environment_now().strftime("%Y-%m-%d %H:%M"),
                "segments": dict(enhanced or {}),
            }
        )
        del history[:-14]

        story_history = self.data.setdefault("daily_story_plan_history", [])
        if not isinstance(story_history, list):
            story_history = []
            self.data["daily_story_plan_history"] = story_history
        story_history[:] = [
            old
            for old in story_history
            if not (isinstance(old, dict) and _single_line(old.get("date"), 16) == date_key)
        ]
        compact_story = dict(story_plan or {})
        compact_story["date"] = date_key
        story_history.append(compact_story)
        del story_history[:-14]
