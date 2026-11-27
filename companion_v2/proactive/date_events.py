"""Optional greetings for explicit recurring dates and fixed Gregorian holidays."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from companion_v2.conversation.date_context import (
    FIXED_HOLIDAYS,
    _LUNAR_DAYS,
    _LUNAR_MONTHS,
    _personal_date_label,
    _personal_date_spec,
    lunar_date_parts,
    project_personal_dates,
)
from companion_v2.execution.models import TaskError, digest, require, stamp
from companion_v2.relationships import anniversaries as relationship_anniversaries

from .configuration import important_dates, local_time
from .contact_models import ContactIntent
from .date_context import project_important_dates
from .models import EvidenceSnapshot, Opportunity, Thought, ThoughtKind


WINDOW = timedelta(minutes=45)
SOURCE_LABELS = {
    "fixed_gregorian_holiday": "固定公历节日",
    "operator_configured_recurring_date": "旧版手动保留的公历日期",
    "operator_configured_lunar_date": "旧版手动保留的农历日期",
    "profile_role_birthday": "角色档案中的生日或纪念日",
    "profile_relationship_date": "当前私聊资料中的生日或纪念日",
    "auto_relationship_anniversary": "每日反思记下的互动纪念日",
}


def _gregorian_days_until(day, month, day_of_month):
    """Whole days from ``day`` to the next occurrence of month/day, or None.

    Four years are searched so 29 February reaches its real next occurrence rather
    than being dropped. A date that exists in no searched year (30 February) is
    skipped rather than guessed at.
    """
    for year in range(day.year, day.year + 5):
        try:
            candidate = day.replace(year=year, month=month, day=day_of_month)
        except ValueError:
            continue
        ahead = (candidate - day).days
        if ahead >= 0:
            return ahead
    return None


def _lunar_days_until(day, month, day_of_month, leap_month=False):
    """Whole days from ``day`` to the next lunar occurrence within ~420 days, or None."""
    if lunar_date_parts(day) is None:
        return None
    target = (month, day_of_month, bool(leap_month))
    for offset in range(420):
        candidate = day + timedelta(days=offset)
        lunar = lunar_date_parts(candidate)
        if lunar is not None and (lunar[1], lunar[2], lunar[3]) == target:
            return offset
    return None


def _format_configured_date_text(calendar, month, day_of_month, leap_month=False):
    if calendar == "lunar" and 1 <= month <= len(_LUNAR_MONTHS) and 1 <= day_of_month <= len(_LUNAR_DAYS):
        return f"农历{'闰' if leap_month else ''}{_LUNAR_MONTHS[month - 1]}{_LUNAR_DAYS[day_of_month - 1]}"
    return f"{month}月{day_of_month}日"


class DateContactEvents:
    def __init__(self, contacts, messages=None):
        self.contacts, self.runtime = contacts, contacts.runtime
        self.messages = messages

    def _entries_for(self, day, configured, lookahead):
        """Date labels for this day, plus any the lookahead window reaches.

        With ``lookahead`` at 0 this is exactly the V1 behaviour for legacy
        operator-configured rows: only today's own labels, and no extra key in
        the marker. Above 0 an approaching date is included with ``days_until``,
        so the role may mention it *before* the day rather than only on it.
        """
        entries = []
        fixed = FIXED_HOLIDAYS.get((day.month, day.day))
        if fixed:
            entries.append({"basis": "fixed_gregorian_holiday", "label": fixed})
        lunar = lunar_date_parts(day) if any(item.calendar == "lunar" for item in configured) else None
        for item in configured:
            if item.calendar == "gregorian":
                if (item.month, item.day) == (day.month, day.day):
                    entries.append({"basis": "operator_configured_recurring_date", "label": item.label})
                elif lookahead:
                    ahead = _gregorian_days_until(day, item.month, item.day)
                    if ahead is not None and 0 < ahead <= lookahead:
                        entries.append({"basis": "operator_configured_recurring_date",
                                        "label": item.label, "days_until": ahead})
            elif (item.calendar == "lunar" and lunar is not None
                    and (item.month, item.day, item.leap_month) == (lunar[1], lunar[2], lunar[3])):
                entries.append({"basis": "operator_configured_lunar_date", "label": item.label,
                                "lunar_year": lunar[0], "leap_month": lunar[3]})
        return entries

    def _profile_values(self, context, at):
        """Read only the explicit birthday/anniversary fields from stable profiles."""
        role_birthday = user_birthday = ""
        messages = getattr(self, "messages", None)
        role_context = getattr(messages, "role_profile_context", None)
        if role_context is not None:
            try:
                snapshot = role_context.capture(context, at)
                fields = snapshot.get("fields", {}) if isinstance(snapshot, dict) else {}
                value = fields.get("birthday", "") if isinstance(fields, dict) else ""
                if isinstance(value, str):
                    role_birthday = value
            except Exception:
                pass
        relationship_context = getattr(messages, "relationship_profile_context", None)
        if relationship_context is not None:
            try:
                snapshot = relationship_context.capture(context, at)
                fields = snapshot.get("fields", {}) if isinstance(snapshot, dict) else {}
                value = fields.get("user_birthday", "") if isinstance(fields, dict) else ""
                if isinstance(value, str):
                    user_birthday = value
            except Exception:
                pass
        return role_birthday, user_birthday

    def _personal_entries(self, context, at, timezone_name, lookahead):
        role_birthday, user_birthday = self._profile_values(context, at)
        if not role_birthday and not user_birthday:
            return ()
        try:
            projection = project_personal_dates(
                at, timezone_name, role_birthday=role_birthday,
                user_birthday=user_birthday, lookahead_days=lookahead)
        except (TypeError, ValueError):
            return ()
        entries = []
        for item in projection.get("entries", ()):
            subject = item.get("subject")
            if subject not in ("role", "user"):
                continue
            entry = {
                "basis": ("profile_role_birthday" if subject == "role"
                          else "profile_relationship_date"),
                "label": item["label"],
                "date": item["date"],
                "calendar": item["calendar"],
            }
            if item.get("days_until"):
                entry["days_until"] = item["days_until"]
            entries.append(entry)
        return tuple(entries)

    def _auto_anniversary_entries(self, context, at, timezone_name, lookahead):
        entries = []
        for row in relationship_anniversaries.upcoming(
                self.runtime, context, at, timezone_name=timezone_name,
                lookahead_days=lookahead):
            entry = {"basis": row["basis"], "label": row["label"],
                     "date": row["date"], "calendar": row["calendar"]}
            if row["days_until"]:
                entry["days_until"] = row["days_until"]
            entries.append(entry)
        return tuple(entries)

    @staticmethod
    def _merge_entries(*groups):
        merged, seen = [], set()
        for entries in groups:
            for entry in entries:
                key = (entry.get("label"), entry.get("days_until", 0))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(entry)
        return merged

    @staticmethod
    def _source_label(entry):
        label = SOURCE_LABELS.get(entry.get("basis"), "日期资料")
        text = label + "「" + str(entry.get("label", "未命名日期")) + "」"
        ahead = entry.get("days_until")
        if type(ahead) is int and ahead > 0:
            text += f"（{ahead} 天后）"
        return text

    def _slot(self, context, preferences, at):
        if not preferences.values["date_checkin_enabled"] or preferences.reason("greeting"):
            return None
        zone = ZoneInfo(preferences.values["quiet_timezone"])
        day = at.astimezone(zone).date()
        configured = important_dates(preferences.values["important_dates"])
        lookahead = int(preferences.values.get("important_date_lookahead_days") or 0)
        entries = self._merge_entries(
            self._entries_for(day, configured, lookahead),
            self._personal_entries(context, at, zone.key, lookahead),
            self._auto_anniversary_entries(context, at, zone.key, lookahead),
        )
        if not entries:
            return None
        wall = local_time(preferences.values["date_checkin_time"])
        opened = datetime.combine(day, wall, tzinfo=zone).astimezone(timezone.utc)
        resolved = opened.astimezone(zone)
        if (resolved.date() != day or resolved.time().replace(tzinfo=None) != wall
                or not opened <= at < opened + WINDOW):
            return None
        opportunity = Opportunity("date-" + digest([context.key, day.isoformat()]),
            context.scope, opened, opened + WINDOW)
        marker = {"local_date": day.isoformat(), "timezone": zone.key,
                  "opened_at": stamp(opened), "expires_at": stamp(opportunity.expires_at),
                  "entries": entries}
        return opportunity, marker

    def select(self, context, preferences, at):
        slot = self._slot(context, preferences, at)
        if slot is None:
            return None
        opportunity, marker = slot
        task_id = self.contacts.task_id(context, opportunity)
        if (self.runtime.read_one("SELECT 1 FROM execution_tasks WHERE task_id = ?", (task_id,))
                or self.runtime.read_one("SELECT 1 FROM proactive_contacts WHERE task_id = ?", (task_id,))):
            return None
        labels = "、".join(self._source_label(item) for item in marker["entries"])
        thought = Thought("date-greeting-" + digest(marker), opportunity, ThoughtKind.CONVERSATION,
            f"按已配置时区，今天是{marker['local_date']}，可参考的日期语境有：{labels}。"
            "生日与纪念日来自角色档案或当前私聊对象的既有资料，其他日期来自固定节日或旧版配置；"
            "固定节日不代表放假、调休或约定已经发生。这些资料不证明今天发生现实事件，"
            "也不需要让对方再次确认。"
            "合适时自然提起，否则跳过；不要把它做成固定的每日问候任务。",
            EvidenceSnapshot(context.scope, at), 1.0)
        return opportunity, (ContactIntent("greeting", thought),), marker

    def upcoming(self, context, preferences, at, *, lookahead_days=None):
        """Return already-resolved upcoming dates for the read-only dashboard card."""
        zone = ZoneInfo(preferences.values["quiet_timezone"])
        if lookahead_days is None:
            lookahead_days = int(preferences.values.get("important_date_lookahead_days") or 0)
        if type(lookahead_days) is not int or not 0 <= lookahead_days <= 30:
            return ()
        local_day = at.astimezone(zone).date()
        rows = []
        for offset in range(lookahead_days + 1):
            candidate = local_day + timedelta(days=offset)
            fixed = FIXED_HOLIDAYS.get((candidate.month, candidate.day))
            if fixed:
                rows.append({"basis": "fixed_gregorian_holiday", "label": fixed,
                             "date": candidate.isoformat(), "days_until": offset,
                             "calendar": "gregorian"})
        try:
            projected = project_important_dates(at, {
                "important_dates": preferences.values["important_dates"],
                "lookahead_days": lookahead_days,
                "timezone": zone.key,
            })
            for item in projected["entries"]:
                rows.append({
                    "basis": ("operator_configured_lunar_date"
                              if item.get("calendar") == "lunar"
                              else "operator_configured_recurring_date"),
                    "label": item["label"], "date": item["date"],
                    "days_until": item["days_until"],
                    "calendar": item.get("calendar", "gregorian"),
                })
        except (TypeError, ValueError):
            pass
        for entry in self._personal_entries(context, at, zone.key, lookahead_days):
            rows.append({**entry, "days_until": int(entry.get("days_until") or 0)})
        rows.extend(relationship_anniversaries.upcoming(
            self.runtime, context, at, timezone_name=zone.key,
            lookahead_days=lookahead_days))
        unique, seen = [], set()
        for row in sorted(rows, key=lambda item: (item.get("days_until", 0), item.get("label", ""))):
            key = (row.get("label"), row.get("date"))
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        return tuple(unique)

    def configured_summary(self, context, preferences, at, *, lookahead_days=None):
        """Return all configured personal/custom/anniversary dates with year-round countdowns."""
        zone = ZoneInfo(preferences.values["quiet_timezone"])
        if lookahead_days is None:
            lookahead_days = int(preferences.values.get("important_date_lookahead_days") or 0)
        local_day = at.astimezone(zone).date()
        items = []

        def _append_item(label, calendar, month, day_num, leap, *, source, basis):
            if calendar == "lunar":
                days_until = _lunar_days_until(local_day, month, day_num, leap)
            else:
                days_until = _gregorian_days_until(local_day, month, day_num)
            next_date = (local_day + timedelta(days=days_until)).isoformat() if days_until is not None else ""
            items.append({
                "label": label,
                "date_text": _format_configured_date_text(calendar, month, day_num, leap),
                "date": next_date,
                "calendar": calendar,
                "source": source,
                "basis": basis,
                "days_until": days_until,
            })

        role_birthday, user_birthday = self._profile_values(context, at)
        for raw, default_label, source, basis in (
            (role_birthday, "角色生日", "角色档案", "profile_role_birthday"),
            (user_birthday, "对方生日", "私聊资料", "profile_relationship_date"),
        ):
            parsed = _personal_date_spec(raw)
            if parsed is None:
                continue
            calendar, month, day_num, leap = parsed
            _append_item(_personal_date_label(raw, default_label), calendar, month, day_num, leap,
                         source=source, basis=basis)

        try:
            configured = important_dates(preferences.values.get("important_dates", "[]"))
        except (TypeError, ValueError):
            configured = ()
        for item in configured:
            basis = ("operator_configured_lunar_date" if item.calendar == "lunar"
                     else "operator_configured_recurring_date")
            _append_item(item.label, item.calendar, item.month, item.day, item.leap_month,
                         source="自定义重要日期", basis=basis)

        for row in relationship_anniversaries.list_active(self.runtime, context):
            _append_item(row["label"], "gregorian", row["month"], row["day"], False,
                         source="反思纪念日", basis="auto_relationship_anniversary")

        deduped, seen = [], set()
        for entry in sorted(items, key=lambda item: (
            item["days_until"] is None,
            item["days_until"] if item["days_until"] is not None else 99999,
            item["label"],
            item["source"],
        )):
            key = (entry["basis"], entry["label"], entry["date_text"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(entry)

        next_upcoming = next((item for item in deduped if type(item.get("days_until")) is int), None)
        return {
            "configured_count": len(deduped),
            "configured_items": deduped,
            "next_upcoming": next_upcoming,
            "lookahead_days": int(lookahead_days),
        }

    def validate(self, context, marker, at):
        if type(marker) is not dict or set(marker) != {
                "local_date", "timezone", "opened_at", "expires_at", "entries"}:
            raise TaskError("contact_date_invalid", "date greeting requires its original local day")
        preferences = require(self.contacts.preferences.read(context, observed_at=at))
        current = self._slot(context, preferences, at)
        if current is None or current[1] != marker:
            raise TaskError("contact_date_expired", "original date greeting is no longer current")
