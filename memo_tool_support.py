# -*- coding: utf-8 -*-
"""Memo tool parsing and presentation boundary.

This component intentionally owns no plugin state.  The mixin keeps its historical
method API and delegates here with the environment clock conversion dependency.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Callable

from .helpers import _safe_float, _single_line


class MemoToolSupport:
    """Pure memo parsing/selection helpers with an explicit time-zone dependency."""

    def __init__(self, fromtimestamp: Callable[[float], datetime]) -> None:
        self._environment_fromtimestamp = fromtimestamp

    def parse_due_time(self, value: Any, *, now: float) -> tuple[float, str]:
        if value is None or value == "":
            return 0.0, ""
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            return (timestamp, "") if timestamp > 0 else (0.0, "提醒时间无效")

        text = _single_line(value, 100).strip()
        if not text:
            return 0.0, ""
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            timestamp = float(text)
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            return (timestamp, "") if timestamp > 0 else (0.0, "提醒时间无效")

        base = self._environment_fromtimestamp(now)
        normalized = text.replace("／", "/").replace("：", ":").strip()
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            if parsed.tzinfo is None and base.tzinfo is not None:
                parsed = parsed.replace(tzinfo=base.tzinfo)
            if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", normalized):
                parsed = parsed.replace(hour=9)
            return parsed.timestamp(), ""
        except ValueError:
            pass
        for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M", "%Y/%m/%d", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(normalized, fmt)
            except ValueError:
                continue
            if parsed.tzinfo is None and base.tzinfo is not None:
                parsed = parsed.replace(tzinfo=base.tzinfo)
            if fmt in {"%Y/%m/%d", "%Y-%m-%d"}:
                parsed = parsed.replace(hour=9)
            return parsed.timestamp(), ""

        def natural_number(raw: str) -> float:
            if re.fullmatch(r"\d+(?:\.\d+)?", raw):
                return float(raw)
            digits = {
                "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
            }
            if raw == "十":
                return 10.0
            if "十" in raw:
                left, right = raw.split("十", 1)
                return float((digits.get(left, 1) * 10) + digits.get(right, 0))
            return float(digits.get(raw, 0))

        relative_day_offset: int | None = None
        duration_match = re.search(r"(\d+(?:\.\d+)?|[一二两三四五六七八九十]+)\s*(分钟|小时|天|周)后", normalized)
        if duration_match:
            amount = natural_number(duration_match.group(1))
            unit = duration_match.group(2)
            seconds = amount * {"分钟": 60, "小时": 3600, "天": 86400, "周": 7 * 86400}[unit]
            has_clock = bool(re.search(r"点|时|:\d|早上|上午|中午|下午|傍晚|晚上|凌晨", normalized))
            if unit in {"天", "周"} and has_clock and amount.is_integer():
                relative_day_offset = int(amount) * (7 if unit == "周" else 1)
            else:
                return now + seconds, ""
        if "半小时后" in normalized:
            return now + 1800, ""

        day_offset: int | None = relative_day_offset
        if "大后天" in normalized:
            day_offset = 3
        elif "后天" in normalized:
            day_offset = 2
        elif any(token in normalized for token in ("明天", "明早", "明晚", "明日下午", "明日上午")):
            day_offset = 1
        elif any(token in normalized for token in ("今天", "今早", "今晚", "今夜", "今日")):
            day_offset = 0

        target_date = (base + timedelta(days=day_offset or 0)).date()
        weekday_match = re.search(r"(下|本|这)?\s*(?:周|星期)([一二三四五六日天])", normalized)
        if weekday_match:
            target_weekday = "一二三四五六日".index("日" if weekday_match.group(2) == "天" else weekday_match.group(2))
            prefix = weekday_match.group(1) or ""
            if prefix == "下":
                days = 7 - base.weekday() + target_weekday
            else:
                days = target_weekday - base.weekday()
                if days < 0:
                    days += 7
            target_date = (base + timedelta(days=days)).date()
            day_offset = days
        else:
            month_day_match = re.search(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})(?:日|号)?", normalized)
            if month_day_match:
                year = int(month_day_match.group(1) or base.year)
                month = int(month_day_match.group(2))
                day = int(month_day_match.group(3))
                try:
                    candidate = base.replace(year=year, month=month, day=day).date()
                except ValueError:
                    return 0.0, "提醒日期无效"
                if not month_day_match.group(1) and candidate < base.date():
                    try:
                        candidate = base.replace(year=base.year + 1, month=month, day=day).date()
                    except ValueError:
                        return 0.0, "提醒日期无效"
                target_date = candidate
                day_offset = (candidate - base.date()).days

        clock_number = r"(?:\d{1,2}|[零〇一二两三四五六七八九十]{1,3})"
        time_match = re.search(
            rf"(?<!\d)({clock_number})\s*(?:点|时|:)(?:\s*({clock_number})\s*分?)?",
            normalized,
        )
        has_half = bool(re.search(r"(?:点|时)\s*半", normalized))
        quarter_match = re.search(r"(?:点|时)\s*([一三])刻", normalized)
        if time_match:
            hour = int(natural_number(time_match.group(1)))
            if has_half:
                minute = 30
            elif quarter_match:
                minute = 15 if quarter_match.group(1) == "一" else 45
            else:
                minute = int(natural_number(time_match.group(2) or "0"))
            if hour > 23 or minute > 59:
                return 0.0, "提醒时间无效"
        elif day_offset is not None:
            if "凌晨" in normalized:
                hour, minute = 0, 0
            elif "中午" in normalized:
                hour, minute = 12, 0
            elif "下午" in normalized:
                hour, minute = 15, 0
            elif "傍晚" in normalized:
                hour, minute = 18, 0
            elif any(token in normalized for token in ("晚上", "今晚", "今夜", "明晚")):
                hour, minute = 20, 0
            else:
                hour, minute = 9, 0
        else:
            return 0.0, "无法识别提醒时间，请提供例如“明早9点”或“2026-07-15 09:00”"

        evening = any(token in normalized for token in ("晚上", "今晚", "今夜", "明晚"))
        if evening and hour in {0, 12}:
            hour = 0
            target_date += timedelta(days=1)
        elif any(token in normalized for token in ("下午", "傍晚")) and hour < 12:
            hour += 12
        elif evening and hour < 12:
            hour += 12
        elif "中午" in normalized and hour < 11:
            hour += 12
        elif "凌晨" in normalized and hour == 12:
            hour = 0
        try:
            parsed = base.replace(
                year=target_date.year,
                month=target_date.month,
                day=target_date.day,
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
        except ValueError:
            return 0.0, "提醒时间无效"
        if weekday_match and parsed.timestamp() <= now:
            parsed += timedelta(days=7)
        elif day_offset is None and parsed.timestamp() <= now:
            parsed += timedelta(days=1)
        return parsed.timestamp(), ""

    def note_view(
        self,
        note: dict[str, Any],
        *,
        number: int = 0,
        content_limit: int = 240,
    ) -> dict[str, Any]:
        due_at = _safe_float(note.get("due_at"), 0.0)
        due_text = ""
        if due_at > 0:
            try:
                due_text = self._environment_fromtimestamp(due_at).strftime("%Y-%m-%d %H:%M")
            except (OSError, OverflowError, ValueError):
                due_text = datetime.fromtimestamp(due_at).strftime("%Y-%m-%d %H:%M")
        raw_content = str(note.get("content") or "")
        result = {
            "id": _single_line(note.get("id"), 64),
            "title": _single_line(note.get("title"), 60),
            "content": raw_content[:content_limit],
            "content_truncated": len(raw_content) > content_limit,
            "status": _single_line(note.get("status"), 20) or "active",
            "due_at": due_at,
            "due_text": due_text,
            "repeat": _single_line(note.get("repeat"), 20) or "none",
            "remind_enabled": bool(note.get("remind_enabled")),
            "pinned": bool(note.get("pinned")),
            "color": _single_line(note.get("color"), 20) or "yellow",
        }
        if number > 0:
            result["number"] = number
        return result

    def find_matches(
        self,
        notes: list[dict[str, Any]],
        selector: Any,
        *,
        status: str = "",
    ) -> list[dict[str, Any]]:
        eligible = [item for item in notes if not status or item.get("status") == status]
        value = _single_line(selector, 100).strip(" \t\r\n‘’“”'\"《》【】[]")
        if not value:
            return []
        exact_id = [item for item in eligible if str(item.get("id") or "") == value]
        if exact_id:
            return exact_id
        number_match = re.fullmatch(r"第?\s*(\d+)\s*(?:张|条|个)?", value)
        if number_match:
            index = int(number_match.group(1)) - 1
            return [eligible[index]] if 0 <= index < len(eligible) else []
        folded = value.casefold()
        exact_title = [item for item in eligible if _single_line(item.get("title"), 60).casefold() == folded]
        if exact_title:
            return exact_title
        return [
            item
            for item in eligible
            if folded in f"{item.get('title', '')}\n{item.get('content', '')}".casefold()
        ]
