# -*- coding: utf-8 -*-
"""overview_limit 域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 311 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import re
import time
from typing import Any



class PrivateCompanionPageApiOverviewLimitMixin:
    """overview_limit 域（从 PrivateCompanionPageApi 拆出）。"""


    @staticmethod
    def _merge_activity_item(target: dict[str, Any], source: dict[str, Any]) -> None:
        def as_float(value: Any, default: float = 0.0) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        def as_int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        target["total_events"] = as_int(target.get("total_events")) + as_int(source.get("total_events"))
        if source.get("display_name") and not target.get("display_name"):
            target["display_name"] = source.get("display_name")
        if source.get("live_username") and not target.get("live_username"):
            target["live_username"] = source.get("live_username")
        target["first_seen"] = min(as_float(target.get("first_seen"), time.time()), as_float(source.get("first_seen"), time.time()))
        target["last_seen"] = max(as_float(target.get("last_seen")), as_float(source.get("last_seen")))
        counts = target.setdefault("event_counts", {})
        if isinstance(counts, dict) and isinstance(source.get("event_counts"), dict):
            for key, value in source["event_counts"].items():
                counts[key] = as_int(counts.get(key)) + as_int(value)
        for field, limit in (("recent_events", 12), ("recent_danmaku", 8)):
            rows = []
            for row in [*(target.get(field) if isinstance(target.get(field), list) else []), *(source.get(field) if isinstance(source.get(field), list) else [])]:
                if isinstance(row, dict):
                    rows.append(row)
            rows.sort(key=lambda row: as_float(row.get("ts")), reverse=True)
            target[field] = rows[:limit]

    def _segment_from_key(self, key: str, plan: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        keyed = re.fullmatch(r"(\d{4}-\d{2}-\d{2}):(\d+):(\d{1,2}:\d{2})", key)
        if keyed:
            index = int(keyed.group(2))
            key_start_text = keyed.group(3)
            start = self.plugin._parse_hhmm_to_minutes(key_start_text)
            items = []
            if isinstance(plan, dict):
                if isinstance(plan.get("items"), list):
                    items = plan.get("items") or []
                elif isinstance(plan.get("schedule"), list):
                    items = plan.get("schedule") or []
            end = None
            current_item = items[index] if isinstance(items, list) and index < len(items) and isinstance(items[index], dict) else None
            if isinstance(current_item, dict) and self._single_line(current_item.get("time"), 8) == key_start_text:
                starts = self.plugin._normalized_plan_item_starts(items)
                if index < len(starts) and starts[index] is not None:
                    start = starts[index]
            else:
                current_item = None
            if isinstance(current_item, dict):
                end = self.plugin._parse_hhmm_to_minutes(current_item.get("end"))
                if start is not None and end is not None and end <= start:
                    end += 24 * 60
            if isinstance(items, list):
                for next_item in items[index + 1:]:
                    if end is not None:
                        break
                    if isinstance(next_item, dict):
                        end = self.plugin._parse_hhmm_to_minutes(next_item.get("time"))
                        if end is not None:
                            break
            if start is not None:
                if end is None:
                    end = self.plugin._plan_item_end_minutes(start, current_item)
                return {
                    "window": f"{self.plugin._minutes_to_hhmm(start)}-{self.plugin._minutes_to_hhmm(end)}",
                    "start": start,
                    "end": end,
                    "index": index,
                    "item": current_item or {},
                }

        inferred = self._segment_from_story_windows(snapshot)
        if inferred:
            return inferred

        match = re.search(r"(?:^|[:|_])(\d{1,4})[-_](\d{1,4})(?:$|[:|_])", key)
        if not match:
            return {"window": key, "start": 99999}
        start = int(match.group(1))
        end = int(match.group(2))
        if not (0 <= start < 24 * 60 and 0 < end <= 28 * 60):
            return {"window": key, "start": 99999}
        return {"window": f"{self.plugin._minutes_to_hhmm(start)}-{self.plugin._minutes_to_hhmm(end)}", "start": start}

    def _limited_state_variables(self, value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, str]] = []
        for item in value[:8]:
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "name": self._single_line(item.get("name") or item.get("key"), 40),
                    "value": self._single_line(item.get("value"), 80),
                    "note": self._single_line(item.get("note"), 100),
                }
            )
        return items

    def _limited_interaction_updates(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for item in value[-8:]:
            if not isinstance(item, dict):
                continue
            updates = item.get("state_updates")
            items.append(
                {
                    "at": self._single_line(item.get("at"), 12),
                    "source": self._single_line(item.get("source"), 24),
                    "reaction": self._single_line(item.get("reaction"), 140),
                    "state_updates": [
                        self._single_line(update, 80)
                        for update in updates[:6]
                        if self._single_line(update, 80)
                    ]
                    if isinstance(updates, list)
                    else [],
                }
            )
        return items

    def _timeline_story_items(
        self,
        value: Any,
        limit: int,
        plan_date: str,
        *,
        parent_start: int | None = None,
        parent_end: int | None = None,
    ) -> list[dict[str, Any]]:
        items = self._limited_story_items(
            value,
            limit,
            parent_start=parent_start,
            parent_end=parent_end,
        )
        for item in items:
            start, end = self.plugin._parse_window_minutes(str(item.get("window") or ""))
            if start is not None and end is not None:
                duration = end - start
                if duration <= 0:
                    duration += 24 * 60
                axis_start = self._story_item_axis_start(
                    start,
                    parent_start=parent_start,
                    parent_end=parent_end,
                )
                item["clock_status"] = self.plugin._schedule_window_runtime_status(
                    axis_start,
                    axis_start + duration,
                    plan_date=plan_date,
                )
            # Story/detail entries are projections.  Their clock window only
            # describes when a generated scene could occur; it is not an
            # execution observation.  Never turn them into ``active`` or
            # ``completed`` solely because the wall clock crossed the window.
            # Preserve explicit editorial transitions (cancelled/changed/
            # deferred), while evidence-backed canonical states may still be
            # surfaced when a producer supplied the full evidence contract.
            explicit = self._single_line(item.get("lifecycle_status"), 24).lower()
            if explicit in {"cancelled", "canceled", "changed", "deferred", "postponed"}:
                item["lifecycle"] = "cancelled" if explicit == "canceled" else (
                    "deferred" if explicit == "postponed" else explicit
                )
                continue
            evidence_kind = self._single_line(item.get("evidence_kind"), 48).lower()
            eligibility = self._single_line(item.get("fact_eligibility"), 48).lower()
            status = self._single_line(item.get("status"), 32).lower()
            if (
                evidence_kind in {"interaction", "tool_action", "external_record"}
                and eligibility in {"current_observed", "history_observed"}
                and status in {"active", "completed", "partially_completed"}
            ):
                item["lifecycle"] = status
            else:
                item["lifecycle"] = "planned"
        return items

    def _limited_story_items(self,
        value: Any,
        limit: int,
        *,
        parent_start: int | None = None,
        parent_end: int | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        indexed_items = [
            (
                self._story_item_axis_start(
                    self._story_item_start_minutes(item),
                    parent_start=parent_start,
                    parent_end=parent_end,
                ),
                index,
                item,
            )
            for index, item in enumerate(value)
            if isinstance(item, dict)
        ]
        indexed_items.sort(key=lambda row: (row[0], row[1]))
        for _, _, item in indexed_items[:limit]:
            if not isinstance(item, dict):
                continue
            evidence_kind = self._single_line(item.get("evidence_kind"), 48).lower()
            if evidence_kind not in {"interaction", "tool_action", "external_record"}:
                evidence_kind = ""
            fact_eligibility = self._single_line(item.get("fact_eligibility"), 48).lower()
            if fact_eligibility not in {"current_observed", "history_observed"}:
                fact_eligibility = ""
            status = self._single_line(item.get("status"), 32).lower()
            if status not in {"planned", "unknown", "active", "completed", "partially_completed"}:
                status = ""
            items.append(
                {
                    "window": self._single_line(item.get("window") or item.get("time"), 24),
                    "text": self._single_line(
                        item.get("event")
                        or item.get("topic")
                        or item.get("summary")
                        or item.get("text")
                        or item.get("content"),
                        160,
                    ),
                    "mood": self._single_line(item.get("mood"), 24),
                    "action": self._single_line(item.get("action"), 24),
                    "reason": self._single_line(item.get("reason"), 32),
                    "lifecycle_status": self._single_line(item.get("lifecycle_status"), 20),
                    "evidence_kind": evidence_kind,
                    "fact_eligibility": fact_eligibility,
                    "status": status,
                    "basis": [
                        self._single_line(value, 24)
                        for value in (item.get("basis") or [])[:3]
                        if self._single_line(value, 24)
                    ] if isinstance(item.get("basis"), list) else [],
                    "confidence": min(1.0, self._float(item.get("confidence"), 0.72)),
                }
            )
        return items

    def _story_item_start_minutes(self, item: Any) -> int:
        if not isinstance(item, dict):
            return 99999
        text = self._single_line(item.get("window") or item.get("time"), 32)
        match = re.search(r"(\d{1,2}):(\d{2})", text)
        if not match:
            return 99999
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour > 23 or minute > 59:
            return 99999
        return hour * 60 + minute

    def _limited_adjustments(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for item in value[-10:]:
            if not isinstance(item, dict):
                continue
            updates = item.get("state_updates")
            items.append(
                {
                    "date": self._single_line(item.get("date"), 12),
                    "source": self._single_line(item.get("source"), 24),
                    "scope": self._single_line(item.get("scope"), 40),
                    "scope_key": self._single_line(item.get("scope_key"), 24),
                    "note": self._single_line(item.get("note"), 140),
                    "reaction": self._single_line(item.get("immediate_reaction"), 140),
                    "state_updates": [
                        self._single_line(update, 80)
                        for update in updates[:6]
                        if self._single_line(update, 80)
                    ]
                    if isinstance(updates, list)
                    else [],
                }
            )
        return items

    def _limited_dream_fragments(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for raw in value[-18:]:
            if isinstance(raw, dict):
                text = self._single_line(
                    raw.get("text") or raw.get("keyword") or raw.get("label"),
                    42,
                )
                if not text:
                    continue
                items.append(
                    {
                        "text": text,
                        "weight": self._float(raw.get("effective_weight") or raw.get("weight")),
                        "source": self._single_line(raw.get("source"), 24),
                        "created_at": self._single_line(raw.get("created_at") or raw.get("created_ts"), 24),
                    }
                )
            else:
                text = self._single_line(raw, 42)
                if text:
                    items.append({"text": text, "weight": 1.0, "source": "", "created_at": ""})
        items.sort(key=lambda item: float(item.get("weight") or 0), reverse=True)
        return items[:14]

    @staticmethod
    def _limited_list(value: Any, limit: int) -> list[Any]:
        return list(value[:limit]) if isinstance(value, list) else []
