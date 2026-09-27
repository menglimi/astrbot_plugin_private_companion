# -*- coding: utf-8 -*-
"""DailyStatePlanPlanGenerateSelectorMixin。

由 tools/split_mixin_domain.py 从 daily_state_plan.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 533 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStatePlanMixin）。
"""
from __future__ import annotations

from .daily_state_plan_shared import _now_ts, _today_key, logger
from .daily_state_plan_shared import Any
from .daily_state_plan_shared import _safe_float
from .daily_state_plan_shared import _safe_int
from .daily_state_plan_shared import _single_line
from .daily_state_plan_shared import deepcopy
from .daily_state_plan_shared import re
from .daily_state_plan_shared import runtime_persona_setting
from .daily_state_plan_shared import time
from .daily_state_plan_shared import unicodedata
from .daily_state_plan_shared import uuid



class DailyStatePlanPlanGenerateSelectorMixin:
    """DailyStatePlanPlanGenerateSelectorMixin（从 DailyStatePlanMixin 拆出）。"""


    def _save_daily_state_sections(self, sections: set[str]) -> None:
        saver = getattr(self, "_save_data_sync", None)
        if not callable(saver):
            return
        try:
            saver(sections=sections)
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc) or "sections" not in str(exc):
                raise
            saver()

    def _detail_enhanced_segments_for_plan_date(
        self,
        plan_date: Any,
        enhanced: Any = None,
        *,
        detail_day: Any = None,
    ) -> dict[str, Any]:
        date_key = _single_line(plan_date, 16)
        recorded_day = self.data.get("detail_enhanced_day") if detail_day is None else detail_day
        if not date_key or _single_line(recorded_day, 16) != date_key:
            return {}
        source = enhanced if isinstance(enhanced, dict) else self.data.get("detail_enhanced_segments")
        if not isinstance(source, dict):
            return {}
        current: dict[str, Any] = {}
        for raw_key, snapshot in source.items():
            key = str(raw_key or "")
            keyed = re.match(r"^(\d{4}-\d{2}-\d{2}):", key)
            if keyed and keyed.group(1) != date_key:
                continue
            if isinstance(snapshot, dict):
                current[key] = snapshot
        return current

    async def _ensure_daily_plan(self, force: bool = False) -> dict[str, Any] | None:
        if not runtime_persona_setting(self, "enable_daily_plan", True) and not force:
            return None

        await self._ensure_daily_state(force=force)
        today = _today_key()
        async with self._data_lock:
            current_plan = self.data.setdefault("daily_plan", {})
            current_plan_date = _single_line(current_plan.get("date"), 16) if isinstance(current_plan, dict) else ""
            detail_day_changed = bool(
                current_plan_date
                and self._is_plan_date_active(current_plan_date)
                and self._sync_detail_enhancement_day_locked(current_plan_date)
            )
            known_users = [
                user for user in self.data.get("users", {}).values() if isinstance(user, dict) and user.get("umo")
            ]
            if not force and current_plan.get("date") == today:
                plan_changed = self._sanitize_daily_plan_inplace(current_plan)
                if plan_changed:
                    self._refresh_daily_state_location_from_plan(plan=current_plan)
                if plan_changed or detail_day_changed:
                    self._save_daily_state_sections(
                        sections={
                            "daily_plan",
                            "daily_state",
                            "detail_enhanced_day",
                            "detail_enhanced_segments",
                            "daily_story_plan",
                        }
                    )
                source = _single_line(current_plan.get("source"), 40).lower()
                retry_after = _safe_float(current_plan.get("retry_after"), 0.0)
                fallback_retry_due = source.startswith("fallback") and (
                    retry_after <= 0 or _now_ts() >= retry_after
                )
                if not fallback_retry_due:
                    return current_plan
            if (
                not force
                and current_plan.get("date") != today
                and self._is_plan_date_active(current_plan.get("date"))
            ):
                plan_changed = self._sanitize_daily_plan_inplace(current_plan)
                if plan_changed:
                    self._refresh_daily_state_location_from_plan(plan=current_plan)
                if plan_changed or detail_day_changed:
                    self._save_daily_state_sections(
                        sections={
                            "daily_plan",
                            "daily_state",
                            "detail_enhanced_day",
                            "detail_enhanced_segments",
                            "daily_story_plan",
                        }
                    )
                return current_plan
            active_persona = str(
                getattr(self, "_active_persona_scope", lambda: "")() or ""
            ).strip()
            primary_persona = str(
                getattr(self, "_primary_persona_id", lambda: "")() or ""
            ).strip()
            configured_empty_secondary = bool(
                getattr(self, "enable_multi_persona_mode", False)
                and active_persona
                and active_persona != primary_persona
                and callable(getattr(self, "_persona_config_exists", None))
                and self._persona_config_exists(active_persona)
            )
            if not force and not known_users and not configured_empty_secondary:
                return current_plan if current_plan.get("date") == today else None
            if not force and not self._is_daily_plan_due():
                if self._is_plan_date_active(current_plan.get("date")):
                    plan_changed = self._sanitize_daily_plan_inplace(current_plan)
                    if plan_changed:
                        self._refresh_daily_state_location_from_plan(plan=current_plan)
                    if plan_changed or detail_day_changed:
                        self._save_daily_state_sections(
                            sections={
                                "daily_plan",
                                "daily_state",
                                "detail_enhanced_day",
                                "detail_enhanced_segments",
                                "daily_story_plan",
                            }
                        )
                    return current_plan
                return None

        generation_lock = self._daily_generation_lock("_daily_plan_generation_lock")
        async with generation_lock:
            # The initial eligibility check intentionally happens before the
            # model call, but another caller may have generated the plan while
            # we were waiting. Re-check the shared plan inside the generation
            # lock so one day/persona only consumes one model request.
            if not force:
                async with self._data_lock:
                    current_plan = self.data.get("daily_plan")
                    if isinstance(current_plan, dict) and _single_line(current_plan.get("date"), 16) == today:
                        source = _single_line(current_plan.get("source"), 40).lower()
                        retry_after = _safe_float(current_plan.get("retry_after"), 0.0)
                        fallback_retry_due = source.startswith("fallback") and (
                            retry_after <= 0 or _now_ts() >= retry_after
                        )
                        if not fallback_retry_due:
                            return current_plan

            plan = await self._generate_daily_plan()
            async with self._data_lock:
                self.data["daily_plan"] = plan
                self._sync_detail_enhancement_day_locked(plan.get("date"), reset=True)
                self._refresh_daily_state_location_from_plan(plan=plan)
                self._save_daily_state_sections(
                    sections={
                        "daily_plan",
                        "daily_state",
                        "detail_enhanced_day",
                        "detail_enhanced_segments",
                        "daily_story_plan",
                    }
                )
        outfit_generator = getattr(self, "_ensure_daily_outfit_photo", None)
        if callable(outfit_generator):
            try:
                await outfit_generator()
            except Exception as exc:
                logger.warning(
                    "今日日程已保存,但每日穿搭照片生成失败: %s",
                    _single_line(exc, 180),
                )
        await self._ensure_daily_news_reading(force=force)
        return plan

    @staticmethod
    def _schedule_segment_selector_cn_number(value: Any) -> int | None:
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

    def _schedule_segment_selector_minutes(self, selector: str) -> list[int]:
        compact = re.sub(r"\s+", "", str(selector or ""))
        match = re.search(
            r"(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|今晚|夜里)?"
            r"(\d{1,2}|[零〇一二两兩三四五六七八九十]{1,3})"
            r"(?:[:：点點时時])"
            r"(\d{1,2}|半|一刻|三刻)?",
            compact,
        )
        if not match:
            return []
        period = str(match.group(1) or "")
        hour = self._schedule_segment_selector_cn_number(match.group(2))
        minute_text = str(match.group(3) or "")
        if hour is None:
            return []
        if minute_text == "半":
            minute = 30
        elif minute_text == "一刻":
            minute = 15
        elif minute_text == "三刻":
            minute = 45
        else:
            minute = _safe_int(minute_text, 0, 0, 59)
        if hour > 23:
            return []
        if period in {"凌晨"} and hour == 12:
            hour = 0
        elif period in {"中午", "下午", "傍晚", "晚上", "今晚", "夜里"}:
            if hour == 12:
                hour = 12 if period == "中午" else 0
            elif hour < 12:
                hour += 12
        elif period in {"早上", "早晨", "上午"} and hour == 12:
            hour = 0
        primary = hour * 60 + minute
        if period or hour == 0 or hour > 12:
            return [primary]
        alternate = primary + 12 * 60
        return [primary, alternate] if alternate < 24 * 60 else [primary]

    @staticmethod
    def _schedule_segment_selector_text(value: Any) -> str:
        text = unicodedata.normalize("NFKC", _single_line(value, 160)).lower()
        text = re.sub(
            r"(?:今天|今日|今儿|当天|这一段|这个|那一段|那个|时段|时间段|日程|安排|计划|细化|活动|任务)",
            "",
            text,
        )
        text = re.sub(
            r"(?:凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|今晚|夜里)?"
            r"(?:\d{1,2}|[零〇一二两兩三四五六七八九十]{1,3})"
            r"(?:[:：点點时時])(?:\d{1,2}|半|一刻|三刻)?",
            "",
            text,
        )
        text = re.sub(r"[\s\-_—:：，,。.!！?？'\"“”‘’（）()【】\[\]]+", "", text)
        return text

    def _schedule_segment_label(self, segment: dict[str, Any]) -> str:
        item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
        start = self._minutes_to_hhmm(_safe_int(segment.get("start"), 0))
        end = self._minutes_to_hhmm(_safe_int(segment.get("end"), 0))
        activity = _single_line(item.get("activity"), 80) or "未命名日程"
        status = self._normalize_schedule_lifecycle_status(item.get("lifecycle_status"))
        suffix = "（已取消）" if status == "cancelled" else ""
        return f"{start}-{end} {activity}{suffix}"

    def _resolve_daily_plan_segment_selector(
        self,
        selector: Any,
        *,
        plan: dict[str, Any] | None = None,
        include_cancelled: bool = True,
    ) -> tuple[dict[str, Any] | None, str]:
        current_plan = plan if isinstance(plan, dict) else self.data.get("daily_plan", {})
        if not isinstance(current_plan, dict) or not current_plan.get("items"):
            return None, "今天还没有可操作的日程。"
        segments = self._collect_detail_segments(current_plan, {}, include_cancelled=include_cancelled)
        if not segments:
            return None, "今天还没有可操作的日程段。"
        raw = _single_line(selector, 160)
        if not raw:
            return None, "请指定时间或活动，例如“陪伴 删除日程 15:00”或“陪伴 重置日程 整理房间”。"

        exact_key = next((segment for segment in segments if _single_line(segment.get("key"), 120) == raw), None)
        if exact_key:
            return exact_key, ""
        compact = re.sub(r"\s+", "", raw.lower())
        if compact in {"当前", "现在", "此刻", "正在进行", "当前段", "这一段", "这段"}:
            current = self._current_detail_segment_for_update()
            if current:
                return current, ""
            return None, "当前没有正在进行或即将开始的日程段。"

        ordinal_match = re.search(
            r"(?:第\s*(\d{1,2}|[一二两三四五六七八九十]{1,3})\s*(?:个|项|段)?|"
            r"(\d{1,2}|[一二两三四五六七八九十]{1,3})\s*(?:个|项|段))",
            raw,
        )
        if ordinal_match:
            ordinal = self._schedule_segment_selector_cn_number(ordinal_match.group(1) or ordinal_match.group(2))
            if ordinal is not None and 1 <= ordinal <= len(segments):
                return segments[ordinal - 1], ""

        requested_minutes = self._schedule_segment_selector_minutes(raw)
        time_matches: list[dict[str, Any]] = []
        if requested_minutes:
            exact_starts = [
                segment
                for segment in segments
                if any(_safe_int(segment.get("start"), -1) % (24 * 60) == minute for minute in requested_minutes)
            ]
            if exact_starts:
                time_matches = exact_starts
            else:
                time_matches = [
                    segment
                    for segment in segments
                    if any(
                        _safe_int(segment.get("start"), 0) <= minute < _safe_int(segment.get("end"), 0)
                        or _safe_int(segment.get("start"), 0) <= minute + 24 * 60 < _safe_int(segment.get("end"), 0)
                        for minute in requested_minutes
                    )
                ]

        activity_query = self._schedule_segment_selector_text(raw)
        activity_matches: list[dict[str, Any]] = []
        if activity_query:
            for segment in segments:
                item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
                activity = self._schedule_segment_selector_text(item.get("activity"))
                if activity_query == activity or activity_query in activity or activity in activity_query:
                    activity_matches.append(segment)

        matches = time_matches
        if activity_matches:
            intersection = [segment for segment in time_matches if segment in activity_matches]
            matches = intersection or activity_matches if time_matches else activity_matches
        if len(matches) == 1:
            return matches[0], ""
        if len(matches) > 1:
            choices = "；".join(self._schedule_segment_label(segment) for segment in matches[:5])
            return None, f"匹配到多段日程，请再具体一点：{choices}"

        choices = "；".join(self._schedule_segment_label(segment) for segment in segments[:8])
        return None, f"没有找到“{raw}”对应的日程。今天可选：{choices}"

    async def _cancel_daily_plan_segment_by_selector(self, selector: Any, *, reason: str = "用户通过聊天命令取消该日程段") -> tuple[bool, str]:
        async with self._data_lock:
            segment, error = self._resolve_daily_plan_segment_selector(selector, include_cancelled=True)
            if not segment:
                return False, error
            key = _single_line(segment.get("key"), 120)
            plan = self.data.get("daily_plan", {})
            items = plan.get("items") if isinstance(plan, dict) else None
            index = _safe_int(segment.get("index"), -1, minimum=-1)
            item = items[index] if isinstance(items, list) and 0 <= index < len(items) and isinstance(items[index], dict) else None
            if not isinstance(item, dict):
                return False, "该日程段已经不存在。"
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                return True, f"这段日程之前已经取消：{self._schedule_segment_label(segment)}"
            label = self._schedule_segment_label(segment)
            item["lifecycle_status"] = "cancelled"
            item["changed_at"] = self._environment_now().strftime("%H:%M")
            item["change_reason"] = _single_line(reason, 120)
            item.pop("_detail_generation_id", None)
            self._sync_detail_enhancement_day_locked(plan.get("date"))
            enhanced = self.data.setdefault("detail_enhanced_segments", {})
            if not isinstance(enhanced, dict):
                enhanced = {}
                self.data["detail_enhanced_segments"] = enhanced
            cancelled = deepcopy(enhanced.get(key)) if isinstance(enhanced.get(key), dict) else {
                "status": "done",
                "summary": "这一段已取消。",
                "today_events": [],
                "proactive_events": [],
                "state_variables": [],
            }
            for event in list(cancelled.get("today_events") or []) + list(cancelled.get("proactive_events") or []):
                if isinstance(event, dict):
                    event["lifecycle_status"] = "cancelled"
            cancelled["status"] = "cancelled"
            cancelled["summary"] = _single_line(cancelled.get("summary"), 120) or "这一段已取消。"
            cancelled["cancelled_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
            for field in ("generation_id", "previous_item_state", "retry_after", "retry_after_ts"):
                cancelled.pop(field, None)
            enhanced[key] = cancelled
            story = self._rebuild_story_plan_from_detail_snapshots(str(plan.get("date") or _today_key()))
            self._remember_detail_enhancement_history(str(plan.get("date") or _today_key()), enhanced, story)
            self._save_data_sync(
                sections={
                    "daily_plan",
                    "detail_enhanced_day",
                    "detail_enhanced_segments",
                    "detail_enhanced_history",
                    "daily_story_plan",
                    "daily_story_plan_history",
                }
            )
            return True, f"已取消：{label}"

    async def _regenerate_daily_plan_segment_by_selector(
        self,
        selector: Any,
        generator: Any,
        *,
        reason: str = "用户通过聊天命令重新细化该日程段",
    ) -> tuple[bool, str, dict[str, Any]]:
        if not callable(generator):
            return False, "当前没有可用的日程细化生成器。", {}
        previous_snapshot: dict[str, Any] = {}
        previous_item_state: dict[str, tuple[bool, Any]] = {}
        generation_id = ""
        key = ""
        segment: dict[str, Any] = {}
        plan: dict[str, Any] = {}
        try:
            async with self._data_lock:
                live_plan = self.data.get("daily_plan", {})
                segment, error = self._resolve_daily_plan_segment_selector(
                    selector,
                    plan=live_plan if isinstance(live_plan, dict) else {},
                    include_cancelled=True,
                )
                if not segment:
                    return False, error, {}
                key = _single_line(segment.get("key"), 120)
                plan = deepcopy(live_plan)
                segment = next(
                    (
                        candidate
                        for candidate in self._collect_detail_segments(plan, {}, include_cancelled=True)
                        if _single_line(candidate.get("key"), 120) == key
                    ),
                    segment,
                )
                state = deepcopy(self.data.get("daily_state", {}))
                items = live_plan.get("items") if isinstance(live_plan, dict) else None
                index = _safe_int(segment.get("index"), -1, minimum=-1)
                live_item = items[index] if isinstance(items, list) and 0 <= index < len(items) and isinstance(items[index], dict) else None
                if not isinstance(live_item, dict):
                    return False, "该日程段已经不存在。", {}
                self._sync_detail_enhancement_day_locked(plan.get("date"))
                enhanced = self.data.setdefault("detail_enhanced_segments", {})
                if not isinstance(enhanced, dict):
                    enhanced = {}
                    self.data["detail_enhanced_segments"] = enhanced
                previous_snapshot = deepcopy(enhanced.get(key)) if isinstance(enhanced.get(key), dict) else {}
                if (
                    _single_line(previous_snapshot.get("status"), 24) == "generating"
                    and self._detail_enhancement_snapshot_blocks_generation(previous_snapshot)
                ):
                    return False, "该时间段正在细化中，请等待当前生成完成后再试。", {}
                for field in ("lifecycle_status", "changed_at", "change_reason", "_detail_generation_id"):
                    previous_item_state[field] = (field in live_item, deepcopy(live_item.get(field)))
                generation_id = uuid.uuid4().hex
                live_item["lifecycle_status"] = "changed"
                live_item["changed_at"] = self._environment_now().strftime("%H:%M")
                live_item["change_reason"] = _single_line(reason, 120)
                live_item["_detail_generation_id"] = generation_id
                segment_item = segment.get("item") if isinstance(segment.get("item"), dict) else None
                if isinstance(segment_item, dict):
                    segment_item["lifecycle_status"] = "changed"
                enhanced[key] = {
                    "status": "generating",
                    "started_at": self._environment_now().strftime("%H:%M"),
                    "started_ts": time.time(),
                    "regenerated": True,
                    "generation_id": generation_id,
                }
                self._save_data_sync(
                    sections={"daily_plan", "detail_enhanced_day", "detail_enhanced_segments"}
                )

            detail = await generator(self, segment, plan, state)
            if not isinstance(detail, dict) or not isinstance(detail.get("today_events"), list) or not detail.get("today_events"):
                raise RuntimeError("局部重生成未返回可用的细化事件")

            async with self._data_lock:
                if not self._detail_generation_is_current(segment, generation_id):
                    return False, "该时间段已被取消、替换或由更新的操作接管，本次迟到结果未写入。", {}
                enhanced = self.data.setdefault("detail_enhanced_segments", {})
                enhanced[key] = {
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
                    "interaction_updates": previous_snapshot.get("interaction_updates", []),
                    "regenerated": True,
                    "regenerated_at": self._environment_now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                self._sanitize_detail_enhanced_segments_inplace(enhanced)
                story = self._rebuild_story_plan_from_detail_snapshots(str(plan.get("date") or _today_key()))
                self._remember_detail_enhancement_history(str(plan.get("date") or _today_key()), enhanced, story)
                current_plan = self.data.get("daily_plan", {})
                current_items = current_plan.get("items") if isinstance(current_plan, dict) else None
                index = _safe_int(segment.get("index"), -1, minimum=-1)
                current_item = current_items[index] if isinstance(current_items, list) and 0 <= index < len(current_items) and isinstance(current_items[index], dict) else None
                if isinstance(current_item, dict) and _single_line(current_item.get("_detail_generation_id"), 64) == generation_id:
                    current_item.pop("_detail_generation_id", None)
                self._refresh_daily_state_location_from_plan(
                    plan=current_plan if isinstance(current_plan, dict) else plan,
                    detail=detail,
                    segment=segment,
                )
                self._save_data_sync(
                    sections={
                        "daily_plan",
                        "daily_state",
                        "detail_enhanced_day",
                        "detail_enhanced_segments",
                        "detail_enhanced_history",
                        "daily_story_plan",
                        "daily_story_plan_history",
                    }
                )
                label = self._schedule_segment_label(segment)
            return True, f"已重新细化：{label}", detail
        except Exception as exc:
            logger.warning("聊天命令局部重生成日程细化失败: %s", exc, exc_info=True)
            async with self._data_lock:
                enhanced = self.data.setdefault("detail_enhanced_segments", {})
                if isinstance(enhanced, dict) and key and generation_id and self._detail_generation_is_current(segment, generation_id):
                    restored = previous_snapshot or {
                        "status": "failed",
                        "today_events": [],
                        "proactive_events": [],
                        "state_variables": [],
                    }
                    restored["regeneration_error"] = _single_line(exc, 180)
                    restored["regeneration_failed_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
                    enhanced[key] = restored
                    live_plan = self.data.get("daily_plan", {})
                    live_items = live_plan.get("items") if isinstance(live_plan, dict) else None
                    index = _safe_int(segment.get("index"), -1, minimum=-1)
                    live_item = live_items[index] if isinstance(live_items, list) and 0 <= index < len(live_items) and isinstance(live_items[index], dict) else None
                    if isinstance(live_item, dict) and _single_line(live_item.get("_detail_generation_id"), 64) == generation_id:
                        for field, (existed, value) in previous_item_state.items():
                            if existed:
                                live_item[field] = value
                            else:
                                live_item.pop(field, None)
                    self._save_data_sync(
                        sections={"daily_plan", "detail_enhanced_segments"}
                    )
            return False, _single_line(exc, 180) or "局部重生成失败。", {}
