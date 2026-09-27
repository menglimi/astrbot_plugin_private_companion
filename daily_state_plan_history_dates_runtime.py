# -*- coding: utf-8 -*-
"""DailyStatePlanHistoryDatesRuntimeMixin。

由 tools/split_mixin_domain.py 从 daily_state_plan.py 机械抽取（29 个方法 + 0 个模块级名字 + 0 个类级赋值 / 477 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStatePlanMixin）。
"""
from __future__ import annotations

from .daily_state_plan_shared import _now_ts, _today_key
from .daily_state_plan_shared import Any
from .daily_state_plan_shared import PromptSection
from .daily_state_plan_shared import _date_key
from .daily_state_plan_shared import _safe_float
from .daily_state_plan_shared import _single_line
from .daily_state_plan_shared import build_daily_plan_prompt
from .daily_state_plan_shared import build_daily_plan_prompt_section
from .daily_state_plan_shared import generate_daily_plan
from .daily_state_plan_shared import get_schedule_planning_prompt
from .daily_state_plan_shared import normalize_plan_item
from .daily_state_plan_shared import random
from .daily_state_plan_shared import re
from .daily_state_plan_shared import runtime_persona_setting
from .daily_state_plan_shared import timedelta



class DailyStatePlanHistoryDatesRuntimeMixin:
    """DailyStatePlanHistoryDatesRuntimeMixin（从 DailyStatePlanMixin 拆出）。"""


    def _format_recent_daily_plan_history_for_prompt(self, limit: int = 5) -> str:
        history = self._recent_daily_plan_history_entries()
        rows: list[str] = []
        for entry in history[-limit:]:
            if not isinstance(entry, dict):
                continue
            date_text = _single_line(entry.get("date"), 16)
            signatures = entry.get("signature")
            if not isinstance(signatures, list):
                signatures = []
            samples = entry.get("sample")
            if not isinstance(samples, list):
                samples = []
            skeleton = " / ".join(_single_line(part, 20) for part in signatures[:12] if part)
            sample_text = "；".join(_single_line(part, 46) for part in samples[:4] if part)
            if skeleton:
                line = f"- {date_text}: {skeleton}"
                if sample_text:
                    line += f"\n  代表活动: {sample_text}"
                rows.append(line)
        return "\n".join(rows) if rows else "暂无最近日程历史。"

    def _plan_repetition_score(self, items: list[dict[str, str]]) -> float:
        signatures = self._plan_signature(items)
        if not signatures:
            return 0.0
        current_set = set(signatures)
        history = self._recent_daily_plan_history_entries()
        best_score = 0.0
        for entry in history[-5:]:
            if not isinstance(entry, dict):
                continue
            old_signatures = entry.get("signature")
            if not isinstance(old_signatures, list) or not old_signatures:
                continue
            old_values = [str(value) for value in old_signatures if value]
            old_set = set(old_values)
            if not old_set:
                continue
            jaccard = len(current_set & old_set) / max(1, len(current_set | old_set))
            paired = min(len(signatures), len(old_values))
            same_positions = 0
            for idx in range(paired):
                if signatures[idx] == old_values[idx]:
                    same_positions += 1
            ordered = same_positions / max(1, paired)
            best_score = max(best_score, jaccard * 0.65 + ordered * 0.35)
        return best_score

    def _plan_is_too_repetitive(self, items: list[dict[str, str]]) -> bool:
        if not items:
            return False
        signatures = self._plan_signature(items)
        if len(signatures) >= 6:
            dominant_count = max(signatures.count(signature) for signature in set(signatures))
            if dominant_count >= max(4, len(signatures) // 2 + 1):
                return True
        return self._plan_repetition_score(items) >= 0.62

    def _daily_plan_history_entry(self, plan: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(plan, dict):
            return None
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            return None
        plan_date = _single_line(plan.get("date"), 16) or _today_key()
        sample: list[str] = []
        compact_items: list[dict[str, str]] = []
        for item in items[:6]:
            if not isinstance(item, dict):
                continue
            time_text = _single_line(item.get("time"), 8)
            activity = _single_line(item.get("activity"), 52)
            if activity:
                sample.append(f"{time_text} {activity}".strip())
        for item in items[:18]:
            if not isinstance(item, dict):
                continue
            compact_items.append(
                {
                    "time": _single_line(item.get("time"), 20),
                    "activity": _single_line(item.get("activity") or item.get("title"), 180),
                    "mood": _single_line(item.get("mood"), 80),
                    "message_seed": _single_line(item.get("message_seed"), 220),
                }
            )
        entry = {
            "date": plan_date,
            "generated_at": _single_line(plan.get("generated_at"), 20) or self._environment_now().strftime("%Y-%m-%d %H:%M"),
            "source": _single_line(plan.get("source"), 16),
            "signature": self._plan_signature(items),
            "sample": sample,
            "items": compact_items,
        }
        return entry

    def _recent_daily_plan_history_entries(self) -> list[dict[str, Any]]:
        history = self.data.get("daily_plan_history", [])
        entries = [entry for entry in history if isinstance(entry, dict)] if isinstance(history, list) else []
        known_dates = {_single_line(entry.get("date"), 16) for entry in entries}
        current_entry = self._daily_plan_history_entry(self.data.get("daily_plan", {}))
        if current_entry and _single_line(current_entry.get("date"), 16) not in known_dates:
            entries.append(current_entry)
        return entries

    def _remember_daily_plan_history(self, plan: dict[str, Any]) -> None:
        entry = self._daily_plan_history_entry(plan)
        if not entry:
            return
        plan_date = _single_line(entry.get("date"), 16)
        history = self.data.setdefault("daily_plan_history", [])
        if not isinstance(history, list):
            history = []
            self.data["daily_plan_history"] = history
        history[:] = [
            old
            for old in history
            if not (isinstance(old, dict) and _single_line(old.get("date"), 16) == plan_date)
        ]
        history.append(entry)
        del history[:-10]

    def _add_important_date_entry(self, value: str) -> tuple[bool, str]:
        parts = value.split(maxsplit=2)
        if len(parts) < 2:
            return False, "格式：陪伴 日期添加 <标题> <YYYY-MM-DD或MM-DD> [备注]"
        title = _single_line(parts[0], 40)
        date_text = _single_line(parts[1], 20)
        note = _single_line(parts[2], 120) if len(parts) >= 3 else ""
        parsed = self._parse_date_value(date_text)
        if parsed is None:
            return False, "日期格式不对,请用 YYYY-MM-DD 或 MM-DD。"
        repeat_yearly = len(date_text) == 5
        entry = {
            "id": f"date-{int(_now_ts())}-{random.randint(1000, 9999)}",
            "title": title,
            "date": date_text,
            "type": "重要日期",
            "note": note,
            "enabled": True,
            "repeat_yearly": repeat_yearly,
                "remind_days": runtime_persona_setting(self, "important_date_lookahead_days", 7),
            "priority": 50,
            "created_at": self._environment_now().strftime("%Y-%m-%d %H:%M"),
        }
        self.data.setdefault("important_dates", []).append(entry)
        return True, f"已添加重要日期：{title}｜{date_text}"

    def _remove_important_date_entry(self, value: str) -> str:
        keyword = _single_line(value, 40)
        if not keyword:
            return "请提供要删除的日期标题关键词。"
        entries = self.data.setdefault("important_dates", [])
        if not isinstance(entries, list):
            self.data["important_dates"] = []
            return "重要日期列表为空。"
        kept = []
        removed = []
        for entry in entries:
            title = str(entry.get("title", "")) if isinstance(entry, dict) else ""
            if keyword in title:
                removed.append(title)
            else:
                kept.append(entry)
        self.data["important_dates"] = kept
        if not removed:
            return "没有找到匹配的重要日期。"
        return "已删除：\n" + "\n".join(f"- {item}" for item in removed)

    def _format_important_dates(self) -> str:
        entries = self.data.get("important_dates", [])
        if not isinstance(entries, list) or not entries:
            return "还没有重要日期。"
        lines = ["重要日期条目："]
        today = self._environment_now().date()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            next_day = self._next_occurrence(entry)
            suffix = ""
            if next_day:
                days = (next_day - today).days
                suffix = "｜今天" if days == 0 else f"｜{days} 天后"
            enabled = "启用" if entry.get("enabled", True) else "停用"
            repeat = "每年" if entry.get("repeat_yearly", True) else "一次"
            lines.append(
                f"- {entry.get('title')}｜{entry.get('date')}｜{repeat}｜{enabled}{suffix}｜{entry.get('note', '')}"
            )
        return "\n".join(lines)

    def _is_daily_plan_due(self) -> bool:
        plan_minutes = self._parse_hhmm_to_minutes(runtime_persona_setting(self, "daily_plan_time", "07:30"))
        if plan_minutes is None:
            plan_minutes = 7 * 60 + 30
        now = self._environment_now()
        return now.hour * 60 + now.minute >= plan_minutes

    def _daily_plan_due_minutes(self) -> int:
        plan_minutes = self._parse_hhmm_to_minutes(runtime_persona_setting(self, "daily_plan_time", "07:30"))
        if plan_minutes is None:
            return 7 * 60 + 30
        return plan_minutes

    def _is_plan_date_active(self, plan_date: str) -> bool:
        plan_date = str(plan_date or "").strip()
        if not plan_date:
            return False
        today = self._environment_now().date()
        today_key = _date_key(today)
        if plan_date == today_key:
            return True
        yesterday_key = _date_key(today - timedelta(days=1))
        if plan_date != yesterday_key:
            return False
        now_minutes = self._environment_now_minutes()
        return now_minutes < self._daily_plan_due_minutes()

    def _get_active_plan(self) -> dict[str, Any]:
        plan = self.data.get("daily_plan", {})
        if isinstance(plan, dict) and self._is_plan_date_active(plan.get("date")):
            return plan
        return {}

    def _effective_plan_now_minutes(self, plan_date: str) -> int | None:
        plan_date = str(plan_date or "").strip()
        if not self._is_plan_date_active(plan_date):
            return None
        now_minutes = self._environment_now_minutes()
        if plan_date == _today_key():
            return now_minutes
        return 24 * 60 + now_minutes

    def _is_sleepy_plan_item(self, item: dict[str, Any] | None) -> bool:
        if not isinstance(item, dict):
            return False
        text = " ".join(
            _single_line(item.get(key), 100)
            for key in ("activity", "mood", "message_seed")
            if _single_line(item.get(key), 100)
        )
        if not text:
            return False
        if re.search(r"继续睡|睡回去|重新入睡|再次入睡|回笼觉", text):
            return True
        if re.search(
            r"自然醒|睡醒|醒来|醒后|刚醒|醒了|已醒|醒着|清醒|睁眼|起床|起身|洗漱|"
            r"不睡|没睡|未睡|还没睡|睡不着|失眠",
            text,
        ):
            return False
        return bool(
            re.search(
                r"睡觉|睡眠|入睡|熟睡|浅睡|午睡|午休|小睡|补觉|回笼觉|打盹|"
                r"眯(?:一|半)?会(?:儿)?|梦乡|被窝|准备睡|睡前|继续睡|睡回去|熄灯休息",
                text,
            )
        )

    def _segment_end_minutes(
        self,
        start: int,
        item: dict[str, Any] | None,
        *,
        next_start: int | None = None,
    ) -> int:
        if next_start is not None:
            return next_start
        if self._is_sleepy_plan_item(item):
            return min(24 * 60 + 240, start + 240)
        return min(24 * 60 + 120, start + 180)

    def _plan_item_end_minutes(
        self,
        start: int,
        item: dict[str, Any] | None,
        *,
        next_start: int | None = None,
    ) -> int:
        explicit = self._parse_hhmm_to_minutes((item or {}).get("end")) if isinstance(item, dict) else None
        if explicit is not None:
            if explicit <= start:
                explicit += 24 * 60
            duration = explicit - start
            if 10 <= duration <= 12 * 60:
                if next_start is not None:
                    normalized_next = next_start + (24 * 60 if next_start <= start else 0)
                    explicit = min(explicit, normalized_next)
                return explicit
        if next_start is not None:
            return next_start + (24 * 60 if next_start <= start else 0)
        return self._segment_end_minutes(start, item)

    def _normalized_plan_item_starts(self, items: Any) -> list[int | None]:
        if not isinstance(items, list):
            return []
        normalized: list[int | None] = []
        day_offset = 0
        previous_raw: int | None = None
        for item in items:
            raw = self._parse_hhmm_to_minutes(item.get("time")) if isinstance(item, dict) else None
            if raw is None:
                normalized.append(None)
                continue
            if previous_raw is not None and raw < previous_raw:
                day_offset += 24 * 60
            normalized.append(raw + day_offset)
            previous_raw = raw
        return normalized

    def _normalize_plan_item_intervals(self, items: Any) -> bool:
        if not isinstance(items, list):
            return False
        starts = self._normalized_plan_item_starts(items)
        changed = False
        for index, item in enumerate(items):
            if not isinstance(item, dict) or starts[index] is None:
                continue
            start = int(starts[index])
            next_start = next((value for value in starts[index + 1 :] if value is not None), None)
            end = self._plan_item_end_minutes(start, item, next_start=next_start)
            end_text = self._minutes_to_hhmm(end)
            if _single_line(item.get("end"), 8) != end_text:
                item["end"] = end_text
                changed = True
            lifecycle = _single_line(item.get("lifecycle_status"), 20).lower()
            if lifecycle not in {"planned", "changed", "cancelled", "deferred"}:
                item["lifecycle_status"] = "planned"
                changed = True
            basis = self._normalize_schedule_basis(item.get("basis"), default=["coarse_plan"])
            if item.get("basis") != basis:
                item["basis"] = basis
                changed = True
            confidence = min(1.0, _safe_float(item.get("confidence"), 0.72))
            if item.get("confidence") != confidence:
                item["confidence"] = confidence
                changed = True
        return changed

    @staticmethod
    def _normalize_schedule_lifecycle_status(value: Any) -> str:
        aliases = {
            "planned": "planned", "计划": "planned", "未开始": "planned",
            "active": "active", "进行": "active", "进行中": "active",
            "completed": "completed", "完成": "completed", "已完成": "completed",
            "changed": "changed", "变更": "changed", "已变更": "changed",
            "cancelled": "cancelled", "canceled": "cancelled", "取消": "cancelled", "已取消": "cancelled",
            "deferred": "deferred", "postponed": "deferred", "顺延": "deferred", "延期": "deferred",
        }
        return aliases.get(_single_line(value, 20).lower(), "")

    def _schedule_window_runtime_status(
        self,
        start: int,
        end: int,
        *,
        plan_date: str = "",
        explicit_status: Any = "",
    ) -> str:
        explicit = self._normalize_schedule_lifecycle_status(explicit_status)
        if explicit == "cancelled":
            return explicit
        date_text = _single_line(plan_date, 16)
        now_minutes = self._effective_plan_now_minutes(date_text) if date_text else self._environment_now_minutes()
        if now_minutes is None:
            today = _today_key()
            return "completed" if date_text and date_text < today else "planned"
        normalized_end = int(end)
        if normalized_end <= start:
            normalized_end += 24 * 60
        if now_minutes < start:
            runtime = "planned"
        elif now_minutes >= normalized_end:
            runtime = "completed"
        else:
            runtime = "active"
        if explicit == "changed" and runtime != "completed":
            return "changed"
        return runtime

    def _plan_item_runtime_status(self, plan: dict[str, Any], item: dict[str, Any], index: int = -1) -> str:
        # Lifecycle display must come from canonical evidence, never from the
        # clock alone.  Keep the legacy helper signature for callers, but map
        # old lifecycle values through a conservative planned/unknown view.
        if isinstance(item, dict):
            legacy = self._normalize_schedule_lifecycle_status(item.get("lifecycle_status"))
            if legacy == "cancelled":
                return "cancelled"
            if legacy == "changed":
                return "changed"
            if legacy == "deferred":
                return "deferred"
            evidence = _single_line(item.get("evidence_kind"), 48).lower()
            eligibility = _single_line(item.get("fact_eligibility"), 48).lower()
            status = _single_line(item.get("status"), 32).lower()
            if evidence in {"interaction", "tool_action", "external_record"} and eligibility in {"current_observed", "history_observed"}:
                if status in {"active", "completed", "partially_completed"}:
                    return status
            if evidence == "self_state_commit" and eligibility == "current_internal":
                return "active"
            plan_date = str((plan or {}).get("date") or item.get("date") or "")
            try:
                canonical = normalize_plan_item(
                    {**item, "date": plan_date or _today_key(), "subject_actor_id": item.get("subject_actor_id") or "bot_self"},
                    plan_id=str(item.get("plan_id") or ""),
                    now=self._environment_now(),
                )
                phase = _single_line(canonical.get("temporal_phase"), 16).lower()
                if phase == "past":
                    # ``normalize_plan_item`` evaluates a HH:MM value on the
                    # calendar date alone.  A plan that deliberately rolls
                    # past midnight therefore looks stale even while its
                    # normalized schedule axis is still current/upcoming.
                    # Preserve the evidence status as ``planned`` in that
                    # case; the separate display status may still project the
                    # wall-clock phase as active.
                    items = plan.get("items") if isinstance(plan, dict) else None
                    starts = self._normalized_plan_item_starts(items)
                    start = starts[index] if isinstance(items, list) and 0 <= index < len(starts) else None
                    if start is not None:
                        next_start = next((value for value in starts[index + 1 :] if value is not None), None)
                        end = self._plan_item_end_minutes(start, item, next_start=next_start)
                        clock_phase = self._schedule_window_runtime_status(
                            start,
                            end,
                            plan_date=plan_date,
                            explicit_status=item.get("lifecycle_status"),
                        )
                        if clock_phase in {"planned", "active"}:
                            return "planned"
                    return "unknown"
                return "planned"
            except Exception:
                return "planned"
        items = plan.get("items") if isinstance(plan, dict) else None
        starts = self._normalized_plan_item_starts(items)
        start = starts[index] if isinstance(items, list) and 0 <= index < len(starts) else self._parse_hhmm_to_minutes(item.get("time"))
        if start is None:
            return "planned"
        next_start = None
        if isinstance(items, list) and index >= 0:
            next_start = next((value for value in starts[index + 1 :] if value is not None), None)
        end = self._plan_item_end_minutes(start, item, next_start=next_start)
        return self._schedule_window_runtime_status(
            start,
            end,
            plan_date=str((plan or {}).get("date") or ""),
            explicit_status=item.get("lifecycle_status"),
        )

    def _plan_item_display_status(self, plan: dict[str, Any], item: dict[str, Any], index: int = -1) -> str:
        """Return the user-facing clock phase without upgrading it to execution evidence."""

        canonical = self._plan_item_runtime_status(plan, item, index)
        if canonical in {"cancelled", "deferred", "overridden", "active", "completed", "partially_completed"}:
            return canonical
        items = plan.get("items") if isinstance(plan, dict) else None
        starts = self._normalized_plan_item_starts(items)
        start = starts[index] if isinstance(items, list) and 0 <= index < len(starts) else self._parse_hhmm_to_minutes(item.get("time"))
        if start is None:
            return canonical or "planned"
        next_start = next((value for value in starts[index + 1 :] if value is not None), None) if index >= 0 else None
        end = self._plan_item_end_minutes(start, item, next_start=next_start)
        return self._schedule_window_runtime_status(
            start,
            end,
            plan_date=str((plan or {}).get("date") or item.get("date") or ""),
            explicit_status=item.get("lifecycle_status"),
        )

    def _parse_hhmm_to_minutes(self, value: Any) -> int | None:
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            return None
        return hour * 60 + minute

    def _minutes_to_hhmm(self, minutes: int) -> str:
        minutes = max(0, int(minutes))
        wrapped = minutes % (24 * 60)
        return f"{wrapped // 60:02d}:{wrapped % 60:02d}"

    async def _generate_daily_plan(self) -> dict[str, Any]:
        await self._ensure_yesterday_conversation_summary()
        await self._ensure_yesterday_screen_diary_context()
        await self._maybe_settle_skill_growth(force=True)
        return await generate_daily_plan(self)

    def _get_schedule_planning_prompt(self) -> str:
        return get_schedule_planning_prompt(self)

    def _build_daily_plan_prompt(self, now: str, memory_companion_context: str = "") -> str:
        return build_daily_plan_prompt(self, now, memory_companion_context=memory_companion_context)

    def _build_daily_plan_prompt_section(
        self,
        now: str,
        memory_companion_context: str = "",
    ) -> PromptSection:
        return build_daily_plan_prompt_section(
            self,
            now,
            memory_companion_context=memory_companion_context,
        )
