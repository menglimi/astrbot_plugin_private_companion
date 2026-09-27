# -*- coding: utf-8 -*-
"""日历/每日域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 458 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import asyncio
import hashlib
from .agenda_contracts import timezone_or_default
from .calendar_contracts import (
    AgendaContractError,
    calendar_lifecycle_summary,
    normalize_calendar_record,
    normalize_calendar_records,
    resolve_calendar_snapshot,
    resolve_calendar_timeline,
)
from .constants import DEFAULT_DAILY_PLAN_ITEMS
from .helpers import _today_key
from .planning import generate_daily_plan
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any

from .logging_util import get_module_logger
from .page_api_shared import _page_api_host, _page_api_host_request as request

logger = get_module_logger(__name__)



class PrivateCompanionPageApiCalendarDailyMixin:
    """日历/每日域（从 PrivateCompanionPageApi 拆出）。"""


    def _calendar_page_date(self, value: Any, *, fallback: date | None = None) -> date:
        raw = self._single_line(value, 40)
        if "T" in raw:
            raw = raw.split("T", 1)[0]
        if raw:
            try:
                return date.fromisoformat(raw)
            except ValueError as exc:
                raise ValueError("日期格式应为 YYYY-MM-DD") from exc
        if fallback is not None:
            return fallback
        now_getter = getattr(self.plugin, "_agenda_now", None)
        try:
            current = now_getter() if callable(now_getter) else datetime.now().astimezone()
            if isinstance(current, datetime):
                tz_getter = getattr(self.plugin, "_agenda_timezone_name", None)
                timezone_name = tz_getter() if callable(tz_getter) else getattr(self.plugin, "calendar_timezone", "Asia/Shanghai")
                return current.astimezone(timezone_or_default(timezone_name)).date()
            if isinstance(current, date):
                return current
        except Exception:
            pass
        return datetime.now().date()

    def _calendar_page_range(self) -> tuple[date, date]:
        """Resolve a bounded month/range query for calendar projections."""
        month = self._single_line(request.args.get("month"), 16)
        start_raw = request.args.get("start") or request.args.get("from") or request.args.get("date")
        end_raw = request.args.get("end") or request.args.get("to")
        if month:
            try:
                month_start = date.fromisoformat(f"{month[:7]}-01")
            except ValueError as exc:
                raise ValueError("月份格式应为 YYYY-MM") from exc
            next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            return month_start, next_month - timedelta(days=1)
        current = self._calendar_page_date(None)
        start = self._calendar_page_date(start_raw, fallback=current)
        end = self._calendar_page_date(end_raw, fallback=start)
        if not start_raw and not end_raw:
            # A month-sized default makes the first page useful while keeping
            # the query bounded for installations with many recurring rules.
            start = current.replace(day=1)
            next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            end = next_month - timedelta(days=1)
        if end < start:
            raise ValueError("结束日期不能早于开始日期")
        if (end - start).days > 366:
            raise ValueError("日历查询范围不能超过 366 天")
        return start, end

    def _calendar_page_records(self) -> list[dict[str, Any]]:
        getter = getattr(self.plugin, "_agenda_calendar_records_store", None)
        rows: list[dict[str, Any]] = []
        if callable(getter):
            try:
                rows = deepcopy(getter())
            except Exception:
                rows = []
        else:
            data = getattr(self.plugin, "data", {})
            if isinstance(data, dict):
                for section in ("calendar_events", "calendar_rules", "calendar_exceptions", "calendar_records"):
                    values = data.get(section)
                    if isinstance(values, list):
                        rows.extend(deepcopy(item) for item in values if isinstance(item, dict))

        # ``important_dates`` predates the long-lived calendar.  Project it as
        # read-only yearly/single-day entries so the page has one place to
        # inspect all durable dates, while the existing reminder pipeline keeps
        # owning edits and proactive birthday/anniversary behavior.
        data = getattr(self.plugin, "data", {})
        important_dates = data.get("important_dates") if isinstance(data, dict) else []
        if isinstance(important_dates, list):
            for entry in important_dates:
                if not isinstance(entry, dict) or not self._normalize_bool_value(entry.get("enabled", True)):
                    continue
                title = self._single_line(entry.get("title") or entry.get("name"), 120)
                raw_date = self._single_line(entry.get("date") or entry.get("day"), 32)
                if not title or not raw_date:
                    continue
                if "T" in raw_date:
                    raw_date = raw_date.split("T", 1)[0]
                display_date = raw_date
                # 只有「月-日」写法（03-05）才默认按年重复；带年份（2026-03-05）或带时间戳时默认单次，避免同一字段的默认值随书写格式漂移。
                repeat_yearly = self._normalize_bool_value(
                    entry.get("repeat_yearly", len(raw_date) == 5 and raw_date[2:3] == "-")
                )
                month_day = raw_date[5:] if len(raw_date) >= 10 and raw_date[4:5] == "-" else raw_date
                if repeat_yearly and len(month_day) == 5:
                    try:
                        month, day = (int(part) for part in month_day.split("-", 1))
                        date.fromisoformat(f"2000-{month:02d}-{day:02d}")
                    except (TypeError, ValueError):
                        continue
                    start_date = f"2000-{month_day}"
                    projected: dict[str, Any] = {
                        "kind": "recurrence",
                        "calendar_id": f"important-date:{self._single_line(entry.get('id'), 80) or hashlib.sha1(f'{title}|{raw_date}'.encode('utf-8', errors='ignore')).hexdigest()[:16]}",
                        "title": title,
                        "start_date": start_date,
                        "date": start_date,
                        "frequency": "yearly",
                        "interval": 1,
                        "all_day": True,
                    }
                else:
                    if len(raw_date) == 5 and raw_date[2:3] == "-":
                        raw_date = f"{self._calendar_page_date(None).year}-{raw_date}"
                    try:
                        date.fromisoformat(raw_date)
                    except ValueError:
                        continue
                    projected = {
                        "kind": "event",
                        "calendar_id": f"important-date:{self._single_line(entry.get('id'), 80) or hashlib.sha1(f'{title}|{raw_date}'.encode('utf-8', errors='ignore')).hexdigest()[:16]}",
                        "title": title,
                        "date": raw_date,
                        "start_date": raw_date,
                        "end_date": raw_date,
                        "all_day": True,
                    }
                projected.update(
                    {
                        "type": projected["kind"],
                        "priority": self._int(entry.get("priority"), 50),
                        "note": self._single_line(entry.get("note"), 180),
                        "source": "important_dates",
                        "read_only": True,
                        "legacy_date": display_date,
                    }
                )
                rows.append(projected)
        return rows

    def _calendar_page_candidates(self) -> list[dict[str, Any]]:
        """Return observation proposals separately from formal calendar rows."""

        data = getattr(self.plugin, "data", {})
        values: list[Any] = []
        if isinstance(data, dict):
            for section in ("calendar_candidates", "calendar_observations"):
                if isinstance(data.get(section), list):
                    values.extend(data[section])
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in values:
            if not isinstance(raw, dict):
                continue
            candidate = deepcopy(raw)
            candidate_id = str(candidate.get("candidate_id") or candidate.get("calendar_id") or "")
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            summary = calendar_lifecycle_summary(candidate)
            candidate["lifecycle_summary"] = summary
            candidate["source_excerpt"] = self._single_line(candidate.get("source_excerpt") or candidate.get("source_text"), 320)
            candidate["title"] = self._single_line(candidate.get("title") or "生活安排", 120)
            result.append(candidate)
        result.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
        return result[:100]

    def _calendar_page_payload(self, start: date, end: date, *, records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        timezone_name = "Asia/Shanghai"
        tz_getter = getattr(self.plugin, "_agenda_timezone_name", None)
        if callable(tz_getter):
            try:
                timezone_name = str(tz_getter() or timezone_name)
            except Exception:
                pass
        raw_records = records if records is not None else self._calendar_page_records()
        normalized = normalize_calendar_records(raw_records, timezone_name=timezone_name)
        candidates = self._calendar_page_candidates()
        candidate_audit: list[dict[str, Any]] = []
        for candidate in candidates:
            candidate_id = str(candidate.get("candidate_id") or candidate.get("calendar_id") or "")
            trace = candidate.get("decision_trace") if isinstance(candidate.get("decision_trace"), list) else []
            for entry in trace:
                if isinstance(entry, dict):
                    row = deepcopy(entry)
                    row["candidate_id"] = candidate_id
                    row["title"] = self._single_line(candidate.get("title"), 120)
                    candidate_audit.append(row)
        candidate_audit.sort(key=lambda row: str(row.get("at") or ""), reverse=True)
        instances: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        cursor = start
        while cursor <= end:
            snapshot = resolve_calendar_snapshot(normalized, cursor, timezone_name=timezone_name)
            day_events = snapshot.get("events") if isinstance(snapshot.get("events"), list) else []
            instances.extend(deepcopy(item) for item in day_events if isinstance(item, dict))
            day_conflicts = snapshot.get("conflicts") if isinstance(snapshot.get("conflicts"), list) else []
            conflicts.extend(deepcopy(item) for item in day_conflicts if isinstance(item, dict))
            cursor += timedelta(days=1)
        seen_conflicts: set[str] = set()
        unique_conflicts: list[dict[str, Any]] = []
        for item in conflicts:
            conflict_id = str(item.get("conflict_id") or "")
            if conflict_id in seen_conflicts:
                continue
            seen_conflicts.add(conflict_id)
            unique_conflicts.append(item)
        conflicts = unique_conflicts
        today = self._calendar_page_date(None)
        # Resolve from the page projection so legacy ``important_dates`` are
        # visible in today's snapshot as well as in the month record list.
        # The pure resolver is the same contract used by AgendaRuntimeMixin.
        today_snapshot = resolve_calendar_snapshot(normalized, today, timezone_name=timezone_name)
        timeline = resolve_calendar_timeline(
            normalized,
            today,
            timezone_name=timezone_name,
            history_days=3,
            horizon_days=14,
        )
        return {
            "records": normalized,
            "candidates": candidates,
            "candidate_audit": candidate_audit[:100],
            "instances": instances,
            "today": today_snapshot,
            "timeline": timeline,
            "conflicts": conflicts,
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "timezone": timezone_name,
            "calendar_version": today_snapshot.get("calendar_version", 1) if isinstance(today_snapshot, dict) else 1,
        }

    async def confirm_calendar_candidate(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        candidate_id = self._single_line(payload.get("candidate_id") or payload.get("id"), 160) if isinstance(payload, dict) else ""
        decide = getattr(self.plugin, "_agenda_decide_calendar_candidate", None)
        if not candidate_id or not callable(decide):
            return self._error("缺少候选记录 ID")
        try:
            lock = getattr(self.plugin, "_data_lock", None)
            if lock is None:
                saved = decide(candidate_id, "confirm", source="manual", note="观察页确认")
            else:
                async with lock:
                    saved = decide(candidate_id, "confirm", source="manual", note="观察页确认")
            if not saved:
                return self._error("未找到对应候选记录", status_code=404)
            return self._ok({"candidate": saved, "confirmed": True})
        except (ValueError, AgendaContractError) as exc:
            return self._error(str(exc))
        except Exception as exc:
            logger.error("确认日历候选失败: %s", self._single_line(exc, 180), exc_info=True)
            return self._exception_error("确认候选失败")

    async def reject_calendar_candidate(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        candidate_id = self._single_line(payload.get("candidate_id") or payload.get("id"), 160) if isinstance(payload, dict) else ""
        decide = getattr(self.plugin, "_agenda_decide_calendar_candidate", None)
        if not candidate_id or not callable(decide):
            return self._error("缺少候选记录 ID")
        try:
            lock = getattr(self.plugin, "_data_lock", None)
            if lock is None:
                saved = decide(candidate_id, "reject", source="manual", note="观察页忽略")
            else:
                async with lock:
                    saved = decide(candidate_id, "reject", source="manual", note="观察页忽略")
            if not saved:
                return self._error("未找到对应候选记录", status_code=404)
            return self._ok({"candidate": saved, "rejected": True})
        except (ValueError, AgendaContractError) as exc:
            return self._error(str(exc))
        except Exception as exc:
            logger.error("忽略日历候选失败: %s", self._single_line(exc, 180), exc_info=True)
            return self._exception_error("忽略候选失败")

    async def get_calendar(self) -> dict[str, Any]:
        try:
            start, end = self._calendar_page_range()
            lock = getattr(self.plugin, "_data_lock", None)
            if lock is None:
                payload = self._calendar_page_payload(start, end)
            else:
                async with lock:
                    payload = self._calendar_page_payload(start, end)
            return self._ok(payload)
        except ValueError as exc:
            return self._error(str(exc))
        except Exception as exc:
            logger.error("获取日历失败: %s", self._single_line(exc, 180), exc_info=True)
            return self._exception_error("获取日历失败")

    async def get_calendar_conflicts(self) -> dict[str, Any]:
        result = await self.get_calendar()
        if not isinstance(result, dict) or result.get("success") is False:
            return result
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        return self._ok({
            "conflicts": data.get("conflicts", []),
            "count": len(data.get("conflicts", [])) if isinstance(data.get("conflicts"), list) else 0,
            "range": data.get("range", {}),
            "today": data.get("today", {}),
        })

    async def preview_calendar(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        record = payload.get("record") if isinstance(payload, dict) and isinstance(payload.get("record"), dict) else payload
        if not isinstance(record, dict):
            return self._error("日历记录格式无效")
        try:
            tz_getter = getattr(self.plugin, "_agenda_timezone_name", None)
            timezone_name = tz_getter() if callable(tz_getter) else getattr(self.plugin, "calendar_timezone", "Asia/Shanghai")
            normalized = normalize_calendar_record(record, timezone_name=str(timezone_name or "Asia/Shanghai"))
            raw_records = self._calendar_page_records()
            existing_id = str(normalized.get("calendar_id") or "")
            merged = [item for item in raw_records if str(item.get("calendar_id") or "") != existing_id]
            merged.append(normalized)
            start_value = payload.get("start") if isinstance(payload, dict) else None
            end_value = payload.get("end") if isinstance(payload, dict) else None
            start = self._calendar_page_date(start_value)
            end = self._calendar_page_date(end_value, fallback=start)
            if end < start or (end - start).days > 366:
                raise ValueError("预览范围无效")
            projected = self._calendar_page_payload(start, end, records=merged)
            projected["record"] = normalized
            return self._ok(projected)
        except (ValueError, AgendaContractError) as exc:
            return self._error(str(exc))
        except Exception as exc:
            logger.error("预览日历失败: %s", self._single_line(exc, 180), exc_info=True)
            return self._exception_error("预览日历失败")

    async def upsert_calendar(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        record = payload.get("record") if isinstance(payload, dict) and isinstance(payload.get("record"), dict) else payload
        if not isinstance(record, dict):
            return self._error("日历记录格式无效")
        upsert = getattr(self.plugin, "_agenda_upsert_calendar_record", None)
        if not callable(upsert):
            return self._error("当前运行环境不支持日历存储", status_code=503)
        try:
            lock = getattr(self.plugin, "_data_lock", None)
            if lock is None:
                saved = upsert(record)
            else:
                async with lock:
                    saved = upsert(record)
            return self._ok({"record": saved})
        except (ValueError, AgendaContractError) as exc:
            return self._error(str(exc))
        except Exception as exc:
            logger.error("保存日历失败: %s", self._single_line(exc, 180), exc_info=True)
            return self._exception_error("保存日历失败")

    async def cancel_calendar(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        calendar_id = self._single_line(
            payload.get("calendar_id") or payload.get("id") or payload.get("record_id"),
            160,
        ) if isinstance(payload, dict) else ""
        cancel = getattr(self.plugin, "_agenda_cancel_calendar_record", None)
        if not calendar_id or not callable(cancel):
            return self._error("缺少日历记录 ID")
        try:
            lock = getattr(self.plugin, "_data_lock", None)
            if lock is None:
                cancelled = bool(cancel(calendar_id))
            else:
                async with lock:
                    cancelled = bool(cancel(calendar_id))
            if not cancelled:
                return self._error("未找到对应日历记录", status_code=404)
            return self._ok({"calendar_id": calendar_id, "cancelled": True})
        except Exception as exc:
            logger.error("取消日历失败: %s", self._single_line(exc, 180), exc_info=True)
            return self._exception_error("取消日历失败")

    def _setup_guide_fallback_daily_plan(self, reason: str = "timeout") -> dict[str, Any]:
        # 与同一计划里的 "date"(_today_key()，插件时区)保持一致，
        # 避免宿主时区与插件时区不同时 "generated_at" 与 "date" 跨天不一致。
        now = datetime.strptime(_today_key(), "%Y-%m-%d").strftime("%Y-%m-%d %H:%M")
        plan = {
            "date": _today_key(),
            "generated_at": now,
            "source": f"setup_fallback:{self._single_line(reason, 40) or 'fallback'}",
            "provider_id": "",
            "raw": "setup_fallback",
            "items": [dict(item) for item in DEFAULT_DAILY_PLAN_ITEMS],
        }
        normalizer = getattr(self.plugin, "_normalize_plan_item_intervals", None)
        if callable(normalizer):
            normalizer(plan["items"])
        return plan

    async def _setup_guide_generate_daily_plan_fast(self, timeout: float = 18.0) -> tuple[dict[str, Any], str, bool]:
        today = _today_key()
        task = getattr(self.plugin, "_setup_guide_daily_plan_task", None)
        async with self.plugin._data_lock:
            current_plan = self.plugin.data.get("daily_plan", {})
            if (
                isinstance(current_plan, dict)
                and current_plan.get("date") == today
                and (isinstance(current_plan.get("items"), list) or isinstance(current_plan.get("schedule"), list))
            ):
                source = str(current_plan.get("source") or "")
                if source.startswith("setup_fallback:") and isinstance(task, asyncio.Task) and not task.done():
                    return dict(current_plan), "background", True
                if source.startswith("setup_fallback:"):
                    pass
                else:
                    return dict(current_plan), "cached", False

        async def _runner() -> dict[str, Any]:
            state_getter = getattr(self.plugin, "_ensure_daily_state", None)
            if callable(state_getter):
                try:
                    await state_getter(force=False, passive_fast=True)
                except TypeError:
                    await state_getter(force=False)
            plan = await generate_daily_plan(self.plugin)
            async with self.plugin._data_lock:
                self.plugin.data["daily_plan"] = plan
                refresher = getattr(self.plugin, "_refresh_daily_state_location_from_plan", None)
                if callable(refresher):
                    refresher(plan=plan)
                self.plugin.data["detail_enhanced_day"] = str((plan or {}).get("date") or today)
                self.plugin.data["detail_enhanced_segments"] = {}
                self.plugin.data["daily_story_plan"] = {}
                self.plugin._save_data_sync(
                    sections={
                        "daily_plan",
                        "daily_state",
                        "detail_enhanced_day",
                        "detail_enhanced_segments",
                        "daily_story_plan",
                    }
                )
            return plan

        if not isinstance(task, asyncio.Task) or task.done():
            task = self._create_page_background_task(_runner(), label="setup_daily_plan")
            if task is None:
                return {}, "unavailable", False
            def _consume_setup_daily_task(done_task: asyncio.Task) -> None:
                try:
                    done_task.result()
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.warning(
                        "首次配置后台日程生成失败: %s",
                        self._single_line(exc, 180),
                        exc_info=True,
                    )
            task.add_done_callback(_consume_setup_daily_task)
            setattr(self.plugin, "_setup_guide_daily_plan_task", task)

        try:
            plan = await asyncio.wait_for(asyncio.shield(task), timeout=max(3.0, float(timeout or 18.0)))
            return dict(plan) if isinstance(plan, dict) else {}, "generated", False
        except asyncio.TimeoutError:
            async with self.plugin._data_lock:
                current_plan = self.plugin.data.get("daily_plan", {})
                if isinstance(current_plan, dict) and current_plan.get("date") == today:
                    return dict(current_plan), "background", True
                fallback = self._setup_guide_fallback_daily_plan("timeout")
                return fallback, "fallback_timeout", True
        except Exception as exc:
            logger.warning(
                "首次配置快速日程生成失败，使用兜底日程: %s",
                self._single_line(exc, 180),
                exc_info=True,
            )
            fallback = self._setup_guide_fallback_daily_plan("error")
            return fallback, "fallback_error", False
