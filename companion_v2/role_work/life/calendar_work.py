"""Authorize background activities against real, revocable calendar occurrences."""

from datetime import datetime, time, timedelta
import json
import re
from zoneinfo import ZoneInfo

from companion_v2.agenda.models import Schedule
from companion_v2.execution.models import TaskError, digest, encode, stamp


# Older plans predate the planner's structured routine field.
_READING = re.compile(r"阅读|读书|读小说|读原文|读一(?:页|段|章)|翻.{0,6}[页书]|书架")
_WRITING = re.compile(
    r"续写|创作|写小说|写故事|写短篇|写散文|写诗|动笔"
)


def routine_for(occurrence):
    if occurrence["kind"] != "role_plan" or occurrence["state"] != "accepted":
        return None
    routine = (occurrence.get("planning") or {}).get("routine")
    if routine in ("reading", "writing"):
        return routine
    title = occurrence["title"]
    if title.startswith(("取消", "暂停", "停止", "暂不", "不再")):
        return None
    if _WRITING.search(title):
        return "writing"
    if _READING.search(title):
        return "reading"
    return None


def require_window(calendar, context, window, at):
    record = calendar.read(context, window["id"])
    sequence = window["sequence"]
    schedule = Schedule.restore(record["schedule"])
    due = schedule.at(sequence)
    duration = timedelta(minutes=record["duration_minutes"])
    if str(sequence) in record["exceptions"]:
        moved = record["exceptions"][str(sequence)]
        due = datetime.fromisoformat(moved) if moved else None
    routine = routine_for(record)
    if (
        due is None
        or stamp(due) != window["start"]
        or stamp(due + duration) != window["end"]
        or routine is None
        or routine != routine_for(window)
        or not due <= at < due + duration
    ):
        raise TaskError(
            "life_schedule_changed",
            "the original calendar window moved, ended or was cancelled",
        )
    return record


def reserve_routine_slot(calendar, context, *, routine, timezone, now, items, sleep_plan=None):
    """Put pending work in the next free afternoon or evening block."""
    title, detail = {
        "reading": ("书架阅读", "按顺序继续书架中的未读片段。"),
        "writing": ("续写未完成作品", "从已有候选稿接着写，保留原有正文与人物设定。"),
    }[routine]
    zone = ZoneInfo(timezone)
    earliest = now + timedelta(minutes=15)
    busy = [
        (datetime.fromisoformat(item["start"]), datetime.fromisoformat(item["end"]))
        for item in items
        if item["state"] == "accepted"
    ]
    if sleep_plan and all(sleep_plan):
        busy.append(tuple(sleep_plan))
    duration = timedelta(minutes=30)
    day = earliest.astimezone(zone).date()
    for offset in range(8):
        date = day + timedelta(days=offset)
        start = max(earliest.astimezone(zone), datetime.combine(date, time(14), zone))
        rounded = start.replace(minute=start.minute // 15 * 15, second=0, microsecond=0)
        start = rounded if start == rounded else rounded + timedelta(minutes=15)
        while start + duration <= datetime.combine(
            date, time(21), zone
        ) and start + duration <= now + timedelta(days=7, hours=1):
            if all(start + duration <= left or start >= right for left, right in busy):
                lineage = [calendar.records.role_key(context), stamp(start), routine]
                identifier = "planned-routine-" + digest(lineage)
                if calendar.records.get(context, identifier) is not None:
                    start += timedelta(minutes=15)
                    continue
                calendar.command(
                    context,
                    identifier=identifier,
                    action="create",
                    expected_revision=0,
                    operation_key=identifier,
                    planning={"mode": "routine", "routine": routine},
                    data={
                        "kind": "role_plan",
                        "title": title,
                        "detail": detail,
                        "schedule": json.loads(encode(Schedule(start, timezone))),
                        "duration_minutes": 30,
                        "presence": "",
                    },
                )
                return stamp(start)
            start += timedelta(minutes=15)
    return None
